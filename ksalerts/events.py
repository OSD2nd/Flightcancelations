"""Cancellation and delay log for the History page.

Each time live airline status shows a flight cancelled (or 15+ minutes late),
we store it once with a weather snapshot: the official airport report and
forecast at the time, small copies of the airport/Red Mountain webcams, and
later the airport report closest to the scheduled time.
"""
import io, json, os, urllib.request
from datetime import datetime, timezone

from . import aviation

EVENTS = "data/events.json"
IMG_DIR = "docs/history/img"
# Red Mountain cams that look over the valley: (archive folder, file prefix, label)
RED_CAMS = [("granite", "granitecam", "Red Mountain, Granite"), ("greyline", "greyline", "Red Mountain, Greyline")]
RED_PAGE = "https://wxdata.redresort.com/wxdata/weather.php"
RED_BASE = "https://wxdata.redresort.com/wxdata/"
_red_cache = {}
DELAY_MIN = 15


def load():
    try:
        with open(EVENTS) as f:
            return json.load(f)
    except Exception:
        return []


def save(evs):
    os.makedirs("data", exist_ok=True)
    evs.sort(key=lambda e: (e["date"], e["sched"]), reverse=True)
    with open(EVENTS, "w") as f:
        json.dump(evs, f, indent=1)


def _minutes(a, b):
    h1, m1 = map(int, a.split(":"))
    h2, m2 = map(int, b.split(":"))
    return (h2 * 60 + m2) - (h1 * 60 + m1)


def _snap(url, path, width=640):
    """Save a small copy of a webcam photo. Returns the path, or None."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "kootenay-flight-risk"})
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            im.thumbnail((width, width))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            im.save(path, "JPEG", quality=70)
        except ImportError:
            if len(raw) > 400_000:
                return None
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(raw)
        return path
    except Exception as e:
        print(f"[warn] snapshot failed {url}: {e}")
        return None


def snapshots(cfg, tag):
    """Grab the webcams listed in config 'history_snapshots' once per day+airport."""
    out = []
    for i, c in enumerate(cfg.get("history_snapshots", [])):
        p = _snap(c["url"], f"{IMG_DIR}/{tag}_{i}.jpg")
        if p:
            out.append({"name": c["name"], "src": p.replace("docs/", "", 1)})
    return out


def wx_block(m):
    """Decoded airport report for the page."""
    if not m:
        return None
    return {"time": datetime.fromtimestamp(m["obsTime"], timezone.utc).isoformat(timespec="minutes")
            if m.get("obsTime") else m.get("time"),
            "ceiling_ft": aviation.ceiling(m.get("clouds"), m.get("vertVis")) if "clouds" in m else m.get("ceiling_ft"),
            "vis_sm": aviation.vis_sm(m.get("visib")) if "visib" in m else m.get("vis_sm"),
            "wx": m.get("wxString") or m.get("wx") or "", "raw": m.get("rawOb") or m.get("raw")}


def _red_frames():
    """{folder: [(local time, url)]} from the resort's 24-hour archive (fetched once per run)."""
    if "f" not in _red_cache:
        import re
        out = {}
        try:
            with urllib.request.urlopen(RED_PAGE, timeout=45) as r:
                page = r.read().decode("utf-8", "ignore")
            for folder, prefix, _ in RED_CAMS:
                for f in set(re.findall(r"camArchive/24hr/%s/(%s_[^'\"]+\.jpg)" % (folder, prefix), page)):
                    m = re.search(r"_(\d\d)-(\d\d)-(\d\d)-(\d\d):(\d\d)", f)
                    if m:
                        y, mo, d, h, mi = map(int, m.groups())
                        out.setdefault(folder, []).append(
                            (datetime(2000 + y, mo, d, h, mi), f"{RED_BASE}camArchive/24hr/{folder}/{f}"))
        except Exception as e:
            print(f"[warn] Red Mountain archive unavailable: {e}")
        _red_cache["f"] = out
    return _red_cache["f"]


def red_at(e, now):
    """Save the Granite and Greyline photos closest to the flight time. Returns True if done."""
    t = datetime.fromisoformat(f"{e['date']}T{e['sched']}")
    if now < t:
        return False  # wait until the flight time has passed
    frames = _red_frames()
    got = []
    for folder, prefix, label in RED_CAMS:
        near = [(abs((ft - t).total_seconds()), ft, u) for ft, u in frames.get(folder, [])]
        near = [x for x in near if x[0] <= 20 * 60]
        if near:
            _, ft, url = min(near)
            name = f"{label} ({ft:%H:%M})"
        elif (now - t).total_seconds() > 20 * 3600:
            # too old for the 5-minute archive: use the resort's one-a-day photo
            url, name = f"{RED_BASE}camArchive/daily/{folder}/{prefix}_{t:%y-%m-%d}.jpg", f"{label} (daily photo)"
        else:
            return False
        src = _snap(url, f"{IMG_DIR}/{e['date']}_{e['airport']}_{e['sched'].replace(':', '')}_{folder}.jpg")
        if src:
            got.append({"name": name, "src": src.replace("docs/", "", 1)})
    e["red"] = got
    return True


def record(results, cfg, now, mets, tafs, to_utc):
    """Log new cancellations/delays from live status; fill in weather at flight time."""
    evs = {f"{e['date']}|{e['flight']}": e for e in load()}
    snapped = {}
    changed = False
    for r in results:
        if r["schedule_source"] != "live":
            continue
        cancelled = str(r["status"]).lower().startswith("cancel")
        late = _minutes(r["dep_local"], r["expected_local"]) if r.get("expected_local") else 0
        if not cancelled and late < DELAY_MIN:
            continue
        key = f"{r['date']}|{r['flight']}"
        st = cfg["airports"][r["airport"]].get("wx_station")
        e = evs.get(key)
        if e is None:
            tag = f"{r['date']}_{r['airport']}"
            if tag not in snapped:
                snapped[tag] = snapshots(cfg, tag)
            e = {"date": r["date"], "flight": r["flight"], "airline": r["airline"], "airport": r["airport"],
                 "airport_name": r["airport_name"], "direction": r["direction"], "from": r["from"], "to": r["to"],
                 "sched": r["dep_local"], "first_seen": now.isoformat(timespec="minutes"),
                 "predicted": r.get("forecast_risk"), "predicted_reasons": r.get("forecast_reasons", []),
                 "wx_when_seen": wx_block((mets.get(st) or [None])[0]) if st else None,
                 "taf_when_seen": (tafs.get(st) or {}).get("rawTAF") if st else None,
                 "photos": snapped[tag],
                 "source": "live"}
            evs[key] = e
            changed = True
        if e.get("kind") == "cancelled" and not cancelled:
            continue  # once cancelled, a later status never downgrades it
        kind = "cancelled" if cancelled else "delayed"
        upd = {"kind": kind, "status": r["status"], "expected": r.get("expected_local"),
               "delay_min": late if not cancelled else None}
        if any(e.get(k) != v for k, v in upd.items()):
            e.update(upd)
            changed = True
    # Red Mountain photos closest to each flight time
    for e in evs.values():
        if "red" not in e and red_at(e, now):
            changed = True
    # weather at the scheduled time, once a report from around then exists
    for e in evs.values():
        st = (cfg["airports"].get(e["airport"]) or {}).get("wx_station")
        if e.get("wx_at_time") or not st or not mets.get(st):
            continue
        t = to_utc(datetime.fromisoformat(f"{e['date']}T{e['sched']}")).timestamp()
        near = [m for m in mets[st] if abs(m.get("obsTime", 0) - t) <= 45 * 60]
        if near:
            e["wx_at_time"] = wx_block(min(near, key=lambda m: abs(m["obsTime"] - t)))
            changed = True
    if changed:
        save(list(evs.values()))
    return list(evs.values())


def publish():
    """Copy the log to the site."""
    os.makedirs("docs", exist_ok=True)
    with open("docs/history.json", "w") as f:
        json.dump({"events": sorted(load(), key=lambda e: (e["date"], e["sched"]), reverse=True)}, f)

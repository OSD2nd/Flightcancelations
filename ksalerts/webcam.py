"""Red Mountain Resort webcams (Rossland, above Trail).

The resort's weather page lists a photo every 5 minutes for the last 24 hours.
We pick frames for a few cameras so the dashboard can play them as a time-lapse.
Mountain-top cams in cloud = cloud is sitting on the ridges around Trail/Castlegar.
"""
import re, urllib.request
from datetime import datetime

TS = re.compile(r"_(\d\d)-(\d\d)-(\d\d)-(\d\d):(\d\d)")


def _when(name):
    m = TS.search(name)
    if not m:
        return None
    y, mo, d, h, mi = map(int, m.groups())
    return datetime(2000 + y, mo, d, h, mi)


def frames(cfg, every_min=15, hours=24):
    """[{id, name, live, frames:[{t, url}]}] oldest first, one frame per `every_min`."""
    with urllib.request.urlopen(cfg["page"], timeout=45) as r:
        page = r.read().decode("utf-8", "ignore")
    out = []
    for cam in cfg["cams"]:
        files = sorted(set(re.findall(r"camArchive/24hr/%s/([^'\"]+\.jpg)" % re.escape(cam["id"]), page)),
                       key=lambda f: _when(f) or datetime.min)
        picked, last = [], None
        for f in files:
            t = _when(f)
            if t is None or (last and (t - last).total_seconds() < every_min * 60 - 60):
                continue
            picked.append({"t": t.isoformat(timespec="minutes"),
                           "url": f"{cfg['base']}camArchive/24hr/{cam['id']}/{f}"})
            last = t
        if picked:
            cutoff = _when(picked[-1]["url"]).timestamp() - hours * 3600
            picked = [p for p in picked if datetime.fromisoformat(p["t"]).timestamp() >= cutoff]
        out.append({"id": cam["id"], "name": cam["name"], "live": f"{cfg['live']}{cam['file']}.jpg",
                    "frames": picked})
    return out

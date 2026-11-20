"""BC highway cameras (DriveBC) around Trail and Castlegar.

The province publishes its highway camera list (name, location, image link) as open data:
images may be reused under the Open Government Licence - British Columbia
(https://images.drivebc.ca/bchighwaycam/pub/html/www/terms.html). We look the list up once a
day and keep the cameras config.json "highway_cams" asks for: summit cams show whether
cloud is down on the ridges, valley cams show the valley floor.

Each config entry: {"match": "Bombi", "label": "Bombi Summit", "airports": ["YZZ", "YCG"]}
matches cameras whose name or caption contains "match" (within ~70 km of Trail).
"""
import json, math, urllib.request

UA = "kootenay-flight-risk/1.0 github.com/OSD2nd/Flightcancelations"
LISTS = ["https://images.drivebc.ca/webcam/api/v1/webcams?format=json",
         "https://www.drivebc.ca/api/webcams/"]
IMG = "https://images.drivebc.ca/bchighwaycam/pub/cameras/{id}.jpg"
PAGE = "https://images.drivebc.ca/bchighwaycam/pub/html/www/{id}.html"
CENTRE = (49.17, -117.62)          # between Trail and Castlegar
RADIUS_KM = 70


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.load(r)


def _norm(c):
    """One camera from either DriveBC list format -> {id, name, caption, lat, lon, img, on}."""
    loc = c.get("location") or {}
    lat = loc.get("latitude", c.get("latitude"))
    lon = loc.get("longitude", c.get("longitude"))
    if lat is None and isinstance(loc.get("coordinates"), list):          # GeoJSON point [lon, lat]
        lon, lat = loc["coordinates"][:2]
    links = c.get("links") or {}
    cid = str(c.get("id"))
    return {"id": cid, "name": c.get("camName") or c.get("name") or "",
            "caption": c.get("caption") or "", "lat": float(lat) if lat is not None else None,
            "lon": float(lon) if lon is not None else None,
            "img": links.get("imageDisplay") or IMG.format(id=cid),
            "on": c.get("isOn", c.get("is_on", True)) is not False}


def fetch_all():
    last = None
    for url in LISTS:
        try:
            d = _get(url)
            rows = d.get("webcams") or d.get("results") or d.get("features") if isinstance(d, dict) else d
            cams = [_norm(c.get("properties", c) if "properties" in c else c) for c in rows or []]
            cams = [c for c in cams if c["lat"] is not None]
            if cams:
                return cams
        except Exception as e:
            last = e
    raise RuntimeError(f"DriveBC camera list unavailable: {last}")


def _km(a, b):
    dy = (a[0] - b[0]) * 111
    dx = (a[1] - b[1]) * 111 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def pick(cams, wanted):
    """The cameras config asks for, in config order: [{id, label, name, caption, img, page, airports}]."""
    near = [c for c in cams if c["on"] and _km((c["lat"], c["lon"]), CENTRE) <= RADIUS_KM]
    out, used = [], set()
    for w in wanted:
        m = w["match"].lower()
        # by camera name first; the caption ("... 22 km south-east of Castlegar") only if no name matches
        hits = [c for c in near if m in c["name"].lower() and c["id"] not in used] or \
               [c for c in near if m in c["caption"].lower() and c["id"] not in used]
        for c in sorted(hits, key=lambda c: c["name"]):
            used.add(c["id"])
            # "Trail - E" -> label "Trail (Hwy 3B) E" when a place has several cameras
            # or "Rossland eastbound" -> "Rossland eastbound" (whatever follows the place name)
            suffix = ""
            if len(hits) > 1:
                suffix = (c["name"].split("-")[-1] if "-" in c["name"]
                          else c["name"].lower().split(m, 1)[-1]).strip().replace("southound", "southbound")
            out.append({"id": c["id"], "label": w["label"] + (f" {suffix}" if suffix else ""),
                        "name": c["name"], "caption": c["caption"], "img": c["img"],
                        "page": PAGE.format(id=c["id"]), "airports": w.get("airports", []),
                        "summit": bool(w.get("summit"))})
    return out

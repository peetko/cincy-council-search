"""Download Cincinnati neighborhood boundaries for the map (site/data/neighborhoods.geojson).

    python scripts/fetch_neighborhoods.py            # only if the file is missing
    python scripts/fetch_neighborhoods.py --refresh  # download again

Boundaries come first from the City/County GIS service (CAGIS) at arcgis.cincinnati-oh.gov,
which publishes official neighborhood boundaries; any neighborhood it doesn't have is filled
in from OpenStreetMap (© OpenStreetMap contributors, ODbL) via the free Overpass service. Neighborhoods are mapped there as boundary relations made of
many border segments ("ways"); this script stitches the segments into closed outlines,
simplifies them so the file stays small, and keeps only names that match the City's list
in config/neighborhoods.json. Boundaries rarely change, so the daily job reuses the saved
file instead of downloading it every day.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

from common import HOOD_RX, ROOT

OUT = os.path.join(ROOT, "site", "data", "neighborhoods.geojson")
ENDPOINTS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
BBOX = "39.04,-84.72,39.23,-84.36"  # south, west, north, east around Cincinnati
# Neighborhoods can be mapped as multi-part "relations" or as single closed outlines ("ways").
QUERY = f"""[out:json][timeout:180];
(
  rel({BBOX})["boundary"="administrative"]["admin_level"~"^(9|10|11)$"];
  rel({BBOX})["place"~"^(neighbourhood|suburb|quarter)$"];
  rel({BBOX})["boundary"~"^(neighborhood|neighbourhood|place)$"];
  way({BBOX})["boundary"="administrative"]["admin_level"~"^(9|10|11)$"];
  way({BBOX})["place"~"^(neighbourhood|suburb|quarter)$"];
  way({BBOX})["boundary"~"^(neighborhood|neighbourhood|place)$"];
);
out geom;"""
VERSION = 3  # bump to make the daily job download boundaries again
CAGIS = "https://arcgis.cincinnati-oh.gov/arcgis/rest/services/Core/miscellaneous/MapServer"

ALIASES = {
    "clifton heights": "CUF", "university heights": "CUF", "fairview": "CUF", "cuf": "CUF",
    "clifton heights university heights fairview": "CUF",
    "central business district": "Downtown", "cbd": "Downtown", "downtown": "Downtown",
    "central business district riverfront": "Downtown",
    "fay apartments": "Villages at Roll Hill", "roll hill": "Villages at Roll Hill",
    "villages of roll hill": "Villages at Roll Hill", "the villages at roll hill": "Villages at Roll Hill",
    "otr": "Over-the-Rhine", "the heights": "CUF", "heights": "CUF",
}


def norm(name):
    n = name.lower().replace("&", " and ")
    n = re.sub(r"\bmount\b", "mt", n)
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return re.sub(r"^the ", "", n)


CANON = {norm(h): h for h in HOOD_RX}
CANON.update({norm(k): v for k, v in ALIASES.items()})


def match(name):
    return CANON.get(norm(name or ""))


def stitch(segments):
    """Join way segments (lists of [lon, lat]) into closed rings."""
    segs = [list(s) for s in segments if len(s) >= 2]
    rings = []
    while segs:
        ring = segs.pop(0)
        changed = True
        while ring[0] != ring[-1] and changed:
            changed = False
            for i, s in enumerate(segs):
                if s[0] == ring[-1]:
                    ring += s[1:]
                elif s[-1] == ring[-1]:
                    ring += s[::-1][1:]
                elif s[-1] == ring[0]:
                    ring = s[:-1] + ring
                elif s[0] == ring[0]:
                    ring = s[::-1][:-1] + ring
                else:
                    continue
                segs.pop(i)
                changed = True
                break
        if ring[0] != ring[-1]:
            ring.append(ring[0])  # close small gaps rather than drop the shape
        if len(ring) >= 4:
            rings.append(ring)
    return rings


def simplify(points, tol=0.00012):
    """Douglas-Peucker line simplification (tolerance in degrees, ~12 m)."""
    if len(points) < 4:
        return points

    def dist(p, a, b):
        if a == b:
            return ((p[0] - a[0]) ** 2 + (p[1] - a[1]) ** 2) ** 0.5
        t = ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2)
        t = max(0, min(1, t))
        x, y = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
        return ((p[0] - x) ** 2 + (p[1] - y) ** 2) ** 0.5

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        best, idx = 0, None
        for k in range(i + 1, j):
            d = dist(points[k], points[i], points[j])
            if d > best:
                best, idx = d, k
        if idx is not None and best > tol:
            keep[idx] = True
            stack += [(i, idx), (idx, j)]
    out = [p for p, k in zip(points, keep) if k]
    return out if len(out) >= 4 else points


def to_features(osm):
    shapes = {}
    for el in osm.get("elements", []):
        name = match(el.get("tags", {}).get("name"))
        if not name or el.get("type") not in ("relation", "way"):
            continue
        if el["type"] == "way":
            ring = [[round(p["lon"], 5), round(p["lat"], 5)] for p in el.get("geometry", [])]
            if len(ring) >= 4 and ring[0] == ring[-1] and not any(
                    poly[0][0] == ring[0] for poly in shapes.get(name, [])):
                shapes.setdefault(name, []).append([simplify(ring)])
            continue
        outer = [[[round(p["lon"], 5), round(p["lat"], 5)] for p in m["geometry"]]
                 for m in el.get("members", []) if m.get("type") == "way" and m.get("role") in ("outer", "")
                 and m.get("geometry")]
        for ring in stitch(outer):
            shapes.setdefault(name, []).append([simplify(ring)])
    return [{"type": "Feature", "properties": {"name": n},
             "geometry": {"type": "MultiPolygon", "coordinates": polys}} for n, polys in sorted(shapes.items())]


def get_json(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "juncta-lucent (github.com)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def from_cagis():
    """Official boundaries from the City's ArcGIS map service. Finds the neighborhood layer
    and its name field automatically. Returns ({name: [polygons]}, description) or ({}, reason)."""
    try:
        info = get_json(CAGIS + "?f=json")
    except Exception as e:
        return {}, f"CAGIS service unreachable ({e})"
    layers = info.get("layers", [])
    print("  CAGIS layers: " + ", ".join(f"{l.get('id')}={l.get('name')}" for l in layers))
    cands = [l for l in layers if re.search(r"neighbo|\bsna\b|statistical", l.get("name", ""), re.I)]
    cands.sort(key=lambda l: 0 if re.search(r"sna|statistical", l["name"], re.I) else 1)
    best = ({}, "no neighborhood layer found in CAGIS")
    for layer in cands:
        url = (f"{CAGIS}/{layer['id']}/query?where=1%3D1&outFields=*&returnGeometry=true"
               f"&outSR=4326&f=json")
        try:
            data = get_json(url, timeout=180)
        except Exception as e:
            print(f"  CAGIS layer {layer['name']}: query failed ({e})")
            continue
        feats = data.get("features", [])
        # Which attribute holds the neighborhood name? The one whose values match our list most.
        fields = {}
        for f in feats:
            for k, v in (f.get("attributes") or {}).items():
                if isinstance(v, str) and match(v):
                    fields[k] = fields.get(k, 0) + 1
        if not fields:
            print(f"  CAGIS layer {layer['name']}: no field matched the neighborhood list")
            continue
        field = max(fields, key=fields.get)
        shapes = {}
        for f in feats:
            name = match((f.get("attributes") or {}).get(field))
            rings = (f.get("geometry") or {}).get("rings") or []
            for ring in rings:
                pts = [[round(x, 5), round(y, 5)] for x, y in ring]
                if len(pts) >= 4 and name:
                    shapes.setdefault(name, []).append([simplify(pts)])
        print(f"  CAGIS layer {layer['name']} (field {field}): {len(shapes)} neighborhoods")
        if len(shapes) > len(best[0]):
            best = (shapes, f"CAGIS layer “{layer['name']}”")
    return best


def download():
    body = urllib.parse.urlencode({"data": QUERY}).encode()
    for url in ENDPOINTS:
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, data=body, headers={"User-Agent": "juncta-lucent (github.com)"})
                with urllib.request.urlopen(req, timeout=240) as r:
                    return json.loads(r.read().decode())
            except Exception as e:  # network trouble: try again, then the next server
                print(f"  {url} failed ({e}); retrying…")
                time.sleep(10)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--from-file", help="use a saved Overpass JSON response (testing)")
    args = ap.parse_args()
    if os.path.exists(OUT) and not args.refresh and not args.from_file:
        try:
            with open(OUT) as f:
                saved = json.load(f)
        except ValueError:
            saved = {}
        if saved.get("version") == VERSION:
            print(f"Neighborhood boundaries already present ({len(saved.get('features', []))} areas); skipping download.")
            return
        print("Saved boundaries are from an older version of this script; downloading again.")
    official, source = ({}, "skipped (test file)") if args.from_file else from_cagis()
    print(f"Official boundaries: {len(official)} neighborhoods from {source}")
    if args.from_file:
        with open(args.from_file) as f:
            osm = json.load(f)
    elif len(official) < len(HOOD_RX):
        print("Filling gaps from OpenStreetMap…")
        osm = download() or {"elements": []}
    else:
        osm = {"elements": []}
    feats = [{"type": "Feature", "properties": {"name": n, "source": "CAGIS"},
              "geometry": {"type": "MultiPolygon", "coordinates": polys}} for n, polys in sorted(official.items())]
    feats += [dict(f, properties={**f["properties"], "source": "OpenStreetMap"}) for f in to_features(osm)
              if f["properties"]["name"] not in official]
    found = {f["properties"]["name"] for f in feats}
    missing = [h for h in HOOD_RX if h not in found]
    unmatched = sorted({el.get("tags", {}).get("name", "?") for el in osm.get("elements", [])
                        if el.get("type") in ("relation", "way") and not match(el.get("tags", {}).get("name"))})
    if not feats:
        print("::warning::No neighborhood boundaries matched; the map will show a list instead.")
        return
    credits = []
    if official:
        credits.append("CAGIS (City of Cincinnati / Hamilton County)")
    if any(f["properties"]["source"] == "OpenStreetMap" for f in feats):
        credits.append("© OpenStreetMap contributors (ODbL)")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"type": "FeatureCollection", "version": VERSION, "attribution": "; ".join(credits),
                   "features": feats}, f, separators=(",", ":"))
    print(f"Saved {len(feats)} of {len(HOOD_RX)} neighborhoods ({os.path.getsize(OUT) // 1024} KB).")
    if missing:
        print(f"Not found in OpenStreetMap: {', '.join(missing)}")
    if unmatched:
        print(f"OpenStreetMap areas not matched to a City neighborhood: {', '.join(unmatched[:40])}")


if __name__ == "__main__":
    sys.exit(main())

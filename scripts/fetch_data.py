"""Get the site's data files, and rebuild the databases from their published pieces.

    python scripts/fetch_data.py --live https://peetko.github.io/cincy-council-search
    python scripts/fetch_data.py --assemble [--fresh-council]

--live      Download the data the live site is serving into site/data (used only when the saved
            copy from the last nightly update isn't available, e.g. the very first time).
--assemble  Before a data update: put docs.db and transcripts.db back together from their pieces,
            and keep a copy of each database as it was (*.prev), published alongside the new one.
            --fresh-council leaves council.db out, so the collector downloads everything again.
"""
import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.request

from common import DEFAULT_DB

DATA = os.path.dirname(DEFAULT_DB)
SITE = os.path.dirname(DATA)
FILES = ["council.db", "stats.json", "analysis.json", "whatsnew.json", "docs-stats.json", "transcripts-stats.json",
         "neighborhoods.geojson"]
INFOS = ["db-info.json", "docs-info.json", "transcripts-info.json"]


def get(url):
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Cache-Control": "no-cache"}), timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if k == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if k == 2:
                raise


def live(base):
    base = base.rstrip("/") + "/"
    os.makedirs(DATA, exist_ok=True)
    for name in FILES + INFOS:
        data = get(base + "data/" + name)
        if data is None:
            print(f"  {name}: not on the site (skipped)")
            continue
        with open(os.path.join(DATA, name), "wb") as f:
            f.write(data)
        if name in INFOS:
            info = json.loads(data)
            for i in range(info["pieces"]):
                rel = f"{info['urlPrefix']}{i:0{info['suffixLength']}d}"
                piece = get(base + rel)
                if piece is None:
                    sys.exit(f"{rel} is missing on the live site")
                path = os.path.join(SITE, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(piece)
        print(f"  {name}: ok")


def assemble(fresh_council):
    for info_name, db_name in (("docs-info.json", "docs.db"), ("transcripts-info.json", "transcripts.db")):
        path = os.path.join(DATA, info_name)
        if not os.path.exists(path):
            print(f"{db_name}: no saved copy yet; it will be started from scratch.")
            continue
        info = json.load(open(path))
        out = os.path.join(DATA, db_name)
        with open(out, "wb") as f:
            for i in range(info["pieces"]):
                with open(os.path.join(SITE, f"{info['urlPrefix']}{i:0{info['suffixLength']}d}"), "rb") as p:
                    f.write(p.read())
        if os.path.getsize(out) != info["databaseLengthBytes"]:
            sys.exit(f"{db_name}: the saved pieces add up to {os.path.getsize(out):,} bytes, not {info['databaseLengthBytes']:,}")
        shutil.copy(out, out + ".prev")
        print(f"{db_name}: {os.path.getsize(out) / 1e6:.1f} MB, put back together from {info['pieces']} pieces.")
    council = os.path.join(DATA, "council.db")
    if fresh_council:
        for p in (council, council + ".prev"):
            if os.path.exists(p):
                os.remove(p)
        print("council.db: left out, so everything is downloaded from Legistar again.")
    elif os.path.exists(council):
        shutil.copy(council, council + ".prev")
        print(f"council.db: {os.path.getsize(council) / 1e6:.1f} MB.")
    else:
        print("council.db: no saved copy yet - doing the full first-time download.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live")
    ap.add_argument("--assemble", action="store_true")
    ap.add_argument("--fresh-council", action="store_true")
    a = ap.parse_args()
    if a.live:
        print(f"Downloading the data the site is serving from {a.live}:")
        live(a.live)
    if a.assemble:
        assemble(a.fresh_council)

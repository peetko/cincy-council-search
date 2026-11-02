"""Split site/data/council.db into numbered pieces for the website.

    python scripts/split_db.py

Why: the page reads the database a few KB at a time. To do that it must know
the file's exact size. GitHub Pages compresses its answer to "how big is this
file?", which hides the size, so the page couldn't load. The fix (the reading
library's "chunked" mode) is to publish the file in fixed-size pieces plus a
small db-info.json that states the exact size up front.

The whole council.db is still published too, so the next daily run can
download it and continue from where it left off.
"""
import argparse
import glob
import json
import os

from common import DEFAULT_DB

CHUNK = 10 * 1024 * 1024  # 10 MB pieces
SUFFIX = 3                 # council.db.000, council.db.001, ...


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--chunk-mb", type=float, default=CHUNK / 1024 / 1024, help="piece size (testing)")
    args = ap.parse_args()
    chunk = int(args.chunk_mb * 1024 * 1024) // 4096 * 4096
    data_dir = os.path.dirname(os.path.abspath(args.db))
    out_dir = os.path.join(data_dir, "db")
    os.makedirs(out_dir, exist_ok=True)
    for old in glob.glob(os.path.join(out_dir, "council.db.*")):
        os.remove(old)

    size = os.path.getsize(args.db)
    n = 0
    with open(args.db, "rb") as f:
        while True:
            piece = f.read(chunk)
            if not piece:
                break
            with open(os.path.join(out_dir, f"council.db.{n:0{SUFFIX}d}"), "wb") as out:
                out.write(piece)
            n += 1

    info = {"databaseLengthBytes": size, "serverChunkSize": chunk, "suffixLength": SUFFIX,
            "urlPrefix": "data/db/council.db.", "pieces": n, "version": f"{size}-{int(os.path.getmtime(args.db))}"}
    with open(os.path.join(data_dir, "db-info.json"), "w") as f:
        json.dump(info, f)
    print(f"Split {size / 1e6:.1f} MB into {n} pieces in {out_dir}")


if __name__ == "__main__":
    main()

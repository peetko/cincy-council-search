"""Split site/data/council.db into numbered pieces for the website.

    python scripts/split_db.py                       # council.db -> data/db/, db-info.json
    python scripts/split_db.py --db site/data/docs.db --out docs --info docs-info.json

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
import hashlib
import json
import os
import shutil

from common import DEFAULT_DB

CHUNK = 10 * 1024 * 1024  # 10 MB pieces
SUFFIX = 3                 # council.db.000, council.db.001, ...


def file_hash(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:12]


def split(path, out_dir, chunk):
    """Write path's pieces into out_dir/<content hash>/ and return (hash, pieces, size)."""
    version = file_hash(path)
    folder = os.path.join(out_dir, version)
    os.makedirs(folder, exist_ok=True)
    base = os.path.basename(path).replace(".prev", "")
    n = 0
    with open(path, "rb") as f:
        while True:
            piece = f.read(chunk)
            if not piece:
                break
            with open(os.path.join(folder, f"{base}.{n:0{SUFFIX}d}"), "wb") as out:
                out.write(piece)
            n += 1
    return version, n, os.path.getsize(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--chunk-mb", type=float, default=CHUNK / 1024 / 1024, help="piece size (testing)")
    ap.add_argument("--out", default="db", help="folder (inside the data folder) for the pieces")
    ap.add_argument("--info", default="db-info.json")
    ap.add_argument("--also", default=None, help="the previous copy of the database, published alongside")
    args = ap.parse_args()
    chunk = int(args.chunk_mb * 1024 * 1024) // 4096 * 4096
    data_dir = os.path.dirname(os.path.abspath(args.db))
    out_dir = os.path.join(data_dir, args.out)
    if not os.path.exists(args.db):
        print(f"{args.db} not found; nothing to split.")
        return
    # Start clean: pieces from older runs (and the old unversioned layout) are removed.
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    base = os.path.basename(args.db)
    version, n, size = split(args.db, out_dir, chunk)
    # Each version's pieces live in their own folder, named by the file's contents. A page that
    # still has the previous db-info.json (web caches can hold it ~10 minutes after a publish)
    # keeps reading the previous, complete set of pieces instead of a mix of old and new.
    kept = ""
    if args.also and os.path.exists(args.also):
        old, _, _ = split(args.also, out_dir, chunk)
        kept = f" (and kept the previous version, {old})" if old != version else ""
    info = {"databaseLengthBytes": size, "serverChunkSize": chunk, "suffixLength": SUFFIX,
            "urlPrefix": f"data/{args.out}/{version}/{base}.", "pieces": n, "version": version}
    with open(os.path.join(data_dir, args.info), "w") as f:
        json.dump(info, f)
    print(f"Split {size / 1e6:.1f} MB into {n} pieces in {out_dir}/{version}{kept}")


if __name__ == "__main__":
    main()

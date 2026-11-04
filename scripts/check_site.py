"""Last check before publishing: is the site complete and working?

    python scripts/check_site.py

Runs right before the site is uploaded. If anything looks wrong, it exits with an error,
which stops that day's publish, so yesterday's site stays up instead of a broken one.
It checks that:
  - the page's script files exist, aren't suspiciously small, and have no syntax errors;
  - index.html still has the parts the scripts rely on;
  - the data files the page loads exist and are readable;
  - every piece of each database is there and adds up to the stated size.
"""
import json
import os
import shutil
import subprocess
import sys

SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "site")
# Smallest believable size for each file (they're far bigger). An emptied or cut-off file fails.
MIN_BYTES = {"index.html": 15000, "app.js": 60000, "glossary.js": 5000, "tour.js": 3000, "styles.css": 15000,
             "vendor/index.js": 1000, "vendor/sqlite.worker.js": 1000, "vendor/sql-wasm.wasm": 100000}
HTML_MUST_HAVE = ['id="search-form"', 'id="q"', 'id="result-list"', 'id="home-cards"', 'id="menu-btn"',
                  'src="app.js"', 'src="glossary.js"', 'src="tour.js"', 'src="vendor/index.js"', "Not an official", "</html>"]

problems = []


def path(rel):
    return os.path.join(SITE, rel)


for rel, minimum in MIN_BYTES.items():
    p = path(rel)
    if not os.path.exists(p):
        problems.append(f"{rel} is missing")
    elif os.path.getsize(p) < minimum:
        problems.append(f"{rel} is only {os.path.getsize(p):,} bytes (expected at least {minimum:,})")

node = shutil.which("node")
for rel in ("app.js", "glossary.js", "tour.js"):
    if node and os.path.exists(path(rel)):
        r = subprocess.run([node, "--check", path(rel)], capture_output=True, text=True)
        if r.returncode != 0:
            lines = (r.stderr or r.stdout).strip().splitlines()
            where = next((l for l in lines if "SyntaxError" in l), lines[0] if lines else "unknown")
            problems.append(f"{rel} has a syntax error: {where.strip()}")
if not node:
    print("::warning::Node.js isn't available, so the script syntax check was skipped.")

if os.path.exists(path("index.html")):
    html = open(path("index.html"), encoding="utf-8").read()
    for needle in HTML_MUST_HAVE:
        if needle.lower() not in html.lower():
            problems.append(f"index.html is missing {needle!r}")

for rel in ("data/stats.json", "data/db-info.json", "data/analysis.json"):
    try:
        json.load(open(path(rel), encoding="utf-8"))
    except Exception as e:
        problems.append(f"{rel} is missing or unreadable ({e.__class__.__name__})")

for info_file, required in (("data/db-info.json", True), ("data/docs-info.json", False), ("data/transcripts-info.json", False)):
    if not os.path.exists(path(info_file)):
        if required:
            problems.append(f"{info_file} is missing")
        continue
    try:
        info = json.load(open(path(info_file)))
        total = 0
        for i in range(info["pieces"]):
            piece = path(f"{info['urlPrefix']}{i:0{info['suffixLength']}d}")
            if not os.path.exists(piece):
                problems.append(f"{info_file}: piece {i} is missing")
                break
            total += os.path.getsize(piece)
        else:
            if total != info["databaseLengthBytes"]:
                problems.append(f"{info_file}: pieces add up to {total:,} bytes, not {info['databaseLengthBytes']:,}")
    except Exception as e:
        problems.append(f"{info_file} couldn't be checked ({e})")

if problems:
    print("The site is NOT being published today, so the current site stays up. Problems found:")
    for p in problems:
        print(f"  - {p}")
        print(f"::error::{p}")
    sys.exit(1)
print("Site check passed: scripts, page, data files and database pieces all look right.")

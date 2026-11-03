"""Collect the full text of legislation into a separate database: site/data/docs.db

    python scripts/collect_docs.py [--minutes 25]

Legistar's title field holds only the opening of long legislation (the rest is
"on file in the Clerk's office"). This script fetches the whole text of each
ordinance, resolution and motion so it can be searched:

  1. the legislation text Legistar stores for the matter, if there is one; otherwise
  2. the attached legislation document (PDF or Word), read with pypdf.

Deliberately narrow, to keep the site free, fast and focused:
  - only ordinances, resolutions and motions (not presentations, reports, appointments);
  - only the legislation document itself: exhibits, maps, contracts, leases, budgets,
    presentations and public comments/letters are skipped;
  - scanned PDFs with no text layer are read with OCR (tesseract), up to OCR_MAX_PAGES pages;
  - each document is capped at doc_max_chars.

It works newest first and stops after --minutes, so the first backfill spreads over
several daily runs. Results (including "nothing found") are remembered, so each
piece of legislation is only fetched again if it shows up on a new agenda.
"""
import argparse
import concurrent.futures
import datetime as dt
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

import legistar
from common import DEFAULT_DB, SETTINGS

DOCS_DB = os.path.join(os.path.dirname(DEFAULT_DB), "docs.db")
MAX_CHARS = int(SETTINGS.get("doc_max_chars", 60000))
MAX_BYTES = 15 * 1024 * 1024
MIN_CHARS = 300  # less than this = a scan, a cover page, or nothing useful
OCR_MAX_PAGES = int(SETTINGS.get("ocr_max_pages", 15))
OCR_AVAILABLE = bool(shutil.which("tesseract") and shutil.which("pdftoppm"))

# Which attachment is "the legislation": its name says so, and it isn't supporting material.
LEGIS_RX = re.compile(r"ordinance|resolution|motion|legislation|\bord\b|\bres\b|emergency|amended|substitute|version", re.I)
SKIP_RX = re.compile(r"exhibit|attachment|certified copy|original\b|\bmap|survey|plat\b|agreement|contract|lease|budget|spreadsheet|"
                     r"presentation|powerpoint|slides|comment|petition|letter|e-?mail|correspondence|photo|rendering|"
                     r"site plan|application|invoice|report|memo|transmittal|minutes|resume|bio\b", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
  matter_id  INTEGER PRIMARY KEY,
  matter_file TEXT,
  status     TEXT,      -- ok / none (no legislation document) / scan (no text layer) / error
  source     TEXT,      -- text (Legistar's stored text) / attachment
  name       TEXT,      -- attachment name, when it came from one
  url        TEXT,
  chars      INTEGER,
  checked    TEXT,      -- date last fetched
  text       TEXT
);
CREATE INDEX IF NOT EXISTS docs_status ON docs(status);
"""
FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
  text, content='docs', content_rowid='matter_id', tokenize='porter unicode61');
"""


def connect(path):
    new = not os.path.exists(path)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    if new:
        con.execute("PRAGMA page_size = 4096")
    con.executescript(SCHEMA)
    return con


def clean(text):
    text = text.replace("\r", "\n").replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = re.sub(r"(?m)^\s*(Page \d+( of \d+)?|-\s*\d+\s*-)\s*$", "", text)  # page numbers
    return text.strip()[:MAX_CHARS]


def rtf_to_text(rtf):
    """Rough RTF -> text, enough for search (used if Legistar has only the RTF version)."""
    rtf = re.sub(r"\\par[d]?\b", "\n", rtf)
    rtf = re.sub(r"\{\\\*[^{}]*\}", "", rtf)
    rtf = re.sub(r"\\'[0-9a-f]{2}", " ", rtf)
    rtf = re.sub(r"\\[a-z]+-?\d* ?", "", rtf)
    return rtf.replace("{", "").replace("}", "")


RULES_VERSION = "4"  # bump when the rules change, to re-check what was saved under older rules
MARKER = re.compile(r"(?im)^\s*\.\.\s*(title|body|header|footer|recommendation|summary|end)\b[^\n]*$")


def body_of(plain):
    """Legistar marks sections with lines like '..Title' and '..Body'. Keep everything except the
    title (which the site already has); if there are no markers, keep it all."""
    parts = MARKER.split(plain)
    if len(parts) == 1:
        return plain
    out, i = [parts[0]], 1
    while i < len(parts) - 1:
        if parts[i].lower() != "title":
            out.append(parts[i + 1])
        i += 2
    return "\n\n".join(x.strip() for x in out if x.strip())


def stored_text(matter_id, title=""):
    """Legistar's stored legislation text for the latest version, if it has more than the title."""
    versions = legistar.get(f"matters/{matter_id}/versions") or []
    if not versions:
        return None
    def vkey(v):
        try:
            return float(str(v.get("Value") or v.get("Key")).strip() or 0)
        except ValueError:
            return 0
    latest = max(versions, key=vkey)
    text_id = latest.get("Key")
    t = legistar.get(f"matters/{matter_id}/texts/{text_id}") or {}
    plain = t.get("MatterTextPlain") or ""
    if len(plain.strip()) < MIN_CHARS and t.get("MatterTextRtf"):
        plain = rtf_to_text(t["MatterTextRtf"])
    body = clean(body_of(plain))
    STATS["stored text checked"] = STATS.get("stored text checked", 0) + 1
    if len(body) - len(clean(title or "")) < MIN_CHARS:  # nothing much beyond the title
        STATS["stored text was only the title"] = STATS.get("stored text was only the title", 0) + 1
        return None
    return clean(plain.replace("..Title", "").replace("..Body", ""))


def pick_attachment(atts):
    usable = [a for a in atts if a.get("MatterAttachmentHyperlink") and not a.get("MatterAttachmentIsHyperlink")]
    named = lambda a: f"{a.get('MatterAttachmentName') or ''} {a.get('MatterAttachmentFileName') or ''}"
    legis = [a for a in usable if LEGIS_RX.search(named(a)) and not SKIP_RX.search(a.get("MatterAttachmentName") or "")]
    if legis:
        return legis[-1]  # the latest version is usually listed last
    rest = [a for a in usable if not SKIP_RX.search(named(a))]
    return rest[0] if len(rest) == 1 else None


def download(url):
    """The file's bytes, or None if it's missing (404/410) or too big."""
    req = urllib.request.Request(url, headers={"User-Agent": "plum-street-ledger (github.com)"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        if e.code in (403, 404, 410):  # the file is gone or not public: nothing to fetch
            STATS["attachment files missing on Legistar"] = STATS.get("attachment files missing on Legistar", 0) + 1
            return None
        raise
    time.sleep(legistar.PAUSE)
    return None if len(data) > MAX_BYTES else data


def file_text(data):
    if data.startswith(b"%PDF"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page in reader.pages[:80]:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                pages.append("")
        return "\n".join(pages)
    if data.startswith(b"PK"):  # .docx is a zip of XML files
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if "word/document.xml" not in z.namelist():
                return ""
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"<[^>]+>", "", xml)
    if data.lstrip().startswith(b"{\\rtf"):
        return rtf_to_text(data.decode("latin-1", "ignore"))
    return ""


SEEN_NAMES = {"chosen": {}, "passed over": {}, "scanned (no text layer)": {}}
STATS = {}  # attachment names, printed for the Data check


def note(kind, name):
    name = re.sub(r"\d+", "#", (name or "").strip())[:60]
    SEEN_NAMES[kind][name] = SEEN_NAMES[kind].get(name, 0) + 1


def ocr_pdf(data):
    """Read a scanned PDF with OCR: render pages to images (pdftoppm), then tesseract, a few
    pages at a time in parallel. Returns '' if the tools aren't installed."""
    if not OCR_AVAILABLE:
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        pdf = os.path.join(tmp, "doc.pdf")
        with open(pdf, "wb") as f:
            f.write(data)
        subprocess.run(["pdftoppm", "-r", "200", "-gray", "-png", "-l", str(OCR_MAX_PAGES), pdf, os.path.join(tmp, "p")],
                       check=True, timeout=300, capture_output=True)
        pages = sorted(x for x in os.listdir(tmp) if x.endswith(".png"))
        env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
        def read(page):
            r = subprocess.run(["tesseract", os.path.join(tmp, page), "-", "-l", "eng", "--psm", "1"],
                               capture_output=True, timeout=180, env=env)
            return r.stdout.decode("utf-8", "ignore")
        with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count() or 2) as pool:
            return "\n\n".join(pool.map(read, pages))


def fetch_one(matter_id, title=""):
    """-> (status, source, name, url, text)"""
    text = stored_text(matter_id, title)
    if text:
        return "ok", "text", None, None, text
    atts = legistar.get(f"matters/{matter_id}/attachments") or []
    a = pick_attachment(atts)
    for x in atts:
        if x is not a:
            note("passed over", x.get("MatterAttachmentName"))
    if not a:
        return "none", None, None, None, None
    note("chosen", a.get("MatterAttachmentName"))
    url = a["MatterAttachmentHyperlink"]
    data = download(url)
    if data is None:
        return "none", "attachment", a.get("MatterAttachmentName"), url, None
    text = clean(file_text(data))
    if len(text) < MIN_CHARS and data.startswith(b"%PDF"):
        note("scanned (no text layer)", a.get("MatterAttachmentName"))
        started = time.time()
        text = clean(ocr_pdf(data))
        STATS["OCR seconds"] = round(STATS.get("OCR seconds", 0) + time.time() - started)
        if len(text) >= MIN_CHARS:
            return "ok", "ocr", a.get("MatterAttachmentName"), url, text
    if len(text) < MIN_CHARS:
        return "scan", "attachment", a.get("MatterAttachmentName"), url, None
    return "ok", "attachment", a.get("MatterAttachmentName"), url, text


def todo(main, docs, today):
    """Legislation to fetch, newest first: never checked, or seen on an agenda since it was checked."""
    rows = main.execute("""
        SELECT matter_id, MAX(matter_file) AS matter_file, MAX(date) AS last_seen, MAX(title) AS title FROM items
        WHERE matter_id IS NOT NULL AND kind IN ('ordinance', 'resolution', 'motion')
        GROUP BY matter_id ORDER BY last_seen DESC""").fetchall()
    known = {r["matter_id"]: r for r in docs.execute("SELECT matter_id, status, checked FROM docs")}
    week_ago = (dt.date.fromisoformat(today) - dt.timedelta(days=7)).isoformat()
    out = []
    for r in rows:
        k = known.get(r["matter_id"])
        if k is None or k["status"] == "error":
            out.append(r)
        elif k["checked"] < week_ago and r["last_seen"] > k["checked"] and r["last_seen"] <= today:
            out.append(r)  # back on an agenda since we looked (it may have been amended)
    return out, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--docs", default=None)
    ap.add_argument("--minutes", type=float, default=float(os.environ.get("DOCS_MINUTES", SETTINGS.get("docs_minutes_per_run", 25))))
    ap.add_argument("--limit", type=int, default=0, help="stop after this many (testing)")
    args = ap.parse_args()
    docs_path = args.docs or os.path.join(os.path.dirname(os.path.abspath(args.db)), "docs.db")
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date().isoformat()

    main_db = sqlite3.connect(args.db)
    main_db.row_factory = sqlite3.Row
    docs = connect(docs_path)
    docs.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = docs.execute("SELECT value FROM meta WHERE key = 'rules_version'").fetchone()
    if not row or row[0] != RULES_VERSION:
        n = docs.execute("DELETE FROM docs WHERE NOT (status = 'ok' AND source IN ('attachment', 'ocr'))").rowcount
        wrong = [r[0] for r in docs.execute("SELECT matter_id, name FROM docs WHERE name IS NOT NULL") if SKIP_RX.search(r[1])]
        for mid in wrong:  # picked under older rules, but it's supporting material, not the legislation
            docs.execute("DELETE FROM docs WHERE matter_id = ?", (mid,))
        n += len(wrong)
        docs.execute("INSERT OR REPLACE INTO meta VALUES ('rules_version', ?)", (RULES_VERSION,))
        docs.commit()
        print(f"Collection rules changed: {n} saved results will be fetched again.")
    queue, total = todo(main_db, docs, today)
    print(f"{len(queue)} of {total} pieces of legislation need their full text fetched."
          + ("" if OCR_AVAILABLE else " (OCR tools not installed: scanned documents will be skipped.)"))

    deadline = time.time() + args.minutes * 60
    done, counts, errors_in_a_row = 0, {}, 0
    for r in queue:
        if time.time() > deadline or (args.limit and done >= args.limit):
            break
        try:
            status, source, name, url, text = fetch_one(r["matter_id"], r["title"])
        except Exception as e:  # one bad file shouldn't stop the run
            status, source, name, url, text = "error", None, None, None, None
            print(f"  {r['matter_file']}: {e}")
            errors_in_a_row += 1 if isinstance(e, RuntimeError) else 0  # only API failures suggest an outage
            if errors_in_a_row >= 5:
                print("Five errors in a row - Legistar may be down. Stopping; the next run will pick up here.")
                break
        else:
            errors_in_a_row = 0
        docs.execute("""INSERT OR REPLACE INTO docs(matter_id, matter_file, status, source, name, url, chars, checked, text)
                        VALUES(?,?,?,?,?,?,?,?,?)""",
                     (r["matter_id"], r["matter_file"], status, source, name, url, len(text or ""), today, text))
        counts[status if status != "ok" else f"ok ({source})"] = counts.get(status if status != "ok" else f"ok ({source})", 0) + 1
        done += 1
        if done % 100 == 0:
            docs.commit()
            print(f"  …{done} done ({legistar.request_count} requests)")
    docs.commit()
    left = len(queue) - done
    print(f"Fetched {done} this run: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) +
          (f". {left} left for the next runs." if left else ". All caught up."))

    if STATS:
        print("Stored text: " + ", ".join(f"{k} {v}" for k, v in STATS.items()))
    for kind, names in SEEN_NAMES.items():
        if names:
            top = sorted(names.items(), key=lambda kv: -kv[1])[:15]
            print(f"Attachment names {kind} (# = any number): " + "; ".join(f"{n} ({c})" for n, c in top))

    # Progress, for the note in the site's tooltip.
    ok = docs.execute("SELECT COUNT(*) FROM docs WHERE status = 'ok'").fetchone()[0]
    checked = docs.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    with open(os.path.join(os.path.dirname(docs_path), "docs-stats.json"), "w") as f:
        json.dump({"with_text": ok, "checked": checked, "total": total, "updated": today}, f)

    # Rebuild the full-text index and compact the file for the website.
    docs.executescript("DROP TABLE IF EXISTS docs_fts;" + FTS)
    docs.execute("INSERT INTO docs_fts(docs_fts) VALUES('rebuild')")
    docs.execute("INSERT INTO docs_fts(docs_fts) VALUES('optimize')")
    docs.commit()
    docs.execute("PRAGMA journal_mode = DELETE")
    docs.execute("VACUUM")
    docs.close()
    print(f"docs.db is {os.path.getsize(docs_path) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()

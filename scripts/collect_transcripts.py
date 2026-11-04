"""Collect meeting transcripts into a separate database: site/data/transcripts.db

    python scripts/collect_transcripts.py [--minutes 30]

Where they come from: CitiCable (the City's TV channel) posts a video of every Council and
committee meeting to the Internet Archive (archive.org), and since mid-2021 each one comes with
the meeting's closed captions: the live captions typed during the broadcast, with timestamps.
This script downloads those caption files (not the videos), so what was said can be searched and
each passage can link to that moment in the video.

What happens to each caption file is in transcripts.py: speaker turns, public comment, names of
members of the public removed (best effort), ALL CAPS turned into ordinary text, short passages.

Meetings are matched to Legistar's meetings by date and committee. Newest first; stops after
--minutes, so the first backfill spreads over several runs. Meetings with no captions yet are
checked again for a month (captions are sometimes added a few days after the video).

The raw caption files are kept in a cache folder (saved between runs by the workflow), so when
the processing rules change, everything can be redone without downloading it all again.
"""
import argparse
import datetime as dt
import gzip
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

import transcripts
from common import DEFAULT_DB, ROOT, SETTINGS

ARCHIVE = os.environ.get("ARCHIVE_BASE", "https://archive.org").rstrip("/")
CACHE = os.environ.get("CAPTION_CACHE", os.path.join(ROOT, ".cache", "captions"))
SINCE = SETTINGS.get("transcripts_since", "2021-01-01")
USER_AGENT = "JunctaLucent/1.0 (independent council-records search; github.com/peetko/cincy-council-search)"
# Bump when transcripts.py's rules change: every meeting is processed again (from the cache).
RULES_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS tmeetings (
  item       TEXT PRIMARY KEY,   -- archive.org identifier, e.g. 10261028-coun
  slot       INTEGER,            -- number used in passage ids (see passage_id)
  event_id   INTEGER,            -- the matching Legistar meeting, if found
  date       TEXT,
  body       TEXT,
  is_council INTEGER,
  title      TEXT,
  status     TEXT,               -- ok / none (no captions) / error
  caption    TEXT,               -- caption file name
  checked    TEXT,
  words      INTEGER,
  passages   INTEGER,
  public_comment INTEGER,        -- passages during public comment
  names_removed  INTEGER,
  duration   INTEGER
);
CREATE INDEX IF NOT EXISTS tmeetings_date ON tmeetings(date);
CREATE TABLE IF NOT EXISTS passages (
  id      INTEGER PRIMARY KEY,   -- see passage_id: newer meetings have bigger ids
  item    TEXT,
  t       INTEGER,               -- seconds from the start of the video
  speaker TEXT,                  -- captioner's label (Council members, some staff), else ''
  pc      INTEGER,               -- 1 = during public comment
  text    TEXT
);
"""
FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(
  text, content='passages', content_rowid='id', tokenize='porter unicode61');
"""


def passage_id(date, slot, seq):
    """YYMMDD, then the meeting's slot that day (0 = City Council), then the passage number.
    Sorting by id = newest first, and the search can filter by date without reading the passages."""
    return int(date[2:4] + date[5:7] + date[8:10]) * 100000 + slot * 10000 + seq


def get(url, tries=3):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 410) or k == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if k == tries - 1:
                raise
        time.sleep(5 * (k + 1))


def archive_items():
    """Council and committee meetings in CitiCable's archive.org collections since SINCE."""
    q = f"(collection:citicable OR collection:citiohv) AND date:[{SINCE} TO 2100-01-01]"
    url = f"{ARCHIVE}/advancedsearch.php?" + urllib.parse.urlencode(
        {"q": q, "fl[]": ["identifier", "title", "date"], "sort[]": "date desc", "rows": 20000, "output": "json"}, doseq=True)
    docs = json.loads(get(url))["response"]["docs"]
    out = []
    for d in docs:
        for k in ("title", "date"):
            if isinstance(d.get(k), list):
                d[k] = d[k][0] if d[k] else ""
        ident, title = d.get("identifier", ""), str(d.get("title", ""))
        # Council and committee items are named like 10261028-coun: a channel number, the date
        # (YYMMDD) and the body's code. Other boards and press events are named differently.
        m = re.match(r"^\d{2}(\d{2})(\d{2})(\d{2})-[a-z0-9]+(?:-[a-z0-9]+)*$", ident)
        if not m or not re.search(r"council|committee", title, re.I):
            continue
        date = f"20{m.group(1)}-{m.group(2)}-{m.group(3)}"
        if date < SINCE:
            continue
        out.append({"item": ident, "title": title.strip(), "date": date})
    return out


STOP = {"committee", "meeting", "the", "and", "of", "special", "public", "hearing", "session", "cincinnati", "city"}


def words_of(s):
    return {w for w in re.findall(r"[a-z]+", s.lower()) if w not in STOP}


def match_meeting(item, meetings_by_date):
    """The Legistar meeting for an archive.org item: same date, best match on the body's name."""
    cands = meetings_by_date.get(item["date"], [])
    council = item["item"].split("-")[1] == "coun" or re.match(r"^(cincinnati )?city council", item["title"], re.I)
    if council:
        cands = [c for c in cands if c["is_council"]]
        return cands[0] if len(cands) == 1 else None
    want = words_of(re.sub(r"\(.*?\)|-.*$", "", item["title"]))
    scored = sorted(((len(want & words_of(c["body"])), c) for c in cands if not c["is_council"]),
                    key=lambda x: -x[0])
    if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return scored[0][1]
    return None


def body_from_title(title):
    t = re.sub(r"\s*[-–(].*$", "", title).strip()
    return t or title


def officials(main):
    names = set()
    for sql in ("SELECT DISTINCT person FROM votes", "SELECT DISTINCT person FROM office_records"):
        try:
            names |= {r[0] for r in main.execute(sql) if r[0]}
        except sqlite3.Error:
            pass
    names |= set(SETTINGS.get("transcript_officials_extra", []))
    return sorted(names)


def places(main):
    try:
        return [r[0] for r in main.execute("SELECT name FROM hood_names")]
    except sqlite3.Error:
        return []


def cache_path(item):
    return os.path.join(CACHE, item + ".srt.gz")


def captions(item):
    """(file name, bytes) of the meeting's closed captions, or (None, None). Uses the cache."""
    path = cache_path(item)
    if os.path.exists(path):
        with gzip.open(path, "rb") as f:
            data = f.read()
        return data.split(b"\n", 1)[0].decode(), data.split(b"\n", 1)[1]
    md = json.loads(get(f"{ARCHIVE}/metadata/{urllib.parse.quote(item)}"))
    names = [f.get("name", "") for f in md.get("files", [])]
    # .disc.srt = the broadcast's closed captions. (Archive.org's own machine transcripts, .asr.srt,
    # exist only for older years and are much less accurate, so they're not used.)
    pick = next((n for n in names if n.endswith(".disc.srt")), None) or \
        next((n for n in names if n.endswith(".srt") and ".asr." not in n), None)
    if not pick:
        return None, None
    data = get(f"{ARCHIVE}/download/{urllib.parse.quote(item)}/{urllib.parse.quote(pick)}")
    os.makedirs(CACHE, exist_ok=True)
    with gzip.open(path, "wb") as f:
        f.write(pick.encode() + b"\n" + data)
    return pick, data


def store(con, row, ps, info, today):
    con.execute("DELETE FROM passages WHERE item = ?", (row["item"],))
    con.executemany("INSERT INTO passages(id, item, t, speaker, pc, text) VALUES(?,?,?,?,?,?)",
                    [(passage_id(row["date"], row["slot"], k), row["item"], p["t"], p["label"], p["pc"], p["text"])
                     for k, p in enumerate(ps[:9999])])
    con.execute("""UPDATE tmeetings SET status='ok', checked=?, words=?, passages=?, public_comment=?, names_removed=?, duration=?
                   WHERE item=?""", (today, info["words"], len(ps), sum(p["pc"] for p in ps), info["names_removed"],
                                     info["duration"], row["item"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=None)
    ap.add_argument("--minutes", type=float, default=float(os.environ.get("TRANSCRIPT_MINUTES") or SETTINGS.get("transcript_minutes_per_run", 30)))
    ap.add_argument("--limit", type=int, default=0, help="stop after this many (testing)")
    args = ap.parse_args()
    out_path = args.out or os.path.join(os.path.dirname(os.path.abspath(args.db)), "transcripts.db")
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date().isoformat()
    started = time.time()

    main_db = sqlite3.connect(args.db)
    main_db.row_factory = sqlite3.Row
    con = sqlite3.connect(out_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA page_size = 4096")
    con.executescript(SCHEMA)
    off, hoods = officials(main_db), places(main_db)

    meetings_by_date = {}
    for r in main_db.execute("SELECT event_id, date, body, is_council FROM meetings"):
        meetings_by_date.setdefault(r["date"], []).append(dict(r))

    # Rules changed: redo every stored meeting from the cached caption files.
    row = con.execute("SELECT value FROM meta WHERE key = 'rules_version'").fetchone()
    if row and row[0] != RULES_VERSION:
        redo = con.execute("SELECT * FROM tmeetings WHERE status = 'ok'").fetchall()
        n = 0
        for r in redo:
            if os.path.exists(cache_path(r["item"])):
                _, data = captions(r["item"])
                ps, info = transcripts.process(data, off, hoods)
                store(con, r, ps, info, r["checked"])
                n += 1
            else:
                con.execute("UPDATE tmeetings SET status = 'error' WHERE item = ?", (r["item"],))  # fetch again
        print(f"Transcript rules changed: re-processed {n} meetings from the cache ({len(redo) - n} to download again).")
    con.execute("INSERT OR REPLACE INTO meta VALUES ('rules_version', ?)", (RULES_VERSION,))
    con.commit()

    try:
        items = archive_items()
    except Exception as e:
        print(f"::warning::Couldn't list the meeting videos on archive.org ({e}); transcripts will catch up next run.")
        items = []
    print(f"{len(items)} Council and committee meeting videos on archive.org since {SINCE}.")

    # New items get a slot number for their date (0 = City Council; committees 1-9).
    known = {r["item"]: dict(r) for r in con.execute("SELECT * FROM tmeetings")}
    used = {}
    for r in known.values():
        used.setdefault(r["date"], set()).add(r["slot"])
    for it in sorted(items, key=lambda x: x["item"]):
        if it["item"] in known:
            continue
        ev = match_meeting(it, meetings_by_date)
        is_council = int(bool(ev["is_council"]) if ev else it["item"].split("-")[1] == "coun")
        taken = used.setdefault(it["date"], set())
        slot = 0 if is_council and 0 not in taken else next((k for k in range(1, 10) if k not in taken), None)
        if slot is None:
            continue  # more than ten videos in a day: not a regular meeting day
        taken.add(slot)
        row = {"item": it["item"], "slot": slot, "event_id": ev["event_id"] if ev else None, "date": it["date"],
               "body": ev["body"] if ev else body_from_title(it["title"]), "is_council": is_council,
               "title": it["title"], "status": "new", "checked": ""}
        con.execute("""INSERT INTO tmeetings(item, slot, event_id, date, body, is_council, title, status, checked)
                       VALUES(:item, :slot, :event_id, :date, :body, :is_council, :title, :status, :checked)""", row)
        known[it["item"]] = row
    # Meetings matched to Legistar later (the meeting was added to Legistar after we saw the video).
    for r in list(known.values()):
        if r.get("event_id") is None:
            ev = match_meeting(r, meetings_by_date)
            if ev:
                con.execute("UPDATE tmeetings SET event_id = ?, body = ? WHERE item = ?", (ev["event_id"], ev["body"], r["item"]))
    con.commit()

    month_ago = (dt.date.fromisoformat(today) - dt.timedelta(days=30)).isoformat()
    queue = [r for r in known.values() if r["status"] in ("new", "error")
             or (r["status"] == "none" and r["date"] >= month_ago and r["checked"] < today)]
    queue.sort(key=lambda r: r["date"], reverse=True)
    print(f"{len(queue)} meetings to fetch.")

    deadline = started + args.minutes * 60
    done, counts, fails = 0, {}, 0
    for r in queue:
        if time.time() > deadline or (args.limit and done >= args.limit):
            break
        try:
            name, data = captions(r["item"])
            if data is None:
                con.execute("UPDATE tmeetings SET status='none', checked=? WHERE item=?", (today, r["item"]))
                status = "no captions"
            else:
                ps, info = transcripts.process(data, off, hoods)
                con.execute("UPDATE tmeetings SET caption=? WHERE item=?", (name, r["item"]))
                store(con, r, ps, info, today)
                status = "ok"
            fails = 0
        except Exception as e:
            con.execute("UPDATE tmeetings SET status='error', checked=? WHERE item=?", (today, r["item"]))
            status = "error"
            print(f"  {r['item']}: {e}")
            fails += 1
            if fails >= 5:
                print("Five errors in a row - archive.org may be down. Stopping; the next run will pick up here.")
                break
        counts[status] = counts.get(status, 0) + 1
        done += 1
        if done % 25 == 0:
            con.commit()
            print(f"  …{done} done")
        time.sleep(float(os.environ.get("ARCHIVE_PAUSE", "0.5")))  # be polite to archive.org
    con.commit()
    left = len(queue) - done
    print(f"Fetched {done} this run: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) +
          (f". {left} left for the next runs." if left else ". All caught up."))

    s = con.execute("""SELECT COUNT(*) AS n, SUM(status='ok') AS ok, SUM(passages) AS p, SUM(words) AS w,
                       SUM(names_removed) AS nr, MIN(CASE WHEN status='ok' THEN date END) AS first,
                       MAX(CASE WHEN status='ok' THEN date END) AS last FROM tmeetings""").fetchone()
    print(f"Transcripts: {s['ok'] or 0} of {s['n']} meetings ({s['w'] or 0:,} words, {s['p'] or 0:,} passages, "
          f"{s['nr'] or 0:,} name mentions removed), {s['first']} to {s['last']}.")
    with open(os.path.join(os.path.dirname(out_path), "transcripts-stats.json"), "w") as f:
        json.dump({"meetings": s["ok"] or 0, "videos": s["n"], "first": s["first"], "last": s["last"],
                   "words": s["w"] or 0, "updated": today, "left": left}, f)

    con.executescript("DROP TABLE IF EXISTS passages_fts;" + FTS)
    con.execute("INSERT INTO passages_fts(passages_fts) VALUES('rebuild')")
    con.execute("INSERT INTO passages_fts(passages_fts) VALUES('optimize')")
    con.commit()
    con.execute("PRAGMA journal_mode = DELETE")
    con.execute("VACUUM")
    con.close()
    print(f"transcripts.db is {os.path.getsize(out_path) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()

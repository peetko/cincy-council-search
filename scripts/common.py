"""Shared helpers: settings, database schema, and text tagging.

Everything here uses only Python's standard library, so there is nothing to
install. SQLite (the database) is built into Python.
"""
import json
import os
import re
import sqlite3

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIG_DIR = os.path.join(ROOT, "config")
DEFAULT_DB = os.path.join(ROOT, "site", "data", "council.db")


def load_json(name):
    with open(os.path.join(CONFIG_DIR, name), encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


SETTINGS = load_json("settings.json")


def rx(pattern):
    return re.compile(pattern, re.IGNORECASE)


# ---------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS meetings (
  event_id       INTEGER PRIMARY KEY,
  guid           TEXT,
  body           TEXT,
  is_council     INTEGER,
  date           TEXT,      -- YYYY-MM-DD
  time           TEXT,
  location       TEXT,
  agenda_url     TEXT,
  minutes_url    TEXT,
  agenda_status  TEXT,
  minutes_status TEXT,
  video_url      TEXT,
  page_url       TEXT,
  last_modified  TEXT
);
CREATE INDEX IF NOT EXISTS meetings_date ON meetings(date);

CREATE TABLE IF NOT EXISTS items (
  item_id        INTEGER PRIMARY KEY,
  event_id       INTEGER,
  date           TEXT,
  body           TEXT,
  is_council     INTEGER,
  seq            INTEGER,
  agenda_number  TEXT,
  matter_id      INTEGER,
  matter_file    TEXT,
  matter_name    TEXT,
  matter_type    TEXT,
  matter_status  TEXT,
  matter_url     TEXT,
  kind           TEXT,      -- ordinance / resolution / motion / communication / other
  title          TEXT,
  action         TEXT,
  action_text    TEXT,
  agenda_note    TEXT,
  minutes_note   TEXT,
  passed         INTEGER,   -- 1 passed, 0 failed, NULL no vote
  outcome        TEXT,      -- short label shown on the site
  outcome_cat    TEXT,      -- passed / failed / referred / filed / held / scheduled / other
  is_final       INTEGER,   -- 1 = a final-passage vote at full Council
  tally          TEXT,
  yes_count      INTEGER,
  no_count       INTEGER,
  mover          TEXT,
  seconder       TEXT,
  sponsors       TEXT,
  departments    TEXT,
  topics         TEXT,
  first_seen     TEXT,      -- when this run first saw the item (used for alerts)
  last_modified  TEXT
);
CREATE INDEX IF NOT EXISTS items_event ON items(event_id);
CREATE INDEX IF NOT EXISTS items_date ON items(date);
CREATE INDEX IF NOT EXISTS items_first_seen ON items(first_seen);

CREATE TABLE IF NOT EXISTS votes (
  item_id   INTEGER,
  person    TEXT,
  value_raw TEXT,
  value     TEXT,          -- yes / no / absent / abstain / other
  PRIMARY KEY (item_id, person)
);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
  title, action_text, minutes_note, agenda_note, matter_file, matter_name,
  sponsors, departments, topics, body,
  content='items', content_rowid='item_id',
  tokenize='porter unicode61'
);
"""


def connect(path):
    new = not os.path.exists(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    if new:
        # 4 KB pages: the website reads the file in 4 KB chunks.
        con.execute("PRAGMA page_size = 4096")
    con.executescript(SCHEMA)
    con.executescript(FTS_SCHEMA)
    return con


def get_meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(con, key, value):
    con.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


FACETS_SCHEMA = """
DROP TABLE IF EXISTS facets;
-- A slim copy of the columns used for filtering, counting and sorting. The
-- website reads this instead of the full items table, so a search only
-- downloads a small slice of the database file. Rebuilt on every run.
CREATE TABLE facets (
  item_id     INTEGER PRIMARY KEY,
  event_id    INTEGER,
  date        TEXT,
  body        TEXT,
  is_council  INTEGER,
  kind        TEXT,
  outcome_cat TEXT,
  seq         INTEGER,
  sponsors    TEXT,
  departments TEXT,
  mover       TEXT,
  dissent     TEXT     -- final-passage votes only: "Name|no; Name|abstain"
);
CREATE INDEX facets_date ON facets(date, is_council, seq);
"""


def rebuild_search_index(con):
    con.executescript(FACETS_SCHEMA)
    con.execute("""INSERT INTO facets(item_id, event_id, date, body, is_council, kind, outcome_cat, seq,
                                      sponsors, departments, mover, dissent)
                   SELECT i.item_id, i.event_id, i.date, i.body, i.is_council, i.kind, i.outcome_cat, i.seq,
                          i.sponsors, i.departments, i.mover,
                          CASE WHEN i.is_final = 1 THEN COALESCE((
                            SELECT group_concat(v.person || '|' || v.value, '; ') FROM votes v
                            WHERE v.item_id = i.item_id AND v.value IN ('no', 'abstain')), '') ELSE '' END
                   FROM items i ORDER BY i.date, i.item_id""")
    con.execute("INSERT INTO items_fts(items_fts) VALUES('rebuild')")


def finalize(con):
    """Shrink the file and make it friendly for reading over the web."""
    con.execute("INSERT INTO items_fts(items_fts) VALUES('optimize')")
    con.commit()
    con.execute("PRAGMA journal_mode = DELETE")
    con.execute("VACUUM")


# ---------------------------------------------------------------- tagging

def _term_pattern(term):
    term = term.strip()
    star = term.endswith("*")
    core = re.escape(term.rstrip("*")).replace(r"\ ", r"\s+")
    return r"\b" + core + (r"\w*" if star else r"\b")


def _compile_groups(groups):
    return {name: re.compile("|".join(_term_pattern(t) for t in terms), re.IGNORECASE)
            for name, terms in groups.items() if terms}


TOPIC_RX = _compile_groups(load_json("topics.json"))
DEPT_RX = _compile_groups(load_json("departments.json"))


def tag(text, groups):
    return [name for name, pat in groups.items() if pat.search(text or "")]


# Case-sensitive on purpose: the sponsor list ends at an ALL-CAPS verb such as
# "AUTHORIZING", or at ", from …", ", on 9/24/2025", "WE MOVE".
SPONSOR_RX = re.compile(
    r"[Ss]ubmitted by (.+?)(?:,\s*(?:from|on|dated)\b|,?\s+(?:WE\s+)?MOVE\b|,\s*[A-Z]{4,}\b|$)")
TITLE_WORDS = re.compile(r"\b(Vice Mayor|President Pro Tem(?:pore)?|Councilmembers?|Council ?[Mm]embers?|Mayor)\b")


def parse_sponsors(title):
    """'MOTION, submitted by Councilmembers Owens and Walsh, WE MOVE…' -> 'Owens; Walsh'."""
    m = SPONSOR_RX.search(title or "")
    if not m:
        return ""
    raw = m.group(1)
    if "City Manager" in raw:
        return "City Manager"
    names = []
    if re.search(r"(?<!Vice )\bMayor\b", raw):
        names.append("Mayor")
        raw = re.sub(r"(?<!Vice )\bMayor(?:\s+[A-Z][a-z]+){0,2}", "", raw)
    for part in re.split(r",|\band\b|&", TITLE_WORDS.sub("", raw)):
        part = part.strip(" .")
        if part and len(part) < 40 and part.lower() not in (n.lower() for n in names):
            names.append(part)
    return "; ".join(names)


KINDS = [("ordinance", "ordinance"), ("resolution", "resolution"), ("motion", "motion"),
         ("communication", "communication"), ("report", "communication"),
         ("appointment", "appointment"), ("registration", "communication"),
         ("presentation", "presentation"), ("statement", "statement")]


def classify_kind(matter_type, title):
    hay = f"{matter_type or ''} {(title or '')[:40]}".lower()
    for word, kind in KINDS:
        if word in hay:
            return kind
    return "other"


SKIP_RX = re.compile(SETTINGS.get("skip_titles_regex") or r"(?!x)x", re.IGNORECASE)


def is_noise(title, matter_id, action):
    """True for section headers and boilerplate lines that aren't real agenda items."""
    t = (title or "").strip()
    if matter_id or (action or "").strip():
        return False
    if not t:
        return True
    if len(t) < 90 and t.upper() == t and any(c.isalpha() for c in t):
        return True  # "ROLL CALL", "AGENDA", "MS. KEARNEY", "BUDGET AND FINANCE COMMITTEE"
    return bool(SKIP_RX.search(t))


def vote_value(raw):
    v = (raw or "").strip().lower()
    for key, words in SETTINGS["vote_values"].items():
        if v in words:
            return key
    return "other"

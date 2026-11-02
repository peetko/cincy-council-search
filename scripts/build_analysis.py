"""Deeper analysis for the site's "Analysis" section → site/data/analysis.json

    python scripts/build_analysis.py

Four parts:
  1. timing     How long legislation takes from first appearance to final vote,
                and items that look stuck in committee.
  2. lobbying   Lobbyist (legislative agent) registrations in the minutes, and
                later agenda items that mention each registered client.
  3. committees Workload per committee: meetings, items, what moves on to Council,
                time spent in committee, and meeting length where minutes record it.
  4. trends     Yearly and per-Council-term counts, and topics over time.

Everything is computed from council.db; the page loads this file only when you
scroll to the Analysis section.
"""
import argparse
import collections
import datetime as dt
import json
import os
import re
import statistics

from common import DEFAULT_DB, SETTINGS, TOPIC_RX, connect

START = SETTINGS["start_date"]
STUCK_DAYS = int(SETTINGS.get("stuck_days", 90))
TERMS = SETTINGS.get("council_terms", [])
# Legislation that first shows up in the first month of data probably started earlier,
# so its timing would be wrong; leave it out of the timing numbers.
TIMING_FROM = (dt.date.fromisoformat(START) + dt.timedelta(days=31)).isoformat()


def days(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def summary(values):
    v = sorted(values)
    if not v:
        return None
    q = lambda p: v[min(len(v) - 1, int(p * (len(v) - 1) + 0.5))]
    return {"n": len(v), "median": statistics.median(v), "p25": q(0.25), "p75": q(0.75)}


def short(text, n=180):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------- 1. timing
def matter_histories(con):
    rows = con.execute("""
        SELECT matter_id, matter_file, item_id, date, body, is_council, kind, outcome_cat, is_final, passed, title
        FROM items WHERE matter_id IS NOT NULL ORDER BY matter_id, date, is_council, seq""").fetchall()
    hist = collections.defaultdict(list)
    for r in rows:
        hist[r["matter_id"]].append(r)
    return hist


def timing(con, today):
    hist = matter_histories(con)
    by_kind, by_year, by_committee = collections.defaultdict(list), collections.defaultdict(list), collections.defaultdict(list)
    stuck = []
    for mid, steps in hist.items():
        first = steps[0]
        final = next((s for s in steps if s["is_final"] and s["passed"] is not None), None)
        if final and first["date"] >= TIMING_FROM:
            d = days(first["date"], final["date"])
            by_kind[final["kind"]].append(d)
            by_year[final["date"][:4]].append(d)
            committee = next((s["body"] for s in steps if not s["is_council"] and s["date"] <= final["date"]), None)
            if committee:
                by_committee[committee].append(d)
        # Stuck: last step was a referral (or a committee hold), nothing decided, and it's been a while.
        if final or first["kind"] not in ("ordinance", "resolution", "motion"):
            continue
        last = steps[-1]
        if any(s["outcome_cat"] in ("filed", "failed", "passed") for s in steps):
            continue
        waiting = days(last["date"], today)
        if last["outcome_cat"] in ("referred", "held", "recommended", "other") and waiting >= STUCK_DAYS:
            referred_to = None
            if last["outcome_cat"] == "referred":
                m = re.search(r"Referred to (.+)", con.execute("SELECT action FROM items WHERE item_id = ?",
                                                                (last["item_id"],)).fetchone()[0] or "")
                referred_to = m.group(1).strip() if m else None
            stuck.append({"file": first["matter_file"], "kind": first["kind"], "title": short(first["title"]),
                          "first": first["date"], "last": last["date"], "waiting": waiting,
                          "where": referred_to or last["body"], "status": last["outcome_cat"]})
    stuck.sort(key=lambda s: s["last"], reverse=True)
    where = collections.Counter(s["where"] for s in stuck)
    return {
        "by_kind": {k: summary(v) for k, v in by_kind.items() if k in ("ordinance", "resolution", "motion")},
        "by_year": {y: summary(v) for y, v in sorted(by_year.items())},
        "by_committee": sorted(({"body": b, **summary(v)} for b, v in by_committee.items() if len(v) >= 5),
                               key=lambda x: -x["n"]),
        "stuck_total": len(stuck),
        "stuck_by_where": where.most_common(12),
        "stuck": stuck[:300],
        "stuck_days": STUCK_DAYS,
        "from": TIMING_FROM,
    }


# ---------------------------------------------------------------- 2. lobbying
AGENT_RX = re.compile(r"Legislative Agents?\s*[:,]?\s*([^,(]+?)(?=\s*[,(]|(?<=\w{2})\.\s|$)", re.I)
PAREN_RX = re.compile(r"\(([^()]{2,160})\)")
NOT_CLIENT = re.compile(r"^\s*(termination|terminated|amended|amendment|renewal|renewed|revised|corrected|update[d]?|"
                        r"emergency|\d+|[a-z]|see attached|attached|[a-z] version|substitute|as amended)\s*$", re.I)


def client_of(title):
    """The client is the last parenthesized name in a registration, e.g. '… OH 45219. (ART ACADEMY OF CINCINNATI)'."""
    groups = [g.strip() for g in PAREN_RX.findall(title or "") if not NOT_CLIENT.match(g)]
    if groups:
        return groups[-1]
    m = re.search(r"\(([^()]{3,160})$", (title or "").strip())  # closing parenthesis missing
    return m.group(1).strip(" .") if m and not NOT_CLIENT.match(m.group(1)) else None


CORP_WORDS = re.compile(r"\b(inc|incorporated|llc|l\.l\.c|llp|co|company|corp|corporation|ltd|lp|pllc|na|n\.a)\b\.?", re.I)
GENERIC = {"city", "cincinnati", "ohio", "county", "state", "group", "association", "foundation", "services",
           "partners", "holdings", "development", "properties", "council", "public", "affairs", "consulting"}
# Capitalization for names that arrive in ALL CAPS ("FC CINCINNATI" -> "FC Cincinnati", "3CDC" stays "3CDC").
SMALL_WORDS = {"of", "the", "and", "for", "in", "at", "on", "to", "a", "an", "by", "or", "de", "la", "du"}
ACRONYMS = {"YMCA", "YWCA", "USA", "OTR", "CBA", "AFL", "CIO", "IBEW", "SEIU", "AFSCME", "NAACP", "UC", "NKU", "DAAP",
            "CVG", "PNC", "CVS", "TQL", "GE", "P&G", "AT&T", "HCDC", "REDI", "SORTA", "TANK", "OKI", "CPS", "CMHA",
            "FOP", "IAFF", "ULI", "NAIOP", "AIA", "MSD", "GCWW", "ODOT", "LISC", "CDC", "FC", "ACLU", "LGBTQ", "AARP",
            "HUD", "UAW", "CEO", "LLC", "BRT", "CNG", "ECDI", "MORTAR", "CUF", "CHCURC", "NAMI", "UCMC", "CCHMC", "OTRCH"}
ACRONYMS.discard("MORTAR")


def _word(w, first):
    core = re.sub(r"[^A-Za-z0-9&]", "", w)
    if not core:
        return w
    if any(ch.isdigit() for ch in core) or core in ACRONYMS:
        return w.upper()
    if not first and core.lower() in SMALL_WORDS:
        return w.lower()
    if len(core) <= 2 or (len(core) <= 5 and not re.search(r"[AEIOUY]", core)):
        return w.upper()  # FC, UC, PNC, CVG
    out = w[0].upper() + w[1:].lower()
    return re.sub(r"(?<=\b[OoDd]')([a-z])|(?<=^Mc)([a-z])", lambda m: m.group(0).upper(), out)  # O'Neill, McDonald


def nice(name):
    """Readable capitalization for ALL-CAPS names; names already in mixed case are left alone."""
    name = re.sub(r"\s+", " ", name).strip(" ,.;")
    if any(ch.islower() for ch in name):
        return name
    words = name.split(" ")
    return " ".join("-".join(_word(part, i == 0 and j == 0) for j, part in enumerate(w.split("-")))
                    for i, w in enumerate(words))


def client_name(raw):
    core = CORP_WORDS.sub(" ", raw)
    core = re.sub(r"[^A-Za-z0-9&'. \-]+", " ", core)
    return nice(re.sub(r"\s+", " ", core))


def client_key(name):
    return re.sub(r"^the ", "", re.sub(r"[^a-z0-9& ]+", "", name.lower())).strip()


SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|esq)\b\.?", re.I)


def agent_name(raw):
    name = re.sub(r"\s+", " ", raw).strip(" .;:")
    name = re.sub(r"^(mr|ms|mrs|dr)\.?\s+", "", name, flags=re.I)
    return nice(name) if 2 <= len(name) <= 60 else None


def agent_key(name):
    parts = SUFFIX.sub("", name).replace(".", " ").split()
    return (parts[0][0].lower(), parts[-1].lower()) if len(parts) >= 2 else (name.lower(), "")


def merge_agents(names):
    """'Doug Moormann' / 'Douglas Moormann' and 'Charles Gerhardt' / 'Charles H. Gerhardt III' -> one person each,
    shown with the most-used spelling (the longest on a tie)."""
    groups = collections.defaultdict(collections.Counter)
    for n in names:
        groups[agent_key(n)][n] += 1
    return sorted(max(c.items(), key=lambda kv: (kv[1], len(kv[0])))[0] for c in groups.values())


def lobbying(con, today):
    regs = con.execute("""
        SELECT item_id, date, title FROM items
        WHERE matter_type LIKE 'Registration%' OR title LIKE 'REGISTRATION%'
        ORDER BY date""").fetchall()
    clients, parsed, per_year = {}, 0, collections.Counter()
    for r in regs:
        t = r["title"] or ""
        raw = client_of(t)
        m_agent = AGENT_RX.search(t)
        per_year[r["date"][:4]] += 1
        if not raw:
            continue
        parsed += 1
        name = client_name(raw)
        key = client_key(name)
        if len(key) < 3 or key in GENERIC:
            continue
        c = clients.setdefault(key, {"names": collections.Counter(), "agents": [], "registrations": [], "key": key})
        c["names"][name] += 1
        agent = agent_name(m_agent.group(1)) if m_agent else None
        if agent:
            c["agents"].append(agent)
        c["registrations"].append(r["date"])

    out, all_agents = [], []
    for key, c in clients.items():
        name = max(c["names"].items(), key=lambda kv: (kv[1], len(kv[0])))[0]
        first, last = min(c["registrations"]), max(c["registrations"])
        until = (dt.date.fromisoformat(last) + dt.timedelta(days=365)).isoformat()
        words = re.sub(r"[^\w&' ]+", " ", name).strip()
        phrase = '"' + words + '"'
        # Legislation only (ordinances, resolutions, motions), matched in its own title text, while the
        # client was registered: first registration through a year after the latest one.
        try:
            hits = con.execute("""
                SELECT i.item_id, i.matter_id, i.date, i.matter_file, i.title, i.outcome, i.body FROM items_fts
                JOIN items i ON i.item_id = items_fts.rowid
                WHERE items_fts MATCH ? AND i.date BETWEEN ? AND ?
                  AND i.kind IN ('ordinance', 'resolution', 'motion') AND i.matter_id IS NOT NULL
                ORDER BY i.date DESC""", ("{title matter_name} : " + phrase, first, until)).fetchall()
        except Exception:
            hits = []
        seen, uniq = set(), []
        for h in hits:  # one row per piece of legislation, newest appearance first
            if h["matter_id"] not in seen:
                seen.add(h["matter_id"])
                uniq.append(h)
        passed = con.execute(f"""SELECT COUNT(DISTINCT matter_id) FROM items WHERE outcome_cat = 'passed'
                                 AND matter_id IN ({",".join("?" * len(seen)) or "NULL"})""", list(seen)).fetchone()[0]
        agents = merge_agents(c["agents"])
        all_agents += agents
        out.append({"client": name, "query": "title:" + phrase, "agents": agents[:8],
                    "registrations": len(c["registrations"]), "first": first, "last": last,
                    "from": first, "to": until, "mentions": len(uniq), "passed": passed,
                    "recent": [{"date": h["date"], "file": h["matter_file"], "title": short(h["title"], 140),
                                "outcome": h["outcome"], "body": h["body"]} for h in uniq[:3]]})
    out.sort(key=lambda c: (-c["mentions"], c["client"]))
    return {"registrations": len(regs), "parsed": parsed, "clients_total": len(out),
            "per_year": dict(sorted(per_year.items())),
            "top_agents": collections.Counter(all_agents).most_common(10), "clients": out}


# ---------------------------------------------------------------- 3. committees
TIME_RX = re.compile(r"(?:began|convened|called to order|start(?:ed)?)[^.]*?at\s+(\d{1,2})[:.](\d{2})\s*([ap])\.?\s*m.*?"
                     r"(?:ended|adjourned|concluded)[^.]*?at\s+(\d{1,2})[:.](\d{2})\s*([ap])\.?\s*m", re.I | re.S)


def to_min(h, m, ap):
    h = int(h) % 12 + (12 if ap.lower() == "p" else 0)
    return h * 60 + int(m)


def committees(con, today):
    since = (dt.date.fromisoformat(today) - dt.timedelta(days=365)).isoformat()
    hist = matter_histories(con)
    stay = collections.defaultdict(list)       # days from first committee appearance to first later Council step
    moved = collections.Counter()               # matters that went on to Council afterwards (last 12 months)
    seen_in = collections.Counter()             # matters seen in committee (last 12 months)
    for steps in hist.values():
        for i, s in enumerate(steps):
            if s["is_council"]:
                continue
            later = next((x for x in steps[i + 1:] if x["is_council"]), None)
            if s["date"] >= since:
                seen_in[s["body"]] += 1
                if later:
                    moved[s["body"]] += 1
            if later:
                stay[s["body"]].append(days(s["date"], later["date"]))
            break  # first committee appearance only
    lengths = collections.defaultdict(list)
    for r in con.execute("""SELECT body, title, minutes_note FROM items
                            WHERE is_council = 0 AND (title LIKE '%began%' OR title LIKE '%convened%' OR title LIKE '%called to order%'
                                  OR minutes_note LIKE '%began%' OR minutes_note LIKE '%called to order%')"""):
        m = TIME_RX.search(" ".join(x or "" for x in (r["title"], r["minutes_note"])))
        if m:
            mins = to_min(m.group(4), m.group(5), m.group(6)) - to_min(m.group(1), m.group(2), m.group(3))
            if 0 < mins < 600:
                lengths[r["body"]].append(mins)
    rows = []
    for r in con.execute("""
        SELECT m.body, COUNT(DISTINCT m.event_id) AS meetings, MIN(m.date) AS first, MAX(m.date) AS last,
               SUM(m.date >= ?) AS recent_meetings
        FROM meetings m WHERE m.is_council = 0 GROUP BY m.body ORDER BY MAX(m.date) DESC""", (since,)):
        items = con.execute("SELECT COUNT(*) FROM items WHERE body = ? AND date >= ?", (r["body"], since)).fetchone()[0]
        rows.append({"body": r["body"], "active": r["last"] >= since, "first": r["first"], "last": r["last"],
                     "meetings_12m": r["recent_meetings"], "meetings_all": r["meetings"], "items_12m": items,
                     "matters_12m": seen_in.get(r["body"], 0), "to_council_12m": moved.get(r["body"], 0),
                     "stay": summary(stay.get(r["body"], [])),
                     "length": summary(lengths.get(r["body"], []))})
    return {"since": since, "committees": rows,
            "length_parsed": sum(len(v) for v in lengths.values())}


# ---------------------------------------------------------------- 4. trends
def count_between(con, a, b):
    q = lambda sql: con.execute(sql, (a, b)).fetchone()[0] or 0
    ord_final = q("""SELECT COUNT(DISTINCT COALESCE(matter_id, -item_id)) FROM items
                     WHERE kind='ordinance' AND is_final=1 AND passed=1 AND date BETWEEN ? AND ?""")
    ord_emerg = q("""SELECT COUNT(DISTINCT COALESCE(matter_id, -item_id)) FROM items
                     WHERE kind='ordinance' AND is_final=1 AND passed=1 AND action LIKE '%emergency%' AND date BETWEEN ? AND ?""")
    return {
        "ordinances": ord_final,
        "emergency_share": round(ord_emerg / ord_final * 100) if ord_final else None,
        "resolutions": q("""SELECT COUNT(DISTINCT COALESCE(matter_id, -item_id)) FROM items
                            WHERE kind='resolution' AND is_final=1 AND passed=1 AND date BETWEEN ? AND ?"""),
        "motions": q("""SELECT COUNT(DISTINCT COALESCE(matter_id, -item_id)) FROM items
                        WHERE kind='motion' AND is_council=1 AND date BETWEEN ? AND ?"""),
        "split_votes": q("""SELECT COUNT(DISTINCT i.item_id) FROM items i JOIN votes v ON v.item_id = i.item_id
                            WHERE i.is_final=1 AND v.value IN ('no','abstain') AND i.date BETWEEN ? AND ?"""),
        "meetings": q("SELECT COUNT(*) FROM meetings WHERE date BETWEEN ? AND ?"),
    }


def topic_series(con, periods):
    out = {t: [] for t in TOPIC_RX}
    for p in periods:
        seen, counts = set(), collections.Counter()
        for r in con.execute("SELECT item_id, matter_id, topics FROM items WHERE date BETWEEN ? AND ?", (p["start"], p["end"])):
            key = r["matter_id"] or -r["item_id"]
            for t in filter(None, (x.strip() for x in (r["topics"] or "").split(";"))):
                if (key, t) not in seen:
                    seen.add((key, t))
                    counts[t] += 1
        for t in TOPIC_RX:
            out[t].append(counts.get(t, 0))
    return out


def trends(con, today):
    first_year, this_year = int(START[:4]), int(today[:4])
    years = []
    for y in range(first_year, this_year + 1):
        a, b = max(f"{y}-01-01", START), min(f"{y}-12-31", today)
        partial = a != f"{y}-01-01" or b != f"{y}-12-31"
        years.append({"label": str(y) + (" (partial)" if partial else ""), "start": a, "end": b, "partial": partial})
    terms = []
    for t in TERMS:
        if t["end"] < START or t["start"] > today:
            continue
        a, b = max(t["start"], START), min(t["end"], today)
        partial = a != t["start"] or b != t["end"]
        terms.append({"label": t["label"] + (" (partial)" if partial else ""), "start": a, "end": b, "partial": partial})
    for group in (years, terms):
        for p in group:
            p.update(count_between(con, p["start"], p["end"]))
            p["days"] = days(p["start"], p["end"]) + 1
    return {"years": years, "terms": terms,
            "topics_by_year": topic_series(con, years), "topics_by_term": topic_series(con, terms)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--today", default=None)
    args = ap.parse_args()
    con = connect(args.db)
    today = args.today or dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date().isoformat()
    out = {"today": today, "timing": timing(con, today), "lobbying": lobbying(con, today),
           "committees": committees(con, today), "trends": trends(con, today)}
    path = os.path.join(os.path.dirname(os.path.abspath(args.db)), "analysis.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    lob = out["lobbying"]
    print(f"Wrote {path} ({os.path.getsize(path) // 1024} KB): {out['timing']['stuck_total']} stuck items, "
          f"{lob['parsed']}/{lob['registrations']} registrations parsed into {lob['clients_total']} clients, "
          f"{out['committees']['length_parsed']} meeting lengths found")


if __name__ == "__main__":
    main()

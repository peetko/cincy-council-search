"""'What's new' on the site: a plain-language log of recent additions -> site/data/whatsnew.json

    python scripts/build_whatsnew.py

Uses dates the pipeline already records: when each agenda item was first seen, when the
full text of each piece of legislation was collected, and when each AI summary was written.
The very first, all-at-once download isn't counted as "new".
"""
import argparse
import collections
import datetime as dt
import json
import os
import re
import sqlite3

from common import DEFAULT_DB

DAYS = 14
EASTERN = dt.timezone(dt.timedelta(hours=-5))


def local_day(ts):
    """'2026-11-03T18:00:09+00:00' -> '2026-11-03' (Eastern)."""
    try:
        return dt.datetime.fromisoformat(ts).astimezone(EASTERN).date().isoformat()
    except (TypeError, ValueError):
        return (ts or "")[:10]


def short(text, n=160):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--today", default=None)
    args = ap.parse_args()
    data_dir = os.path.dirname(os.path.abspath(args.db))
    today = args.today or dt.datetime.now(EASTERN).date().isoformat()
    since = (dt.date.fromisoformat(today) - dt.timedelta(days=DAYS - 1)).isoformat()
    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

    first_load = con.execute("SELECT MIN(first_seen) FROM items").fetchone()[0]
    rows = con.execute("""
        SELECT i.item_id, i.event_id, i.first_seen, i.date, i.body, i.is_council, i.kind, i.matter_file, i.title,
               m.time, m.agenda_url, m.minutes_url
        FROM items i JOIN meetings m ON m.event_id = i.event_id
        WHERE i.first_seen IS NOT NULL AND i.first_seen <> ?""", (first_load,)).fetchall()
    days = collections.defaultdict(lambda: {"items": 0, "full_texts": 0, "summaries": 0})
    meetings = {}
    for r in rows:
        day = local_day(r["first_seen"])
        if day < since:
            continue
        if r["date"] < (dt.date.fromisoformat(day) - dt.timedelta(days=60)).isoformat():
            continue  # an old meeting filled in by a catch-up download, not news
        days[day]["items"] += 1
        m = meetings.setdefault(r["event_id"], {"event_id": r["event_id"], "date": r["date"], "time": r["time"],
                                                "body": r["body"], "added": day, "new_items": 0,
                                                "agenda_url": r["agenda_url"], "minutes_url": r["minutes_url"],
                                                "kind": "agenda" if r["date"] >= day else "minutes"})
        m["new_items"] += 1
        m["added"] = max(m["added"], day)

    decided = [dict(r) for r in con.execute("""
        SELECT i.matter_file, i.kind, i.title, i.outcome, i.outcome_cat, i.date FROM items i
        WHERE i.is_final = 1 AND i.outcome_cat IN ('passed', 'failed') AND i.date BETWEEN ? AND ?
        ORDER BY i.date DESC, i.seq LIMIT 20""", (since, today))]
    for d in decided:
        d["title"] = short(d["title"])

    summaries, docs_info = [], {}
    docs_path = os.path.join(data_dir, "docs.db")
    if os.path.exists(docs_path):
        dc = sqlite3.connect(docs_path)
        for day, n in dc.execute("SELECT checked, COUNT(*) FROM docs WHERE status = 'ok' AND checked >= ? GROUP BY checked", (since,)):
            days[day]["full_texts"] += n
        has_sum = dc.execute("SELECT 1 FROM sqlite_master WHERE name = 'summaries'").fetchone()
        if has_sum:
            for day, n in dc.execute("SELECT created, COUNT(*) FROM summaries WHERE status = 'ok' AND created >= ? GROUP BY created", (since,)):
                days[day]["summaries"] += n
            titles = {r[0]: r[1] for r in con.execute("SELECT matter_file, MAX(title) FROM items WHERE matter_file IS NOT NULL GROUP BY matter_file")}
            for file, summary, created in dc.execute("""SELECT matter_file, summary, created FROM summaries
                                                        WHERE status = 'ok' ORDER BY created DESC, matter_id DESC LIMIT 12"""):
                summaries.append({"file": file, "created": created, "summary": summary, "title": short(titles.get(file, ""), 120)})
            docs_info["summaries_total"] = dc.execute("SELECT COUNT(*) FROM summaries WHERE status = 'ok'").fetchone()[0]
        docs_info["full_texts_total"] = dc.execute("SELECT COUNT(*) FROM docs WHERE status = 'ok'").fetchone()[0]

    log = [{"day": d, **days[d]} for d in sorted(days, reverse=True) if d >= since and any(days[d].values())]
    out = {"today": today, "since": since, "days": DAYS,
           "updated_at": dt.datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M"),
           "meetings": sorted(meetings.values(), key=lambda m: (m["added"], m["date"]), reverse=True)[:20],
           "decided": decided, "summaries": summaries, "log": log, **docs_info}
    path = os.path.join(data_dir, "whatsnew.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"Wrote {path}: {len(out['meetings'])} meetings with new items, {len(decided)} recent decisions, "
          f"{sum(d['summaries'] for d in log)} new summaries, {sum(d['full_texts'] for d in log)} new full texts in {DAYS} days")


if __name__ == "__main__":
    main()

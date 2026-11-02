"""Compute the running statistics shown on the site into site/data/stats.json.

    python scripts/build_stats.py

Every comparison is "to date": e.g. on Nov 2, "Year" compares Jan 1–Nov 2 this
year with Jan 1–Nov 2 last year, so a partial year is never compared with a
full one.
"""
import argparse
import collections
import datetime as dt
import json
import os

from common import DEFAULT_DB, TOPIC_RX, DEPT_RX, connect, get_meta

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MIN_TOPIC_VOLUME = 3   # ignore topics with fewer items than this in both periods


def shift_year(d, years=-1):
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # Feb 29
        return d.replace(year=d.year + years, day=28)


def spans(today):
    q_start_month = 3 * ((today.month - 1) // 3) + 1
    starts = {
        "month": today.replace(day=1),
        "quarter": today.replace(month=q_start_month, day=1),
        "year": today.replace(month=1, day=1),
    }
    out = {}
    for key, start in starts.items():
        out[key] = {"cur": (start.isoformat(), today.isoformat()),
                    "prev": (shift_year(start).isoformat(), shift_year(today).isoformat())}
    q = (today.month - 1) // 3 + 1
    out["month"]["long"] = f"{MONTHS[today.month - 1]} {today.year} so far, compared with the same days of {today.year - 1}"
    out["month"]["short"] = f"{MONTHS[today.month - 1]} {today.year} vs. {MONTHS[today.month - 1]} {today.year - 1}"
    out["quarter"]["long"] = f"Q{q} {today.year} so far, compared with the same days of Q{q} {today.year - 1}"
    out["quarter"]["short"] = f"Q{q} {today.year} vs. Q{q} {today.year - 1}"
    out["year"]["long"] = f"{today.year} year to date, compared with the same stretch of {today.year - 1}"
    out["year"]["short"] = f"{today.year} YTD vs. {today.year - 1}"
    return out


def one(con, sql, args=()):
    row = con.execute(sql, args).fetchone()
    return row[0] if row and row[0] is not None else 0


def distinct_passed(con, kind, a, b):
    return one(con, """SELECT COUNT(DISTINCT COALESCE(matter_id, -item_id)) FROM items
                       WHERE kind = ? AND is_final = 1 AND passed = 1 AND date BETWEEN ? AND ?""", (kind, a, b))


def motions(con, a, b):
    return one(con, """SELECT COUNT(DISTINCT COALESCE(matter_id, -item_id)) FROM items
                       WHERE kind = 'motion' AND is_council = 1 AND date BETWEEN ? AND ?""", (a, b))


def meetings_with_minutes(con, a, b):
    rows = con.execute("""SELECT is_council, COUNT(*) FROM meetings
                          WHERE minutes_url IS NOT NULL AND minutes_url <> '' AND date BETWEEN ? AND ?
                          GROUP BY is_council""", (a, b)).fetchall()
    d = {r[0]: r[1] for r in rows}
    return d.get(1, 0), d.get(0, 0)


def vote_summary(con, a, b):
    """Per final-passage vote: how many members took part, and was it unanimous."""
    rows = con.execute("""
        SELECT i.item_id,
               SUM(v.value IN ('yes','no','abstain')) AS present,
               SUM(v.value = 'no') AS nos,
               COUNT(v.person) AS n
        FROM items i JOIN votes v ON v.item_id = i.item_id
        WHERE i.is_final = 1 AND i.date BETWEEN ? AND ?
        GROUP BY i.item_id""", (a, b)).fetchall()
    if not rows:
        return None, None, 0
    avg_present = sum(r["present"] for r in rows) / len(rows)
    unanimous = sum(1 for r in rows if r["nos"] == 0) / len(rows)
    return avg_present, unanimous, len(rows)


def pct_change(cur, prev):
    if prev == 0:
        return None
    return round((cur - prev) / prev * 100)


def kpis(con, sp):
    (a, b), (pa, pb) = sp["year"]["cur"], sp["year"]["prev"]
    out = []

    def counted(label, cur, prev):
        ch = pct_change(cur, prev)
        if ch is None:
            delta, arrow = f"none in the same stretch of last year", ""
        else:
            arrow = "▲" if ch > 0 else ("▼" if ch < 0 else "")
            delta = f"{abs(ch)}% vs. same point last year" if ch else "same as this point last year"
        out.append({"label": label, "value": f"{cur:,}", "arrow": arrow, "delta": delta,
                    "cur": cur, "prev": prev})

    counted("Ordinances passed", distinct_passed(con, "ordinance", a, b), distinct_passed(con, "ordinance", pa, pb))
    counted("Resolutions adopted", distinct_passed(con, "resolution", a, b), distinct_passed(con, "resolution", pa, pb))
    counted("Motions introduced", motions(con, a, b), motions(con, pa, pb))
    c, k = meetings_with_minutes(con, a, b)
    out.append({"label": "Meetings with minutes", "value": f"{c + k:,}", "arrow": "",
                "delta": f"Council {c} · committees {k}"})
    avg, unan, n = vote_summary(con, a, b)
    pavg, punan, pn = vote_summary(con, pa, pb)
    out.append({"label": "Average members present", "value": f"{avg:.1f} of 9" if avg is not None else "—",
                "arrow": "", "delta": f"across {n} final votes" if n else "no final votes recorded yet"})
    if unan is not None:
        arrow, delta = "", "no votes to compare last year"
        if punan is not None:
            diff = round((unan - punan) * 100)
            arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "")
            delta = f"{abs(diff)} points vs. same point last year" if diff else "same as last year"
        out.append({"label": "Unanimous final votes", "value": f"{round(unan * 100)}%", "arrow": arrow, "delta": delta})
    else:
        out.append({"label": "Unanimous final votes", "value": "—", "arrow": "", "delta": "no final votes recorded yet"})
    return out


def monthly(con, today):
    def series(year, last_month):
        res = []
        for m in range(1, last_month + 1):
            a = dt.date(year, m, 1)
            b = (dt.date(year + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
            if m == today.month:  # current month: compare the same days only
                b = today if year == today.year else shift_year(today, year - today.year)
            res.append(distinct_passed(con, "ordinance", a.isoformat(), b.isoformat()))
        return res
    return {"year": today.year, "labels": MONTHS[:today.month],
            "cur": series(today.year, today.month), "prev": series(today.year - 1, today.month)}


def topic_counts(con, a, b):
    counts = collections.Counter()
    seen = set()
    for r in con.execute("SELECT item_id, matter_id, topics FROM items WHERE date BETWEEN ? AND ?", (a, b)):
        key = r["matter_id"] or -r["item_id"]
        for t in filter(None, (x.strip() for x in (r["topics"] or "").split(";"))):
            if (key, t) not in seen:
                seen.add((key, t))
                counts[t] += 1
    return counts


def topics(con, sp):
    cur = topic_counts(con, *sp["cur"])
    prev = topic_counts(con, *sp["prev"])
    out = []
    for name in TOPIC_RX:
        c, p = cur.get(name, 0), prev.get(name, 0)
        if max(c, p) < MIN_TOPIC_VOLUME:
            continue
        ch = pct_change(c, p)
        out.append({"name": name, "cur": c, "prev": p, "change": ch})
    # Biggest risers first; brand-new topics (no prior items) at the very top.
    out.sort(key=lambda t: (t["change"] is None, t["change"] if t["change"] is not None else 0), reverse=True)
    return out


def members(con, sp):
    a, b = sp["cur"]
    rows = con.execute("""
        SELECT v.item_id, v.person, v.value FROM votes v JOIN items i ON i.item_id = v.item_id
        WHERE i.is_final = 1 AND i.date BETWEEN ? AND ?""", (a, b)).fetchall()
    by_item = collections.defaultdict(list)
    for r in rows:
        by_item[r["item_id"]].append((r["person"], r["value"]))
    people = collections.defaultdict(lambda: collections.Counter())
    for item, vs in by_item.items():
        yes = sum(1 for _, v in vs if v == "yes")
        no = sum(1 for _, v in vs if v == "no")
        majority = "yes" if yes >= no else "no"
        for person, v in vs:
            c = people[person]
            c["total"] += 1
            c[v] += 1
            if v in ("yes", "no") and v != majority:
                c["against"] += 1
    out = []
    for person, c in people.items():
        absent = c["absent"] + c["other"]
        out.append({"name": person, "total": c["total"], "yes": c["yes"], "no": c["no"],
                    "absent": absent, "abstain": c["abstain"], "against": c["against"],
                    "present": round((c["total"] - absent) / c["total"] * 100) if c["total"] else 0})
    out.sort(key=lambda m: m["name"].split()[-1] if m["name"] else "")
    return {"final_votes": len(by_item), "members": out}


def lists(con):
    bodies = [r[0] for r in con.execute(
        "SELECT body FROM meetings GROUP BY body ORDER BY MAX(is_council) DESC, COUNT(*) DESC")]
    counter = collections.Counter()
    for r in con.execute("SELECT sponsors FROM items WHERE sponsors <> ''"):
        for s in r[0].split(";"):
            s = s.strip()
            if s:
                counter[s] += 1
    sponsors = sorted([s for s, n in counter.most_common(60) if n >= 3])
    return {"bodies": bodies, "sponsors": sponsors, "departments": list(DEPT_RX)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=None)
    ap.add_argument("--today", default=None, help="YYYY-MM-DD (testing)")
    args = ap.parse_args()
    out_path = args.out or os.path.join(os.path.dirname(os.path.abspath(args.db)), "stats.json")

    con = connect(args.db)
    today = (dt.date.fromisoformat(args.today) if args.today
             else dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date())
    sp = spans(today)
    stats = {
        "today": today.isoformat(),
        "updated_at": get_meta(con, "updated_at"),
        "meetings_indexed": one(con, "SELECT COUNT(*) FROM meetings"),
        "items_indexed": one(con, "SELECT COUNT(*) FROM items"),
        "first_date": con.execute("SELECT MIN(date) FROM meetings").fetchone()[0],
        "kpis": kpis(con, sp),
        "monthly": monthly(con, today),
        "periods": {},
        "lists": lists(con),
        "db_bytes": os.path.getsize(args.db),
    }
    for key in ("month", "quarter", "year"):
        stats["periods"][key] = {
            "long": sp[key]["long"], "short": sp[key]["short"],
            "topics": topics(con, sp[key]),
            **members(con, sp[key]),
        }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()

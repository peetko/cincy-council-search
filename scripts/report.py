"""Print a quick health check of the database, with warnings when a setting in
config/settings.json probably needs adjusting for Cincinnati's real data.

    python scripts/report.py
"""
import argparse
import sys

from common import DEFAULT_DB, connect


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args()
    con = connect(args.db)
    q = lambda sql: con.execute(sql).fetchall()
    warnings = []

    print("== Meeting bodies (is_council = counted as full City Council) ==")
    for r in q("SELECT body, is_council, COUNT(*) n, MIN(date), MAX(date) FROM meetings GROUP BY body ORDER BY n DESC"):
        print(f"  {r[2]:5}  {'COUNCIL ' if r[1] else '        '} {r[0]}  ({r[3]} to {r[4]})")
    if not q("SELECT 1 FROM meetings WHERE is_council = 1 LIMIT 1"):
        warnings.append("No meeting matched council_body_regex - fix it in config/settings.json.")

    print("\n== Vote words seen ==")
    total = 0
    other = 0
    for r in q("SELECT value_raw, value, COUNT(*) FROM votes GROUP BY value_raw, value ORDER BY 3 DESC"):
        print(f"  {r[2]:7}  {r[0]!r} -> {r[1]}")
        total += r[2]
        other += r[2] if r[1] == "other" else 0
    if total and other / total > 0.02:
        warnings.append("Some vote words map to 'other' - add them under vote_values in config/settings.json.")
    if not total:
        warnings.append("No roll-call votes were downloaded - check vote_bodies_regex in config/settings.json.")

    print("\n== Most common actions (at full Council) ==")
    for r in q("""SELECT action, SUM(is_final), COUNT(*) FROM items WHERE is_council = 1 AND action <> ''
                  GROUP BY action ORDER BY 3 DESC LIMIT 15"""):
        print(f"  {r[2]:6}  {'FINAL ' if r[1] else '      '} {r[0]}")
    if not q("SELECT 1 FROM items WHERE is_final = 1 LIMIT 1"):
        warnings.append("No item counted as a final-passage vote - adjust final_passage_regex / not_final_regex.")

    print("\n== Item types ==")
    for r in q("SELECT kind, matter_type, COUNT(*) FROM items GROUP BY kind, matter_type ORDER BY 3 DESC LIMIT 15"):
        print(f"  {r[2]:6}  {r[0]:<14} {r[1]}")

    print("\n== Final-passage votes by action (full Council) ==")
    for r in q("""SELECT action, COUNT(*), SUM(passed = 1) FROM items WHERE is_final = 1
                  GROUP BY action ORDER BY 2 DESC"""):
        print(f"  {r[1]:6}  {r[0]}  ({r[2]} passed)")

    print("\n== Samples of items with no type (kind = other, blank matter type) ==")
    for r in q("""SELECT action, substr(title, 1, 110), COUNT(*) OVER () FROM items
                  WHERE kind = 'other' AND matter_type = '' ORDER BY random() LIMIT 12"""):
        print(f"  [{r[0] or '-'}] {r[1]}")
    for r in q("""SELECT substr(title, 1, 25) AS start, COUNT(*) FROM items
                  WHERE kind = 'other' AND matter_type = '' GROUP BY start ORDER BY 2 DESC LIMIT 12"""):
        print(f"  {r[1]:6}  starts with: {r[0]!r}")

    print("\n== Committee memberships (office records) ==")
    n_off = q("SELECT COUNT(*) FROM office_records")[0][0]
    print(f"  {n_off} records")
    for r in q("SELECT body, title, COUNT(*) FROM office_records WHERE end_date = '' OR end_date >= date('now') GROUP BY body, title ORDER BY body LIMIT 30"):
        print(f"  {r[2]:3}  {r[1] or '-':<12} {r[0]}")
    voters = {r[0] for r in q("SELECT DISTINCT person FROM votes")}
    office_people = {r[0] for r in q("SELECT DISTINCT person FROM office_records")}
    if n_off and voters and not (voters & office_people):
        warnings.append("No voter names match office-record names; member pages won't show committee roles.")
    if not n_off:
        warnings.append("No office records downloaded; member pages won't show committee roles.")

    print("\n== Neighborhood mentions (items) ==")
    counts = {}
    for r in q("SELECT neighborhoods FROM items WHERE neighborhoods <> ''"):
        for h in r[0].split("; "):
            counts[h] = counts.get(h, 0) + 1
    for h, c in sorted(counts.items(), key=lambda x: -x[1])[:15]:
        print(f"  {c:6}  {h}")
    import os, json
    gj = os.path.join(os.path.dirname(os.path.abspath(args.db)), "neighborhoods.geojson")
    if os.path.exists(gj):
        names = [f["properties"]["name"] for f in json.load(open(gj))["features"]]
        print(f"  map has boundaries for {len(names)} neighborhoods")
    else:
        print("  map boundaries: not available (the site shows a list instead)")

    print("\n== Tag coverage ==")
    n = q("SELECT COUNT(*) FROM items")[0][0] or 1
    for col in ("topics", "departments", "neighborhoods", "sponsors"):
        k = q(f"SELECT COUNT(*) FROM items WHERE {col} <> ''")[0][0]
        print(f"  {col:<12} {k / n:.0%} of {n} items")

    print("\n== Analysis inputs ==")
    print("  sample lobbyist registration titles:")
    for (t,) in q("""SELECT title FROM items WHERE title LIKE 'REGISTRATION%' OR title LIKE '%Legislative Agent%'
                     ORDER BY date DESC LIMIT 4"""):
        print("    - " + (t or "")[:200])
    print("  sample meeting start/end notes:")
    for (t,) in q("""SELECT COALESCE(NULLIF(minutes_note, ''), title) FROM items WHERE is_council = 0
                     AND (title LIKE '%began%' OR title LIKE '%called to order%' OR minutes_note LIKE '%began%'
                          OR minutes_note LIKE '%called to order%' OR title LIKE '%adjourn%' OR minutes_note LIKE '%adjourn%')
                     ORDER BY date DESC LIMIT 4"""):
        print("    - " + (t or "")[:200])
    an = os.path.join(os.path.dirname(os.path.abspath(args.db)), "analysis.json")
    if os.path.exists(an):
        a = json.load(open(an))
        lob, tim, com = a["lobbying"], a["timing"], a["committees"]
        print(f"  registrations: {lob['registrations']} found, {lob['parsed']} with a client name, {lob['clients_total']} clients")
        print(f"  stuck items: {tim['stuck_total']}; timing medians: " +
              ", ".join(f"{k} {v['median']}d (n={v['n']})" for k, v in tim["by_kind"].items() if v))
        print(f"  meeting lengths parsed: {com['length_parsed']}")
        if lob["registrations"] and lob["parsed"] < lob["registrations"] * 0.6:
            warnings.append("Fewer than 60% of lobbyist registrations had a client name in parentheses - check CLIENT_RX in build_analysis.py.")

    if warnings:
        print("\nWARNINGS:")
        for w in warnings:
            print("  - " + w)
        # Annotate the GitHub Actions run page too.
        for w in warnings:
            print(f"::warning::{w}")
    else:
        print("\nAll checks passed.")


if __name__ == "__main__":
    sys.exit(main())

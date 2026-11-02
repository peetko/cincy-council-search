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

    print("\n== Tag coverage ==")
    n = q("SELECT COUNT(*) FROM items")[0][0] or 1
    for col in ("topics", "departments", "sponsors"):
        k = q(f"SELECT COUNT(*) FROM items WHERE {col} <> ''")[0][0]
        print(f"  {col:<12} {k / n:.0%} of {n} items")

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

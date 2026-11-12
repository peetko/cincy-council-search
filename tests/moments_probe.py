"""Temporary: check video-moment matching against the real data (prints samples to the log)."""
import random, sqlite3, sys
sys.path.insert(0, "scripts")
import item_moments, transcripts
main = sqlite3.connect("site/data/council.db"); main.row_factory = sqlite3.Row
con = sqlite3.connect("site/data/transcripts.db"); con.row_factory = sqlite3.Row
stop = {w.lower() for w in transcripts.FUNCTION}
res, total, by = [], 0, {}
for m in con.execute("SELECT item, event_id, body, date FROM tmeetings WHERE status='ok' AND event_id IS NOT NULL"):
    items = [dict(r) for r in main.execute("SELECT item_id, agenda_number, COALESCE(NULLIF(title,''), matter_name) AS title, kind FROM items WHERE event_id=?", (m["event_id"],))]
    ps = [(r["id"], r["t"], r["text"]) for r in con.execute("SELECT id, t, text FROM passages WHERE item=? ORDER BY id", (m["item"],))]
    found = item_moments.find(ps, items, stop)
    total += len(items)
    text = {p[0]: p[2] for p in ps}
    for it in items:
        if it["item_id"] in found:
            pid, t, how = found[it["item_id"]]
            by[how] = by.get(how, 0) + 1
            res.append((m["body"], m["date"], it, t, how, text[pid]))
print(f"placed {len(res)} of {total}: {by}")
kinds = {}
for r in res: kinds[r[2]['kind']] = kinds.get(r[2]['kind'], 0) + 1
print("by kind:", kinds)
random.seed(3)
for how in ("number", "title"):
    print(f"\n======== samples by {how}")
    for body, date, it, t, h, txt in random.sample([r for r in res if r[4] == how], min(20, sum(1 for r in res if r[4] == how))):
        print(f"\n[{date} {body}] item {it['agenda_number']} at {t//60}:{t%60:02d}\n  TITLE: {(it['title'] or '')[:230]}\n  SAID:  {txt[:300]}")

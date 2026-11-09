"""Checks that run before anything is published. If one fails, the workflow stops and the
current site stays up.

    python tests/run_checks.py

What's checked (all with made-up data, no network):
  1. transcript name removal, against the cases in tests/redaction_cases.json;
  2. outcome labels: only final-passage actions are "Passed";
  3. the AI-summary number check: a wrong amount is caught;
  4. sponsor names: parsing, and one member's count never includes another's;
  5. "unanimous" means nobody voted no or abstained.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)

import collect      # noqa: E402
import common       # noqa: E402
import summarize    # noqa: E402
import transcripts  # noqa: E402

failures, passed = [], 0


def check(ok, label, detail=""):
    global passed
    if ok:
        passed += 1
    else:
        failures.append(f"{label}: {detail}" if detail else label)


def srt(lines):
    """Caption lines -> a caption file, eight seconds per line."""
    out = []
    for i, line in enumerate(lines):
        a, b = i * 8, i * 8 + 8
        out.append(f"{i + 1}\n00:{a // 60:02d}:{a % 60:02d},000 --> 00:{b // 60:02d}:{b % 60:02d},000\n{line}\n")
    return "\n".join(out)


# ---- 1. name removal
data = json.load(open(os.path.join(HERE, "redaction_cases.json"), encoding="utf-8"))
if len(transcripts.first_names()) < 1000:
    print("::warning::The first-name list (gender-guesser) isn't installed, so the name-removal checks were skipped.")
else:
    for case in data["cases"]:
        ps, _ = transcripts.process(srt(case["lines"]), data["officials"], [])
        text = " ".join((p["label"] + ": " if p["label"] else "") + p["text"] for p in ps)
        low = text.lower()
        for w in case.get("gone", []):
            check(w.lower() not in low, f"name removal / {case['name']}", f"“{w}” should be gone, but the result is: {text}")
        for w in case.get("kept", []):
            check(w.lower() in low, f"name removal / {case['name']}", f"“{w}” should still be there, but the result is: {text}")

# ---- 2. outcome labels (full Council, Legistar's passed flag set)
EXPECT = {"Passed": "passed", "Passed Emergency": "passed", "Adopted": "passed", "Filed": "filed", "Held": "held",
          "Referred to Budget and Finance Committee": "referred", "Indefinitely Postponed": "held",
          "Approved & Filed": "filed", "Emergency clause to remain": "procedural",
          "Suspension of the three readings": "procedural"}
for action, want in EXPECT.items():
    label, cat = collect.outcome_for(1, action, 1, "2025-01-01", "2026-01-01")
    check(cat == want, f"outcome / {action}", f"category is {cat}, expected {want}")
    if want != "passed":
        check(not label.startswith("Passed"), f"outcome / {action}", f"labeled “{label}”")
check(collect.outcome_for(0, "Passed", 1, "2025-01-01", "2026-01-01")[1] == "failed", "outcome / a failed final vote")

# ---- 3. summary number check
TEXT = ("AUTHORIZING the City Manager to accept and appropriate a grant of $250,000.00 from the Ohio Department of "
        "Transportation for 14 sidewalk repairs in Westwood by 2027, with a local match of 1, 500 dollars.")
FACT = [{"claim": "grant", "quote": "accept and appropriate a grant"}]
ok_summary = "The City Manager may accept a $250,000 grant for 14 sidewalk repairs in Westwood by 2027, with a $1,500 match."
check(summarize.local_checks(ok_summary, FACT, TEXT) == [], "summary check / a correct summary passes",
      str(summarize.local_checks(ok_summary, FACT, TEXT)))
for wrong in ("$50,000", "$25,000", "$2,500,000"):
    probs = summarize.local_checks(ok_summary.replace("$250,000", wrong), FACT, TEXT)
    check(any(wrong in p for p in probs), f"summary check / {wrong} is caught when the text says $250,000", str(probs))
probs = summarize.local_checks(ok_summary.replace("14 sidewalk", "4 sidewalk"), FACT, TEXT)
check(any("4" in p for p in probs), "summary check / 4 is caught when the text says 14", str(probs))

# ---- 4. sponsors
SPONSORS = {"MOTION, submitted by Councilmembers Owens and Walsh, WE MOVE that the administration": "Owens; Walsh",
            "MOTION, submitted by Vice Mayor Kearney, Councilmembers Parks, Harris and Jeffreys, WE MOVE": "Kearney; Parks; Harris; Jeffreys",
            "ORDINANCE (EMERGENCY) submitted by Sheryl M. M. Long, City Manager, on 9/24/2025, AUTHORIZING": "City Manager",
            "ORDINANCE, submitted by Mayor Aftab Pureval and Councilmember Johnson, from Emily Smart Woerner": "Mayor; Johnson",
            "MOTION, dated 5/1/2024, submitted by Councilmember Nolan, I MOVE that the City": "Nolan",
            "MOTION, submitted by Councilmember Walsh, To Whom It May Concern: WE MOVE": "Walsh"}
for title, want in SPONSORS.items():
    got = common.parse_sponsors(title)
    check(got == want, f"sponsors / {title[:50]}…", f"got “{got}”, expected “{want}”")

# ---- 4 and 5, through the real statistics script on a tiny database
tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "council.db")
con = common.connect(db)


def item(i, sponsors):
    con.execute("""INSERT INTO items(item_id, event_id, date, body, is_council, seq, matter_id, matter_file, kind, title,
                   action, passed, outcome, outcome_cat, is_final, sponsors, topics, first_seen)
                   VALUES(?,?,?,?,1,1,?,?,'motion','MOTION','Adopted',1,'Adopted','passed',1,?,'','x')""",
                (i, i, "2021-03-10", "Cincinnati City Council", i, f"2021{i:05d}", sponsors))
    con.execute("INSERT INTO meetings(event_id, body, is_council, date) VALUES(?,?,1,?)", (i, "Cincinnati City Council", "2021-03-10"))


for k in range(1, 6):
    item(k, "Sundermann")
item(6, "Mann; Betsy Sundermann")
VOTERS = ["David Mann", "Betsy Sundermann", "Greg Landsman"]
for i in range(1, 7):
    for p in VOTERS:
        value = "abstain" if (i == 1 and p == "Greg Landsman") else ("no" if (i == 2 and p == "David Mann") else "yes")
        con.execute("INSERT INTO votes VALUES(?,?,?,?)", (i, p, value.title(), value))
con.commit()
con.close()
run = subprocess.run([sys.executable, os.path.join(SCRIPTS, "build_stats.py"), "--db", db, "--today", "2021-12-01"],
                     capture_output=True, text=True)
check(run.returncode == 0, "statistics / build_stats.py runs", run.stderr[-400:])
if run.returncode == 0:
    stats = json.load(open(os.path.join(tmp, "stats.json"), encoding="utf-8"))
    got = {n: p["sponsored"]["total"] for n, p in stats["profiles"].items()}
    check(got.get("David Mann") == 1, "statistics / Mann's sponsored count leaves out Sundermann's items", str(got))
    check(got.get("Betsy Sundermann") == 6, "statistics / Sundermann's sponsored count", str(got))
    unan = next(k for k in stats["kpis"] if k["label"] == "Unanimous final votes")
    check(unan["value"] == "67%", "statistics / unanimous = nobody voted no or abstained (4 of 6 votes)", unan["value"])

if failures:
    print(f"{len(failures)} check(s) FAILED ({passed} passed). Nothing is published until these pass:")
    for f in failures:
        print(f"  - {f}")
        print(f"::error::{f[:300]}")
    sys.exit(1)
print(f"All {passed} checks passed.")

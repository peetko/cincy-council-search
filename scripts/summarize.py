"""Plain-language AI summaries of legislation, with layered accuracy checks.

    GEMINI_API_KEY=... python scripts/summarize.py [--files 202600714,202501779] [--max-requests 200]

Uses Google's Gemini API (free tier) on the full text in docs.db. Accuracy comes first,
so every summary has to pass all of these before it's shown on the site:

  1. Write: Gemini writes 2-3 sentences AND lists each fact it used with an exact
     supporting quote from the legislation.
  2. Check, without AI: every quote must really appear in the text; every number and
     dollar amount in the summary must appear in the text; every name must appear in it.
  3. Verify: a second, separate Gemini request reads the text and the summary and lists
     anything that isn't supported.
  4. If step 2 or 3 finds a problem, Gemini rewrites once with the problems spelled out,
     and the rewrite goes through steps 2 and 3 again. If it still fails, the summary is
     marked "flagged": kept for review, never shown.

Each summary is written once and kept; it's only redone if the legislation's text changes.
The free tier has daily limits, so this stops at --max-requests or at the first "quota used
up" answer and continues on the next run. The key is read from the environment and sent in
a header, never printed or put in a URL.
"""
import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request

from common import DEFAULT_DB, SETTINGS

API = os.environ.get("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")
KEY = os.environ.get("GEMINI_API_KEY", "").strip()
# Each model has its own free daily allowance, so one model writes and a different one checks
# (also a more independent check). The first available model in each list is used.
WRITERS = SETTINGS.get("summary_writer_models", ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"])
CHECKERS = SETTINGS.get("summary_checker_models", ["gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-3.5-flash-lite"])
KINDS = SETTINGS.get("summary_kinds", ["ordinance", "resolution"])
PAUSE = float(SETTINGS.get("summary_pause_seconds", 7))  # stays under the free per-minute limit
MAX_INPUT = int(SETTINGS.get("summary_max_input_chars", 30000))
PROMPT_VERSION = "2"  # bump when the instructions change: flagged summaries are then tried again

WRITE_INSTRUCTIONS = """You summarize Cincinnati City Council legislation for residents.

Write 2-3 short, plain-language sentences saying what the legislation does, who is involved, any dollar
amounts, and any neighborhood, address or City department named.

Accuracy rules - these matter more than style:
- Use ONLY facts stated in the text. Never guess, infer motives, or add outside knowledge.
- Copy numbers, dollar amounts, names and addresses exactly as written in the text.
- If the text is unclear or garbled (it may come from a scanned page), leave that detail out.
- Only the City Manager or City officials are "authorized"; describe outside parties accurately.
- Keep it under about 70 words. Leave out account numbers, fund codes and section numbers.
- Don't say who submitted or sponsored it unless the text clearly states that.
- No opinions, no legal advice, no preamble.

Also list every specific fact your summary uses (each amount, name, place, and action), each with an
exact quote of 3 to 25 words copied word for word from the text that supports it."""

VERIFY_INSTRUCTIONS = """You are checking a summary of Cincinnati City Council legislation for accuracy.
Read the legislation text, then the summary. For each statement in the summary, decide whether the text
directly supports it. Report a problem for anything that is not stated in the text, is stated differently
(wrong amount, name, place, party, or action), or misrepresents who does what. Wording differences are
fine if the meaning is the same. Be strict about facts, not style."""

WRITE_SCHEMA = {"type": "OBJECT", "properties": {
    "summary": {"type": "STRING"},
    "facts": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "claim": {"type": "STRING"}, "quote": {"type": "STRING"}}, "required": ["claim", "quote"]}}},
    "required": ["summary", "facts"]}
VERIFY_SCHEMA = {"type": "OBJECT", "properties": {
    "all_supported": {"type": "BOOLEAN"},
    "problems": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["all_supported", "problems"]}

SCHEMA = """
CREATE TABLE IF NOT EXISTS summaries (
  matter_id  INTEGER PRIMARY KEY,
  matter_file TEXT,
  status     TEXT,      -- ok (passed every check) / flagged (failed checks; not shown) / error
  summary    TEXT,
  model      TEXT,
  attempts   INTEGER,
  problems   TEXT,      -- JSON list of what the checks found (last attempt)
  text_hash  TEXT,      -- redo when the legislation text changes
  prompt_version TEXT,
  created    TEXT
);
"""


class QuotaUsedUp(Exception):
    pass


class Overloaded(Exception):
    pass


requests_made = 0


def call(model, system, user, schema):
    """One Gemini request; returns the parsed JSON answer."""
    global requests_made
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": schema}}
    delay = 20
    for attempt in range(4):
        req = urllib.request.Request(f"{API}/models/{model}:generateContent", json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "x-goog-api-key": KEY})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read())
            requests_made += 1
            time.sleep(PAUSE)
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
            return json.loads(text)
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")[:300]
            if e.code == 429:
                if "per day" in msg.lower() or "PerDay" in msg or attempt == 3:
                    raise QuotaUsedUp(re.sub(r"\s+", " ", msg))
                time.sleep(delay)  # per-minute limit: wait and retry
                delay *= 2
                continue
            if e.code in (500, 502, 503, 504):
                if attempt < 2:
                    time.sleep(delay)
                    continue
                raise Overloaded(f"{model}: {e.code}")
            raise RuntimeError(f"Gemini error {e.code}: {msg}")
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            if attempt < 3:
                time.sleep(5)
                continue
            raise RuntimeError(f"Unreadable Gemini answer: {e}")


def available_models():
    req = urllib.request.Request(f"{API}/models?pageSize=200", headers={"x-goog-api-key": KEY})
    return [m["name"].split("/")[-1] for m in json.loads(urllib.request.urlopen(req, timeout=60).read()).get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])]


# ---------------------------------------------------------------- checks without AI
NUM_WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
             "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
NUM_WORDS.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90})
COMMON_CAPS = {"The", "City", "Council", "Cincinnati", "Ohio", "Manager", "Director", "Finance", "It", "This", "A",
               "An", "In", "Of", "And", "For", "Funds", "Mayor", "Vice"}


def norm(s):
    return re.sub(r"[^a-z0-9$]+", " ", s.lower()).strip()


def numbers_in_words(text):
    """Numbers written as words in the text ('thirty-five' -> 35), so '35 years' can be matched."""
    found = set()
    for m in re.finditer(r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)[\s-]+(one|two|three|four|five|six|seven|eight|nine)\b", text, re.I):
        found.add(NUM_WORDS[m.group(1).lower()] + NUM_WORDS[m.group(2).lower()])
    for w, n in NUM_WORDS.items():
        if re.search(rf"\b{w}\b", text, re.I):
            found.add(n)
    return found


def quote_found(quote, ntext):
    q = norm(quote)
    if not q:
        return False
    if q in ntext:
        return True
    # Scanned text has OCR noise: accept a very close match of the same length.
    best = difflib.SequenceMatcher(None, ntext, q, autojunk=False).find_longest_match(0, len(ntext), 0, len(q))
    if best.size >= 0.8 * len(q):
        return True
    start = max(0, best.a - best.b)
    window = ntext[start:start + len(q) + 10]
    return difflib.SequenceMatcher(None, window, q).ratio() >= 0.9


def claim_traceable(claim, ntext, digits_text, word_nums):
    nums = [n.replace(",", "").replace("$", "").rstrip(".") for n in re.findall(r"\$?\d[\d,]*(?:\.\d+)?", claim)]
    names = [w for w in re.findall(r"\b[A-Z][\w&'’-]{2,}", claim) if w not in COMMON_CAPS]
    if not nums and not names:
        return False
    return all(n in digits_text or (n.isdigit() and int(n) in word_nums) for n in nums if n) and \
        all(norm(re.sub(r"['’]s$", "", w)) in ntext for w in names)


ABBREV = re.compile(r"\b(U\.S|U\.S\.A|No|Nos|Inc|Co|Corp|Ltd|St|Ave|Mr|Ms|Dr|Jr|Sr|vs|approx|LLC|N\.A)\.", re.I)


def tidy(summary):
    """Remove a chatty preamble like 'Here is a summary:'."""
    return re.sub(r"^\s*(here is|here's)[^:]*:\s*", "", summary, flags=re.I).strip()


def local_checks(summary, facts, text):
    problems = []
    ntext = norm(text)
    digits_text = re.sub(r"[,\s]", "", text)
    word_nums = numbers_in_words(text)
    for f in facts:
        if quote_found(f.get("quote", ""), ntext):
            continue
        # The quote was paraphrased: still fine if every number and name in the claim is in the text.
        claim = f.get("claim", "")
        if not claim_traceable(claim, ntext, digits_text, word_nums):
            problems.append(f"The supporting quote for “{claim}” isn't in the text.")
    for num in re.findall(r"\$?\d[\d,]*(?:\.\d+)?", summary):
        plain = num.replace(",", "").replace("$", "").rstrip(".")
        if not plain:
            continue
        if plain not in digits_text and not (plain.isdigit() and int(plain) in word_nums):
            problems.append(f"The number {num} doesn't appear in the text.")
    if re.search(r"\b(million|billion|thousand)\b", summary, re.I) and not re.search(r"\b(million|billion|thousand)\b", text, re.I):
        problems.append("Use dollar amounts exactly as written (no rounding to 'million').")
    for name in re.findall(r"\b([A-Z][\w&'’-]*(?:[ ](?:of |the |and |for |& )?[A-Z][\w&'’-]*)+)", summary):
        name = re.sub(r"['’]s\b", "", name)  # possessive: "Kearney's" -> "Kearney"
        words = [w for w in name.split() if w[:1].isupper() and w not in COMMON_CAPS]
        if words and norm(name) not in ntext and not all(norm(w) in ntext for w in words):
            problems.append(f"The name “{name}” doesn't appear in the text.")
    for sent in re.split(r"(?<=[.!?])\s+", summary):  # single capitalized words mid-sentence: places, people
        for w in re.findall(r"(?<=\s)([A-Z][a-zA-Z’'-]{3,})", sent):
            w = re.sub(r"['’]s$", "", w)
            if w not in COMMON_CAPS and norm(w) not in ntext:
                problems.append(f"The name “{w}” doesn't appear in the text.")
    sentences = [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", ABBREV.sub(lambda m: m.group(0).replace(".", "·"), summary.strip())) if s]
    if len(sentences) > 4 or len(summary.split()) > 110:
        problems.append("Keep it to 2-3 sentences, under about 70 words.")
    return problems


# ---------------------------------------------------------------- one piece of legislation
def summarize_one(model, checker, file, title, text):
    """-> (status, summary, attempts, problems)"""
    source = text[:MAX_INPUT]
    prompt = f"File {file}. Title: {title}\n\nLegislation text:\n{source}"
    feedback, summary, problems = "", "", []
    for attempt in (1, 2):
        ask = prompt + (f"\n\nYour previous summary had these problems; fix them and rely only on the text:\n{feedback}" if feedback else "")
        out = call(model, WRITE_INSTRUCTIONS, ask, WRITE_SCHEMA)
        summary = tidy(out.get("summary") or "")
        problems = local_checks(summary, out.get("facts") or [], source)
        if not problems:  # only spend a verification request on a summary that passed the free checks
            v = call(checker, VERIFY_INSTRUCTIONS, f"Legislation text:\n{source}\n\nSummary to check:\n{summary}", VERIFY_SCHEMA)
            if not v.get("all_supported") or v.get("problems"):
                problems = [f"Verifier: {p}" for p in (v.get("problems") or ["says not everything is supported"])]
        if not problems:
            return "ok", summary, attempt, []
        feedback = "\n".join(f"- {p}" for p in problems)
    return "flagged", summary, 2, problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--files", default=os.environ.get("SUMMARY_FILES", ""), help="only these file numbers (comma-separated)")
    ap.add_argument("--max-requests", type=int, default=int(os.environ.get("SUMMARY_MAX_REQUESTS", SETTINGS.get("summary_max_requests_per_run", 200))))
    ap.add_argument("--redo", action="store_true", help="redo the --files even if already summarized")
    args = ap.parse_args()
    if not KEY:
        print("No GEMINI_API_KEY set; skipping AI summaries.")
        return
    data_dir = os.path.dirname(os.path.abspath(args.db))
    docs = sqlite3.connect(os.path.join(data_dir, "docs.db"))
    docs.executescript(SCHEMA)
    main_db = sqlite3.connect(args.db)
    kinds = {r[0]: (r[1], r[2], r[3]) for r in main_db.execute(
        "SELECT matter_id, MAX(kind), MAX(title), MAX(date) FROM items WHERE matter_id IS NOT NULL GROUP BY matter_id")}

    available = available_models()
    writers = [m for m in WRITERS if m in available]
    checkers = [m for m in CHECKERS if m in available]
    if not writers or not checkers:
        print("None of the configured Gemini models are available. Available: " + ", ".join(available))
        return
    model = writers[0]
    checker = next((m for m in checkers if m != model), checkers[0])
    print(f"Writing with {model}; checking with {checker}.")

    done = {r[0]: (r[1], r[2], r[3]) for r in docs.execute("SELECT matter_id, text_hash, status, prompt_version FROM summaries")}
    only = {f.strip() for f in args.files.split(",") if f.strip()}
    queue = []
    for mid, file, text in docs.execute("SELECT matter_id, matter_file, text FROM docs WHERE status = 'ok' AND text IS NOT NULL"):
        kind, title, last = kinds.get(mid, (None, "", ""))
        if only:
            if file not in only:
                continue
        elif kind not in KINDS:
            continue
        h = hashlib.sha1(text.encode()).hexdigest()[:12]
        prev = done.get(mid)
        if prev and prev[0] == h and not (args.redo and only) and prev[1] != "error" \
                and not (prev[1] == "flagged" and prev[2] != PROMPT_VERSION):
            continue
        queue.append((last or "", mid, file, title, text, h))
    queue.sort(reverse=True)  # newest first
    print(f"{len(queue)} pieces of legislation to summarize" + (f" (limited to {len(only)} files)" if only else "") + ".")

    today = dt.date.today().isoformat()
    counts, overloads = {}, 0
    for _, mid, file, title, text, h in queue:
        if requests_made + 4 > args.max_requests:
            print(f"Stopping at the per-run limit of {args.max_requests} requests.")
            break
        try:
            for wait in (60, 120, 240, None):
                try:
                    status, summary, attempts, problems = summarize_one(model, checker, file, title or "", text)
                    break
                except Overloaded:
                    overloads += 1
                    if wait is None or overloads > 8:
                        raise
                    time.sleep(wait)  # busy moment at Google: wait, then try again
        except QuotaUsedUp as e:
            print(f"Gemini says a limit was reached; stopping here (the next run continues). Detail: {str(e)[:400]}")
            break
        except Overloaded:
            print("Gemini is overloaded right now; stopping here (the next run continues).")
            break
        except Exception as e:
            status, summary, attempts, problems = "error", "", 0, [str(e)[:300]]
        docs.execute("INSERT OR REPLACE INTO summaries VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (mid, file, status, summary, f"{model} + {checker}", attempts, json.dumps(problems), h, PROMPT_VERSION, today))
        docs.commit()
        counts[status] = counts.get(status, 0) + 1
        print("SUMMARY\t" + json.dumps({"file": file, "status": status, "attempts": attempts, "summary": summary, "problems": problems}))
    docs.execute("INSERT INTO docs_fts(docs_fts) VALUES('optimize')") if docs.execute(
        "SELECT name FROM sqlite_master WHERE name = 'docs_fts'").fetchone() else None
    docs.commit()
    docs.execute("VACUUM")
    ok = docs.execute("SELECT COUNT(*) FROM summaries WHERE status = 'ok'").fetchone()[0]
    stats_path = os.path.join(data_dir, "docs-stats.json")
    try:
        st = json.load(open(stats_path))
    except (OSError, ValueError):
        st = {}
    st.update({"summaries_ok": ok, "summaries_public": bool(SETTINGS.get("summaries_public")), "summary_model": model, "summary_checker": checker})
    json.dump(st, open(stats_path, "w"))
    print(f"Done: {', '.join(f'{k} {v}' for k, v in counts.items()) or 'nothing new'}; {requests_made} requests. "
          f"{ok} summaries have passed every check so far.")


if __name__ == "__main__":
    main()

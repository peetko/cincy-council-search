"""Find the moment in a meeting's video where each agenda item comes up.

Used by collect_transcripts.py. For one meeting it takes the transcript passages (in order) and
the meeting's agenda items from Legistar, and returns, for each item it can place with
confidence, the passage where it starts. Two ways:

1. The chair says the item's number: "next up is item number 3, a presentation…". Committee
   agendas are numbered, and the chair usually announces each one. A number said as an aside
   ("it should get rolled into item 4") is less trusted than one introducing the item.
2. Otherwise, the item's distinctive title words are said: "a job creation tax credit for Amend
   Consulting … 310 Plum Street". Used mostly for Council meetings, where items are rarely
   announced by number. A match needs at least three distinctive words, a good share of them,
   and close to the best score in the meeting; the earliest such passage is taken, since an item
   is usually introduced before it's discussed.

Items that can't be placed get no link: a missing link is better than a wrong one.
"""
import math
import re

NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}
ITEM_RX = re.compile(r"\bitems? (?:number |no\.? |#)?(\d{1,2}|" + "|".join(NUMBER_WORDS) + r")\b", re.I)
# Words just before or after "item number N" that mean the item is being introduced now.
INTRO_BEFORE = re.compile(r"(?:next|up|first|start(?:ing)? with|move (?:on )?to|go (?:on )?to|turn to|on to|take up|consider|"
                          r"we have|now|begin with|bring up|call)\W+(?:\w+\W+){0,3}$", re.I)
INTRO_AFTER = re.compile(r"^\W*(?:is\b|will be|a |an |the |which is|it's|on the agenda|we're going to|we will|let's|"
                         r"to (?:consider|discuss)|first|up next)", re.I)
# "…put item number 2 on for passage": the end of an item, not its start.
CLOSING = re.compile(r"^\W*(?:on for passage|for passage|on file|to file|as well|without objection|be filed|held|is held|"
                     r"will be held|for a vote|on hold)", re.I)

# Words in nearly every title, which say nothing about which item this is.
LEGAL = set("""ordinance ordinances resolution resolutions motion motions authorizing authorize authorizes manager city council
cincinnati establishing establish amending amend amended amendment chapter chapters section sections municipal code providing
approving approve approval execute executing agreement agreements department departments fund funds appropriating appropriate
appropriation transfer transferring sum sums total amount purpose purposes various related relating therein thereof hereby
administration administrative submitted councilmember councilmembers mayor vice president tem pro emergency effective
office director accept accepting acceptance grant grants received receive dollars state ohio county hamilton federal year
years fiscal general capital improvement program programs project projects services service report reports reporting
communication presentation including include includes within upon through regarding concerning support supporting
expressing request requesting requests administration's legislation honorary naming recognize recognizing""".split())


def stem(w):
    """Rough word stem, so "repainting" matches "repaint" and "railings" matches "railing"."""
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def tokens(text):
    return [w if w.isdigit() else stem(w.strip("'-")) for w in re.findall(r"[a-z][a-z'\-]{3,}|\d{3,}", (text or "").lower())]


LEGAL_STEMS = {stem(w) for w in LEGAL}


def distinctive(title, stop):
    """The words of a title that could identify it when said out loud."""
    seen = []
    stop = {stem(x) for x in stop} | set(stop)
    for w in tokens(title):
        w = w.strip("'-")
        if w in LEGAL or stem(w) in LEGAL_STEMS or w in stop or w in seen or len(w) < 4 and not w.isdigit():
            continue
        if w.isdigit() and (len(w) == 4 and w.startswith("20") or len(w) >= 6):
            continue  # years and file numbers aren't said in a way that matches
        seen.append(w)
    return seen


def number_of(agenda_number):
    m = re.match(r"\s*(\d{1,2})\b", agenda_number or "")
    return int(m.group(1)) if m else None


def find(passages, items, stop=()):
    """passages: [(passage_id, t, text)] in order; items: [dict(item_id, agenda_number, title)].
    Returns {item_id: (passage_id, seconds, how)}. The time is estimated within the passage, from
    where in its text the item comes up (a passage is about 20 seconds of speech)."""
    nxt = {p[0]: (passages[k + 1][1] if k + 1 < len(passages) else p[1] + 20) for k, p in enumerate(passages)}

    def at(pid, t, text, pos):
        return int(t + (nxt[pid] - t) * pos / max(1, len(text)))
    out = {}
    by_number = {}
    for it in items:
        n = number_of(it.get("agenda_number"))
        if n is not None:
            by_number.setdefault(n, []).append(it)
    # 1. "Item number N"
    first, intro = {}, {}
    for pid, t, text in passages:
        for m in ITEM_RX.finditer(text):
            raw = m.group(1).lower()
            n = int(raw) if raw.isdigit() else NUMBER_WORDS[raw]
            if n not in by_number or len(by_number[n]) != 1:
                continue
            after = text[m.end():m.end() + 24]
            first.setdefault(n, (pid, t))
            if n not in intro and not CLOSING.search(after) and (
                    INTRO_BEFORE.search(text[max(0, m.start() - 40):m.start()]) or INTRO_AFTER.search(after)):
                intro[n] = (pid, at(pid, t, text, m.start()))
    # Trust spoken numbers only if the chair is clearly numbering this agenda: several different
    # item numbers mentioned. Only an introduction counts; a closing mention marks the end.
    if len(first) >= 2:
        for n, (pid, t) in intro.items():
            out[by_number[n][0]["item_id"]] = (pid, t, "number")
    # 2. Title words, weighted by how rare each is in this meeting (a word said in many passages,
    #    like "housing" at the housing committee, says little about where an item comes up).
    words = [(pid, t, set(tokens(text)), text) for pid, t, text in passages]
    n_pass = max(1, len(words))
    df = {}
    for _, _, ws, _ in words:
        for w in ws:
            df[w] = df.get(w, 0) + 1
    for it in items:
        if it["item_id"] in out:
            continue
        want = [w for w in distinctive(it.get("title"), stop) if 0 < df.get(w, 0) <= max(2, 0.15 * n_pass)]
        if len(want) < 2:
            continue
        weight = {w: math.log(n_pass / df[w]) for w in want}
        scored = []
        for pid, t, ws, text in words:
            got = [w for w in want if w in ws]
            enough = len(got) >= 3 or (len(got) == 2 and any(w.isdigit() for w in got))
            if enough and len(got) >= 0.35 * len(want):
                low = text.lower()
                pos = min((low.find(w) for w in got if low.find(w) >= 0), default=0)
                scored.append((sum(weight[w] for w in got), pid, at(pid, t, text, max(0, pos - 60))))
        if not scored:
            continue
        best = max(sc for sc, _, _ in scored)
        sc, pid, t = next(x for x in scored if x[0] >= 0.65 * best)   # the earliest strong match: the introduction
        out[it["item_id"]] = (pid, t, "title")
    return out

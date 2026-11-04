"""Clean up text read from scanned documents with OCR (text recognition).

OCR reads every mark on a page as if it were text. Charts, signatures, stamps, shading and
sideways or upside-down lines (a web address printed along the margin) come out as nonsense:
"Smmmmmmmmemn 63,7", "/8beu-uxxg/Aouejoadx3", "SouslayJlp". This removes it, line by line:

  - each word is checked against a vocabulary of real words: every word that appears in at least
    two pieces of Cincinnati legislation whose text didn't need OCR (plus everyday words);
  - a line that is mostly real words and numbers is kept; obvious junk words inside it
    ("Mmmm", "SEREEEemns") are dropped;
  - in table-like lines (several numbers), stray marks that aren't real words are dropped too;
  - a line that is mostly junk is dropped.

Unknown words that look like ordinary words (a street or a surname) are kept.
"""
import os
import re

STRIP = ".,;:()[]{}\"'“”‘’!?*|_~`<>=+«»•·"
NUM_RX = re.compile(r"^[$€#]?\(?\d[\d.,%:/\-()]*[a-zA-Z]{0,2}\)?$")   # 62.9, 63,7, 2026, 1-5, 10th, 3rd
VOWEL_RX = re.compile(r"[aeiouy]", re.I)


def load_vocab(texts, extra=()):
    """Words (lower case) seen in at least two of the given texts, plus extra words."""
    seen = {}
    for t in texts:
        for w in set(re.findall(r"[a-z]{2,}", (t or "").lower())):
            seen[w] = seen.get(w, 0) + 1
    vocab = {w for w, n in seen.items() if n >= 2}
    vocab |= {w.lower() for w in extra}
    path = os.path.join(os.path.dirname(__file__), "..", "config", "common_words.txt")
    try:
        with open(path, encoding="utf-8") as f:
            vocab |= {l.strip().lower() for l in f if l.strip() and not l.startswith("#")}
    except OSError:
        pass
    return vocab


def is_junk(core):
    low = core.lower()
    letters = re.sub(r"[^a-z]", "", low)
    if re.search(r"([a-z])\1\1", low):                                   # mmm, eee
        return True
    if re.search(r"[a-z][A-Z]", core[1:]) and not re.match(r"^(?:Mc|Mac|O'|D'|La|De)[A-Z]", core):
        return True                                                       # SouslayJlp, SEs, ‘sajAysa
    if re.search(r"[bcdfghjklmnpqrstvwxz]{5,}", low):                     # consonant pile-ups
        return True
    inner = re.sub(r"[A-Za-z0-9'’\-./]", "", core)
    if len(inner) >= 1 and len(core) >= 3:                                # }s/A0B, 7(,9 mixed with symbols
        return True
    if re.search(r"[a-z]\d|\d[a-z]{3,}", low) and not NUM_RX.match(core):  # Aouejoadx3, 1lou
        return True
    if len(letters) >= 4 and not VOWEL_RX.search(letters):
        return True
    return False


def kind(tok, vocab):
    core = tok.strip(STRIP)
    if not core or not re.search(r"[A-Za-z0-9]", core):
        return "punct", core
    if NUM_RX.match(core):
        return "num", core
    parts = [p for p in re.split(r"[/\-–—]", core) if p]
    def known(p):
        p = p.lower().rstrip(".")
        p = re.sub(r"['’]s$", "", p)
        return p in vocab or (len(p) == 1 and p in "aiu") or bool(re.fullmatch(r"\d+", p))
    if parts and all(known(p) for p in parts):
        return "word", core
    if is_junk(core):
        return "junk", core
    return "unknown", core


def clean_line(line, vocab):
    toks = line.split()
    if not toks:
        return "", 0, 0
    kinds = [kind(t, vocab) for t in toks]
    counted = [k for k, _ in kinds if k != "punct"]
    if not counted:
        return "", 0, len(toks)
    good = sum(1 for k in counted if k in ("word", "num"))
    words = sum(1 for k in counted if k == "word")
    junk = sum(1 for k in counted if k == "junk")
    nums = sum(1 for k in counted if k == "num")
    score = good / len(counted)
    long_word = any(k == "word" and len(c) >= 3 for k, c in kinds)
    if score < 0.5 or (junk >= 2 and score < 0.7) or (len(counted) <= 6 and not long_word and nums < 2) or (words == 0 and nums < 3):
        return "", 0, len(toks)
    table = nums >= 3
    keep = []
    for t, (k, c) in zip(toks, kinds):
        if k == "junk":
            continue
        if table and k == "unknown" and (not c.isupper() or len(c) <= 2):
            continue                                                       # "Samm", "Sms" next to numbers
        if k == "punct" and len(t) >= 2 and not re.fullmatch(r"[-–—.…]+", t):
            continue                                                       # "=", "§1" style debris
        keep.append(t)
    return " ".join(keep), len(keep), len(toks)


def clean_ocr(text, vocab):
    """Returns (cleaned text, share of words kept)."""
    out, kept, total = [], 0, 0
    for para in re.split(r"\n\s*\n", text or ""):
        lines = []
        for line in para.split("\n"):
            c, k, n = clean_line(line, vocab)
            kept += k
            total += n
            if c:
                lines.append(c)
        if lines:
            out.append("\n".join(lines))
    cleaned = "\n\n".join(out)
    cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
    return cleaned, (kept / total if total else 1.0)

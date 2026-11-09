"""Turn a meeting's closed-caption file (SRT) into searchable, readable transcript segments.

Used by collect_transcripts.py. Standard library only, plus an optional list of first names
(the gender-guesser package's name dictionary, installed by the workflow) that helps find
speakers' names in ALL-CAPS captions.

Steps, for one meeting:
  1. parse the caption cues (start time, end time, text);
  2. split them into speaker turns at ">>" (the captioner's "new speaker" mark); Council members
     and some staff are labeled ("Councilmember Owens:"), everyone else isn't;
  3. find the public comment parts of the meeting (the chair calls speakers up for two minutes);
  4. remove the names of members of the public: what they say is kept, who they are isn't.
     Best effort: captions are all capitals and often misspell names, so a name can slip through;
  5. change ALL CAPS to ordinary sentence case, with known names and places capitalized;
  6. cut each turn into short passages (about a paragraph) with the time they start.
"""
import os
import re

NAME_MARK = "[name]"
NUMBER_MARK, PHONE_MARK, EMAIL_MARK = "[number]", "[phone]", "[email]"
MARKS = (NAME_MARK, NUMBER_MARK, PHONE_MARK, EMAIL_MARK)

# ------------------------------------------------------------------ word lists

# Words that end a name ("MY NAME IS JANE DOE AND I LIVE..." stops at AND).
FUNCTION = set("""
A AN THE AND OR BUT NOR SO YET FOR OF IN ON AT TO FROM BY WITH WITHOUT ABOUT AS INTO ONTO OVER UNDER
UP DOWN OUT OFF THAN THEN THERE HERE THIS THAT THESE THOSE IT ITS IT'S I I'M I'VE I'LL I'D ME MY MINE
WE WE'RE WE'VE WE'LL US OUR YOU YOU'RE YOU'VE YOU'LL YOUR HE HE'S HIM HIS SHE SHE'S HER THEY THEY'RE
THEM THEIR WHO WHOM WHOSE WHAT WHICH WHEN WHERE WHY HOW IS ARE WAS WERE BE BEEN BEING AM DO DOES DID
DONE HAVE HAS HAD HAVING CAN COULD SHALL SHOULD WOULD MUST MIGHT WILL WON'T CAN'T DON'T DIDN'T ISN'T
NOT NO YES YEAH OKAY OK ALL ANY SOME EACH EVERY JUST ALSO VERY REALLY STILL AGAIN NOW TODAY TONIGHT
TOMORROW YESTERDAY MORNING AFTERNOON EVENING GOOD THANK THANKS PLEASE SORRY WELCOME HELLO HI HEY
FOLLOWED FOLLOWING NEXT FIRST LAST FINAL SPEAKER SPEAKERS SPEAK SPEAKING ONLINE LINE PERSON
PRESIDENT VICE CHAIR CHAIRMAN CHAIRWOMAN MEMBER MEMBERS COUNCIL COUNCILMEMBER COUNCILMAN COUNCILWOMAN
COMMITTEE MAYOR DIRECTOR EXECUTIVE RESIDENT RESIDENTS NEIGHBOR LIVE LIVED LIVING WORK WORKED WORKING
REPRESENT REPRESENTING BEHALF OWNER FOUNDER CEO BOARD PASTOR REVEREND HERE'S THAT'S THERE'S WHAT'S
GO GOING GOT GET GETS GIVE GIVEN COME COMING CAME SAY SAID SAYS ASK ASKED ASKING WANT WANTED WANTS LIKE
KNOW THINK SEE SEEN LOOK HEAR HEARD CALL CALLED CALLING TAKE TOOK MAKE MADE NEED NEEDS TELL TOLD LET
MINUTES MINUTE SECONDS TIME FLOOR YOURS PRIVILEGE ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE TEN
AGENDA ITEM PUBLIC COMMENT COMMENTS TESTIMONY CITY CINCINNATI CHAMBERS CHAMBER
MR MS MRS MISS DR SIR MA'AM FOLKS EVERYONE EVERYBODY ANYONE SOMEONE NOBODY
""".split())

# First names that are also everyday words: never treated as names on their own, never capitalized.
AMBIGUOUS = set("""
WILL MAY GRANT HOPE SUMMER STORM MAJOR LANE MARK BILL MILES CHANCE AMI RED JUSTICE SPRING RIVER RICH
AMBER FOREST BRAIN QUEEN JOY FAITH KING ROCK VAN GUY JUDGE SKIP LIBERTY SKY RUSH EVE CHASE RAY PAT
HARMONY PRECIOUS SUNSHINE CADENCE PATIENCE DEE SUE DON DREW ROSE STONE CRYSTAL WARD MACK TY ART AL ED
DAWN IVY LILY PEARL BUD BUCK IRIS MAX SAGE HAZEL GOLDIE HONEY BELLE CAROL AUGUST APRIL JUNE AMERICA
PARIS HUNTER CHRISTIAN GENERAL ROBIN MASON CHRISTIAN SUNNY BUNNY DUSTY RUSTY CLAY HEATH GLEN DALE COLE
BUSTER LOVE BLESSING DESTINY TRINITY SHAWN NORMAN BOND LONG JAMES WALKER PARKER TURNER COOK BAKER
CARTER HARRIS DAVIS MARTIN MORGAN FOSTER BAILEY JORDAN LEE CHURCH YOUNG
""".split())
# (Some of the last words above are common surnames that are also first names; they only count
#  as names when a stronger sign is present, e.g. "MY NAME IS ...".)

# "My name is ..., with the Department of ..." - City staff introducing themselves at a presentation.
STAFF_CONTEXT = re.compile(r"\b(?:CITY OF CINCINNATI|WITH THE CITY|CITY'S|DEPARTMENT|DIVISION|OFFICE OF|CITY MANAGER|SOLICITOR|"
                           r"ADMINISTRATION|CPD|CFD|POLICE|FIRE CHIEF|MSD|SMU|DOTE|DCED|GCWW|WATER WORKS|DORS|RECREATION|"
                           r"PLANNING|BUDGET|FINANCE DIRECTOR|ASSISTANT CITY)\b")

PROPER_WORDS = """Cincinnati Ohio Kentucky Indiana Columbus Cleveland Dayton Covington Newport America American
Americans Christmas Thanksgiving Juneteenth Easter Halloween Bengals Reds Kroger Procter Uptown Riverfront Smale Findlay
Medicaid Medicare Covid Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February April June July
August September October November December English Spanish African Latino Latina Hispanic Jewish Muslim God Jesus
Congress Statehouse Paycor Cincy Clifton Northside Avondale Evanston Westwood Pendleton Madisonville Oakley Corryville
Roselawn Linwood Hartwell Carthage Sedamsville Queensgate Millvale Riverside Mt. Mt Hamilton Norwood Ohioans
Cincinnatians Councilmember Councilmembers""".split()
PROPER_PHRASES = """Hamilton County|Duke Energy|Fifth Third|Music Hall|Fountain Square|Washington Park|Union Terminal|
Freedom Center|Ohio River|Mill Creek|City Hall|Brent Spence|Western Hills Viaduct|Wasson Way|Liberty Street|Reading Road|
Vine Street|Main Street|Central Parkway|Martin Luther King|Black Lives Matter|United States|Supreme Court|White House|
General Assembly|Port Authority|Metro|Talbert House|Sierra Club|Park Board|Board of Health|Cincinnati Public Schools|
Cincinnati Police Department|Cincinnati Fire Department|City Council|Vice Mayor|President Pro Tem|Over-the-Rhine Foundation|
Banks|Smale Riverfront Park|Procter & Gamble|Kroger|University of Cincinnati|Xavier|Mount Airy|Mount Auburn|Mount Washington|
Mount Lookout|Hyde Park|Bond Hill|College Hill|Walnut Hills|East Walnut Hills|Price Hill|East Price Hill|West Price Hill|
Lower Price Hill|West End|East End|Camp Washington|Columbia Tusculum|Kennedy Heights|Pleasant Ridge|Winton Hills|
North Fairmount|South Fairmount|English Woods|Sayler Park|Spring Grove|Paddock Hills|North Avondale|South Cumminsville|
Mount Adams|Lower Price Hill|Evanston|Downtown|CUF|Fay Apartments""".replace("\n", "").split("|")
ACRONYMS = set("""CPD CFD MSD SMU DORS CMHA 3CDC OTR CPS SORTA ADA HUD ARPA TIF CRA CBA CEO CFO COO FEMA ODOT EPA OKI UC
CVG DOTE DCED NAACP LGBTQ LGBTQ+ CCA CDBG YMCA USA AI ID TV NFL MLS FC OH DEI RFP RFQ LLC CPA CDC CRS CPA CPOA CCAC
SB HB ATV ATVS NBA GOP PTSD EMS ATM IRS DUI OVI PPE HVAC GIS GPS AMI CEOS NPR WCPO WLWT WKRP DC NY KY HR HQ GED ESL
ACLU CEAC CRC CNC CBC MOU MOA TV PAC GM EV EVS""".split())
CAPITALIZE_AFTER = set("""MR. MS. MRS. DR. MR MS MRS DR MISS COUNCILMEMBER COUNCILMAN COUNCILWOMAN MAYOR DIRECTOR
CHIEF SERGEANT LIEUTENANT CAPTAIN PASTOR REVEREND BISHOP SENATOR COMMISSIONER PROFESSOR""".split())
SPECIAL = {"over-the-rhine": "Over-the-Rhine", "i-71": "I-71", "i-75": "I-75", "i-74": "I-74", "i-471": "I-471",
           "a.m.": "a.m.", "p.m.": "p.m.", "3cdc's": "3CDC's", "u.s.": "U.S.", "d.c.": "D.C."}

# A "name" followed by one of these is an organization or a place ("Sierra Club", "Talbert House").
ORG_WORDS = set("""CLUB FOUNDATION CENTER CENTRE HOUSE CHURCH SCHOOL SCHOOLS PARK STREET AVENUE AVE ROAD DRIVE BOULEVARD
HALL COMPANY BANK GROUP COLLEGE UNIVERSITY ACADEMY HOSPITAL INSTITUTE MUSEUM LIBRARY PLACE SQUARE APARTMENTS HOMES
COMMONS VILLAGE HEIGHTS HILL HILLS TOWNSHIP COUNTY CITY CORPORATION CORP INC LLC ASSOCIATION ALLIANCE COALITION
COUNCIL COMMITTEE PROJECT FUND TRUST MINISTRIES PARTNERS PARTNERSHIP PLAZA TOWER BUILDING THEATER THEATRE AWARD ACT
LAW BILL PLAN REPORT PROGRAM""".split())
HONORIFICS = {"MR", "MS", "MRS", "MISS", "DR", "MX"}
# Places and other proper words that are never a person's name on their own ("then Kroger...", "in Clifton").
PLACE_WORDS = {w.upper().strip(".") for w in PROPER_WORDS} | {w.upper() for p in PROPER_PHRASES for w in p.split() if len(w) > 2}
PHRASE_PAIRS = {(a.upper(), b.upper()) for p in PROPER_PHRASES for a, b in zip(p.split(), p.split()[1:])}
STREET_WORDS = set("""STREET ST AVENUE AVE ROAD RD DRIVE DR BOULEVARD BLVD LANE LN COURT CT PLACE PL WAY TERRACE PIKE
PARKWAY PKWY CIRCLE ALLEY""".split())
_common = None


def common_words():
    """Everyday words (config/common_words.txt): never treated as a surname on their own."""
    global _common
    if _common is None:
        path = os.path.join(os.path.dirname(__file__), "..", "config", "common_words.txt")
        try:
            with open(path, encoding="utf-8") as f:
                _common = {l.strip() for l in f if l.strip() and not l.startswith("#")}
        except OSError:
            _common = set()
    return _common


_first_names = None


def first_names():
    """Common US/UK first names (upper case), from the gender-guesser name dictionary if installed."""
    global _first_names
    if _first_names is not None:
        return _first_names
    names = set()
    try:
        import gender_guesser  # noqa: F401  (only its data file is used)
        path = os.path.join(os.path.dirname(gender_guesser.__file__), "data", "nam_dict.txt")
        hexv = lambda c: int(c, 16) if c.strip() else 0  # noqa: E731
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith(("#", "=")) or len(line) < 34 or line[29:30] == "+":
                    continue
                name = line[3:29].strip()
                if re.fullmatch(r"[A-Za-z'-]+", name) and (hexv(line[32]) >= 1 or hexv(line[30]) >= 2):
                    names.add(name.upper())
    except Exception:
        pass
    try:
        with open(os.path.join(os.path.dirname(__file__), "..", "config", "first_names_extra.txt"), encoding="utf-8") as f:
            names |= {l.strip().upper() for l in f if l.strip() and not l.startswith("#")}
    except OSError:
        pass
    _first_names = names - AMBIGUOUS - FUNCTION
    return _first_names


# ------------------------------------------------------------------ parsing

TIME = r"(\d+):(\d\d):(\d\d)[,.](\d+)"
CUE_RX = re.compile(r"(?:^|\n)\s*\d+\s*\n\s*" + TIME + r"\s*-->\s*" + TIME + r"[^\n]*\n(.*?)(?=\n\s*\n|\Z)", re.S)


def secs(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms[:3].ljust(3, "0")) / 1000


def parse_srt(data):
    """[(start_seconds, end_seconds, text), ...]"""
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    cues = []
    for m in CUE_RX.finditer(text):
        body = re.sub(r"<[^>]+>", "", m.group(9)).replace("\n", " ").strip()
        if body:
            cues.append((secs(*m.group(1, 2, 3, 4)), secs(*m.group(5, 6, 7, 8)), body))
    return cues


LABEL_RX = re.compile(r"^\s*((?=[^:]*[a-z])[A-Z][A-Za-z.'’\- ]{1,48}?):\s*")


def turns(cues):
    """Split cues into speaker turns. Each turn: {"label": str, "tokens": [(word, seconds), ...]}"""
    out = []
    cur = None
    for start, end, body in cues:
        parts = re.split(r"\s*>>+\s*", body)
        words_total = sum(len(p.split()) for p in parts) or 1
        k = 0
        for i, part in enumerate(parts):
            if i > 0 or cur is None:
                cur = {"label": "", "tokens": []}
                out.append(cur)
            if i > 0 or not cur["tokens"]:
                lm = LABEL_RX.match(part)
                if lm:
                    cur["label"] = re.sub(r"\s+", " ", lm.group(1)).strip()
                    part = part[lm.end():]
            for w in part.split():
                t = start + (end - start) * k / words_total
                cur["tokens"].append((w, round(t, 1)))
                k += 1
    return [t for t in out if t["tokens"]]


# ------------------------------------------------------------------ public comment

PC_START = re.compile(r"PUBLIC COMMENT|PUBLIC TESTIMONY|PUBLIC HEARING|PRIVILEGE OF THE FLOOR|"
                      r"(?:FIRST|NEXT) SPEAKER|SPEAKER CARDS?|SIGNED UP TO SPEAK|STATE YOUR NAME|"
                      r"\b(?:ONE|TWO|THREE|[123]) MINUTES? TO (?:ADDRESS|SPEAK)|YOU (?:WILL )?HAVE (?:ONE|TWO|THREE|[123]) MINUTES|"
                      r"EVERYONE GETS (?:ONE|TWO|THREE|[123]) MINUTES|PUBLIC COMMENTERS?")
PC_NOT_START = re.compile(r"(?:CONCLUDES?|CLOSES?|CLOSED|ENDS?|ENDED|COMPLETES?|DONE WITH|HEARD|HEAR FROM [A-Z ]{0,30}IN|"
                          r"MENTIONED IN|DURING|SAID IN|RAISED IN|FROM)\s+(?:THE\s+|OUR\s+|TODAY'S\s+)?PUBLIC (?:COMMENT|TESTIMONY)")
PC_END = re.compile(r"(?:CONCLUDES?|CLOSES?|CLOSED|ENDS|COMPLETES?)\s+(?:THE\s+|OUR\s+)?PUBLIC (?:COMMENT|TESTIMONY|HEARING)|"
                    r"(?:THAT'S|THAT IS|THAT WAS) (?:IT|ALL|EVERYONE|EVERYBODY) (?:FOR|ON|WITH) (?:THE |OUR )?PUBLIC (?:COMMENT|TESTIMONY)|"
                    r"ANY (?:OTHER|MORE|FURTHER|ADDITIONAL) PUBLIC (?:COMMENT|TESTIMONY)|"
                    r"\b(?:GO|MOVE|TURN|GET) (?:ON )?(?:TO|INTO) (?:OUR |THE )?(?:FIRST |NEXT )?(?:AGENDA ITEM|ITEM NUMBER|ITEM NO)|"
                    r"(?:THAT|THAT'S|THAT IS|THOSE ARE) ALL (?:OF )?(?:OUR|THE|MY) (?:SPEAKERS|PUBLIC COMMENT)|"
                    r"NO (?:MORE|FURTHER|OTHER|ADDITIONAL) (?:SPEAKERS|PUBLIC COMMENT|ONE SIGNED)|"
                    r"\bROLL CALL\b|CLERK,? (?:PLEASE |WOULD YOU )?(?:READ|CALL)|"
                    r"MOVE (?:ON )?(?:TO|INTO) (?:THE |OUR )?(?:AGENDA|PRESENTATION|FIRST ITEM|ITEM)")
PC_RENEW = re.compile(r"THANK YOU|MY NAME|NEXT|FOLLOWED BY|FLOOR|MINUTES|CAN YOU HEAR ME|SPEAKER|"
                      r"^(?:GOOD (?:MORNING|AFTERNOON|EVENING)|HELLO|HI\b)")
# A Council member's turn during public comment is usually the chair calling the next speaker or thanking
# the last one. One without any of these signs means the meeting has moved on.
PC_CALL = re.compile(r"THANK|MINUTE|FLOOR|NEXT|FOLLOWED|SPEAKER|PUBLIC|ON ?LINE|HEAR ME|HEAR YOU|WELCOME|GO AHEAD|"
                     r"COME UP|COME ON UP|PODIUM|MICROPHONE|MIC\b|YOUR NAME|TESTIMONY|COMMENT|UNMUTE|ON DECK|TIME")
PC_TIMEOUT = 300
COUNCIL_LABEL = re.compile(r"^(?:Council ?(?:member|man|woman)|Vice Mayor|Mayor|President|Chair)", re.I)
MOVING_ON = re.compile(r"\b(?:DIRECTOR|MANAGER|ADMINISTRATION|PRESENTATION|DEPARTMENT|ITEM|ORDINANCE|RESOLUTION|MOTION|"
                       r"QUESTIONS? FOR|COLLEAGUES)\b")


def _moves_on(t, text):
    """A labeled turn that shows the meeting has moved past public comment."""
    if not t["label"]:
        return False
    if not COUNCIL_LABEL.search(t["label"]):
        return True                       # a labeled staff member presenting
    if PC_CALL.search(text) and not MOVING_ON.search(text):
        return False
    return len(text.split()) >= 25 or bool(MOVING_ON.search(text))   # seconds without any sign of public comment carrying on


REFERENCE = re.compile(r"(?:\bIN|DURING|FROM|AT|THROUGH|HEARD|HEARD FROM [A-Z' ]{1,30} IN)\s+(?:THE\s+|OUR\s+|TODAY'S\s+)?$")


def _starts(text):
    """Where public comment starts in this turn (character offset), or None. Mentions like "as we heard
    in public comment" don't count."""
    for m in PC_START.finditer(text):
        if not REFERENCE.search(text[max(0, m.start() - 45):m.start()]) and not PC_NOT_START.search(text[max(0, m.start() - 30):m.end() + 5]):
            return m.start()
    return None


def _split(t, char_pos, after=False):
    """Split a turn in two at the start of the sentence holding a character offset of its text
    (or, with after=True, at the end of that sentence)."""
    words = [w for w, _ in t["tokens"]]
    offs = _offsets(words)
    i = max([k for k, o in enumerate(offs) if o <= char_pos] or [0])
    if after:
        while i < len(words) and not re.search(r"[.?!]$", words[i]):
            i += 1
        i += 1
    else:
        while i > 0 and not re.search(r"[.?!]$", words[i - 1]):
            i -= 1
    if i <= 0 or i >= len(words):
        return None
    return dict(t, tokens=t["tokens"][:i]), dict(t, tokens=t["tokens"][i:])


def mark_public_comment(tlist):
    """Set t["pc"] = True on turns that are part of public comment (speakers and the chair calling them).
    A turn where public comment starts or ends partway through is split there. Returns the new list."""
    out = []
    on, last = False, 0.0
    queue = list(tlist)
    while queue:
        t = queue.pop(0)
        text = " ".join(w for w, _ in t["tokens"]).upper()
        start = t["tokens"][0][1]
        if not on:
            pos = None if t.get("_closed") else _starts(text)
            if pos is None:
                t["pc"] = False
                out.append(t)
                continue
            if pos > 0 and not t.get("_split"):
                parts = _split(t, pos)
                if parts:
                    a, b = parts
                    a["pc"] = False
                    out.append(a)
                    b["_split"] = True
                    queue.insert(0, b)
                    continue
            if PC_END.search(text, pos):
                t["pc"] = True       # starts and ends in one turn ("any other public comment? ... item 8")
                out.append(t)
                continue
            on, last = True, start
            t["pc"] = True
        else:
            end = PC_END.search(text)
            if end:
                parts = _split(t, end.start(), after=True)
                # keep the closing sentence (it often names the last speaker) in public comment
                if parts:
                    a, b = parts
                    a["pc"] = True
                    out.append(a)
                    on = False
                    b["_split"] = b["_closed"] = True   # "if anyone still wants to make public comment..." isn't a new start
                    queue.insert(0, b)
                    continue
                t["pc"] = True
                out.append(t)
                on = False
                continue
            if start - last > PC_TIMEOUT or _moves_on(t, text):
                on = False
                queue.insert(0, t)
                continue
            if PC_RENEW.search(text) or _starts(text) is not None:
                last = start
            t["pc"] = True
        out.append(t)
    for t in out:
        t.pop("_split", None)
        t.pop("_closed", None)
    return out


# ------------------------------------------------------------------ name removal

def core(word):
    return re.sub(r"[^A-Z'\-]", "", word.upper()).strip("'-")


def ends_phrase(word):
    return bool(re.search(r"[,.;:?!]$", word))


# (trigger, most words, strict): strict = the first word must be a known first name or an unusual word.
AFTER_TRIGGERS = [
    (re.compile(r"\bMY NAME(?:'S|S| IS| HE IS| IS IS)?\s"), 3, False),
    (re.compile(r"\bNAME IS\s"), 3, False),
    (re.compile(r"\b(?:NEXT|FIRST|LAST|FINAL) SPEAKERS?(?:,|\s+(?:I HAVE|WE HAVE|WILL BE|IS|UP|THAT I HAVE))*(?:\s+IS)?\s"), 3, True),
    (re.compile(r"\b(?:ON DECK|UP NEXT)(?:\s+(?:IS|WILL BE|WE HAVE))?\s"), 3, True),
    (re.compile(r"\bFOLLOWED BY\s"), 3, True),
    (re.compile(r"\b(?:MR|MS|MRS|MISS|DR)\.?\s"), 1, False),
]
BEFORE_TRIGGERS = re.compile(r",?\s(?:YOU HAVE|YOU'LL HAVE|YOU WILL HAVE|THE FLOOR IS YOURS|YOU'RE UP|YOU ARE UP|YOU'RE NEXT|"
                             r"JOIN US|(?:PLEASE )?COME (?:ON )?(?:UP|FORWARD|DOWN)|IS (?:FIRST|NEXT|UP NEXT)|"
                             r"WILL BE (?:OUR|THE) (?:FIRST|NEXT|LAST|FINAL) SPEAKER|YOU MAY BEGIN|GO AHEAD)")


def _offsets(words):
    pos, offs = 0, []
    for w in words:
        offs.append(pos)
        pos += len(w) + 1
    return offs


# The chair or clerk reading out who signed up: "we have Jane Doe, John Roe, and then Sam Poe".
LIST_TRIGGERS = re.compile(r"\b(?:WE HAVE|WE'VE GOT|I HAVE|(?:SPEAKER )?CARDS? (?:FROM|FOR)|(?:AND )?THEN|HEAR FROM|"
                           r"SIGNED UP(?: TO SPEAK)?(?: (?:IS|ARE))?|NEXT (?:IS|UP IS|WE HAVE))\s")
INTRO_RX = re.compile(r"(?:^|[.,!?] |\bAND )(?:I'M|I AM)\s")


def find_names(words, officials, strong_only=False, lists=False, intros=False):
    """Indexes of words in this turn that are a person's name. words are the turn's raw (upper-case) words.
    lists: the turn is someone calling speakers, so names read out in a list are found too.
    intros: also find an "I'm Jane Doe" at the very start of the turn (used outside public comment)."""
    text = " ".join(words).upper()
    offs = _offsets(words)
    idx_at = {o: i for i, o in enumerate(offs)}
    hits = set()
    common = common_words()

    def is_off(i):
        """An official's surname, or an official's first name followed by their surname."""
        c = core(words[i])
        return c in officials or (i + 1 < len(words) and f"{c} {core(words[i + 1])}" in officials)

    def take_after(i, n, strict=False):
        got = 0
        if strict and i < len(words) and ((core(words[i]) in common and core(words[i]) not in first_names())
                                          or core(words[i]) in PLACE_WORDS or core(words[i]) in ACRONYMS):
            return
        while i < len(words) and got < n:
            c = core(words[i])
            if got == 0 and c in HONORIFICS and not ends_phrase(words[i]):
                i += 1          # "MY NAME IS MISS JONES": skip the "Miss"
                continue
            if not c or c in FUNCTION or is_off(i) or len(c) < 2:
                break
            if got > 0 and c in common and not ends_phrase(words[i]):
                break
            hits.add(i)
            got += 1
            if ends_phrase(words[i]):
                break
            i += 1

    for rx, n, strict in AFTER_TRIGGERS:
        for m in rx.finditer(text + " "):
            i = idx_at.get(m.end())
            if i is not None:
                take_after(i, n, strict)
    for m in BEFORE_TRIGGERS.finditer(text):
        # the word just before the match (its comma, if any, is part of the match)
        i = max(k for k, o in enumerate(offs) if o <= m.start()) if m.start() > 0 else None
        if i is None:
            continue
        got = 0
        while i >= 0 and got < 3:
            c = core(words[i])
            if not c or c in FUNCTION or is_off(i) or len(c) < 2:
                break
            if got > 0 and ends_phrase(words[i]):
                break
            if got == 0 and re.search(r"[.?!]$", words[i]):
                break      # "THANK YOU SO MUCH. YOU HAVE TWO MINUTES": a new sentence, not someone's name
            if c in common and c not in first_names() and got != 1:
                break      # (the word right before a surname may be an unusual first name: "STEPHAN PRYOR")
            hits.add(i)
            got += 1
            i -= 1
    if intros and strong_only:
        # Outside public comment: "I'M PRIYA RAO AND I OWN..." in the first few words of a turn, where the
        # next two words are both unusual (so "I'M REALLY GLAD" and "I'M CONCERNED ABOUT" are left alone).
        for m in INTRO_RX.finditer(text):
            i = idx_at.get(m.end())
            if i is None or i > 8 or i + 1 >= len(words):
                continue
            a, b = core(words[i]), core(words[i + 1])
            ok = lambda c, k: c and len(c) > 1 and c not in FUNCTION and c not in PLACE_WORDS and not is_off(k) \
                and (c in first_names() or c not in common)
            if ok(a, i) and (ends_phrase(words[i]) and a in first_names() or ok(b, i + 1)):
                take_after(i, 3)
    if lists:
        for m in LIST_TRIGGERS.finditer(text + " "):
            i = idx_at.get(m.end())
            if i is not None:
                take_after(i, 3, True)
    if not strong_only:
        # "I'M CLAIRA MARTIN, ...": a self-introduction with a word that isn't an everyday word
        for m in re.finditer(r"(?:^|[.,!?] |\bAND )(?:I'M|I AM|THIS IS)\s", text):
            i = idx_at.get(m.end())
            if i is not None and core(words[i]) not in common and core(words[i]) not in FUNCTION and not is_off(i):
                take_after(i, 3)
        names = first_names()
        for i, w in enumerate(words):
            c = core(w)
            nxt = core(words[i + 1]) if i + 1 < len(words) else ""
            nxt2 = core(words[i + 2]) if i + 2 < len(words) else ""
            if nxt in ORG_WORDS or nxt2 in ORG_WORDS:
                continue
            if (c, nxt) in PHRASE_PAIRS or (i > 0 and (core(words[i - 1]), c) in PHRASE_PAIRS):
                continue      # "BRENT SPENCE", "MARTIN LUTHER KING": a bridge and a drive, not speakers
            if c in PLACE_WORDS and i > 0 and core(words[i - 1]) in ("IN", "OF", "FROM", "TO", "NEAR", "AROUND", "THROUGH"):
                continue      # "I LIVE IN CLIFTON": the neighborhood, not someone called Clifton
            if c in names and not is_off(i):
                hits.add(i)
                if not ends_phrase(w) and i + 1 < len(words):
                    c2 = core(words[i + 1])
                    if (c2 and c2 not in FUNCTION and not is_off(i + 1) and len(c2) > 1 and not re.search(r"\d", words[i + 1])
                            and (c2 not in common or ends_phrase(words[i + 1]))):
                        hits.add(i + 1)
    if lists:
        # A name that ends with a comma is followed by the next name on the list: "JANE DOE, RAJ PATEL, AND SAM POE."
        grew = True
        while grew:
            before = len(hits)
            for i in sorted(hits):
                if words[i].endswith(",") and i + 1 < len(words) and i + 1 not in hits:
                    j = i + 1
                    while j < len(words) and core(words[j]) in ("AND", "THEN") and not ends_phrase(words[j]):
                        j += 1
                    if j not in hits:
                        take_after(j, 3, True)
            grew = len(hits) > before
    return hits


def spelled_names(words):
    """Indexes of a name spelled out letter by letter ("K-O-W-A-L-S-K-I", or "K O W A L S K I"), and the name."""
    idx, names = set(), set()
    run = []
    for i, w in enumerate(list(words) + [""]):
        c = core(w)
        if re.fullmatch(r"[A-Z](?:-[A-Z]){2,}", c):
            idx.add(i)
            names.add(c.replace("-", ""))
        if len(c) == 1 and re.fullmatch(r"[A-Z][,.]?", w.upper()):
            run.append(i)
            continue
        if len(run) >= 4:
            idx.update(run)
            names.add("".join(core(words[k]) for k in run))
        run = []
    return idx, names


PHONE_RX = re.compile(r"^\(?\d{3}\)?[-.]?\d{3}[-.]\d{4}$|^\d{3}[-.]\d{4}$")


def personal_details(words):
    """{index: mark} for house numbers, phone numbers and email addresses a member of the public gave.
    The street name stays ("I live on [number] Elm Street"), so streets can still be searched. None = drop the word."""
    out = {}
    bare = [re.sub(r"^[^\w(]+|[^\w)]+$", "", w) for w in words]
    for i, b in enumerate(bare):
        if "@" in b and "." in b:
            out[i] = EMAIL_MARK
        elif PHONE_RX.match(b):
            out[i] = PHONE_MARK
        elif (re.fullmatch(r"\(?\d{3}\)?", b) and i + 2 < len(bare) and re.fullmatch(r"\d{3}", bare[i + 1])
              and re.fullmatch(r"\d{4}", bare[i + 2])):
            out[i], out[i + 1], out[i + 2] = PHONE_MARK, None, None      # "513 555 0142"
        elif re.fullmatch(r"\d{1,5}", b) and i not in out and not ends_phrase(words[i]):
            for j in range(i + 1, min(i + 5, len(words))):      # "1234 ELM STREET", "12 EAST MCMICKEN AVENUE"
                c = core(words[j])
                if c in STREET_WORDS and j > i + 1:
                    out[i] = NUMBER_MARK
                    break
                if not c or c in FUNCTION or re.search(r"\d", words[j]) or ends_phrase(words[j]):
                    break
    return out


# The chair calling up speakers, even where public comment wasn't recognized as such.
SPEAKER_CALL = re.compile(r"FLOOR IS YOURS|PRIVILEGE OF THE FLOOR|YOU HAVE (?:TWO|THREE|ONE|[123]) MINUTES|NEXT SPEAKER|"
                          r"LAST SPEAKER|FIRST SPEAKER|FOLLOWED BY|ON DECK|COME (?:ON )?UP|SIGNED UP TO SPEAK|SPEAKER CARDS?")
# A resident speaking about their own life outside the public comment part of a meeting.
RESIDENT_CONTEXT = re.compile(r"\b(?:I|WE) (?:LIVE|LIVED|OWN|RENT)\b|\bI'VE LIVED\b|\bRESIDENT OF\b|\bHOMEOWNER\b|\bMY NEIGHBORS?\b|"
                              r"\bHER NEIGHBOR\b|\bHIS NEIGHBOR\b|\bMY (?:SON|DAUGHTER|HUSBAND|WIFE|MOTHER|FATHER|MOM|DAD|KIDS|CHILDREN|GRANDSON|GRANDDAUGHTER)\b")


def redact(tlist, officials):
    """Replace the names of members of the public with [name]. Returns how many words were removed."""
    found = set()
    for t in tlist:
        words = [w for w, _ in t["tokens"]]
        text = " ".join(words).upper()
        calling = bool(SPEAKER_CALL.search(text))
        personal = bool(t.get("pc")) and not COUNCIL_LABEL.search(t["label"] or "")
        if t.get("pc") or calling:
            # Names read out in a list are looked for where someone is calling speakers: the chair, or an
            # unlabeled clerk, during public comment.
            hits = find_names(words, officials, lists=calling or bool(COUNCIL_LABEL.search(t["label"] or "")))
        elif not t["label"]:
            # Outside public comment: residents talking about their own lives, and clear self-introductions
            # by people who aren't City staff.
            hits = set()
            staff = STAFF_CONTEXT.search(text)
            m = re.search(r"\bMY NAME", text)
            if RESIDENT_CONTEXT.search(text) and not staff:
                hits = find_names(words, officials)
                personal = True
            elif m and not STAFF_CONTEXT.search(" ".join(text[m.start():].split()[:18])):
                hits = find_names(words, officials, strong_only=True)
            elif INTRO_RX.search(text) and not STAFF_CONTEXT.search(" ".join(text.split()[:26])):
                hits = find_names(words, officials, strong_only=True, intros=True)
        else:
            hits = set()
        if hits or t.get("pc"):
            sp, spelled = spelled_names(words)
            hits |= sp
            found |= spelled
        # House numbers, phone numbers and email addresses given by members of the public.
        t["masked"] = {i: m for i, m in personal_details(words).items() if i not in hits} if personal else {}
        t["hits"] = hits
        for i in hits:
            found.add(core(words[i]))
    # The same surnames elsewhere in the meeting ("as Ms. Doe said earlier") go too. First names and
    # everyday words don't spread this way (too many false matches).
    common, names = common_words(), first_names()
    surnames = {n for n in found if len(n) >= 3 and n not in officials and n not in AMBIGUOUS and n not in FUNCTION
                and n not in common and n not in names}
    removed = 0
    for t in tlist:
        hits = t.pop("hits")
        for i, (w, _) in enumerate(t["tokens"]):
            if core(w) in surnames:
                hits.add(i)
                # a first name right before a known surname ("FRANK [RUSSEL]") goes with it
                if i > 0 and core(t["tokens"][i - 1][0]) in found and core(t["tokens"][i - 1][0]) not in officials \
                        and not ends_phrase(t["tokens"][i - 1][0]):
                    hits.add(i - 1)
        removed += len(hits)
        t["redacted"] = hits
    return removed


# ------------------------------------------------------------------ readable casing

class Caser:
    def __init__(self, officials=(), places=()):
        self.words = {}       # lower -> display, single words
        self.phrases = {}     # tuple(lower words) -> list(display words)
        common = common_words()
        for item in PROPER_WORDS + [w for n in officials for w in n.split()
                                    if len(core(w)) > 2 and core(w) not in AMBIGUOUS and core(w) not in common and core(w) not in FUNCTION]:
            self.words.setdefault(item.lower(), item)
        for n in first_names():
            self.words.setdefault(n.lower(), n.capitalize())
        for p in list(places) + PROPER_PHRASES:
            ws = p.split()
            if len(ws) == 1:
                self.words[ws[0].lower()] = ws[0]
            else:
                self.phrases[tuple(w.lower() for w in ws)] = ws
        for name in officials:
            ws = name.split()
            if len(ws) > 1:
                self.phrases[tuple(w.lower() for w in ws)] = ws
        self.maxlen = max([len(k) for k in self.phrases] + [1])

    def word(self, w):
        m = re.match(r"^([^A-Za-z0-9]*)(.*?)([^A-Za-z0-9.']*)$", w)
        pre, mid, post = m.groups()
        low = mid.lower()
        key = low.rstrip(".")
        if low in SPECIAL:
            mid = SPECIAL[low]
        elif key.upper() in ACRONYMS or (re.search(r"\d", mid) and re.search(r"[A-Za-z]{2}", mid) and not re.search(r"\d(?:st|nd|rd|th|s)$", low)):
            mid = mid.upper()
        elif re.fullmatch(r"i(?:'m|'ve|'ll|'d)?", low):
            mid = "I" + low[1:]
        elif key in self.words:
            disp = self.words[key]
            mid = disp + low[len(key):]
        elif "'" in low and low.split("'")[0] in self.words:
            base = low.split("'")[0]
            mid = self.words[base] + low[len(base):]
        else:
            mid = low
        return pre + mid + post

    def __call__(self, words):
        """words: upper-case words (and [name] marks). Returns display words."""
        is_mark = lambda w: w.startswith(MARKS)
        out = [self.word(w) if not is_mark(w) else w for w in words]
        lows = [re.sub(r"[^a-z0-9'\-]", "", w.lower()) for w in words]
        i = 0
        while i < len(out):
            for n in range(min(self.maxlen, len(out) - i), 1, -1):
                disp = self.phrases.get(tuple(lows[i:i + n]))
                if disp:
                    for k in range(n):
                        out[i + k] = re.sub(re.escape(lows[i + k]), disp[k], out[i + k], count=1, flags=re.I)
                    i += n - 1
                    break
            i += 1
        start = True
        prev = ""
        for i, w in enumerate(out):
            if is_mark(w):
                start, prev = bool(re.search(r"[.?!]$", w)), w   # "[name]." ends a sentence
                continue
            cap_next = prev.upper().rstrip(",") in CAPITALIZE_AFTER and core(w) not in FUNCTION
            month = re.fullmatch(r"(?:may|march)", w.lower()) and i + 1 < len(out) and re.match(r"\d", out[i + 1])
            if start or cap_next or month:
                out[i] = re.sub(r"^([^A-Za-z]*)([a-z])", lambda m: m.group(1) + m.group(2).upper(), w)
            start = bool(re.search(r"[.?!][\"')\]]*$", w)) and not re.search(r"^(?:mr|ms|mrs|dr|st|jr|sr|a\.m|p\.m|u\.s)\.$", w.lower())
            prev = words[i]
        return out


# ------------------------------------------------------------------ passages

def passages(tlist, caser, target=55, most=110):
    """Cut turns into passages: [{"t": seconds, "label": str, "pc": 0/1, "text": str}, ...]"""
    out = []
    for t in tlist:
        words, times, red, masked = [], [], t.get("redacted", set()), t.get("masked", {})
        for i, (w, s) in enumerate(t["tokens"]):
            if i in masked and i not in red:
                if masked[i] is None:
                    continue
                tail = re.search(r"[,.;:?!]+$", w)
                w = masked[i] + (tail.group(0) if tail else "")
            elif i in red:
                tail = re.search(r"[,.;:?!]+$", w)
                if words and words[-1].startswith(NAME_MARK):
                    words[-1] = NAME_MARK + (tail.group(0) if tail else "")
                    continue
                w = NAME_MARK + (tail.group(0) if tail else "")
            words.append(w)
            times.append(s)
        shown = caser(words)
        chunk, t0 = [], None
        for w, s in zip(shown, times):
            if not chunk:
                t0 = s
            chunk.append(w)
            if (len(chunk) >= target and re.search(r"[.?!]$", w)) or len(chunk) >= most:
                out.append({"t": int(t0), "label": t["label"], "pc": int(bool(t.get("pc"))), "text": " ".join(chunk)})
                chunk = []
        if chunk:
            if out and out[-1]["label"] == t["label"] and len(chunk) < 12 and out[-1]["t"] >= t["tokens"][0][1] - 1:
                out[-1]["text"] += " " + " ".join(chunk)
            else:
                out.append({"t": int(t0), "label": t["label"], "pc": int(bool(t.get("pc"))), "text": " ".join(chunk)})
    return out


def label_names(tlist):
    """Surnames from the captioner's labels ("Councilmember Owens" -> OWENS): officials, never removed."""
    names = set()
    for t in tlist:
        for w in t["label"].split():
            c = core(w)
            if len(c) >= 3 and c not in CAPITALIZE_AFTER and c not in FUNCTION:
                names.add(c)
    return names


def process(srt_data, officials=(), places=()):
    """Everything above, for one caption file. officials: full names of Council members etc.
    Returns (passages, info)."""
    cues = parse_srt(srt_data)
    tl = turns(cues)
    # Officials' surnames are never removed; their first names only when followed by their surname
    # (a member of the public can share a Council member's first name).
    official_words = set(label_names(tl))
    for n in officials:
        ws = [core(w) for w in n.split() if len(core(w)) >= 2]
        if ws:
            official_words.add(ws[-1])
            official_words.add(f"{ws[0]} {ws[-1]}")
    tl = mark_public_comment(tl)
    removed = redact(tl, official_words)
    caser = Caser(officials, places)
    ps = passages(tl, caser)
    info = {"cues": len(cues), "turns": len(tl), "words": sum(len(t["tokens"]) for t in tl),
            "pc_turns": sum(1 for t in tl if t.get("pc")), "names_removed": removed,
            "duration": int(cues[-1][1]) if cues else 0}
    return ps, info

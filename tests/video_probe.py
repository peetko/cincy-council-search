"""Probe 2: caption files on Archive.org - quality, committees, older years, coverage."""
import json, collections, re, urllib.parse, urllib.request

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "juncta-lucent probe"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()

def search(q, rows=10000):
    url = "https://archive.org/advancedsearch.php?" + urllib.parse.urlencode(
        {"q": q, "fl[]": ["identifier", "title", "date"], "sort[]": "date desc", "rows": rows, "output": "json"}, doseq=True)
    return json.loads(get(url))["response"]["docs"]

docs = search("collection:citicable OR collection:citiohv")
for d in docs:  # a few items store title/date as lists
    for k in ("title", "date"):
        if isinstance(d.get(k), list):
            d[k] = d[k][0] if d[k] else ""
        d.setdefault(k, "")
print(len(docs), "items in the two CitiCable collections")
council = [d for d in docs if re.search(r"city council|committee|council", d.get("title", ""), re.I) and "torrance" not in d.get("title", "").lower()]
by_year = collections.Counter(d.get("date", "")[:4] for d in council)
print("council/committee items by year:", dict(sorted(by_year.items())))

def files(ident):
    return json.loads(get(f"https://archive.org/metadata/{ident}"))

print("\n=== Caption files by year (sample of 4 per year)")
for year in sorted(by_year):
    sample = [d for d in council if d.get("date", "").startswith(year)][:: max(1, by_year[year] // 4)][:4]
    for d in sample:
        md = files(d["identifier"])
        names = [f["name"] for f in md["files"]]
        srt = [n for n in names if n.endswith(".srt")]
        print(f"  {d['date'][:10]} {d['identifier']:<28} cc={md['metadata'].get('closed_captioning')} srt={srt[:1]} | {d['title'][:60]}")

def show(ident, n=40):
    md = files(ident)
    txt = next((f["name"] for f in md["files"] if f["name"].endswith(".disc.txt")), None)
    srt = next((f["name"] for f in md["files"] if f["name"].endswith(".srt")), None)
    print(f"\n=== {ident}: {md['metadata'].get('title')}")
    if srt:
        s = get(f"https://archive.org/download/{ident}/{urllib.parse.quote(srt)}").decode("utf-8", "ignore")
        cues = re.findall(r"\d+\n(\d\d:\d\d:\d\d),\d+ --> (\d\d:\d\d:\d\d),\d+\n(.*?)(?:\n\n|\Z)", s, re.S)
        words = sum(len(c[2].split()) for c in cues)
        print(f"srt: {len(cues)} cues, {words} words, first {cues[0][0] if cues else '-'} last {cues[-1][1] if cues else '-'}")
        text = " ".join(c[2].replace("\n", " ") for c in cues)
        print("first 1500 chars:", text[:1500])
        mid = len(text) // 2
        print("\nmiddle 1500 chars:", text[mid:mid + 1500])
    elif txt:
        print(get(f"https://archive.org/download/{ident}/{urllib.parse.quote(txt)}").decode("utf-8", "ignore")[:2500])
    else:
        print("no caption file")

show("10261028-coun")
show("14261020-psql")
old = [d for d in council if d.get("date", "")[:4] in ("2020", "2021")]
if old:
    show(old[len(old) // 2]["identifier"])

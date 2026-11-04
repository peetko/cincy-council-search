"""Probe 3: save the item list and a spread of real caption files to the diag5 branch."""
import json, re, os, random, urllib.parse, urllib.request, collections

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "juncta-lucent probe"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()

def search(q, rows=10000):
    url = "https://archive.org/advancedsearch.php?" + urllib.parse.urlencode(
        {"q": q, "fl[]": ["identifier", "title", "date", "closed_captioning", "publicdate", "subject"],
         "sort[]": "date desc", "rows": rows, "output": "json"}, doseq=True)
    return json.loads(get(url))["response"]["docs"]

docs = search("collection:citicable OR collection:citiohv")
for d in docs:
    for k in ("title", "date", "closed_captioning", "publicdate"):
        if isinstance(d.get(k), list):
            d[k] = d[k][0] if d[k] else ""
        d.setdefault(k, "")
os.makedirs("probe", exist_ok=True)
json.dump(docs, open("probe/items.json", "w"), indent=0)
print(len(docs), "items")
recent = [d for d in docs if d["date"][:10] >= "2021-05-01" and re.match(r"^\d{8}-[a-z]+", d["identifier"])]
codes = collections.Counter(d["identifier"].split("-", 1)[1] for d in recent)
print("codes:", codes.most_common(60))
random.seed(7)
pick = []
by_code = collections.defaultdict(list)
for d in recent:
    by_code[d["identifier"].split("-", 1)[1]].append(d)
for code, items in sorted(by_code.items(), key=lambda kv: -len(kv[1]))[:14]:
    pick += random.sample(items, min(3, len(items)))
for d in pick:
    try:
        md = json.loads(get(f"https://archive.org/metadata/{d['identifier']}"))
        srt = next((f["name"] for f in md["files"] if f["name"].endswith(".srt")), None)
        if not srt:
            print("no srt", d["identifier"]); continue
        data = get(f"https://archive.org/download/{d['identifier']}/{urllib.parse.quote(srt)}")
        open(f"probe/{d['identifier']}.srt", "wb").write(data)
        names = [f["name"] for f in md["files"]]
        print("saved", d["identifier"], srt, len(data), "| files:", [n for n in names if not n.endswith(('.jpg', '.png', '.gif'))][:12])
    except Exception as e:
        print("fail", d["identifier"], e)

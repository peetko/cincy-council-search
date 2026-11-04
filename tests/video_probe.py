"""Probe: where Cincinnati meeting videos live and whether captions/transcripts can be had."""
import json, os, sqlite3, subprocess, urllib.parse, urllib.request

def get(url, n=None, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "juncta-lucent probe", **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read(n) if n else r.read()

print("=== 1. Legistar video links in our database")
base = "https://peetko.github.io/cincy-council-search/"
info = json.loads(get(base + "data/db-info.json"))
open("/tmp/c.db", "wb").write(b"".join(get(base + info["urlPrefix"] + f"{i:03d}") for i in range(info["pieces"])))
c = sqlite3.connect("/tmp/c.db")
print(c.execute("SELECT COUNT(*), SUM(video_url IS NOT NULL AND video_url <> '') FROM meetings").fetchone())
for r in c.execute("SELECT date, body, video_url FROM meetings WHERE video_url <> '' ORDER BY date DESC LIMIT 5"):
    print("  ", r)

print("\n=== 2. Archive.org search for CitiCable council meetings")
for q in ['title:("City Council") AND mediatype:movies AND (uploader:citicable* OR collection:citiohv OR creator:CitiCable)',
          'collection:citiohv', 'creator:(CitiCable) AND title:(Council)']:
    url = "https://archive.org/advancedsearch.php?" + urllib.parse.urlencode(
        {"q": q, "fl[]": ["identifier", "title", "date", "collection"], "sort[]": "date desc", "rows": 12, "output": "json"}, doseq=True)
    try:
        docs = json.loads(get(url))["response"]
        print(f"query {q!r}: {docs['numFound']} found")
        for d in docs["docs"][:12]:
            print("  ", d.get("date", "")[:10], d["identifier"], "|", d.get("title"), "|", d.get("collection"))
    except Exception as e:
        print("  error", e)

print("\n=== 3. Files in recent council meeting items")
url = "https://archive.org/advancedsearch.php?" + urllib.parse.urlencode(
    {"q": 'title:("Cincinnati City Council") AND mediatype:movies', "fl[]": ["identifier", "title", "date"], "sort[]": "date desc", "rows": 4, "output": "json"}, doseq=True)
items = json.loads(get(url))["response"]["docs"]
for d in items[:3]:
    md = json.loads(get(f"https://archive.org/metadata/{d['identifier']}"))
    meta = md.get("metadata", {})
    print(f"\n{d['identifier']} | {meta.get('title')} | date {meta.get('date')} | closed_captioning={meta.get('closed_captioning')} | uploader {meta.get('uploader')} | collection {meta.get('collection')}")
    for f in md.get("files", []):
        print(f"   {f.get('format','?'):<28} {int(f.get('size', 0) or 0)/1e6:9.1f} MB  {f['name']}")

if items:
    ident = items[0]["identifier"]
    md = json.loads(get(f"https://archive.org/metadata/{ident}"))
    mp4 = next((f["name"] for f in md["files"] if f["name"].lower().endswith(".mp4")), None)
    mxf = next((f["name"] for f in md["files"] if f["name"].lower().endswith((".mxf", ".ts", ".mpg", ".mpeg"))), None)
    print("\n=== 4. Embedded captions?")
    for name, mb in ((mp4, 40), (mxf, 120)):
        if not name:
            continue
        u = f"https://archive.org/download/{ident}/{urllib.parse.quote(name)}"
        part = "/tmp/part" + os.path.splitext(name)[1]
        try:
            open(part, "wb").write(get(u, headers={"Range": f"bytes=0-{mb * 1000000}"}))
        except Exception as e:
            print("  download error", e); continue
        print(f"-- {name}: ffprobe streams")
        print(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=index,codec_type,codec_name,codec_tag_string:stream_side_data", "-of", "compact", part], capture_output=True, text=True).stdout[:1500])
        r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"movie={part}[out0+subcc]", "-map", "0:1", "-y", "/tmp/cc.srt"], capture_output=True, text=True)
        txt = open("/tmp/cc.srt").read() if os.path.exists("/tmp/cc.srt") else ""
        print(f"   EIA-608 captions extracted: {len(txt)} chars; error: {r.stderr[-300:]}")
        print("   sample:", " ".join(txt.split())[:600])
        if os.path.exists("/tmp/cc.srt"): os.remove("/tmp/cc.srt")

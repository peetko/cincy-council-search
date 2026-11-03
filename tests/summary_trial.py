"""Summary trial: run small open models (via Ollama) on 10 ordinances; print results + a Gemini paste pack."""
import json, os, sqlite3, sys, time, urllib.request

BASE = "https://peetko.github.io/cincy-council-search/"
MODELS = [] if sys.argv[1:] == ["none"] else (sys.argv[1:] or ["llama3.2:3b", "qwen2.5:3b", "gemma3:4b"])
INSTRUCTIONS = ("You summarize Cincinnati City Council legislation for residents. For each document, write 2-3 short, "
                "plain-language sentences saying what it does, who is involved, any dollar amounts, and any neighborhood "
                "or department named. Use only facts stated in the text; if something is unclear, leave it out. "
                "No opinions, no legal advice, no preamble. Don't start with 'This ordinance'.")
MAX_CHARS = 6000


def fetch(path):
    return urllib.request.urlopen(BASE + path, timeout=120).read()


def load(name, info_file):
    info = json.loads(fetch("data/" + info_file))
    data = b"".join(fetch(info["urlPrefix"] + f"{i:03d}") for i in range(info["pieces"]))
    open(f"/tmp/{name}.db", "wb").write(data)
    return sqlite3.connect(f"/tmp/{name}.db")


main, docs = load("council", "db-info.json"), load("docs", "docs-info.json")
kinds = dict(main.execute("SELECT matter_id, MAX(kind) FROM items WHERE matter_id IS NOT NULL GROUP BY matter_id"))
rows = docs.execute("SELECT matter_id, matter_file, source, chars, text FROM docs WHERE status='ok' ORDER BY matter_id DESC").fetchall()
ords = [r for r in rows if kinds.get(r[0]) == "ordinance"]
# A varied sample: short, medium and long; readable and OCR'd.
ords.sort(key=lambda r: r[3])
pick, n = [], len(ords)
for frac in [0.1, 0.25, 0.4, 0.55, 0.7, 0.8, 0.9, 0.97]:
    pick.append(ords[int(frac * (n - 1))])
ocr = [r for r in ords if r[2] == "ocr" and r not in pick]
pick += ocr[:2]
sample = [{"file": r[1], "source": r[2], "chars": r[3], "text": r[4][:MAX_CHARS]} for r in pick[:10]]

print("PACK_START")
print(INSTRUCTIONS + "\n\nLabel each summary with its file number, in the same order.\n")
for s in sample:
    print(f"=== File {s['file']} ===\n{s['text']}\n")
print("PACK_END")
for s in sample:
    print("DOC\t" + json.dumps({k: s[k] for k in ("file", "source", "chars")}))

for model in MODELS:
    os.system(f"ollama pull {model} > /dev/null 2>&1")
    for s in sample:
        body = {"model": model, "stream": False, "options": {"temperature": 0.2, "num_ctx": 4096},
                "messages": [{"role": "system", "content": INSTRUCTIONS},
                             {"role": "user", "content": f"File {s['file']}:\n\n{s['text']}"}]}
        t = time.time()
        req = urllib.request.Request("http://localhost:11434/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            out = json.loads(urllib.request.urlopen(req, timeout=900).read())["message"]["content"].strip()
        except Exception as e:
            out = f"(error: {e})"
        print("RESULT\t" + json.dumps({"model": model, "file": s["file"], "seconds": round(time.time() - t, 1), "summary": out}))
        sys.stdout.flush()

"""A stand-in for archive.org's search, metadata and download endpoints, for testing.

    python tests/fake_archive.py 8798 [folder of .srt files named like 10261028-coun.srt]
    ARCHIVE_BASE=http://localhost:8798 ARCHIVE_PAUSE=0 python scripts/collect_transcripts.py

Without a folder it serves three small made-up meetings.
"""
import glob
import http.server
import json
import os
import re
import sys
import urllib.parse

FOLDER = sys.argv[2] if len(sys.argv) > 2 else None
SAMPLE = """1
00:00:05,000 --> 00:00:09,000
>> Councilmember Owens: GOOD MORNING. WE WILL BEGIN WITH PUBLIC COMMENT.

2
00:00:09,000 --> 00:00:14,000
JANE EXAMPLE, YOU HAVE THE PRIVILEGE OF THE FLOOR FOR TWO MINUTES.

3
00:00:15,000 --> 00:00:22,000
>> THANK YOU. MY NAME IS JANE EXAMPLE AND I LIVE IN WESTWOOD. PLEASE FIX THE SIDEWALKS ON HARRISON AVENUE.

4
00:00:23,000 --> 00:00:30,000
>> Councilmember Owens: THANK YOU MS. EXAMPLE. THAT CONCLUDES PUBLIC COMMENT. DIRECTOR, THE FLOOR IS YOURS.

5
00:00:31,000 --> 00:00:40,000
>> GOOD MORNING. THE STREETCAR BUDGET AND AFFORDABLE HOUSING IN OVER-THE-RHINE ARE ON TODAY'S AGENDA.
"""


def items():
    if FOLDER:
        out = {}
        for p in glob.glob(os.path.join(FOLDER, "*.srt")):
            ident = os.path.basename(p)[:-4]
            out[ident] = open(p, "rb").read()
        return out
    return {"10260601-coun": SAMPLE.encode(), "11260602-bfg": SAMPLE.encode(), "14260603-psql": b""}


ITEMS = items()
TITLES = {"coun": "Cincinnati City Council Meeting", "bfg": "Budget, Finance & Governance Committee", "bfc": "Budget & Finance Committee",
          "psql": "Public Safety & Quality of Life Committee", "hg": "Housing & Growth Committee"}


def date_of(ident):
    m = re.match(r"^\d\d(\d\d)(\d\d)(\d\d)", ident)
    return f"20{m.group(1)}-{m.group(2)}-{m.group(3)}"


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype="application/json", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/advancedsearch.php":
            docs = [{"identifier": i, "date": date_of(i) + "T00:00:00Z",
                     "title": f"{TITLES.get(i.split('-')[1], 'Some Committee')} ({date_of(i)})"} for i in ITEMS]
            docs.append({"identifier": "city-planning-6-1-26", "title": "City Planning Commission", "date": "2026-06-01"})
            return self.send(json.dumps({"response": {"docs": docs}}).encode())
        m = re.match(r"^/metadata/(.+)$", u.path)
        if m:
            ident = urllib.parse.unquote(m.group(1))
            files = [{"name": ident + ".mp4"}] + ([{"name": ident + ".disc.srt"}] if ITEMS.get(ident) else [])
            return self.send(json.dumps({"files": files, "metadata": {"identifier": ident}}).encode())
        m = re.match(r"^/download/([^/]+)/(.+)$", u.path)
        if m and ITEMS.get(urllib.parse.unquote(m.group(1))):
            return self.send(ITEMS[urllib.parse.unquote(m.group(1))], "text/plain")
        self.send(b"not found", "text/plain", 404)


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()

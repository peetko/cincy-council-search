"""A stand-in for webapi.legistar.com, used only for testing.

It serves made-up meetings shaped like the real API's JSON so the collector,
stats and site can be exercised without touching the city's server:

    python tests/fake_legistar.py 8799 &
    LEGISTAR_API_BASE=http://localhost:8799/v1 LEGISTAR_PAUSE=0 python scripts/collect.py --db /tmp/test.db
"""
import datetime as dt
import http.server
import json
import random
import re
import sys
import urllib.parse

TODAY = dt.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else dt.date.today()
MEMBERS = [f"Member {c}" for c in "ABCDEFGHI"]
BODIES = [("Cincinnati City Council", 2), ("Budget, Finance & Governance Committee", 0),
          ("Housing & Growth", 1), ("Climate, City Services & Infrastructure", 1)]
SUBJECTS = [
    ("affordable housing in Over-the-Rhine", "Community and Economic Development"),
    ("a new playground in Westwood", "Cincinnati Parks"),
    ("traffic calming in East Price Hill", "Transportation and Engineering"),
    ("a development agreement in Madisonville", "Community and Economic Development"),
    ("the Cincinnati Streetcar", "Transportation and Engineering"),
    ("police recruit classes", "Police Department"),
    ("a tax abatement for a Kroger grocery store", "Community and Economic Development"),
    ("sidewalk repairs", "Transportation and Engineering"),
    ("the Office of Data and Performance Analytics dashboard", "Office of Data and Performance Analytics"),
    ("park improvements at Washington Park", "Cincinnati Parks"),
    ("Water Works capital improvements", "Greater Cincinnati Water Works"),
    ("data center zoning regulations", "City Planning"),
    ("youth summer jobs", "City Manager"),
    ("solar installations on City buildings", "Environment and Sustainability"),
    ("the fiscal year operating budget", "Office of Budget and Evaluation"),
]
VERBS = ["AUTHORIZING", "ESTABLISHING", "MODIFYING", "APPROPRIATING funds for"]

events, items, votes = [], {}, {}
office_records = []


def build():
    rnd = random.Random(42)
    pending = []  # committee items waiting for a full-Council vote
    eid = iid = mid = 1000
    d = dt.date(2020, 6, 29)
    end = TODAY + dt.timedelta(days=21)
    while d <= end:
        for body, weekday in BODIES:
            day = d + dt.timedelta(days=weekday)
            if day > end:
                continue
            eid += 1
            past = day < TODAY
            ev = {"EventId": eid, "EventGuid": f"G-{eid}", "EventBodyName": body,
                  "EventDate": f"{day.isoformat()}T00:00:00", "EventTime": "2:00 PM" if "Council" == body[-7:] else "1:00 PM",
                  "EventLocation": "Council Chambers, Room 300, City Hall",
                  "EventAgendaFile": f"https://cincinnatioh.legistar.com/View.ashx?M=A&ID={eid}&GUID=G-{eid}",
                  "EventMinutesFile": f"https://cincinnatioh.legistar.com/View.ashx?M=M&ID={eid}&GUID=G-{eid}" if past else None,
                  "EventAgendaStatusName": "Final", "EventMinutesStatusName": "Final" if past else "Draft",
                  "EventVideoPath": None,
                  "EventInSiteURL": f"https://cincinnatioh.legistar.com/MeetingDetail.aspx?LEGID={eid}&GID=G-{eid}",
                  "EventLastModifiedUtc": f"{(day + dt.timedelta(days=9 if past else -5)).isoformat()}T15:00:00"}
            events.append(ev)
            council = body == "Cincinnati City Council"
            lst = []
            lst.append({"EventItemId": (iid := iid + 1), "EventItemTitle": None, "EventItemMatterFile": None})
            lst.append({"EventItemId": (iid := iid + 1), "EventItemTitle": "ROLL CALL", "EventItemMatterFile": None,
                        "EventItemMatterId": None, "EventItemActionName": None})
            lst.append({"EventItemId": (iid := iid + 1), "EventItemTitle": "Council observed a moment of silent prayer.",
                        "EventItemMatterFile": None, "EventItemMatterId": None, "EventItemActionName": None})
            for k in range(rnd.randint(10, 14) if council else rnd.randint(3, 6)):
                iid += 1
                reuse = council and pending and rnd.random() < 0.6
                if reuse:
                    r_mid, r_kind, r_subj, r_dept, r_title, r_day = pending.pop(0)
                    mid_this, kind, subj, dept = r_mid, r_kind, r_subj, r_dept
                else:
                    mid += 1
                    mid_this = mid
                    kind = rnd.choices(["ORDINANCE", "RESOLUTION", "MOTION", "COMMUNICATION"], [5, 2, 3, 1])[0]
                    subj, dept = rnd.choice(SUBJECTS)
                if kind == "MOTION":
                    s1, s2 = rnd.sample(MEMBERS, 2)
                    title = (f"MOTION, submitted by Councilmembers {s1[-1]}son and {s2[-1]}ford, WE MOVE that the "
                             f"Administration prepare a report within 60 days on {subj}, including costs, timelines "
                             f"and coordination with the {dept}.")
                elif kind == "ORDINANCE":
                    em = rnd.random() < 0.6
                    title = (f"ORDINANCE {'(EMERGENCY) ' if em else ''}submitted by Sheryl M. Long, City Manager, on "
                             f"{day.month}/{day.day}/{day.year}, {rnd.choice(VERBS)} the City Manager to execute "
                             f"agreements related to {subj}; and AUTHORIZING the Director of Finance to deposit and "
                             f"appropriate the related funds, administered by the {dept}.")
                elif kind == "RESOLUTION":
                    title = (f"RESOLUTION, submitted by Councilmember {rnd.choice(MEMBERS)[-1]}son, from Emily Smart "
                             f"Woerner, City Solicitor, EXPRESSING the support of Council for {subj}.")
                else:
                    title = f"COMMUNICATION, submitted by the Clerk of Council regarding {subj}."
                if reuse:
                    title = r_title
                elif not council and kind != "COMMUNICATION":
                    pending.append((mid_this, kind, subj, dept, title, day))
                final_vote = council and past and kind in ("ORDINANCE", "RESOLUTION") and rnd.random() < 0.85
                passed = None
                action = None
                if past:
                    if final_vote:
                        passed = 1 if rnd.random() < 0.95 else 0
                        action = ("Passed Emergency" if "(EMERGENCY)" in title else "Passed") if kind == "ORDINANCE" else "Adopted"
                    elif kind == "MOTION":
                        action = rnd.choice(["Referred", "Adopted", "Held"])
                    else:
                        action = rnd.choice(["Filed", "Recommend Passage", "Held"])
                it = {"EventItemId": iid, "EventItemGuid": f"I-{iid}", "EventItemEventId": eid,
                      "EventItemAgendaSequence": k + 1, "EventItemMinutesSequence": k + 1,
                      "EventItemAgendaNumber": str(k + 1), "EventItemTitle": title,
                      "EventItemActionName": action, "EventItemActionText": None,
                      "EventItemPassedFlag": passed, "EventItemPassedFlagName": None,
                      "EventItemRollCallFlag": 1 if final_vote else 0, "EventItemTally": None,
                      "EventItemAgendaNote": None, "EventItemMinutesNote": None,
                      "EventItemMover": rnd.choice(MEMBERS) if final_vote else None, "EventItemSeconder": None,
                      "EventItemMatterId": mid_this, "EventItemMatterGuid": f"M-{mid_this}",
                      "EventItemMatterFile": f"{(r_day if reuse else day).year}{mid_this % 100000:05d}",
                      "EventItemMatterName": None, "EventItemMatterType": kind.title(),
                      "EventItemMatterStatus": "Passed" if passed == 1 else "Pending",
                      "EventItemLastModifiedUtc": ev["EventLastModifiedUtc"]}
                lst.append(it)
                if final_vote:
                    vs = []
                    for m in MEMBERS:
                        r = rnd.random()
                        val = "Absent" if r < 0.04 else ("Abstain" if r < 0.05 else ("No" if (r < 0.10 or (m == "Member C" and r < 0.2)) else "Yes"))
                        vs.append({"VotePersonName": m, "VoteValueName": val, "VoteEventItemId": iid})
                    votes[iid] = vs
            items[eid] = lst
        d += dt.timedelta(days=7)


def build_offices():
    rid = 1
    for i, m in enumerate(MEMBERS):
        office_records.append({"OfficeRecordId": rid, "OfficeRecordFullName": m, "OfficeRecordBodyName": "Cincinnati City Council",
                               "OfficeRecordTitle": "Vice Mayor" if i == 0 else "Member", "OfficeRecordStartDate": "2024-01-02T00:00:00",
                               "OfficeRecordEndDate": "2027-12-31T00:00:00"}); rid += 1
        for j, (body, _) in enumerate(BODIES[1:]):
            if (i + j) % 2 == 0:
                office_records.append({"OfficeRecordId": rid, "OfficeRecordFullName": m, "OfficeRecordBodyName": body,
                                       "OfficeRecordTitle": "Chair" if (i + j) % 6 == 0 else "Member",
                                       "OfficeRecordStartDate": "2024-01-02T00:00:00", "OfficeRecordEndDate": "2027-12-31T00:00:00"}); rid += 1
    office_records.append({"OfficeRecordId": rid, "OfficeRecordFullName": "Member A", "OfficeRecordBodyName": "Major Projects & Smart Government",
                           "OfficeRecordTitle": "Chair", "OfficeRecordStartDate": "2020-01-02T00:00:00", "OfficeRecordEndDate": "2021-12-31T00:00:00"})


def parse_filter(f):
    conds = re.findall(r"(EventDate|EventLastModifiedUtc) (ge|le) datetime'([^']+)'", f or "")
    def ok(ev):
        for field, op, val in conds:
            v = ev[field][:19]
            if op == "ge" and not v >= val[:19]:
                return False
            if op == "le" and not v <= val[:19]:
                return False
        return True
    return ok


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        parts = u.path.strip("/").split("/")  # v1, client, ...
        if parts[2:] == ["events"]:
            ok = parse_filter(q.get("$filter"))
            rows = [e for e in events if ok(e)]
            skip, top = int(q.get("$skip", 0)), int(q.get("$top", 1000))
            return self.send_json(rows[skip:skip + top])
        if parts[2:] == ["officerecords"]:
            skip, top = int(q.get("$skip", 0)), int(q.get("$top", 1000))
            return self.send_json(office_records[skip:skip + top])
        if len(parts) == 5 and parts[2] == "events" and parts[4] == "eventitems":
            return self.send_json(items.get(int(parts[3]), []))
        if len(parts) == 5 and parts[2] == "eventitems" and parts[4] == "votes":
            return self.send_json(votes.get(int(parts[3]), []))
        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    build()
    build_offices()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    print(f"fake Legistar: {len(events)} events on :{port}", flush=True)
    http.server.ThreadingHTTPServer(("", port), H).serve_forever()

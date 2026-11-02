"""Download Cincinnati City Council meetings, agenda/minutes items and votes
from Legistar into one SQLite file (site/data/council.db).

    python scripts/collect.py                 # update (or build from scratch)
    python scripts/collect.py --retag         # just re-apply topics/departments
    python scripts/collect.py --max-events 20 # quick trial run

First run downloads everything since July 2020 (allow ~30-60 minutes).
Later runs only re-check meetings that changed, plus a window of recent and
upcoming meetings, so they take a minute or two.
"""
import argparse
import datetime as dt
import sys

import legistar
from common import (DEFAULT_DB, DEPT_RX, SETTINGS, TOPIC_RX, classify_kind, connect, finalize,
                    get_meta, is_noise, parse_sponsors, rebuild_search_index, rx, set_meta, tag, vote_value)

INCLUDE_BODIES = rx(SETTINGS["include_bodies_regex"])
COUNCIL_BODY = rx(SETTINGS["council_body_regex"])
VOTE_BODIES = rx(SETTINGS["vote_bodies_regex"])
FINAL_RX = rx(SETTINGS["final_passage_regex"])
NOT_FINAL_RX = rx(SETTINGS["not_final_regex"])
SITE = SETTINGS["legistar_site"].rstrip("/")


def legislation_url(matter_id):
    """Link to the item's Legistar page. The API's GUID doesn't match the one the public
    site expects ("Invalid parameters!"), so use Legistar's gateway, which needs only the ID
    and redirects to the right LegislationDetail page."""
    return f"{SITE}/gateway.aspx?M=L&ID={matter_id}" if matter_id else None


def now_utc():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def clean(s):
    return (s or "").strip()


def outcome_for(item, date, today):
    action = clean(item.get("EventItemActionName"))
    passed = item.get("EventItemPassedFlag")
    low = action.lower()
    if passed == 1:
        return ("Adopted" if "adopt" in low else "Passed"), "passed"
    if passed == 0:
        return "Failed", "failed"
    if "refer" in low:
        return "Referred to committee", "referred"
    if "hold" in low or "held" in low:
        return "Held", "held"
    if "file" in low:
        return "Filed", "filed"
    if date >= today and not action:
        return "On upcoming agenda", "scheduled"
    return (action or "On agenda"), "other"


def save_event(con, ev):
    body = clean(ev.get("EventBodyName"))
    date = clean(ev.get("EventDate"))[:10]
    con.execute("""
        INSERT INTO meetings(event_id, guid, body, is_council, date, time, location, agenda_url,
                             minutes_url, agenda_status, minutes_status, video_url, page_url, last_modified)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(event_id) DO UPDATE SET
          guid=excluded.guid, body=excluded.body, is_council=excluded.is_council, date=excluded.date,
          time=excluded.time, location=excluded.location, agenda_url=excluded.agenda_url,
          minutes_url=excluded.minutes_url, agenda_status=excluded.agenda_status,
          minutes_status=excluded.minutes_status, video_url=excluded.video_url,
          page_url=excluded.page_url, last_modified=excluded.last_modified
    """, (ev["EventId"], ev.get("EventGuid"), body, 1 if COUNCIL_BODY.search(body) else 0, date,
          clean(ev.get("EventTime")), clean(ev.get("EventLocation")), ev.get("EventAgendaFile"),
          ev.get("EventMinutesFile"), clean(ev.get("EventAgendaStatusName")),
          clean(ev.get("EventMinutesStatusName")), ev.get("EventVideoPath"),
          ev.get("EventInSiteURL"), ev.get("EventLastModifiedUtc")))
    return body, date


def save_items(con, ev, body, date, today, seen_at, stats):
    is_council = 1 if COUNCIL_BODY.search(body) else 0
    items = legistar.event_items(ev["EventId"])
    keep = []
    for it in items:
        title = clean(it.get("EventItemTitle"))
        if not it.get("EventItemMatterFile") and is_noise(title, it.get("EventItemMatterId"), it.get("EventItemActionName")):
            continue  # section headers like "ROLL CALL" and boilerplate lines
        item_id = it["EventItemId"]
        keep.append(item_id)
        action = clean(it.get("EventItemActionName"))
        outcome, cat = outcome_for(it, date, today)
        is_final = is_final_vote(is_council, it.get("EventItemPassedFlag"), action)
        text = " ".join([title, clean(it.get("EventItemMatterName")), clean(it.get("EventItemAgendaNote")),
                         clean(it.get("EventItemMinutesNote")), clean(it.get("EventItemActionText"))])
        matter_id = it.get("EventItemMatterId")
        matter_url = legislation_url(matter_id)
        con.execute("""
            INSERT INTO items(item_id, event_id, date, body, is_council, seq, agenda_number, matter_id,
              matter_file, matter_name, matter_type, matter_status, matter_url, kind, title, action,
              action_text, agenda_note, minutes_note, passed, outcome, outcome_cat, is_final, tally,
              mover, seconder, sponsors, departments, topics, first_seen, last_modified)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(item_id) DO UPDATE SET
              event_id=excluded.event_id, date=excluded.date, body=excluded.body,
              is_council=excluded.is_council, seq=excluded.seq, agenda_number=excluded.agenda_number,
              matter_id=excluded.matter_id, matter_file=excluded.matter_file,
              matter_name=excluded.matter_name, matter_type=excluded.matter_type,
              matter_status=excluded.matter_status, matter_url=excluded.matter_url, kind=excluded.kind,
              title=excluded.title, action=excluded.action, action_text=excluded.action_text,
              agenda_note=excluded.agenda_note, minutes_note=excluded.minutes_note,
              passed=excluded.passed, outcome=excluded.outcome, outcome_cat=excluded.outcome_cat,
              is_final=excluded.is_final, tally=excluded.tally, mover=excluded.mover,
              seconder=excluded.seconder, sponsors=excluded.sponsors,
              departments=excluded.departments, topics=excluded.topics,
              last_modified=excluded.last_modified
        """, (item_id, ev["EventId"], date, body, is_council,
              it.get("EventItemMinutesSequence") or it.get("EventItemAgendaSequence"),
              clean(it.get("EventItemAgendaNumber")), matter_id, clean(it.get("EventItemMatterFile")),
              clean(it.get("EventItemMatterName")), clean(it.get("EventItemMatterType")),
              clean(it.get("EventItemMatterStatus")), matter_url,
              classify_kind(it.get("EventItemMatterType"), title), title, action,
              clean(it.get("EventItemActionText")), clean(it.get("EventItemAgendaNote")),
              clean(it.get("EventItemMinutesNote")), it.get("EventItemPassedFlag"), outcome, cat, is_final,
              clean(it.get("EventItemTally")), clean(it.get("EventItemMover")),
              clean(it.get("EventItemSeconder")), parse_sponsors(title),
              "; ".join(tag(text, DEPT_RX)), "; ".join(tag(text, TOPIC_RX)), seen_at,
              it.get("EventItemLastModifiedUtc")))
        stats["items"] += 1

        wants_votes = (date < today and VOTE_BODIES.search(body)
                       and (it.get("EventItemRollCallFlag") == 1 or it.get("EventItemPassedFlag") is not None
                            or clean(it.get("EventItemTally"))))
        if wants_votes:
            save_votes(con, item_id)
            stats["vote_lists"] += 1

    # Remove items that disappeared from a re-published agenda.
    if keep:
        marks = ",".join("?" * len(keep))
        gone = [r[0] for r in con.execute(
            f"SELECT item_id FROM items WHERE event_id = ? AND item_id NOT IN ({marks})", [ev["EventId"], *keep])]
    else:
        gone = [r[0] for r in con.execute("SELECT item_id FROM items WHERE event_id = ?", (ev["EventId"],))]
    for g in gone:
        con.execute("DELETE FROM votes WHERE item_id = ?", (g,))
        con.execute("DELETE FROM items WHERE item_id = ?", (g,))


def save_votes(con, item_id):
    rows = legistar.votes(item_id)
    con.execute("DELETE FROM votes WHERE item_id = ?", (item_id,))
    yes = no = 0
    for v in rows:
        person = clean(v.get("VotePersonName"))
        if not person:
            continue
        val = vote_value(v.get("VoteValueName"))
        yes += val == "yes"
        no += val == "no"
        con.execute("INSERT OR REPLACE INTO votes(item_id, person, value_raw, value) VALUES(?,?,?,?)",
                    (item_id, person, clean(v.get("VoteValueName")), val))
    if rows:
        con.execute("UPDATE items SET yes_count = ?, no_count = ?, tally = ? WHERE item_id = ?",
                    (yes, no, f"{yes}–{no}", item_id))
        con.execute("UPDATE items SET outcome = outcome || ' ' || ? WHERE item_id = ? "
                    "AND outcome_cat IN ('passed','failed')", (f"{yes}–{no}", item_id))


def is_final_vote(is_council, passed, action):
    return 1 if (is_council and passed is not None and FINAL_RX.search(action or "")
                 and not NOT_FINAL_RX.search(action or "")) else 0


def retag(con):
    """Re-apply everything that depends on config/ files, so edits take effect on old items too."""
    noise = [r[0] for r in con.execute("SELECT item_id, title, matter_id, action, matter_file FROM items")
             if not r[4] and is_noise(r[1], r[2], r[3])]
    for item_id in noise:
        con.execute("DELETE FROM votes WHERE item_id = ?", (item_id,))
        con.execute("DELETE FROM items WHERE item_id = ?", (item_id,))
    if noise:
        print(f"Removed {len(noise)} section headers / boilerplate lines.")
    con.execute("UPDATE items SET matter_url = ? || '/gateway.aspx?M=L&ID=' || matter_id "
                "WHERE matter_id IS NOT NULL", (SITE,))
    rows = con.execute("SELECT item_id, title, matter_name, agenda_note, minutes_note, action_text, "
                       "matter_type, is_council, passed, action FROM items").fetchall()
    for r in rows:
        text = " ".join(x or "" for x in (r["title"], r["matter_name"], r["agenda_note"],
                                         r["minutes_note"], r["action_text"]))
        con.execute("UPDATE items SET topics=?, departments=?, sponsors=?, kind=?, is_final=? WHERE item_id=?",
                    ("; ".join(tag(text, TOPIC_RX)), "; ".join(tag(text, DEPT_RX)),
                     parse_sponsors(r["title"]), classify_kind(r["matter_type"], r["title"]),
                     is_final_vote(r["is_council"], r["passed"], r["action"]), r["item_id"]))
    print(f"Re-tagged {len(rows)} items.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--max-events", type=int, default=0, help="stop after this many meetings (testing)")
    ap.add_argument("--retag", action="store_true", help="only re-apply topic/department tags")
    ap.add_argument("--full", action="store_true", help="re-check every meeting since start_date")
    args = ap.parse_args()

    con = connect(args.db)
    if args.retag:
        retag(con)
        rebuild_search_index(con)
        finalize(con)
        return

    started = now_utc()
    seen_at = started.isoformat()
    today = started.astimezone(dt.timezone(dt.timedelta(hours=-5))).date().isoformat()
    last_sync = None if args.full else get_meta(con, "last_sync")
    first_run = get_meta(con, "last_sync") is None

    if last_sync:
        since = (dt.datetime.fromisoformat(last_sync) - dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S")
        win_from = (started - dt.timedelta(days=SETTINGS["refresh_days_back"])).date().isoformat()
        win_to = (started + dt.timedelta(days=SETTINGS["refresh_days_ahead"])).date().isoformat()
        filters = [f"EventLastModifiedUtc ge {legistar.odata_date(since)}",
                   f"EventDate ge {legistar.odata_date(win_from)} and EventDate le {legistar.odata_date(win_to)}"]
        print(f"Updating: meetings changed since {since[:10]} plus {win_from} to {win_to}")
    else:
        filters = [f"EventDate ge {legistar.odata_date(SETTINGS['start_date'])}"]
        print(f"Full download of meetings since {SETTINGS['start_date']} - this takes a while the first time.")

    events = {}
    for f in filters:
        for ev in legistar.events(f):
            if ev.get("EventDate", "")[:10] >= SETTINGS["start_date"]:
                events[ev["EventId"]] = ev
    todo = sorted(events.values(), key=lambda e: e.get("EventDate", ""))
    todo = [e for e in todo if INCLUDE_BODIES.search(clean(e.get("EventBodyName")))]
    if args.max_events:
        todo = todo[-args.max_events:]
    print(f"{len(todo)} meetings to check")

    stats = {"items": 0, "vote_lists": 0}
    for n, ev in enumerate(todo, 1):
        body, date = save_event(con, ev)
        save_items(con, ev, body, date, today, seen_at, stats)
        if n % 25 == 0 or n == len(todo):
            con.commit()
            print(f"  {n}/{len(todo)} meetings · {stats['items']} items · {stats['vote_lists']} vote lists "
                  f"· {legistar.request_count} API requests", flush=True)

    retag(con)  # re-apply topic/department lists in case they were edited
    rebuild_search_index(con)
    set_meta(con, "last_sync", started.isoformat())
    set_meta(con, "last_run_seen_at", seen_at)
    set_meta(con, "last_run_was_first", "1" if first_run else "0")
    set_meta(con, "updated_at", now_utc().isoformat())
    con.commit()
    finalize(con)
    total = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    meetings = con.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    print(f"Done: {meetings} meetings, {total} items in {args.db}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

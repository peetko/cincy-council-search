"""Email people when a watched word or phrase shows up in a newly posted
agenda item (an upcoming agenda, or new minutes).

The watch list is private: it lives in a GitHub secret named ALERT_WATCHLIST
(or, on your own computer, in config/watchlist.local.json, which is never
uploaded). Format:

    {"subscribers": [
        {"email": "you@example.com",
         "terms": ["Office of Data and Performance Analytics", "Kroger", "tax abatement*"]}
    ]}

Terms are whole words/phrases, case-insensitive; a trailing * matches endings.

Email is sent through any SMTP account. Free option: a Gmail account with an
"app password" (see README). Settings come from environment variables /
GitHub secrets: SMTP_USER, SMTP_PASSWORD, and optionally SMTP_HOST
(default smtp.gmail.com), SMTP_PORT (465), ALERT_FROM, SITE_URL.

    python scripts/alerts.py --dry-run        # print instead of sending
"""
import argparse
import collections
import html
import json
import os
import re
import smtplib
import urllib.parse
import ssl
import sys
from email.message import EmailMessage

from common import CONFIG_DIR, DEFAULT_DB, _term_pattern, connect, get_meta


def load_watchlist():
    raw = os.environ.get("ALERT_WATCHLIST", "").strip()
    if not raw:
        local = os.path.join(CONFIG_DIR, "watchlist.local.json")
        if os.path.exists(local):
            with open(local, encoding="utf-8") as f:
                raw = f.read()
    if not raw:
        return []
    data = json.loads(raw)
    subs = data.get("subscribers", data if isinstance(data, list) else [])
    return [s for s in subs if s.get("email") and s.get("terms")]


def new_items(con):
    seen_at = get_meta(con, "last_run_seen_at")
    if not seen_at or get_meta(con, "last_run_was_first") == "1":
        return []  # the very first download is history, not news
    return con.execute("""
        SELECT i.*, m.agenda_url, m.minutes_url, m.page_url, m.time
        FROM items i JOIN meetings m ON m.event_id = i.event_id
        WHERE i.first_seen = ?
        ORDER BY i.date, i.body, i.seq""", (seen_at,)).fetchall()


def item_text(r):
    return " ".join(x or "" for x in (r["title"], r["matter_name"], r["agenda_note"],
                                     r["minutes_note"], r["action_text"], r["matter_file"]))


def build_email(sub, matches, site_url):
    total = sum(len(v) for v in matches.values())
    subject = (f"Council agenda alert: {', '.join(list(matches)[:3])}"
               f"{' and more' if len(matches) > 3 else ''} ({total} item{'s' if total != 1 else ''})")
    text_lines, html_parts = [], []
    for term, rows in matches.items():
        text_lines.append(f"== {term} ==")
        html_parts.append(f"<h2 style='font:700 18px sans-serif;margin:24px 0 8px'>“{html.escape(term)}”</h2>")
        for r in rows:
            title = r["title"] if len(r["title"]) < 600 else r["title"][:600] + "…"
            when = f"{r['date']} {r['time'] or ''}".strip()
            links = [("Agenda", r["agenda_url"]), ("Legislation page", r["matter_url"]),
                     ("Meeting page", r["page_url"])]
            if site_url:
                links.append(("Search the record", f"{site_url.rstrip('/')}/?q={urllib.parse.quote(term)}"))
            links = [(n, u) for n, u in links if u]
            text_lines += [f"- {r['body']} · {when} · {r['matter_file'] or ''} {r['outcome'] or ''}",
                           f"  {title}"] + [f"  {n}: {u}" for n, u in links] + [""]
            pat = re.compile(_term_pattern(term), re.IGNORECASE)
            safe = html.escape(title)
            safe = pat.sub(lambda m: f"<mark>{m.group(0)}</mark>", safe)
            html_parts.append(
                "<div style='border:1px solid #dfe3e8;border-radius:10px;padding:12px 14px;margin:0 0 10px;"
                "font:15px/1.5 sans-serif;color:#0f1b2d'>"
                f"<div style='font-size:13px;color:#4a5566'><b>{html.escape(r['body'])}</b> · {html.escape(when)}"
                f" · {html.escape(r['matter_file'] or '')} · {html.escape(r['outcome'] or '')}</div>"
                f"<p style='margin:6px 0'>{safe}</p>"
                + " · ".join(f"<a href='{html.escape(u)}'>{html.escape(n)}</a>" for n, u in links)
                + "</div>")
    footer = ("You get this because your email is on the Juncta Lucent watch list. "
              "Reply to the sender to change your terms or stop these emails.")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["To"] = sub["email"]
    msg.set_content("\n".join(text_lines + ["", footer]))
    msg.add_alternative(
        "<div style='max-width:680px'>"
        "<p style='font:15px sans-serif;color:#0f1b2d'>New Cincinnati City Council items matching your watch list:</p>"
        + "".join(html_parts)
        + f"<p style='font:12px sans-serif;color:#5b6778;margin-top:24px'>{footer}</p></div>",
        subtype="html")
    return msg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--dry-run", action="store_true", help="print the emails instead of sending")
    args = ap.parse_args()

    subs = load_watchlist()
    if not subs:
        print("No watch list set (ALERT_WATCHLIST) - skipping alerts.")
        return
    con = connect(args.db)
    rows = new_items(con)
    print(f"{len(rows)} new items this run; {len(subs)} subscriber(s).")
    if not rows:
        return

    user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    dry = args.dry_run or not (user and password)
    if dry and not args.dry_run:
        print("SMTP_USER / SMTP_PASSWORD not set - printing emails instead of sending.")
    site_url = os.environ.get("SITE_URL", "")

    outgoing = []
    for sub in subs:
        matches = collections.OrderedDict()
        for term in sub["terms"]:
            pat = re.compile(_term_pattern(term), re.IGNORECASE)
            hits = [r for r in rows if pat.search(item_text(r))]
            if hits:
                matches[term] = hits
        if matches:
            msg = build_email(sub, matches, site_url)
            msg["From"] = os.environ.get("ALERT_FROM") or user or "juncta-lucent@localhost"
            outgoing.append(msg)

    if not outgoing:
        print("No matches today.")
        return
    if dry and not args.dry_run:
        # GitHub's run logs are public; the watch list is private. Say how many, nothing more.
        print(f"{len(outgoing)} alert email(s) matched but were not sent. "
              "Run with --dry-run on your own computer to see them.")
        return
    if dry:
        for m in outgoing:
            print("-" * 60)
            print(f"To: {m['To']}\nSubject: {m['Subject']}\n")
            print(m.get_body(("plain",)).get_content()[:3000])
        return

    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as s:
        s.login(user, password)
        for m in outgoing:
            s.send_message(m)
            print(f"Sent alert to subscriber #{outgoing.index(m) + 1}")  # no addresses in public logs


if __name__ == "__main__":
    try:
        main()
    except json.JSONDecodeError as e:
        print(f"ALERT_WATCHLIST is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)

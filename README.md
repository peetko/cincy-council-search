# Council Record

A free, searchable record of Cincinnati City Council and committee meetings since July 2020, with running statistics and optional email alerts when a subject you care about shows up on an agenda.

- **Coming up at City Hall**: the next two weeks of Council and committee meetings; click one to see its agenda.
- **Search** every agenda and minutes item, grouped into one card per piece of legislation with its history (committee → Council → final vote). Every card has a shareable link, a "copy citation" button, and any result list downloads as CSV. Search covers ordinances, resolutions, motions, communications. Filter by type, meeting body, sponsor, department, outcome and date. Each result links to the minutes or agenda PDF, the Legistar legislation page, and the roll-call votes.
- **Running stats**: what's passed this year compared with last year, which topics are coming up more or less often (month, quarter or year), and how each member voted.
- **Split votes**: Council votes are usually unanimous, so the site makes the exceptions easy to find. Filter to any split vote, or click a member's "No / abstain" count to see exactly what they opposed.
- **Built-in explainers**: hover over or tap the ⓘ icons, item types and outcomes to learn how Council works: what an ordinance is, what "passed as an emergency" means, the three-readings rule, and more.
- **Alerts**: emails when a watched phrase (e.g. "Kroger") appears on a newly posted agenda.

Everything runs on free GitHub services. There's no server to rent.

## How it works (the short version)

```
Legistar public API ──► scripts/collect.py ──► site/data/council.db (SQLite)
                                               │
                    scripts/build_stats.py ────┼──► site/data/stats.json
                    scripts/alerts.py ─────────┘──► emails (optional)

GitHub Actions runs all of that every morning, then publishes /site to GitHub Pages.
```

- **Legistar** is the system the City uses to publish agendas, minutes and legislation. It has a free public API at `webapi.legistar.com/v1/cincinnatioh/...`.
- **SQLite** is a whole database in a single file. Its built-in *full-text search* keeps a word index, like the index at the back of a book, so searches are instant.
- **The website is just HTML, CSS and JavaScript.** A small library in `site/vendor/` (sql.js-httpvfs) runs SQLite *inside the browser* and downloads only the few pieces of the database file each search needs, not the whole file.
- **GitHub Actions** is GitHub's free scheduled-job runner, and **GitHub Pages** is its free web hosting. Both are free for public repositories.

## Set it up (about 15 minutes, plus a 30–60 minute first download)

1. **Create a public repository** on GitHub (this one is `cincy-council-search`).
2. **Upload these files** (already done for this repo). For a fresh copy, the easiest way is [GitHub Desktop](https://desktop.github.com): clone the empty repo, copy this folder's contents into it, then Commit and Push.
   *If you use the website's "Upload files" instead:* your computer may hide the `.github` folder. In that case, create the workflow by hand: **Add file → Create new file**, name it `.github/workflows/update.yml`, and paste in that file's contents.
3. **Turn on Pages:** repo **Settings → Pages → Build and deployment → Source: GitHub Actions**.
4. **Run the first update:** **Actions** tab → **Update council data** → **Run workflow**. The first run downloads about six years of meetings, so it takes a while. Later daily runs take a minute or two.
5. **Open your site:** <https://peetko.github.io/cincy-council-search/>
6. **Check the data:** open the finished run in the Actions tab and expand the **Data check** step. It lists the meeting bodies, vote words and actions it found, and warns you if a setting in `config/settings.json` needs adjusting (see below).
7. **Optional footer line:** `site/index.html` has a commented-out "ask to be added to the watch list" line. Add your contact and uncomment it if you want people to request alerts.

## Email alerts (optional, free)

Each daily run looks at agenda items it has **never seen before**, such as newly posted agendas for upcoming meetings or newly published minutes. It checks them against a private watch list and sends one digest email per person who has matches.

1. **An email account to send from.** A free Gmail account works. Turn on 2-Step Verification, then create an **App password** at <https://myaccount.google.com/apppasswords>. That 16-character password lets a script send mail without your real password.
2. In your repo: **Settings → Secrets and variables → Actions → New repository secret**, and add:
   - `SMTP_USER`: the Gmail address
   - `SMTP_PASSWORD`: the app password
   - `ALERT_WATCHLIST`: who gets what, as JSON. For example:
     ```json
     {"subscribers": [
       {"email": "you@example.com", "terms": ["Office of Data and Performance Analytics", "Kroger"]},
       {"email": "friend@example.com", "terms": ["Northside", "tax abatement*"]}
     ]}
     ```
   Terms are whole words or phrases and ignore capitalization. A trailing `*` matches word endings.
3. That's it. *Secrets* stay private even in a public repo. They never appear on the site or in the run logs, and the logs don't print addresses.

To add or remove someone, edit the `ALERT_WATCHLIST` secret. (A public self-service sign-up form would need a paid server, so this version keeps the list in your hands.) Gmail's free limit is several hundred emails a day, which is far more than this needs. Any other SMTP service also works; set `SMTP_HOST` as an extra secret if it isn't Gmail.

To test without sending anything: create `config/watchlist.local.json` in the same JSON format (git ignores it) and run `python scripts/alerts.py --dry-run`.

## Customizing

All the knobs are plain JSON files in `config/`. Edit them right on GitHub (pencil icon); the next run applies the changes.

| File | What it controls |
|---|---|
| `config/topics.json` | Topics for the "coming up more / less" chart, each defined by keywords. |
| `config/departments.json` | Department names and the words that identify them, used for the Department filter. |
| `config/settings.json` | Which meeting counts as full Council, which actions count as a final vote, how vote words (Yea/Nay/Absent…) map to yes/no/absent, and how far back to refresh. |

Colors and fonts live at the top of `site/styles.css` (Cincinnati flag navy `#002673` and red `#C70F2E`). The tooltip explanations are in `site/glossary.js`, one line per term, so you can reword them without touching any code.

## Run it on your own computer (optional)

You need Python 3.10 or newer. There's nothing to install: the scripts use only Python's standard library.

```bash
python scripts/collect.py --max-events 30   # quick trial: the 30 most recent meetings
python scripts/build_stats.py
python scripts/report.py                    # data check
python scripts/split_db.py                  # cut the database into pieces for the website
python scripts/serve.py                     # then open http://localhost:8000
```

Use `scripts/serve.py` rather than `python -m http.server`: the page reads the database in small "range" requests, and Python's built-in server doesn't support those.

## Notes and limits

- **What the minutes contain:** Cincinnati's minutes are *action* minutes, meaning the items, actions and votes. That's exactly what this indexes, item by item. Debate isn't in the minutes; it's only in the meeting videos (CitiCable and YouTube).
- **The stats are a guide, not an official count.** Topic and department tags come from keyword matching, and "final passage" is recognized from Legistar's action names. Run the Data check after the first run and adjust `config/settings.json` if anything looks off.
- **This is not an official City record.** The Clerk of Council keeps the official minutes.
- If a daily run fails (for example, Legistar is down), the site keeps serving the previous day's data and the next run catches up.

## Files

```
.github/workflows/update.yml   the daily job
config/                        settings, topic and department keyword lists
scripts/collect.py             download from Legistar into SQLite
scripts/build_stats.py         compute site/data/stats.json
scripts/alerts.py              email digests for watched terms
scripts/report.py              data check
scripts/split_db.py            cut the database into pieces the website can read
scripts/serve.py               local preview server
site/                          the website (index.html, styles.css, app.js, glossary.js, vendor/)
tests/fake_legistar.py         a fake Legistar API used for testing
```

The in-browser SQLite library in `site/vendor/` is [sql.js-httpvfs](https://github.com/phiresky/sql.js-httpvfs) (Apache-2.0 license, included).

/* Plum Street Ledger — page logic.
 *
 * How it works, briefly:
 *  - data/stats.json holds the pre-computed numbers for the side panel and charts.
 *  - data/council.db is a SQLite database. The library in vendor/ runs SQLite
 *    inside the browser and fetches only the small pieces of the file a query
 *    needs (a few hundred KB), so the page stays fast even as the file grows.
 */
(function () {
  "use strict";

  const PAGE = 20;
  const pageSize = () => (!state.q.trim() && !anyFilter()) ? 10 : PAGE;
  const $ = (id) => document.getElementById(id);
  const state = { q: "", kind: "", body: "", sponsor: "", dept: "", outcome: "", vote: "", topic: "", hood: "", date: "", file: "", item: "", meeting: "", member: "", sort: "newest", offset: 0, period: "year", docs: "" };
  const FILTERS = ["kind", "body", "sponsor", "dept", "topic", "hood", "outcome", "vote", "date"];
  const FOCUS = ["file", "item", "meeting", "member"];  // set by links (a permalink or a meeting), not by dropdowns
  let stats = null;
  let dbPromise = null;
  let dbVersion = null, lastVersionCheck = 0;

  // Before searching, make sure the database hasn't been republished since this page opened
  // (a tiny request, at most once a minute). Mixing old and new pieces of the file can give
  // errors or silently empty results, so reconnect when the version changes.
  async function ensureFreshDb() {
    if (!dbPromise || Date.now() - lastVersionCheck < 60000) return;
    lastVersionCheck = Date.now();
    try {
      const r = await fetch("data/db-info.json", { cache: "no-cache" });
      if (!r.ok) return;
      const info = await r.json();
      if (dbVersion && info.version && info.version !== dbVersion) await reconnect();
    } catch (e) { /* offline or similar: carry on with what we have */ }
  }

  // ------------------------------------------------------------- helpers
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = (iso) => {
    if (!iso) return "";
    const d = new Date(iso + "T12:00:00");
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  };
  const today = () => (stats && stats.today) || new Date().toISOString().slice(0, 10);
  const addDays = (iso, n) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
  const KIND_LABEL = { ordinance: "Ordinance", resolution: "Resolution", motion: "Motion", communication: "Communication", appointment: "Appointment", presentation: "Presentation", statement: "Statement", other: "Minutes note" };

  // Turn what someone typed into a safe SQLite full-text query.
  // "exact phrase" stays a phrase, word* matches endings, everything else is ANDed.
  // title:"…" (or title:word) matches only an item's title, the legislation's own text.
  function ftsQuery(input) {
    const parts = [];
    input = input.replace(/\btitle:(?:"([^"]*)"|(\S+))/gi, (_, ph, w) => {
      const words = (ph || w || "").match(/[\p{L}\p{N}]+/gu);
      if (words) parts.push('{title matter_name} : "' + words.join(" ") + '"');
      return " ";
    });
    const rest = input.replace(/"([^"]*)"/g, (_, p) => {
      const words = p.match(/[\p{L}\p{N}]+/gu);
      if (words) parts.push('"' + words.join(" ") + '"');
      return " ";
    });
    for (const raw of rest.split(/\s+/)) {
      const star = raw.endsWith("*");
      const words = raw.match(/[\p{L}\p{N}]+/gu);
      if (!words) continue;
      parts.push('"' + words.join(" ") + '"' + (star ? "*" : ""));
    }
    return parts.join(" ");
  }

  // ------------------------------------------------------------- database
  function db() {
    if (!dbPromise) {
      const base = new URL(".", location.href);
      // db-info.json (written by scripts/split_db.py) says how the database is split into
      // pieces and its exact size, so the page never has to ask the server for it.
      dbPromise = fetch("data/db-info.json", { cache: "no-cache" })
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null)
        .then((info) => {
          dbVersion = info && info.version;
          lastVersionCheck = Date.now();
          const cacheBust = (info && info.version) || (stats && stats.updated_at) || String(Date.now());
          const config = info
            ? { serverMode: "chunked", requestChunkSize: 65536, databaseLengthBytes: info.databaseLengthBytes,
                serverChunkSize: info.serverChunkSize, suffixLength: info.suffixLength,
                urlPrefix: new URL(info.urlPrefix, base).href, cacheBust }
            : { serverMode: "full", url: new URL("data/council.db", base).href, requestChunkSize: 4096, cacheBust };
          return createDbWorker([{ from: "inline", config }],
            new URL("vendor/sqlite.worker.js", base).href, new URL("vendor/sql-wasm.wasm", base).href);
        })
        .then((w) => w.db);
    }
    return dbPromise;
  }

  // The full text of legislation is a second, separate database (docs.db), opened only when
  // needed, so ordinary searches stay as fast as before.
  let docsPromise = null, docsAvailable = null;
  function docsDb() {
    if (!docsPromise) {
      const base = new URL(".", location.href);
      docsPromise = fetch("data/docs-info.json", { cache: "no-cache" })
        .then((r) => (r.ok ? r.json() : null)).catch(() => null)
        .then((info) => {
          docsAvailable = !!info;
          if (!info) return null;
          const config = { serverMode: "chunked", requestChunkSize: 65536, databaseLengthBytes: info.databaseLengthBytes,
            serverChunkSize: info.serverChunkSize, suffixLength: info.suffixLength,
            urlPrefix: new URL(info.urlPrefix, base).href, cacheBust: info.version };
          return createDbWorker([{ from: "inline", config }],
            new URL("vendor/sqlite.worker.js", base).href, new URL("vendor/sql-wasm.wasm", base).href).then((w) => w.db);
        });
    }
    return docsPromise;
  }
  async function docsQuery(sql, args) {
    const d = await docsDb();
    if (!d) return [];
    try { return await d.query(sql, args); }
    catch (e) {
      if (!isStale(e)) throw e;
      docsPromise = null;  // republished while open: reconnect once
      const d2 = await docsDb();
      return d2 ? d2.query(sql, args) : [];
    }
  }
  // Legislation whose full text matches (ids only; the main search does the rest).
  const docMatches = (fts) => docsQuery(`SELECT rowid AS m FROM docs_fts WHERE docs_fts MATCH ? ORDER BY rank LIMIT 5000`, [fts])
    .then((rows) => rows.map((r) => Number(r.m)).filter(Number.isFinite));

  // Filters apply to the slim "facets" table (alias f), which keeps searches light.
  function whereClause() {
    const where = [], args = [];
    if (state.file) { where.push("f.matter_file = ?"); args.push(state.file); }
    if (state.item) { where.push("f.item_id = ?"); args.push(Number(state.item)); }
    if (state.meeting) { where.push("f.event_id = ?"); args.push(Number(state.meeting)); }
    if (state.kind === "@legislation") where.push("f.kind IN ('ordinance', 'resolution', 'motion')");
    else if (state.kind) { where.push("f.kind = ?"); args.push(state.kind); }
    if (state.body === "@council") where.push("f.is_council = 1");
    else if (state.body === "@committees") where.push("f.is_council = 0");
    else if (state.body) { where.push("f.body_id = (SELECT id FROM body_names WHERE name = ?)"); args.push(state.body); }
    if (state.sponsor) { where.push("(f.sponsors LIKE ? OR f.mover = ?)"); args.push("%" + state.sponsor + "%", state.sponsor); }
    if (state.dept) { where.push("f.dept_ids LIKE '%|' || (SELECT id FROM dept_names WHERE name = ?) || '|%'"); args.push(state.dept); }
    if (state.outcome) { where.push("f.outcome_cat = ?"); args.push(state.outcome); }
    if (state.topic) { where.push("f.topic_ids LIKE '%|' || (SELECT id FROM topic_names WHERE name = ?) || '|%'"); args.push(state.topic); }
    if (state.hood) { where.push("f.hood_ids LIKE '%|' || (SELECT id FROM hood_names WHERE name = ?) || '|%'"); args.push(state.hood); }
    if (state.member) {
      const prof = stats && stats.profiles && stats.profiles[state.member];
      const last = prof ? prof.last_name : state.member.split(" ").pop();
      where.push("f.is_council = 1 AND (f.sponsors LIKE ? OR f.mover = ?)"); args.push("%" + last + "%", state.member);
    }
    if (state.vote === "@split") where.push("f.dissent <> ''");
    else if (state.vote) { where.push("f.dissent LIKE ?"); args.push("%" + state.vote + "|%"); }
    const t = today();
    const year = Number(t.slice(0, 4));
    const month = Number(t.slice(5, 7));
    const qStart = String(3 * Math.floor((month - 1) / 3) + 1).padStart(2, "0");
    const ranges = {
      mtd: [t.slice(0, 8) + "01", t], qtd: [year + "-" + qStart + "-01", t],
      upcoming: [t, null], "90d": [addDays(t, -90), t], "12m": [addDays(t, -365), t],
      ytd: [year + "-01-01", t], lastyear: [(year - 1) + "-01-01", (year - 1) + "-12-31"],
    };
    // Custom ranges look like "2026-06-01..2026-06-30" (set by clicking a chart).
    const custom = /^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$/.exec(state.date || "");
    const r = custom ? [custom[1], custom[2]] : ranges[state.date];
    if (r) {
      if (r[0]) { where.push("f.date >= ?"); args.push(r[0]); }
      if (r[1]) { where.push("f.date <= ?"); args.push(r[1]); }
    }
    return { where, args };
  }

  const COLS = `i.item_id, i.matter_id, i.date, i.body, i.kind, i.matter_file, i.title, i.outcome, i.outcome_cat, i.yes_count, i.action,
    (SELECT dissent FROM facets WHERE facets.item_id = i.item_id) AS dissent,
    i.sponsors, i.departments, i.matter_url, m.minutes_url, m.agenda_url, m.video_url, m.page_url`;

  // Results are grouped by piece of legislation (its Legistar file), so an ordinance that went
  // through committee and then Council appears once, with its history as a timeline.
  const KEY = "COALESCE(f.matter_id, -f.item_id)";
  const marks = (a) => a.map(() => "?").join(",");

  function searchParts(docMids) {
    const { where, args } = whereClause();
    const fts = ftsQuery(state.q);
    // The default "latest items" view only needs recent meetings; don't group all six years.
    if (!fts && !anyFilter()) { where.push("f.date >= ?"); args.push(addDays(today(), -90)); }
    let from, allArgs, order;
    if (fts) {
      from = "items_fts JOIN facets f ON f.item_id = items_fts.rowid";
      where.unshift("items_fts MATCH ?");
      allArgs = [fts, ...args];
      order = state.sort === "relevance" ? "MIN(score)" : "MAX(d) DESC, MAX(ic) DESC";
    } else {
      from = "facets f";
      allArgs = args;
      order = state.meeting ? "MIN(sq)" : "MAX(d) DESC, MAX(ic) DESC, MIN(sq)";
    }
    const W = where.length ? "WHERE " + where.join(" AND ") : "";
    // One row per matching item. "LIMIT -1" stops SQLite from merging this into the grouped
    // query below, which it must not do because bm25() can't run inside a GROUP BY.
    let hits = `(SELECT ${KEY} AS k, f.event_id AS ev, f.date AS d, f.is_council AS ic, f.seq AS sq`
      + (fts ? ", bm25(items_fts, 10.0, 2.0, 2.0, 2.0, 5.0, 3.0, 3.0, 1.0, 1.0, 0.5) AS score" : "")
      + ` FROM ${from} ${W} LIMIT -1)`;
    if (fts && docMids && docMids.length) {
      // Also legislation whose full text matches (ids are numbers from our own database, so they
      // can go straight into the SQL). These rank after matches in the agenda text itself.
      const W2 = "WHERE " + [`f.matter_id IN (${docMids.join(",")})`, ...where.slice(1)].join(" AND ");
      hits = `(SELECT * FROM ${hits} UNION ALL SELECT ${KEY}, f.event_id, f.date, f.is_council, f.seq, 0 FROM facets f ${W2} LIMIT -1)`;
      allArgs = [...allArgs, ...args];
    }
    return { fts, from, allArgs, order, W, hits };
  }

  async function runSearch(append) {
    const started = performance.now();
    $("results").classList.add("busy");
    if (append) { $("more").textContent = "Loading…"; }
    else { $("res-h").innerHTML = `<span class="searching"><span class="spinner" aria-hidden="true"></span>Searching…</span>`; $("timing").textContent = ""; }
    await ensureFreshDb();
    try {
      try { await runSearchInner(append); }
      catch (e) {
        if (!isStale(e)) throw e;
        // The data was republished while this page was open: reconnect and try once more.
        await reconnect();
        await runSearchInner(append);
      }
    } catch (e) {
      console.error(e);
      $("res-h").textContent = "Search error";
      $("spark").hidden = true;
      renderNothing("That search could not run. Try removing punctuation, or reload the page. (" + esc(e.message || e) + ")");
    } finally {
      $("results").classList.remove("busy");
      $("more").textContent = "Show more results";
      const secs = ((performance.now() - started) / 1000).toFixed(1);
      const total = stats ? stats.items_indexed.toLocaleString() : "all";
      $("timing").textContent = (state.q.trim() || anyFilter()) ? `Searched ${total} agenda and minutes items in ${secs} seconds` : "";
    }
  }

  let lastDocMids = new Set();
  async function runSearchInner(append) {
    const d = await db();
    if (state.q.trim() && !ftsQuery(state.q)) { renderNothing("Type at least one word or number."); return; }
    const ftsQ = ftsQuery(state.q);
    const useDocs = !!(state.docs && ftsQ && !/\{title/.test(ftsQ));
    const docMids = useDocs ? await docMatches(ftsQ).catch((e) => { console.error(e); return []; }) : null;
    lastDocMids = new Set(docMids || []);
    const { fts, allArgs, order, hits } = searchParts(docMids);
    try {
      // Step 1: which 20 pieces of legislation, in what order (reads only the index + facets).
      const groups = await d.query(`SELECT k FROM ${hits} GROUP BY k ORDER BY ${order} LIMIT ${pageSize()} OFFSET ${state.offset}`, allArgs);
      const keys = groups.map((g) => g.k);
      const mids = keys.filter((k) => k > 0), lone = keys.filter((k) => k < 0).map((k) => -k);
      // Step 2: every step in those pieces' histories, plus highlighted snippets of the matches.
      let steps = [], snips = [];
      if (keys.length) {
        // Only include the lists that have something in them: an empty "IN ()" makes SQLite
        // scan the whole items table, which means downloading most of the database.
        const conds = [], cargs = [];
        if (mids.length) { conds.push(`i.matter_id IN (${marks(mids)})`); cargs.push(...mids); }
        if (lone.length) { conds.push(`i.item_id IN (${marks(lone)})`); cargs.push(...lone); }
        steps = await d.query(conds.map((c) => `SELECT ${COLS} FROM items i JOIN meetings m ON m.event_id = i.event_id WHERE ${c}`)
          .join(" UNION ALL ") + " ORDER BY 2, 3", cargs).then((rows) => rows.sort((a, b) =>
            a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
        if (fts) {
          // Pick one matching item per card using the slim facets table, then build snippets for
          // just those items (snippet() reads the full text, so keep it to 20 rows).
          const fconds = [], fargs = [fts];
          if (mids.length) { fconds.push(`f.matter_id IN (${marks(mids)})`); fargs.push(...mids); }
          if (lone.length) { fconds.push(`f.item_id IN (${marks(lone)})`); fargs.push(...lone); }
          const hitRows = await d.query(`SELECT ${KEY} AS k, MIN(f.item_id) AS id FROM items_fts JOIN facets f ON f.item_id = items_fts.rowid
                                         WHERE items_fts MATCH ? AND (${fconds.join(" OR ")}) GROUP BY k`, fargs);
          const ids = hitRows.map((h) => h.id);
          if (ids.length) {
            snips = await d.query(`SELECT items_fts.rowid AS item_id, snippet(items_fts, -1, char(1), char(2), ' … ', 60) AS snip
                                   FROM items_fts WHERE items_fts MATCH ? AND items_fts.rowid IN (${marks(ids)})`, [fts, ...ids]);
            const kOf = new Map(hitRows.map((h) => [h.id, h.k]));
            snips.forEach((x) => { x.k = kOf.get(x.item_id); });
          }
        }
      }
      if (!append) {
        const [c] = await d.query(`SELECT COUNT(DISTINCT k) AS n, COUNT(DISTINCT ev) AS meetings FROM ${hits}`, allArgs);
        renderHead(c.n, c.meetings, !!fts);
        $("docs-offer").hidden = !(fts && !state.docs && docsAvailable && !/\{title/.test(fts));
        if (useDocs) $("res-sub").textContent += ", including the full text of legislation";
        if (fts) {
          const q = await d.query(`SELECT substr(d,1,4) AS y, (CAST(substr(d,6,2) AS INTEGER)+2)/3 AS q, COUNT(*) AS n
                                   FROM ${hits} GROUP BY y, q`, allArgs);
          renderSpark(q);
        } else {
          $("spark").hidden = true;
        }
        $("result-list").innerHTML = "";
        $("csv").hidden = !c.n;
        if (!c.n) renderNothing(fts ? "Nothing matches. Try fewer words, a word ending with *, or clear the filters." : "Nothing matches these filters.");
        $("more").hidden = c.n <= state.offset + pageSize();
        $("more").dataset.total = c.n;
      } else {
        $("more").hidden = Number($("more").dataset.total) <= state.offset + pageSize();
      }
      const byKey = new Map(keys.map((k) => [k, []]));
      steps.forEach((r) => { const k = r.matter_id || -r.item_id; if (byKey.has(k)) byKey.get(k).push(r); });
      const snipByKey = new Map();
      snips.forEach((x) => { if (!snipByKey.has(x.k) || /\u0001/.test(x.snip)) snipByKey.set(x.k, x.snip); });
      $("result-list").insertAdjacentHTML("beforeend", keys.map((k) => renderGroup(byKey.get(k), snipByKey.get(k))).join(""));
      enhanceWithDocs(mids, fts, snipByKey).catch((e) => console.error(e));
    } catch (e) {
      throw e;  // handled (and retried if the data was just updated) in runSearch
    }
  }

  // A daily update replaces the database file. A page opened before the update has cached
  // pieces of the old file, and mixing them with the new one looks like a damaged database.
  // Also covers fetch failures ("doXHR failed", "Couldn't load", HTTP 404/416): the old page
  // asked for a piece of the file that changed size or moved in the new version.
  const isStale = (e) => /malformed|disk image|not a database|file is not|xhr|couldn.t load|status: ?4\d\d|range/i.test(String(e && e.message || e));
  async function reconnect() {
    dbPromise = null;
    try { const r = await fetch("data/stats.json", { cache: "no-cache" }); if (r.ok) stats = await r.json(); } catch (e) { /* keep old stats */ }
    if (analysis) loadAnalysis();
    console.info("Plum Street Ledger: the data was updated while this page was open; reconnected.");
    $("timing").textContent = "The data was just updated; reconnecting…";
    return db();
  }
  function renderHead(n, meetings, isSearch) {
    const things = (x) => `${x.toLocaleString()} ${x === 1 ? "item" : "items"}`;
    let h;
    if (state.member) h = `${things(n)} sponsored by ${esc(state.member)}`;
    else if (state.file) h = `File ${esc(state.file)}`;
    else if (state.meeting && !state.member) h = esc($("res-h").dataset.meetingTitle || "Meeting agenda");
    else if (isSearch) h = `${n.toLocaleString()} result${n === 1 ? "" : "s"} for “${esc(state.q.trim())}”`;
    else if (state.vote === "@split") h = `${n.toLocaleString()} split vote${n === 1 ? "" : "s"}`;
    else if (state.vote) h = `${things(n)} where ${esc(state.vote)} voted no or abstained`;
    else h = anyFilter() ? `${things(n)} match` : "Latest agenda and minutes items";
    if (state.file && n === 0) h = `File ${esc(state.file)} not found`;
    $("res-h").innerHTML = h;
    $("res-sub").textContent = (isSearch || anyFilter()) ? `in ${meetings.toLocaleString()} meeting${meetings === 1 ? "" : "s"}`
      : `from ${meetings.toLocaleString()} meetings in the last 90 days and coming up`;
    $("sorter").hidden = !n;
    document.querySelectorAll("#sorter button[data-sort]").forEach((b) => { b.hidden = !isSearch; b.setAttribute("aria-pressed", String(b.dataset.sort === state.sort)); });
    document.querySelector("#sorter .sortlabel").hidden = !isSearch;
    document.querySelector('#sorter [data-tip="relevance"]').hidden = !isSearch;
  }

  function renderNothing(msg) {
    $("result-list").innerHTML = `<div class="card notice">${msg}</div>`;
    $("more").hidden = true;
  }

  const ICON_CHECK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12l5 5L20 7"/></svg>';
  const ICON_X = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  const ICON_ARROW = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="M13 6l6 6-6 6"/></svg>';
  const ICON_CAL = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>';

  // Long legislation is cut short in Legistar's title field and ends "(BALANCE ON FILE IN THE CLERK'S
  // OFFICE)": the rest is in the full document. Drop the phrase and show a link to it instead.
  const BALANCE_RX = /\s*\(?\s*balance\s+on\s+file\s+(?:in|with)\s+the\s+clerk['’]?s\s+office\s*\)?\s*\.?\s*$/i;
  const stripBalance = (t) => (t || "")
    .replace(/\s*\((?:statement|exhibit|attachment)s?\s+attached\)\s*\.?/gi, " ")
    .replace(BALANCE_RX, " …")
    .replace(/\s*\(\s*B(?:A(?:L(?:A(?:N(?:C(?:E[A-Z\s']*)?)?)?)?)?)?\s*…\s*$/, " …");  // snippet cut mid-phrase
  function balanceLink(r) {
    if (!BALANCE_RX.test(r.title || "")) return "";
    const label = "Rest of the text is in the full document";
    return r.matter_url
      ? `<a class="balance" href="${esc(r.matter_url)}" target="_blank" rel="noopener" data-tip="balance_on_file">${label} ↗</a>`
      : `<span class="balance" tabindex="0" data-tip="balance_on_file">${label}</span>`;
  }
  function highlight(snip, title) {
    let text = stripBalance(snip || title || "");
    if (!snip && text.length > 420) text = text.slice(0, 420) + " …";
    return esc(text).replace(/\u0001/g, "<mark>").replace(/\u0002/g, "</mark>");
  }

  // Highlight the searched words in full text (the database's own highlighting only covers snippets).
  function highlightTerms(text) {
    const terms = [];
    (state.q || "").replace(/\btitle:/gi, " ").replace(/"([^"]*)"|(\S+)/g, (_, ph, w) => {
      const words = (ph || w || "").match(/[\p{L}\p{N}]+/gu) || [];
      if (ph && words.length) terms.push(words.map((x) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\W+"));
      else words.forEach((x) => { if (x.length > 1 || /\d/.test(x)) terms.push(x.replace(/(ing|ed|es|s)$/i, "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&") || x); });
    });
    let html = esc(text);
    if (!terms.length) return html;
    const rx = new RegExp(`\\b(?:${terms.join("|")})[\\p{L}\\p{N}]*`, "giu");
    return html.replace(rx, (m) => `<mark>${m}</mark>`);
  }
  const isCut = (snip, title) => snip ? /^\s*…|…\s*$/.test(snip) || (title || "").length > snip.length + 40 : (title || "").length > 420;

  // "Show full text": the whole legislation text when we have it (from docs.db), otherwise the whole
  // title, plus any notes the minutes recorded at each step (fetched on demand).
  const VOTE_PHRASE = /\s*by the following vote\s*:?\s*$/i;
  function voteLine(rows) {
    const groups = {};
    rows.forEach((v) => { (groups[v.value] = groups[v.value] || []).push(v.person.split(" ").slice(-1)[0]); });
    const order = [["yes", "Yes"], ["no", "No"], ["abstain", "Abstained"], ["remote", "Remote"], ["absent", "Absent"], ["other", "Other"]];
    return order.filter(([k]) => groups[k]).map(([k, l]) => `<span class="v-${k}"><b>${l} ${groups[k].length}:</b> ${esc(groups[k].join(", "))}</span>`).join(" ");
  }
  async function toggleFullText(btn) {
    const card = btn.closest("article.result");
    const short = card.querySelector("p.snip"), full = card.querySelector(".fulltext");
    const open = full.hidden;
    full.hidden = !open; short.hidden = open;
    const ds = card.querySelector(".doc-snip");
    if (ds) ds.hidden = open;
    btn.textContent = open ? "Show less" : (btn.dataset.doc ? "Read the full legislation text" : "Show full text");
    btn.setAttribute("aria-expanded", String(open));
    if (open && !full.dataset.loaded) {
      full.dataset.loaded = "1";
      try {
        if (btn.dataset.doc) {
          const [doc] = await docsQuery("SELECT text, source, name, url FROM docs WHERE matter_id = ?", [Number(btn.dataset.doc)]);
          if (doc && doc.text) {
            const paras = doc.text.split(/\n{2,}/).map((t) => t.replace(/\s*\n\s*/g, " ").trim()).filter(Boolean);
            const named = doc.name ? " “" + esc(doc.name) + "”" : "";
            const from = doc.source === "ocr" ? `a scanned copy of the attached document${named}, read by text-recognition software, so expect some errors`
              : doc.source === "attachment" ? `the attached document${named}` : "Legistar's legislation text";
            full.querySelector(".title-text").outerHTML = `<div class="doctext">${paras.map((t) => `<p>${highlightTerms(t)}</p>`).join("")}</div>
              <span class="muted small">Full text from ${from}${doc.url ? ` (<a href="${esc(doc.url)}" target="_blank" rel="noopener">original</a>)` : ""}. Long documents are shortened; the legislation page has the official version.</span>`;
          }
        }
        const ids = btn.dataset.ids.split(",").map(Number);
        const d = await db();
        const rows = await d.query(`SELECT item_id, date, body, minutes_note, agenda_note, action_text FROM items WHERE item_id IN (${ids.map(() => "?").join(",")})
          AND (COALESCE(minutes_note, '') <> '' OR COALESCE(agenda_note, '') <> '' OR COALESCE(action_text, '') <> '') ORDER BY date`, ids);
        // "…carried by the following vote:" is followed by the roll call in the minutes PDF, not in
        // the data; fill it in from the recorded votes (or end the sentence if none were recorded).
        const needVotes = rows.filter((r) => [r.agenda_note, r.action_text, r.minutes_note].some((x) => VOTE_PHRASE.test(x || ""))).map((r) => r.item_id);
        const votes = {};
        if (needVotes.length) {
          (await d.query(`SELECT item_id, person, value FROM votes WHERE item_id IN (${needVotes.map(() => "?").join(",")}) ORDER BY person`, needVotes))
            .forEach((v) => { (votes[v.item_id] = votes[v.item_id] || []).push(v); });
        }
        const noteHtml = (r, x) => {
          if (!VOTE_PHRASE.test(x)) return `<p>${highlightTerms(x)}</p>`;
          const vs = votes[r.item_id];
          return vs && vs.length ? `<p>${highlightTerms(x.replace(/\s*:?\s*$/, ":"))}</p><p class="vote-line">${voteLine(vs)}</p>`
            : `<p>${highlightTerms(x.replace(VOTE_PHRASE, "."))}</p>`;
        };
        if (rows.length) {
          full.insertAdjacentHTML("beforeend", `<div class="notes"><b>Notes from the agendas and minutes</b>${rows.map((r) =>
            `<div class="note"><span class="muted small">${esc(fmtDate(r.date))} · ${esc(r.body)}</span>${[r.agenda_note, r.action_text, r.minutes_note].filter(Boolean)
              .filter((x, i, a) => a.indexOf(x) === i).map((x) => noteHtml(r, x)).join("")}</div>`).join("")}</div>`);
        }
      } catch (e) { console.error(e); }
    }
    if (!open) card.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  // After cards render: mark the ones whose full legislation text we have, and for full-text
  // searches show where in that text the words matched.
  let docsStats = null;
  async function enhanceWithDocs(mids, fts, snipByKey) {
    if (!mids.length || docsAvailable === false) return;
    if (docsStats && docsStats.summaries_public) {
      const sums = await docsQuery(`SELECT matter_id, summary FROM summaries WHERE status = 'ok' AND matter_id IN (${mids.join(",")})`, []).catch(() => []);
      sums.forEach((r) => {
        const card = document.querySelector(`#result-list article.result[data-mid="${Number(r.matter_id)}"]`);
        if (!card || card.querySelector(".ai-sum")) return;
        card.querySelector(".tags").insertAdjacentHTML("afterend",
          `<div class="ai-sum"><span class="ai-tag" tabindex="0" data-tip="ai_summary">AI summary</span><p>${esc(r.summary)}</p></div>`);
      });
    }
    const rows = await docsQuery(`SELECT matter_id FROM docs WHERE status = 'ok' AND matter_id IN (${mids.join(",")})`, []);
    const have = new Set(rows.map((r) => Number(r.matter_id)));
    let docSnips = new Map();
    const want = fts && state.docs ? mids.filter((m) => have.has(m) && lastDocMids.has(m)) : [];
    if (want.length) {
      (await docsQuery(`SELECT rowid AS m, snippet(docs_fts, 0, char(1), char(2), ' … ', 32) AS s FROM docs_fts
                        WHERE docs_fts MATCH ? AND rowid IN (${want.join(",")})`, [fts])).forEach((r) => docSnips.set(Number(r.m), r.s));
    }
    document.querySelectorAll("#result-list article.result[data-mid]").forEach((card) => {
      const mid = Number(card.dataset.mid);
      if (!have.has(mid) || card.dataset.docDone) return;
      card.dataset.docDone = "1";
      const btn = card.querySelector("[data-fulltext]");
      btn.dataset.doc = mid;
      btn.hidden = false;
      btn.textContent = "Read the full legislation text";
      const bal = card.querySelector(".balance");
      if (bal) bal.remove();  // the full text is right here now
      const ds = docSnips.get(mid);
      if (ds && !/\u0001/.test(snipByKey.get(mid) || "")) {
        card.querySelector("p.snip").insertAdjacentHTML("afterend",
          `<p class="doc-snip"><span class="doc-tag" tabindex="0" data-tip="docs_search">In the full text</span> ${highlight(ds)}</p>`);
      }
    });
  }

  const outcomeTipFor = (r) => /^Passed as emergency|^Emergency clause/.test(r.outcome || "") ? "passed_emergency"
    : /^Three readings/.test(r.outcome || "") ? "three_readings"
    : (GLOSSARY[r.outcome_cat] ? r.outcome_cat : "outcome");
  const OUTCOME_ICON = { passed: ICON_CHECK, failed: ICON_X, referred: ICON_ARROW, scheduled: ICON_CAL };

  function dissentBox(r) {
    if (!r || !r.dissent) return "";
    const no = [], ab = [];
    r.dissent.split("; ").forEach((x) => { const [who, v] = x.split("|"); (v === "no" ? no : ab).push(who); });
    return `<div class="dissent"><span class="lab"><b>Split vote</b><button type="button" class="tip" data-tip="dissent" aria-label="About split votes">i</button></span>`
      + (no.length ? `<span class="who"><b>Voted no:</b> ${esc(no.join(", "))}</span>` : "")
      + (ab.length ? `<span class="who"><b>Abstained:</b> ${esc(ab.join(", "))}</span>` : "") + `</div>`;
  }

  function citation(r) {
    const kind = KIND_LABEL[r.kind] || "Item";
    const what = r.outcome_cat === "passed" ? `${r.outcome.replace(/ \d+–\d+$/, "").toLowerCase()} ${fmtDate(r.date)}` : `${r.body}, ${fmtDate(r.date)}`;
    return `Cincinnati City Council, ${kind}${r.matter_file ? " " + r.matter_file : ""}, ${what}. ${permalink(r)}`;
  }
  const permalink = (r) => {
    const u = new URL(location.pathname, location.origin);
    if (r.matter_file) u.searchParams.set("file", r.matter_file); else u.searchParams.set("item", r.item_id);
    return u.href;
  };

  // One card per piece of legislation. `steps` are its agenda appearances in date order.
  function renderGroup(steps, snip) {
    if (!steps || !steps.length) return "";
    const decisive = [...steps].reverse().find((r) => r.outcome_cat === "passed" || r.outcome_cat === "failed");
    const latest = steps[steps.length - 1];
    const main = decisive || latest;
    const head = latest.outcome_cat === "scheduled" ? latest : main;
    const icon = OUTCOME_ICON[head.outcome_cat] || "";
    const links = [];
    if (main.matter_url) links.push(`<a href="${esc(main.matter_url)}" target="_blank" rel="noopener">Legislation page</a>`);
    if (main.minutes_url) links.push(`<a href="${esc(main.minutes_url)}" target="_blank" rel="noopener">Minutes PDF</a>`);
    else if (main.agenda_url) links.push(`<a href="${esc(main.agenda_url)}" target="_blank" rel="noopener">Agenda PDF</a>`);
    if (main.yes_count != null) links.push(`<button type="button" class="linkish" data-votes="${main.item_id}">Roll-call votes</button>`);
    if (main.video_url) links.push(`<a href="${esc(main.video_url)}" target="_blank" rel="noopener">Meeting video</a>`);
    links.push(`<button type="button" class="linkish" data-copy="${esc(permalink(main))}">Copy link</button>`);
    links.push(`<button type="button" class="linkish" data-copy="${esc(citation(main))}">Copy citation</button>`);
    const meta = [];
    if (main.sponsors) meta.push(`<span>Sponsor: ${esc(main.sponsors.replace(/; /g, ", "))}</span>`);
    if (main.departments) meta.push(`<span>${esc(main.departments.replace(/; /g, ", "))}</span>`);
    let timeline = "";
    if (steps.length > 1) {
      const shown = steps.length > 5 ? steps.slice(-5) : steps;
      timeline = `<details class="timeline"${steps.length <= 4 ? " open" : ""}><summary>History: ${steps.length} steps, ${esc(fmtDate(steps[0].date))} – ${esc(fmtDate(latest.date))}</summary><ol>`
        + (steps.length > shown.length ? `<li class="more-steps">${steps.length - shown.length} earlier step${steps.length - shown.length === 1 ? "" : "s"} not shown</li>` : "")
        + shown.map((r) => `<li><span class="when">${esc(fmtDate(r.date))}</span><span class="where">${esc(r.body)}</span>
            <span class="what" tabindex="0" data-tip="${outcomeTipFor(r)}">${esc(r.outcome || "")}</span>
            ${r.minutes_url ? `<a href="${esc(r.minutes_url)}" target="_blank" rel="noopener">minutes</a>` : (r.agenda_url ? `<a href="${esc(r.agenda_url)}" target="_blank" rel="noopener">agenda</a>` : "")}</li>`).join("")
        + `</ol></details>`;
    } else {
      meta.unshift(`<span><strong>${esc(fmtDate(main.date))}</strong> · ${esc(main.body)}</span>`);
    }
    return `<article class="card result" data-mid="${main.matter_id || ""}">
      <div class="tags">
        <span class="kind" tabindex="0" data-tip="${GLOSSARY[main.kind] ? main.kind : "other"}">${esc(KIND_LABEL[main.kind] || "Item")}</span>
        ${main.matter_file ? `<span class="file mono" tabindex="0" data-tip="file_number" style="cursor:help">${esc(main.matter_file)}</span>` : ""}
        ${head.outcome ? `<span class="outcome" tabindex="0" data-tip="${outcomeTipFor(head)}">${icon}${esc(head.outcome)}${steps.length > 1 ? ` · ${esc(fmtDate(head.date))}` : ""}</span>` : ""}
      </div>
      <p class="snip">${highlight(snip, main.title)}</p>
      <div class="fulltext" hidden><p class="title-text">${highlightTerms(stripBalance(main.title))}</p></div>
      <button type="button" class="linkish read-more" data-fulltext data-ids="${steps.map((r) => r.item_id).join(",")}" aria-expanded="false"${isCut(snip, main.title) ? "" : " hidden"}>Show full text</button>
      ${balanceLink(main)}
      ${dissentBox(decisive)}
      <div class="meta">${meta.join("")}</div>
      ${timeline}
      <div class="links">${links.join("")}</div>
      <div class="votes" id="votes-${main.item_id}" hidden></div>
    </article>`;
  }

  async function showVotes(itemId, btn) {
    const box = $("votes-" + itemId);
    if (!box.hidden) { box.hidden = true; return; }
    const d = await db();
    const rows = await d.query("SELECT person, value_raw, value FROM votes WHERE item_id = ? ORDER BY value, person", [Number(itemId)]);
    const groups = {};
    rows.forEach((v) => { (groups[v.value] = groups[v.value] || []).push(v.person); });
    const order = [["yes", "Yes"], ["no", "No"], ["abstain", "Abstained"], ["remote", "Took part remotely"], ["absent", "Absent"], ["other", "Other"]];
    box.innerHTML = rows.length
      ? order.filter(([k]) => groups[k]).map(([k, l]) => `<div style="font-size:14px"><b>${l} (${groups[k].length}):</b> ${esc(groups[k].join(", "))}</div>`).join("")
      : `<div style="font-size:14px" class="muted">No roll call recorded for this item.</div>`;
    box.style.cssText = "display:flex;flex-direction:column;gap:4px;border-top:1px solid var(--line);padding-top:10px";
    box.hidden = false;
  }

  function renderSpark(rows) {
    const counts = {};
    rows.forEach((r) => { counts[r.y + "Q" + r.q] = r.n; });
    const first = (stats && stats.first_date) || "2020-07-01";
    let y = Number(first.slice(0, 4)), q = Math.floor((Number(first.slice(5, 7)) - 1) / 3) + 1;
    const t = today(), ty = Number(t.slice(0, 4)), tq = Math.floor((Number(t.slice(5, 7)) - 1) / 3) + 1;
    const quarters = [];
    while (y < ty || (y === ty && q <= tq)) {
      quarters.push({ key: y + "Q" + q, label: `Q${q} ${y}`, n: counts[y + "Q" + q] || 0, y });
      q++; if (q > 4) { q = 1; y++; }
    }
    const max = Math.max(1, ...quarters.map((x) => x.n));
    $("spark-bars").innerHTML = quarters.map((x, i) =>
      `<div class="${i === quarters.length - 1 ? "last" : ""} ${x.n ? "" : "zero"}" style="height:${(x.n / max) * 100}%" title="${x.label}: ${x.n} item${x.n === 1 ? "" : "s"}"></div>`).join("");
    $("spark-bars").setAttribute("aria-label", "Items per quarter: " + quarters.filter((x) => x.n).map((x) => `${x.label} ${x.n}`).join(", "));
    const years = [...new Set(quarters.map((x) => x.y))];
    $("spark-axis").innerHTML = years.filter((_, i) => years.length < 8 || i % 2 === 0 || i === years.length - 1).map((yy) => `<span>${yy}</span>`).join("");
    $("spark-range").textContent = `${quarters[0].label} – ${quarters[quarters.length - 1].label} · latest quarter in dark blue`;
    $("spark").hidden = false;
  }

  // ------------------------------------------------------------- rendering: stats
  function renderKpis() {
    $("kpi-h").textContent = `${stats.today.slice(0, 4)} so far`;
    const TIPS = { "Ordinances passed": "kpi_ordinances", "Resolutions adopted": "kpi_resolutions", "Motions introduced": "kpi_motions",
      "Meetings with minutes": "kpi_meetings", "Average members present": "kpi_present", "Unanimous final votes": "kpi_unanimous" };
    const y = stats.today.slice(0, 4);
    const ytd = { from: y + "-01-01", to: stats.today, label: `${y} so far` };
    const GO = { "Ordinances passed": { kind: "ordinance", body: "@council", outcome: "passed", ...ytd },
      "Resolutions adopted": { kind: "resolution", body: "@council", outcome: "passed", ...ytd },
      "Motions introduced": { kind: "motion", body: "@council", ...ytd } };
    $("kpi-list").innerHTML = stats.kpis.map((k) => `<div class="card kpi${GO[k.label] ? " go" : ""}"${GO[k.label] ? ` data-go='${esc(JSON.stringify(GO[k.label]))}' title="Click to see them"` : ""}>
      <span class="l">${esc(k.label)}${TIPS[k.label] ? `<button type="button" class="tip" data-tip="${TIPS[k.label]}" aria-label="About ${esc(k.label.toLowerCase())}">i</button>` : ""}</span>
      <span class="v">${esc(k.value)}</span>
      <span class="d">${k.arrow ? `<span aria-hidden="true">${k.arrow}</span> ${k.arrow === "▲" ? "Up" : "Down"} ` : ""}${esc(k.delta)}</span>
      ${k.label === "Unanimous final votes" ? `<button type="button" class="kpi-link" data-vote="@split" data-date="ytd">See this year's split votes</button>` : ""}</div>`).join("");
  }

  function niceMax(v) {
    const steps = [5, 10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 150, 200, 250, 300, 400, 500, 600, 800, 1000];
    return steps.find((s) => s >= v) || Math.ceil(v / 100) * 100;
  }

  const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  // First and last day of a month; for the current month, stop at the same day as today.
  function monthRange(year, month, soFar) {
    const mm = String(month).padStart(2, "0");
    let end = new Date(year, month, 0).getDate();
    if (soFar) end = Math.min(end, Number(today().slice(8, 10)));
    return [`${year}-${mm}-01`, `${year}-${mm}-${String(end).padStart(2, "0")}`];
  }

  function renderMonthly() {
    const m = stats.monthly;
    const top = niceMax(Math.max(1, ...m.cur, ...m.prev));
    $("month-legend").innerHTML = `<span><i style="background:var(--series-cur)"></i>${m.year}</span><span><i style="background:var(--series-prev)"></i>${m.year - 1}</span>`;
    $("month-y").innerHTML = [top, Math.round(top * 2 / 3), Math.round(top / 3), 0].map((v) => `<span>${v}</span>`).join("");
    const grid = `<div class="g" style="top:0"></div><div class="g" style="top:33.33%"></div><div class="g" style="top:66.66%"></div><div class="g base" style="bottom:0"></div>`;
    const last = m.labels.length - 1;
    const bar = (year, i, n, color) => {
      const [a, b] = monthRange(year, i + 1, i === last);
      const name = `${MONTH_NAMES[i]} ${year}`;
      return `<button type="button" class="bar" data-go='${JSON.stringify({ kind: "ordinance", body: "@council", outcome: "passed", from: a, to: b, label: name })}'
        title="${name}: ${n} ordinance${n === 1 ? "" : "s"} passed${i === last ? " (so far)" : ""}. Click to see them."
        aria-label="${name}: ${n} ordinances passed. Show them."><span style="height:${(n / top) * 100}%;background:${color}"></span></button>`;
    };
    const cols = m.labels.map((l, i) => `<div class="col">${bar(m.year - 1, i, m.prev[i], "var(--series-prev)")}${bar(m.year, i, m.cur[i], "var(--series-cur)")}</div>`).join("");
    $("month-plot").innerHTML = grid + `<div class="cols" role="img" aria-label="${esc(m.labels.map((l, i) => `${l}: ${m.cur[i]} vs ${m.prev[i]}`).join("; "))}">${cols}</div>`;
    $("month-x").innerHTML = m.labels.map((l) => `<span>${l}</span>`).join("");
    // Label past months with no passage votes in either year (usually Council's July summer break).
    const empties = m.labels.map((l, i) => (i < m.labels.length - 1 && !m.cur[i] && !m.prev[i]) ? i : -1).filter((i) => i >= 0);
    if (empties.length) {
      const cols = $("month-plot").querySelectorAll(".col");
      empties.forEach((i) => {
        cols[i].innerHTML = `<span class="novotes" tabindex="0" data-tip="no_votes_month">No votes</span>`;  // nothing to click
      });
    }
  }

  function renderPeriod() {
    const p = stats.periods[state.period];
    document.querySelectorAll(".toggle button[data-period]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.period === state.period)));
    document.querySelectorAll(".period-long").forEach((el) => { el.textContent = p.long; });
    $("period-short-1").textContent = p.short;
    $("period-short-2").textContent = p.short;
    renderHoods().catch((e) => console.error(e));

    // Topics: up to 5 risers and 5 fallers.
    const up = p.topics.filter((t) => t.change === null ? t.cur > 0 : t.change > 0).slice(0, 5);
    const down = p.topics.filter((t) => t.change !== null && t.change < 0).slice(-5);
    const shown = [...up, ...down];
    const maxAbs = Math.max(1, ...shown.map((t) => (t.change === null ? 0 : Math.abs(t.change))));
    $("topics").innerHTML = shown.length ? shown.map((t) => {
      const isNew = t.change === null;
      const v = isNew ? maxAbs : t.change;
      const w = Math.max(1, (Math.abs(v) / maxAbs) * 48);
      const pos = v >= 0;
      const style = pos ? `left:50%;width:${w}%;background:var(--series-cur);border-radius:0 4px 4px 0`
                        : `right:50%;width:${w}%;background:var(--series-down);border-radius:4px 0 0 4px`;
      const label = isNew ? "new" : (pos ? "+" : "−") + Math.abs(t.change) + "%";
      const go = { topic: t.name, from: p.cur[0], to: p.cur[1], label: p.short.split(" vs.")[0] };
      return `<button type="button" class="topic" data-go='${esc(JSON.stringify(go))}' title="${esc(t.name)}: ${t.cur} items vs ${t.prev} a year earlier. Click to see this period's items."
        aria-label="${esc(t.name)}, ${label}. Show items.">
        <span class="n">${esc(t.name)}</span>
        <span class="track"><span class="mid"></span><span class="bar" style="${style}"></span></span>
        <span class="v">${label}</span></button>`;
    }).join("") : `<div class="empty">Not enough items yet in this period to compare. Try Quarter or Year.</div>`;

    // Members
    $("members-sub").textContent = `${p.final_votes.toLocaleString()} roll-call votes on final passage, ${p.short}`;
    $("remote-legend").hidden = !p.members.some((m) => m.remote);
    $("member-rows").innerHTML = p.members.length ? p.members.map((m) => {
      const tot = m.total || 1;
      const out = m.absent + m.abstain;
      const dissent = m.no + m.abstain;
      return `<tr>
        <th scope="row"><button type="button" data-member="${esc(m.name)}" title="Open ${esc(m.name)}'s page" style="font:inherit;font-weight:600;color:var(--ink);background:none;border:0;padding:0;cursor:pointer;text-align:left">${esc(m.name)}</button></th>
        <td class="num">${m.present}%</td>
        <td><div class="stack" title="${esc(m.name)}: ${m.yes} yes, ${m.no} no${m.remote ? `, ${m.remote} remote` : ""}, ${out} absent or abstained">${
          [[m.yes, "var(--yes)"], [m.remote || 0, "var(--remote)"], [m.no, "var(--no)"], [out, "var(--absent)"]]
            .filter(([n]) => n > 0)  // skip empty segments so gaps don't add up
            .map(([n, c]) => `<div style="flex:${n} 1 0;background:${c}"></div>`).join("")}</div></td>
        <td class="num"><button type="button" class="count-btn" data-vote="${esc(m.name)}" data-date="${{ month: "mtd", quarter: "qtd", year: "ytd" }[state.period]}" ${dissent ? "" : "disabled"} aria-label="${dissent} no or abstain votes by ${esc(m.name)}: show them">${dissent}</button></td>
        <td class="num">${m.against}</td></tr>`;
    }).join("") : `<tr><td colspan="5" class="empty">No final votes recorded in this period yet.</td></tr>`;
  }

  function fillFilters() {
    const add = (id, values, labelFn) => {
      const sel = $(id);
      values.forEach((v) => { const o = document.createElement("option"); o.value = v; o.textContent = labelFn ? labelFn(v) : v; sel.appendChild(o); });
    };
    add("f-body", stats.lists.bodies);
    add("f-sponsor", stats.lists.sponsors);
    add("f-dept", stats.lists.departments);
    add("f-vote", stats.lists.voters || [], (v) => "No or abstain: " + v);
    add("f-topic", stats.lists.topics || []);
    add("f-hood", stats.lists.neighborhoods || []);
    const when = stats.updated_at ? new Date(stats.updated_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" }) : "—";
    $("updated").textContent = `Last updated ${when} · ${stats.meetings_indexed.toLocaleString()} meetings and ${stats.items_indexed.toLocaleString()} items indexed.`;
  }

  // ------------------------------------------------------------- member pages
  function renderMember() {
    const box = $("member-card");
    const p = state.member && stats && stats.profiles ? stats.profiles[state.member] : null;
    if (!state.member) { box.hidden = true; box.innerHTML = ""; return; }
    if (!p) { box.hidden = false; box.innerHTML = `<p class="muted">No voting record found for ${esc(state.member)}.</p>`; return; }
    const y = stats.today.slice(0, 4);
    const pct = (t) => t.total ? `${t.present}%` : "—";
    const roles = p.roles.length
      ? p.roles.map((r) => `<li><b>${esc(r.title || "Member")}</b> · ${esc(r.body)}</li>`).join("")
      : `<li class="muted">No current committee assignments listed in Legistar.</li>`;
    const past = p.past_roles.length ? `<details><summary>Earlier roles (${p.past_roles.length})</summary><ul>${p.past_roles.map((r) =>
      `<li>${esc(r.title || "Member")} · ${esc(r.body)} <span class="muted">(${esc(fmtDate(r.start))} – ${esc(fmtDate(r.end))})</span></li>`).join("")}</ul></details>` : "";
    const sp = p.sponsored;
    const kinds = ["ordinance", "resolution", "motion"].filter((k) => sp.by_kind[k]).map((k) => `${sp.by_kind[k]} ${KIND_LABEL[k].toLowerCase()}${sp.by_kind[k] === 1 ? "" : "s"}`).join(", ");
    const topics = sp.top_topics.map((t) => `<button type="button" class="chip" data-go='${esc(JSON.stringify({ member: p.name, topic: t }))}'>${esc(t)}</button>`).join("");
    const dissents = p.all_time.no + p.all_time.abstain;
    box.hidden = false;
    box.innerHTML = `
      <div class="member-head">
        <div>
          <h2>${esc(p.name)}${p.active ? "" : ' <span class="badge-past">Former member</span>'}</h2>
          <p class="muted">Final votes in this record: ${esc(fmtDate(p.first_vote))} – ${esc(fmtDate(p.last_vote))}</p>
        </div>
        <button type="button" class="linkish" data-copy="${esc(location.origin + location.pathname + "?member=" + encodeURIComponent(p.name))}">Copy link</button>
      </div>
      <div class="member-stats">
        <div><span class="v">${pct(p.this_year)}</span><span class="l lab">Present for final votes, ${y}<button type="button" class="tip" data-tip="col_present" aria-label="About attendance">i</button></span></div>
        <div><span class="v">${pct(p.all_time)}</span><span class="l">Present, all years</span></div>
        <div><span class="v">${p.all_time.yes.toLocaleString()}</span><span class="l">Yes votes</span></div>
        <div><span class="v">${dissents ? `<button type="button" class="count-btn big" data-vote="${esc(p.name)}">${dissents}</button>` : "0"}</span><span class="l lab">No or abstain<button type="button" class="tip" data-tip="col_dissent" aria-label="About no votes">i</button></span></div>
        <div><span class="v">${sp.total.toLocaleString()}</span><span class="l lab">Items sponsored<button type="button" class="tip" data-tip="member_sponsored" aria-label="About sponsorship">i</button></span></div>
      </div>
      <div class="member-cols">
        <div><h3>Current roles</h3><ul class="roles">${roles}</ul>${past}</div>
        <div><h3>What they sponsor</h3>
          <p>${kinds ? esc(kinds) : "No sponsored items found"}${sp.passed ? `; ${sp.passed} passed or adopted` : ""}.</p>
          ${topics ? `<div class="chips">${topics}</div>` : ""}</div>
      </div>`;
  }

  // ------------------------------------------------------------- neighborhoods
  let hoodShapes;  // undefined = not loaded yet, null = unavailable
  async function loadHoodShapes() {
    if (hoodShapes !== undefined) return hoodShapes;
    try {
      const r = await fetch("data/neighborhoods.geojson", { cache: "no-cache" });
      hoodShapes = r.ok ? await r.json() : null;
    } catch (e) { hoodShapes = null; }
    return hoodShapes;
  }

  const SEQ = ["#b7d3f6", "#86b6ef", "#5598e7", "#2a78d6", "#1c5cab"];  // light → dark blue

  async function renderHoods() {
    const p = stats.periods[state.period];
    const rows = (p.neighborhoods || []).slice();
    const byName = new Map(rows.map((r) => [r.name, r]));
    const nonzero = rows.map((r) => r.cur).filter((n) => n > 0).sort((a, b) => a - b);
    const cut = (q) => nonzero.length ? nonzero[Math.min(nonzero.length - 1, Math.floor(q * nonzero.length))] : 0;
    const bins = [cut(0.2), cut(0.4), cut(0.6), cut(0.8)];
    const colorFor = (n) => !n ? "#eef1f6" : SEQ[bins.filter((b) => n > b).length];
    const go = (name) => esc(JSON.stringify({ hood: name, from: p.cur[0], to: p.cur[1], label: p.short.split(" vs.")[0] }));
    const change = (r) => r.prev ? `${r.cur >= r.prev ? "+" : "−"}${Math.abs(Math.round((r.cur - r.prev) / r.prev * 100))}% vs. a year earlier` : (r.cur ? "none a year earlier" : "");

    // Ranked list (also the fallback when the map can't be drawn)
    const top = rows.filter((r) => r.cur > 0).sort((a, b) => b.cur - a.cur).slice(0, 12);
    const max = Math.max(1, ...top.map((r) => Math.max(r.cur, r.prev || 0)));
    const prevLabel = p.short.split(" vs. ")[1] || "a year earlier";
    const delta = (r) => {
      const d = r.cur - (r.prev || 0);
      return d > 0 ? `<span class="dl up">▲${d}</span>` : d < 0 ? `<span class="dl down">▼${-d}</span>` : `<span class="dl">–</span>`;
    };
    $("hood-list").innerHTML = top.length ? `<li class="hood-key" aria-hidden="true"><span></span><span><i></i>${esc(prevLabel)}, same stretch</span><span></span><span>Change</span></li>` + top.map((r) => `<li><button type="button" class="hood-row" data-go='${go(r.name)}'
        title="${esc(r.name)}: ${r.cur} items vs. ${r.prev || 0} in ${esc(prevLabel)}. Click to see them." aria-label="${esc(r.name)}: ${r.cur} items, ${r.prev || 0} in ${esc(prevLabel)}. Show them.">
        <span class="n">${esc(r.name)}</span><span class="bar"><span style="width:${(r.cur / max) * 100}%"></span>${r.prev ? `<i class="prev" style="left:${(r.prev / max) * 100}%"></i>` : ""}</span><span class="v">${r.cur}</span>${delta(r)}</button></li>`).join("")
      : `<li class="empty">No neighborhood mentions in this period yet.</li>`;

    const shapes = await loadHoodShapes();
    const mapBox = $("hood-map");
    if (!shapes || !shapes.features || !shapes.features.length) { mapBox.hidden = true; $("hood-attrib").hidden = true; return; }
    // Simple map projection: longitude scaled by cos(latitude) so shapes aren't stretched.
    let minX = 180, maxX = -180, minY = 90, maxY = -90;
    shapes.features.forEach((f) => f.geometry.coordinates.forEach((poly) => poly[0].forEach(([x, y]) => {
      minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    })));
    const kx = Math.cos(((minY + maxY) / 2) * Math.PI / 180);
    const W = 600, scale = W / ((maxX - minX) * kx), H = Math.round((maxY - minY) * scale);
    const pt = ([x, y]) => `${((x - minX) * kx * scale).toFixed(1)},${((maxY - y) * scale).toFixed(1)}`;
    const paths = shapes.features.map((f) => {
      const name = f.properties.name, r = byName.get(name) || { cur: 0, prev: 0 };
      const d = f.geometry.coordinates.map((poly) => "M" + poly[0].map(pt).join("L") + "Z").join("");  // holes render via fill-rule
      return `<path d="${d}" fill="${colorFor(r.cur)}" tabindex="0" role="button" data-go='${go(name)}'
        data-tip-title="${esc(name)}" data-tip-text="${r.cur} item${r.cur === 1 ? "" : "s"} ${esc(p.short.split(" vs.")[0])}${change(r) ? " · " + esc(change(r)) : ""}. Click to see them."
        aria-label="${esc(name)}: ${r.cur} items. Show them."></path>`;
    }).join("");
    mapBox.hidden = false;
    $("hood-attrib").hidden = false;
    if (shapes.attribution) $("hood-attrib").textContent = "Neighborhood boundaries: " + shapes.attribution + ".";
    mapBox.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="group" aria-label="Map of Cincinnati neighborhoods shaded by number of Council items">${paths}</svg>
      <div class="legend hood-legend" aria-hidden="true"><span>Fewer items</span>${["#eef1f6", ...SEQ].map((c) => `<i style="background:${c}"></i>`).join("")}<span>More</span></div>`;
  }

  // ------------------------------------------------------------- copy, CSV, upcoming meetings
  async function copyText(text, btn) {
    try { await navigator.clipboard.writeText(text); }
    catch (e) {
      const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select();
      document.execCommand("copy"); t.remove();
    }
    const old = btn.textContent; btn.textContent = "Copied!"; setTimeout(() => { btn.textContent = old; }, 1500);
  }

  const CSV_LIMIT = 5000;
  async function downloadCsv() {
    const btn = $("csv"); const old = btn.textContent; btn.textContent = "Preparing…";
    try {
      const d = await db();
      const { from, allArgs, W } = searchParts();
      const rows = await d.query(`SELECT i.date, i.body, i.kind, i.matter_file, i.title, i.outcome, i.sponsors, i.departments,
            i.topics, m.minutes_url, m.agenda_url, i.matter_url
          FROM ${from} JOIN items i ON i.item_id = f.item_id JOIN meetings m ON m.event_id = i.event_id
          ${W} ORDER BY f.date DESC, f.seq LIMIT ${CSV_LIMIT}`, allArgs);
      const cols = ["date", "body", "kind", "matter_file", "title", "outcome", "sponsors", "departments", "topics", "minutes_url", "agenda_url", "matter_url"];
      const cell = (v) => { const t = v == null ? "" : String(v); return /[",\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t; };
      const csv = [cols.join(","), ...rows.map((r) => cols.map((c) => cell(r[c])).join(","))].join("\n");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv" }));
      a.download = `cincinnati-council-${(state.q || "items").replace(/[^a-z0-9]+/gi, "-").slice(0, 40)}.csv`;
      document.body.appendChild(a); a.click(); a.remove();
      btn.textContent = rows.length >= CSV_LIMIT ? `Downloaded first ${CSV_LIMIT.toLocaleString()} rows` : "Downloaded";
      setTimeout(() => { btn.textContent = old; }, 2500);
    } catch (e) { console.error(e); btn.textContent = old; alert("Could not build the CSV: " + (e.message || e)); }
  }

  // "Coming up at City Hall": meetings in the next 14 days (or the most recent ones if none).
  async function renderUpcoming() {
    const d = await db();
    const t = today();
    let rows = await d.query(`SELECT m.event_id, m.date, m.time, m.body, m.agenda_url, m.location,
        (SELECT COUNT(*) FROM facets f WHERE f.event_id = m.event_id) AS n
      FROM meetings m WHERE m.date >= ? AND m.date <= ? ORDER BY m.date, m.time LIMIT 12`, [t, addDays(t, 14)]);
    let recent = false;
    if (!rows.length) {
      recent = true;
      rows = await d.query(`SELECT m.event_id, m.date, m.time, m.body, m.agenda_url, m.location,
          (SELECT COUNT(*) FROM facets f WHERE f.event_id = m.event_id) AS n
        FROM meetings m WHERE m.date < ? ORDER BY m.date DESC LIMIT 6`, [t]);
    }
    $("upcoming-h").textContent = recent ? "Recent meetings" : "Coming up at City Hall";
    if (!recent && rows[0]) {
      const r0 = rows[0], when0 = new Date(r0.date + "T12:00:00").toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" });
      $("teaser-coming").textContent = `${r0.date === t ? "Today" : "Next"}: ${r0.body}${r0.date === t ? (r0.time ? " at " + r0.time : "") : ", " + when0}`;
      $("teaser-coming").dataset.next = "1";
    }
    $("upcoming-list").innerHTML = rows.length ? rows.map((r) => {
      const title = `${r.body}, ${fmtDate(r.date)}`;
      const when = new Date(r.date + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
      return `<li><button type="button" class="meeting" data-meeting="${r.event_id}" data-title="${esc(title)}">
          <span class="d">${esc(when)}${r.time ? " · " + esc(r.time) : ""}</span>
          <span class="b">${esc(r.body)}</span>
          <span class="n">${r.n ? `${r.n} agenda item${r.n === 1 ? "" : "s"}` : "Agenda not posted yet"}</span></button>
        ${r.agenda_url ? `<a href="${esc(r.agenda_url)}" target="_blank" rel="noopener" class="pdf">Agenda PDF</a>` : ""}</li>`;
    }).join("") : `<li class="empty">No meetings found.</li>`;
  }

  // ------------------------------------------------------------- explainer tooltips
  // Any element with data-tip="key" shows GLOSSARY[key] on hover, keyboard focus, or tap.
  function bindTooltips() {
    const tip = $("tooltip");
    let current = null, shownAt = 0;
    const show = (el) => {
      const g = el.dataset.tipText ? [el.dataset.tipTitle || "", el.dataset.tipText] : GLOSSARY[el.dataset.tip];
      if (!g) return;
      if (current && current !== el) hide();
      tip.innerHTML = (g[0] ? `<b>${esc(g[0])}</b>` : "") + `<span>${esc(g[1])}</span>`;
      if (current !== el) shownAt = Date.now();
      tip.hidden = false;
      el.setAttribute("aria-describedby", "tooltip");
      if (el.matches("button.tip")) el.setAttribute("aria-expanded", "true");
      current = el;
      place();
    };
    const place = () => {
      if (!current) return;
      const r = current.getBoundingClientRect();
      if (r.bottom < 0 || r.top > window.innerHeight) { hide(); return; }
      const w = tip.offsetWidth, h = tip.offsetHeight;
      let left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), window.innerWidth - w - 8);
      let top = r.bottom + 8;
      if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 8);
      tip.style.left = left + "px";
      tip.style.top = top + "px";
    };
    const hide = () => {
      if (!current) return;
      current.removeAttribute("aria-describedby");
      if (current.matches("button.tip")) current.setAttribute("aria-expanded", "false");
      current = null;
      tip.hidden = true;
    };
    // Phones fire fake mouse events around a tap; ignore them so a tapped tip stays open.
    let lastTouch = 0;
    const touchy = () => Date.now() - lastTouch < 1500 || window.matchMedia("(hover: none)").matches;
    document.addEventListener("pointerdown", (e) => { if (e.pointerType !== "mouse") lastTouch = Date.now(); }, true);
    document.addEventListener("mouseover", (e) => { if (touchy()) return; const el = e.target.closest("[data-tip],[data-tip-text]"); if (el) show(el); });
    document.addEventListener("mouseout", (e) => {
      if (touchy()) return;
      const el = e.target.closest("[data-tip],[data-tip-text]");
      if (el && el === current && !el.contains(e.relatedTarget)) hide();
    });
    document.addEventListener("focusin", (e) => { const el = e.target.closest("[data-tip],[data-tip-text]"); if (el) show(el); });
    document.addEventListener("focusout", (e) => { if (e.target.closest("[data-tip],[data-tip-text]") === current) hide(); });
    // Touch: tap to open, tap again (or anywhere else) to close.
    document.addEventListener("pointerup", (e) => {
      if (e.pointerType === "mouse") return;
      const el = e.target.closest("[data-tip],[data-tip-text]");
      if (el && el === current && Date.now() - shownAt > 400) hide();
      else if (el) show(el);
      else hide();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") hide();
      if ((e.key === "Enter" || e.key === " ") && e.target.matches && e.target.matches('path[role="button"]')) { e.preventDefault(); e.target.dispatchEvent(new MouseEvent("click", { bubbles: true })); }
    });
    window.addEventListener("scroll", place, { passive: true });
    window.addEventListener("resize", place);
  }

  // ------------------------------------------------------------- state <-> URL
  const KEYS = ["q", ...FILTERS, ...FOCUS, "sort", "docs"];
  // Make the dropdowns show the current filters; a custom date range gets its own option.
  function syncFilterControls(dateLabel) {
    const sel = $("f-date");
    [...sel.options].filter((o) => o.dataset.custom).forEach((o) => o.remove());
    if (/\.\./.test(state.date)) {
      const [a, b] = state.date.split("..");
      const o = document.createElement("option");
      o.value = state.date; o.dataset.custom = "1";
      o.textContent = dateLabel || `${fmtDate(a)} – ${fmtDate(b)}`;
      sel.appendChild(o);
    }
    FILTERS.forEach((k) => {
      const el = $("f-" + k);
      if (!el) return;
      if (state[k] && ![...el.options].some((o) => o.value === state[k])) {
        const o = document.createElement("option"); o.value = o.textContent = state[k]; el.appendChild(o);
      }
      el.value = state[k];
    });
  }

  function readUrl() {
    const p = new URLSearchParams(location.search);
    KEYS.forEach((k) => { if (p.has(k)) state[k] = p.get(k); });
    $("q").value = state.q;
    $("docs-toggle").checked = !!state.docs;
    syncFilterControls();
  }
  function writeUrl() {
    const p = new URLSearchParams();
    KEYS.forEach((k) => { if (state[k] && !(k === "sort" && state[k] === "newest")) p.set(k, state[k]); });
    history.replaceState(null, "", p.toString() ? "?" + p : location.pathname);
  }
  const anyFilter = () => [...FILTERS, ...FOCUS].some((k) => state[k]);

  // Highlight the filters that are shaping the current results.
  function markActiveFilters() {
    let n = 0;
    FILTERS.forEach((k) => {
      const el = $("f-" + k);
      if (!el) return;
      const on = !!state[k];
      n += on;
      el.classList.toggle("active", on);
      el.closest(".f").classList.toggle("active", on);
    });
    const reset = document.querySelector(".filters .reset");
    reset.textContent = n ? `Clear ${n} filter${n === 1 ? "" : "s"}` : "Clear filters";
    reset.classList.toggle("active", n > 0);
    $("filters-toggle-label").textContent = n ? `Filters (${n} on)` : "Filters";
    $("filters-toggle").classList.toggle("active", n > 0);
    if (n && !filtersOpen) setFiltersOpen(true);
  }

  function search() {
    if (view !== "search") {  // results live on the Search page; keep the page we came from in Back history
      history.pushState(null, "", location.pathname + location.search);
      showView("search", false);
    }
    renderMember();
    markActiveFilters();
    state.q = $("q").value;
    state.offset = 0;
    writeUrl();
    const active = updateHome();
    if (active) runSearch(false);
  }

  // ------------------------------------------------------------- pages ("views")
  // One HTML file, several pages: the address bar's #part says which one is showing
  // (#coming-up, #neighborhoods, …). The Search page shows the four question cards
  // until someone searches or browses, then the filters and results.
  const VIEWS = { "search": "Search", "coming-up": "What's coming up", "neighborhoods": "Neighborhoods",
                  "council": "Council members", "how-it-works": "How City Hall works" };
  const ALIASES = { "": "search", upcoming: "coming-up", trends: "coming-up", members: "council", analysis: "how-it-works" };
  let view = "search", browsing = false, filtersOpen = false;

  function viewFromHash() {
    const h = decodeURIComponent(location.hash.replace(/^#\/?/, ""));
    return VIEWS[h] ? h : (h in ALIASES ? ALIASES[h] : null);
  }
  function showView(v, scroll = true) {
    view = v;
    document.querySelectorAll("[data-view]").forEach((el) => { el.hidden = el.dataset.view !== v; });
    document.body.classList.toggle("inner", v !== "search");
    document.querySelectorAll("nav.main [data-nav]").forEach((a) => {
      const on = a.dataset.nav === v;
      a.classList.toggle("on", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    document.title = v === "search" ? "Plum Street Ledger — search Cincinnati City Council minutes" : `${VIEWS[v]} — Plum Street Ledger`;
    if (v === "how-it-works" && !analysisLoading) analysisLoading = loadAnalysis();
    if (scroll) window.scrollTo(0, 0);
  }
  function route() { const v = viewFromHash(); if (v && v !== view) showView(v); else if (v) window.scrollTo(0, 0); }

  // Search page: question cards when idle, results once there is a search, filter or "browse".
  function updateHome() {
    const active = !!(state.q.trim() || anyFilter() || browsing);
    $("home-cards").hidden = active;
    $("results-area").hidden = !active;
    return active;
  }
  function setFiltersOpen(open) {
    filtersOpen = open;
    $("filters").hidden = !open;
    $("filters-toggle").setAttribute("aria-expanded", String(open));
  }

  // One line of live data on each home card.
  function renderTeasers() {
    if (stats) {
      const k = (label) => (stats.kpis || []).find((x) => x.label === label);
      const ord = k("Ordinances passed");
      if (ord && !$("teaser-coming").dataset.next) $("teaser-coming").textContent = `${ord.value} ordinances passed so far this year`;
      const hoods = ((stats.periods.year || {}).neighborhoods || []).filter((r) => r.cur > 0).sort((a, b) => b.cur - a.cur);
      if (hoods[0]) $("teaser-hoods").textContent = `Most mentioned this year: ${hoods[0].name} (${hoods[0].cur} items)`;
      const un = k("Unanimous final votes");
      if (un) $("teaser-council").textContent = `${un.value} of final votes this year were unanimous`;
    }
    const ord = analysis && analysis.timing && analysis.timing.by_kind && analysis.timing.by_kind.ordinance;
    if (ord) $("teaser-how").textContent = `A typical ordinance goes from first agenda to final vote in ${ord.median === 0 ? "a single day" : plural(Math.round(ord.median), "day")}`;
  }

  // ------------------------------------------------------------- wire up
  function bind() {
    $("search-form").addEventListener("submit", (e) => { e.preventDefault(); FOCUS.forEach((k) => { state[k] = ""; }); state.q = $("q").value; search(); });
    window.addEventListener("hashchange", route);
    const setDocs = (on) => { state.docs = on ? "1" : ""; $("docs-toggle").checked = on; if (state.q.trim() || $("q").value.trim()) search(); else writeUrl(); };
    $("docs-toggle").addEventListener("change", (e) => setDocs(e.target.checked));
    document.addEventListener("click", (e) => { if (e.target.closest("[data-docs-on]")) setDocs(true); });
    window.addEventListener("popstate", route);
    $("filters-toggle").addEventListener("click", () => setFiltersOpen(!filtersOpen));
    document.addEventListener("click", (e) => {
      const br = e.target.closest("[data-browse], [data-latest]");
      if (!br) return;
      [...FILTERS, ...FOCUS].forEach((k) => { state[k] = ""; });
      $("q").value = ""; state.q = "";
      browsing = true;
      syncFilterControls();
      if (br.dataset.browse !== undefined) setFiltersOpen(true);
      search();
      $("results-area").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    bindTooltips();
    document.querySelectorAll("[data-try]").forEach((b) => b.addEventListener("click", () => { $("q").value = b.dataset.try; state.q = b.dataset.try; search(); }));
    $("filters").addEventListener("change", (e) => {
      const k = e.target.id.replace("f-", "");
      state[k] = e.target.value;
      FOCUS.forEach((f) => { state[f] = ""; });
      search();
    });
    $("filters").addEventListener("reset", () => {
      [...FILTERS, ...FOCUS].forEach((k) => { state[k] = ""; });
      setTimeout(search, 0);
    });
    document.querySelectorAll("#sorter button").forEach((b) => b.addEventListener("click", () => { state.sort = b.dataset.sort; search(); }));
    $("more").addEventListener("click", () => { state.offset += pageSize(); runSearch(true); });
    $("csv").addEventListener("click", downloadCsv);
    document.querySelectorAll(".toggle button[data-period]").forEach((b) => b.addEventListener("click", () => { state.period = b.dataset.period; renderPeriod(); }));
    document.addEventListener("click", (e) => {
      const v = e.target.closest("[data-votes]");
      if (v) showVotes(v.dataset.votes, v);
      const mb = e.target.closest("[data-member]");
      if (mb) {
        [...FILTERS, ...FOCUS].forEach((k) => { state[k] = ""; });
        state.member = mb.dataset.member; $("q").value = ""; state.q = "";
        syncFilterControls(); search();
        $("member-card").scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
      const ft = e.target.closest("[data-fulltext]");
      if (ft) { toggleFullText(ft); return; }
      const cp = e.target.closest("[data-copy]");
      if (cp) { copyText(cp.dataset.copy, cp); return; }
      const mt = e.target.closest("[data-meeting]");
      if (mt) {
        [...FILTERS, ...FOCUS].forEach((k) => { state[k] = ""; });
        state.meeting = mt.dataset.meeting; $("q").value = ""; state.q = "";
        $("res-h").dataset.meetingTitle = mt.dataset.title;
        syncFilterControls(); search();
        document.getElementById("res-h").scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
      const g = e.target.closest("[data-go]");
      if (g && !e.target.closest("button.tip")) {
        const go = JSON.parse(g.dataset.go);
        [...FILTERS, ...FOCUS].forEach((k) => { state[k] = ""; });
        ["kind", "body", "sponsor", "dept", "topic", "hood", "outcome", "vote", "member", "file"].forEach((k) => { if (go[k]) state[k] = go[k]; });
        if (go.from) state.date = `${go.from}..${go.to}`;
        $("q").value = go.q || ""; state.q = go.q || "";
        syncFilterControls(go.label);
        search();
        document.getElementById("res-h").scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
      const vb = e.target.closest("[data-vote]");
      if (vb) {
        const sel = $("f-vote");
        const val = vb.dataset.vote;
        if (![...sel.options].some((o) => o.value === val)) { const o = document.createElement("option"); o.value = val; o.textContent = "No or abstain: " + val; sel.appendChild(o); }
        [...FILTERS, ...FOCUS].forEach((k) => { state[k] = ""; const el = $("f-" + k); if (el) el.value = ""; });
        state.vote = val; sel.value = val;
        if (vb.dataset.date) { state.date = vb.dataset.date; $("f-date").value = vb.dataset.date; }
        $("q").value = ""; state.q = "";
        search();
        document.getElementById("search").scrollIntoView({ behavior: "smooth" });
        return;
      }
      const s = e.target.closest("[data-sponsor]");
      if (s) {
        const name = s.dataset.sponsor;
        const sel = $("f-sponsor");
        if (![...sel.options].some((o) => o.value === name)) { const o = document.createElement("option"); o.value = o.textContent = name; sel.appendChild(o); }
        sel.value = name; state.sponsor = name; search();
        document.getElementById("search").scrollIntoView({ behavior: "smooth" });
      }
    });
  }

  // ------------------------------------------------------------- analysis (loaded when scrolled into view)
  let analysis = null, analysisLoading = null;
  const an = { span: "years", metric: "ordinances", stuckWhere: "", stuckShown: 6, lobbyShown: 12, lobbySort: "mentions", lobbyDir: "desc" };
  const METRICS = [
    { key: "ordinances", label: "Ordinances passed", go: { kind: "ordinance", body: "@council", outcome: "passed" }, noun: "ordinances passed" },
    { key: "resolutions", label: "Resolutions adopted", go: { kind: "resolution", body: "@council", outcome: "passed" }, noun: "resolutions adopted" },
    { key: "motions", label: "Motions", go: { kind: "motion", body: "@council" }, noun: "motions at Council" },
    { key: "split_votes", label: "Split votes", go: { vote: "@split" }, noun: "final votes with at least one no or abstain" },
    { key: "emergency_share", label: "Emergency share", go: { kind: "ordinance", body: "@council", outcome: "passed" }, noun: "of ordinances passed as emergencies", pct: true, tip: "an_emergency_share" },
    { key: "meetings", label: "Meetings", go: {}, noun: "meetings of Council and its committees" },
  ];
  // Sequential scale for the topic grid: gray (that topic's quietest period) to blue (its busiest).
  const GRID_RAMP = [
    { bg: "#eceef2", fg: "#0d1b3a" }, { bg: "#d3e1f3", fg: "#0d1b3a" }, { bg: "#a9c8ef", fg: "#0d1b3a" },
    { bg: "#6fa5e8", fg: "#0d1b3a" }, { bg: "#2f7bd8", fg: "#fff" }, { bg: "#174f9c", fg: "#fff" },
  ];
  // Explain why a period is partial: the record starts in July 2020, and the current period isn't over.
  const partialTip = (p) => {
    const label = p.label.replace(" (partial)", "");
    const early = p.start > (an.span === "years" ? `${p.start.slice(0, 4)}-01-01` : "0000");
    const why = p.start === (analysis && analysis.trends.years[0].start) && early
      ? `This record starts in July 2020, so ${label} covers only ${fmtDate(p.start)} to ${fmtDate(p.end)}.`
      : `${label} isn't over yet: this covers ${fmtDate(p.start)} to ${fmtDate(p.end)}.`;
    return `data-tip-title="Partial ${an.span === "years" ? "year" : "term"}" data-tip-text="${esc(why + " Its totals will look lower than a full period's; hover a bar for the monthly pace, and the topic grid already compares items per month.")}"`;
  };
  const plural = (n, one, many) => `${n.toLocaleString()} ${n === 1 ? one : (many || one + "s")}`;
  const dayWord = (n) => n === 0 ? "same day" : plural(Math.round(n), "day");
  const goAttr = (o) => `data-go='${esc(JSON.stringify(o))}'`;

  async function loadAnalysis() {
    try {
      const r = await fetch("data/analysis.json", { cache: "no-cache" });
      if (!r.ok) throw new Error("analysis.json " + r.status);
      analysis = await r.json();
      $("analysis-status").hidden = true;
      renderAnalysis();
      renderTeasers();
    } catch (e) {
      console.error(e);
      $("analysis-status").textContent = "The analysis isn't available yet. It's built with each daily update.";
    }
  }

  function renderAnalysis() {
    if (!analysis) return;
    const parts = [renderLongTrends, renderTiming, renderStuck, renderLobbying, renderCommittees];
    parts.forEach((fn) => { try { fn(); } catch (e) { console.error(e); } });
  }

  // ---- The long view: one metric per year or Council term, plus a topic grid
  function renderLongTrends() {
    const t = analysis.trends, periods = t[an.span], m = METRICS.find((x) => x.key === an.metric);
    $("long-trends").hidden = false;
    document.querySelectorAll("[data-span]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.span === an.span)));
    $("metric-chips").innerHTML = METRICS.map((x) => `<button type="button" class="chip${x.key === an.metric ? " on" : ""}" data-metric="${x.key}" aria-pressed="${x.key === an.metric}">${esc(x.label)}</button>`).join("")
      + (m.tip ? `<button type="button" class="tip" data-tip="${m.tip}" aria-label="About this measure">i</button>` : "");
    const vals = periods.map((p) => p[m.key] == null ? 0 : p[m.key]);
    const top = m.pct ? 100 : niceMax(Math.max(1, ...vals));
    const cols = periods.map((p, i) => {
      const v = p[m.key];
      const months = p.days / 30.44;
      const rate = !m.pct && v != null && p.partial ? ` That's about ${(v / months).toFixed(v / months < 10 ? 1 : 0)} a month over the ${Math.round(months)} months covered.` : "";
      const label = p.label.replace(" (partial)", "");
      const text = v == null ? "No ordinances passed in this period." : `${m.pct ? v + "%" : v.toLocaleString()} ${m.noun}, ${fmtDate(p.start)} – ${fmtDate(p.end)}.${rate}${m.key === "meetings" ? " Click to see the items." : " Click to see them."}`;
      return `<div class="col"><button type="button" class="bar" ${goAttr({ ...m.go, from: p.start, to: p.end, label })}
          data-tip-title="${esc(label)}${p.partial ? " (partial)" : ""}" data-tip-text="${esc(text)}" aria-label="${esc(label)}: ${esc(text)}">
          <em class="val">${v == null ? "–" : (m.pct ? v + "%" : v.toLocaleString())}</em>
          <span class="${p.partial ? "partial" : ""}" style="height:${((v || 0) / top) * 100}%"></span></button></div>`;
    }).join("");
    const grid = `<div class="g" style="top:0"></div><div class="g" style="top:50%"></div><div class="g base" style="bottom:0"></div>`;
    $("lt-chart").innerHTML = `<div class="bars-chart"><div class="y lt-y"><span>${m.pct ? "100%" : top}</span><span>${m.pct ? "50%" : Math.round(top / 2)}</span><span>0</span></div>
      <div class="plotwrap"><div class="plot lt-plot"><div class="inner">${grid}<div class="cols">${cols}</div></div></div>
      <div class="xlabels">${periods.map((p) => `<span>${esc(p.label.replace(" (partial)", ""))}${p.partial ? `<small class="partial-tip" tabindex="0" ${partialTip(p)}>partial</small>` : ""}</span>`).join("")}</div></div></div>`;

    // Topic grid: color each cell by how far that period's pace (items per month) is above or
    // below the topic's own average: blue = busier than usual, red = quieter, gray = about usual.
    const series = t[an.span === "years" ? "topics_by_year" : "topics_by_term"];
    const rows = Object.entries(series).map(([name, counts]) => ({ name, counts, total: counts.reduce((a, b) => a + b, 0) }))
      .filter((r) => r.total > 0).sort((a, b) => b.total - a.total);
    const head = `<thead><tr><th scope="col">Topic</th>${periods.map((p) => `<th scope="col" class="num">${p.partial ? `<span class="partial-tip" tabindex="0" ${partialTip(p)}>${esc(p.label.replace(" (partial)", ""))}*</span>` : esc(p.label.replace(" (partial)", ""))}</th>`).join("")}</tr></thead>`;
    const body = rows.map((r) => {
      const rates = r.counts.map((c, i) => c / Math.max(1, periods[i].days));
      const avg = rates.reduce((a, b) => a + b, 0) / rates.length || 1;
      const lo = Math.min(...rates), hi = Math.max(...rates);
      return `<tr><th scope="row">${esc(r.name)}</th>${r.counts.map((c, i) => {
        const ratio = rates[i] / avg;
        const pos = hi > lo ? (rates[i] - lo) / (hi - lo) : 0.5;
        const step = GRID_RAMP[Math.min(GRID_RAMP.length - 1, Math.floor(pos * GRID_RAMP.length))];
        const p = periods[i], label = p.label.replace(" (partial)", "");
        const pct = Math.round((ratio - 1) * 100);
        const how = Math.abs(pct) < 10 ? "about its usual pace" : `${Math.abs(pct)}% ${pct > 0 ? "busier" : "quieter"} than usual`;
        return `<td class="num"><button type="button" class="cell" style="background:${step.bg};color:${step.fg}" ${goAttr({ topic: r.name, from: p.start, to: p.end, label })}
          aria-label="${esc(r.name)}, ${esc(label)}: ${c} items, ${how}. Show them." data-tip-title="${esc(r.name)}, ${esc(label)}${p.partial ? " (partial)" : ""}"
          data-tip-text="${esc(`${c} item${c === 1 ? "" : "s"}: ${how} for this topic, comparing items per month. Click to see them.`)}">${c}</button></td>`;
      }).join("")}</tr>`;
    }).join("");
    $("topic-grid").innerHTML = head + `<tbody>${body}</tbody>`;
    $("grid-legend").innerHTML = `<span>Quietest</span>${GRID_RAMP.map((d) => `<i style="background:${d.bg}"></i>`).join("")}<span>Busiest period for each topic</span>`;
  }

  // ---- How long legislation takes
  function renderTiming() {
    const t = analysis.timing;
    if (!t || !t.by_kind) return;
    $("time-to-pass").hidden = false;
    const tiles = [["ordinance", "Ordinances"], ["resolution", "Resolutions"], ["motion", "Motions"]].filter(([k]) => t.by_kind[k]).map(([k, label]) => {
      const s = t.by_kind[k];
      return `<div><span class="v">${dayWord(s.median)}</span><span class="l">${label}: typical time (median) across ${s.n.toLocaleString()}. The middle half took ${s.p25}–${plural(s.p75, "day")}.</span></div>`;
    }).join("");
    $("timing-tiles").innerHTML = tiles;
    const rows = (t.by_committee || []).slice(0, 8);
    const max = Math.max(30, ...rows.map((r) => r.p75));
    $("timing-committees").innerHTML = rows.length ? `<span class="t lab" style="font-size:14px">By the committee that heard it first</span>` + rows.map((r) => `
      <button type="button" class="range-row" ${goAttr({ body: r.body })} data-tip-title="${esc(r.body)}"
        data-tip-text="${esc(`Of ${r.n} items that went through this committee and got a final vote, half took less than ${dayWord(r.median)} and the middle half took ${r.p25}–${r.p75} days. Click to see this committee's items.`)}">
        <span class="n">${esc(r.body)}</span>
        <span class="track"><span class="iqr" style="left:${(r.p25 / max) * 100}%;width:${Math.max(1, ((r.p75 - r.p25) / max) * 100)}%"></span><span class="dot" style="left:${(r.median / max) * 100}%"></span></span>
        <span class="v">${Math.round(r.median)}d</span></button>`).join("")
      + `<div class="range-axis"><span>0 days</span><span>${max} days</span></div><div class="legend"><span><i class="lg-iqr"></i>Middle half of items</span><span><i class="lg-dot"></i>Median</span></div>` : "";
    const years = Object.entries(t.by_year || {}).filter(([, s]) => s && s.n >= 10);
    const ymax = Math.max(1, ...years.map(([, s]) => s.median));
    $("timing-years").innerHTML = years.length ? `<span class="t lab" style="font-size:14px">Typical days to a final vote, by year of the vote</span>
      <span class="year-cols">${years.map(([y, s]) => `<span class="yc" tabindex="0" data-tip-title="${y}" data-tip-text="${esc(`Median ${dayWord(s.median)} across ${s.n} items that got a final vote in ${y}; the middle half took ${s.p25}–${s.p75} days.`)}">
        <em>${Math.round(s.median)}</em><span style="height:calc((100% - 20px) * ${(s.median / ymax).toFixed(3)})"></span><small>${y}</small></span>`).join("")}</span>` : "";
  }

  // ---- Items that seem stuck
  function renderStuck() {
    const t = analysis.timing;
    if (!t || !t.stuck) return;
    $("stuck").hidden = false;
    $("stuck-sub").textContent = `${t.stuck_total.toLocaleString()} ordinances, resolutions and motions whose last step was a referral or hold at least ${t.stuck_days} days ago, with no final vote and not filed. Most recently stalled first; some were later replaced by new legislation.`;
    const where = (t.stuck_by_where || []).slice(0, 6);
    $("stuck-where").innerHTML = [["", "All", t.stuck_total], ...where.map(([w, n]) => [w, /city council$/i.test(w) ? "Full Council" : w, n])].map(([v, label, n]) =>
      `<button type="button" class="chip${an.stuckWhere === v ? " on" : ""}" data-stuck-where="${esc(v)}" aria-pressed="${an.stuckWhere === v}">${esc(label)} <b>${n.toLocaleString()}</b></button>`).join("");
    const list = t.stuck.filter((s) => !an.stuckWhere || s.where === an.stuckWhere);
    const whereName = (w) => /city council$/i.test(w || "") ? "Full Council" : (w || "");
    const STATUS = { referred: "Referred", held: "Held", recommended: "Recommended, no final vote", other: "No action recorded" };
    $("stuck-list").innerHTML = list.slice(0, an.stuckShown).map((s) => `<li>
        <div class="top"><button type="button" class="linkish mono" ${goAttr({ file: s.file })} aria-label="Show file ${esc(s.file)}">${esc(s.file)}</button>
        <span class="kind" data-tip="${esc(s.kind)}" tabindex="0">${esc(KIND_LABEL[s.kind] || s.kind)}</span>
        <span class="wait">${plural(s.waiting, "day")} waiting</span></div>
        <p>${esc(s.title)}</p>
        <span class="muted small">${esc(STATUS[s.status] || s.status)} · ${esc(whereName(s.where))} · last step ${fmtDate(s.last)}${s.first !== s.last ? `, first seen ${fmtDate(s.first)}` : ""}</span></li>`).join("")
      || `<li class="empty">Nothing here right now.</li>`;
    const more = $("stuck-more");
    more.hidden = list.length <= an.stuckShown;
    more.textContent = `Show more (${Math.min(list.length, t.stuck.length) - an.stuckShown} more${t.stuck_total > t.stuck.length && !an.stuckWhere ? `; the ${t.stuck.length} most recent are listed` : ""})`;
  }

  // ---- Lobbying
  function renderLobbying() {
    const l = analysis.lobbying;
    if (!l || !l.registrations) return;
    $("lobbying").hidden = false;
    $("lobby-sub").textContent = `${plural(l.registrations, "lobbyist registration")} filed with the Clerk since July 2020, for ${plural(l.clients_total, "client")}. Click a column heading to sort.`;
    const years = Object.entries(l.per_year || {});
    const max = Math.max(1, ...years.map(([, n]) => n));
    $("lobby-years").setAttribute("aria-label", "Registrations per year: " + years.map(([y, n]) => `${y}: ${n}`).join(", "));
    $("lobby-years").innerHTML = `<span class="mb-title">Registrations per year</span><span class="mb-bars">${years.map(([y, n]) => `<span class="mb" data-tip-title="${y}" data-tip-text="${plural(n, "registration")}" tabindex="0"><span style="height:${(n / max) * 100}%"></span><small>${y.slice(2)}</small></span>`).join("")}</span>`;
    // Sorting: text columns A→Z first, number and date columns biggest/newest first.
    const SORTS = {
      client: (c) => c.client.toLowerCase().replace(/^the /, ""), agents: (c) => (c.agents.length ? c.agents.length : -1),
      registrations: (c) => c.registrations, mentions: (c) => c.mentions * 10000 + (c.passed || 0), recent: (c) => (c.recent[0] ? c.recent[0].date : ""),
    };
    const key = SORTS[an.lobbySort], dir = an.lobbyDir === "asc" ? 1 : -1;
    const sorted = l.clients.slice().sort((a, b) => {
      const x = key(a), y = key(b);
      return (x < y ? -1 : x > y ? 1 : 0) * dir || b.mentions - a.mentions || a.client.localeCompare(b.client);
    });
    document.querySelectorAll("#lobbying th[data-sort-col]").forEach((th) => {
      const on = th.dataset.sortCol === an.lobbySort;
      th.setAttribute("aria-sort", on ? (an.lobbyDir === "asc" ? "ascending" : "descending") : "none");
      th.classList.toggle("sorted", on);
    });
    $("lobby-rows").innerHTML = sorted.slice(0, an.lobbyShown).map((c) => {
      const r = c.recent[0];
      return `<tr><th scope="row">${esc(c.client)}</th>
        <td>${esc(c.agents.join(", ")) || "<span class='muted'>not parsed</span>"}</td>
        <td class="num"><span title="First ${esc(fmtDate(c.first))}, latest ${esc(fmtDate(c.last))}">${c.registrations}</span></td>
        <td class="num">${c.mentions ? `<button type="button" class="count-btn" ${goAttr({ q: c.query, kind: "@legislation", from: c.from, to: c.to, label: `${fmtDate(c.from)} – ${fmtDate(c.to)}` })} aria-label="Show ${c.mentions} pieces of legislation naming ${esc(c.client)}">${c.mentions.toLocaleString()}</button>${c.passed ? `<span class="passed-n">${c.passed} passed</span>` : ""}` : `<span class="muted">0</span>`}</td>
        <td class="recent">${r ? `<span class="muted small">${fmtDate(r.date)}${r.file ? ` · <button type="button" class="linkish mono" ${goAttr({ file: r.file })}>${esc(r.file)}</button>` : ""}</span><span class="small">${esc(r.title)}</span>` : ""}</td></tr>`;
    }).join("") || `<tr><td colspan="5" class="empty">No registrations could be matched to clients.</td></tr>`;
    const more = $("lobby-more");
    more.hidden = l.clients.length <= an.lobbyShown;
  }

  // ---- Committee workload
  function renderCommittees() {
    const c = analysis.committees;
    if (!c || !c.committees) return;
    $("committees").hidden = false;
    const showLen = c.length_parsed > 0;
    document.querySelectorAll(".col-length").forEach((el) => { el.hidden = !showLen; });
    const active = c.committees.filter((r) => r.active && r.meetings_12m > 0).sort((a, b) => b.items_12m - a.items_12m);
    const to = today();
    // A thin bar beside each number, scaled to the column's largest value, for at-a-glance comparison.
    const colMax = (f) => Math.max(1, ...active.map(f).filter((v) => v != null));
    const mx = { meetings: colMax((r) => r.meetings_12m), items: colMax((r) => r.items_12m),
                 stay: colMax((r) => r.stay && r.stay.median), length: colMax((r) => r.length && r.length.median) };
    const withBar = (v, max, text) => v == null ? "<span class='muted'>–</span>"
      : `<span class="cell-bar"><span class="cb"><span style="width:${Math.max(2, (v / max) * 100)}%"></span></span><span class="cv">${text}</span></span>`;
    $("committee-rows").innerHTML = active.map((r) => {
      const pct = r.matters_12m ? Math.round((r.to_council_12m / r.matters_12m) * 100) : null;
      return `<tr><th scope="row"><button type="button" class="linkish" ${goAttr({ body: r.body, from: c.since, to, label: "Last 12 months" })}>${esc(r.body)}</button></th>
        <td class="num">${withBar(r.meetings_12m, mx.meetings, r.meetings_12m)}</td><td class="num">${withBar(r.items_12m, mx.items, r.items_12m.toLocaleString())}</td>
        <td>${pct == null ? "<span class='muted'>–</span>" : `<span class="pct" title="${r.to_council_12m} of ${r.matters_12m} pieces of legislation first heard here in the last 12 months"><span class="pbar"><span style="width:${pct}%"></span></span>${pct}%</span>`}</td>
        <td class="num">${withBar(r.stay ? r.stay.median : null, mx.stay, r.stay ? Math.round(r.stay.median) : "")}</td>
        ${showLen ? `<td class="num">${withBar(r.length ? r.length.median : null, mx.length, r.length ? `${Math.round(r.length.median)} min` : "")}</td>` : ""}</tr>`;
    }).join("") || `<tr><td colspan="6" class="empty">No committee meetings in the last 12 months.</td></tr>`;
    const past = c.committees.filter((r) => !active.includes(r));
    $("past-committees").hidden = !past.length;
    $("past-committee-list").innerHTML = past.map((r) => `<li><button type="button" class="linkish" ${goAttr({ body: r.body })}>${esc(r.body)}</button> <span class="muted small">${plural(r.meetings_all, "meeting")}, ${fmtDate(r.first)} – ${fmtDate(r.last)}</span></li>`).join("");
  }

  function bindAnalysis() {
    $("analysis").addEventListener("click", (e) => {
      const sp = e.target.closest("[data-span]");
      if (sp) { an.span = sp.dataset.span; renderLongTrends(); return; }
      const mt = e.target.closest("[data-metric]");
      if (mt) { an.metric = mt.dataset.metric; renderLongTrends(); $("metric-chips").querySelector(`[data-metric="${an.metric}"]`).focus(); return; }
      const sw = e.target.closest("[data-stuck-where]");
      if (sw) { an.stuckWhere = sw.dataset.stuckWhere; an.stuckShown = 6; renderStuck(); return; }
      const ls = e.target.closest("[data-lobby-sort]");
      if (ls) {
        const k = ls.dataset.lobbySort;
        if (an.lobbySort === k) an.lobbyDir = an.lobbyDir === "asc" ? "desc" : "asc";
        else { an.lobbySort = k; an.lobbyDir = k === "client" ? "asc" : "desc"; }
        renderLobbying();
        return;
      }
    });
    $("stuck-more").addEventListener("click", () => { an.stuckShown += 20; renderStuck(); });
    $("lobby-more").addEventListener("click", () => { an.lobbyShown += 25; renderLobbying(); });
  }

  async function start() {
    bind();
    bindAnalysis();
    try {
      const res = await fetch("data/stats.json", { cache: "no-cache" });
      if (!res.ok) throw new Error("stats.json " + res.status);
      stats = await res.json();
      fillFilters();
      renderKpis();
      renderMonthly();
      renderPeriod();
      renderTeasers();
    } catch (e) {
      console.error(e);
      $("kpi-list").innerHTML = `<div class="empty">Statistics are not available yet. They appear after the first data update runs.</div>`;
    }
    readUrl();
    showView(viewFromHash() || "search", false);
    try {
      await db();
      if (view === "search") search(); else updateHome();  // opened on another page: don't jump to results
      setTimeout(() => { if (!analysisLoading) analysisLoading = loadAnalysis(); }, 1500);  // for the home-card teaser
      docsDb().then(() => { $("docs-toggle-wrap").hidden = !docsAvailable; }).catch(() => {});
      // Say how far the collection has got, in the tooltip.
      fetch("data/docs-stats.json", { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).then((ds) => {
        docsStats = ds;
        if (!ds || !ds.total || !GLOSSARY.docs_search) return;
        const pct = Math.round((ds.checked / ds.total) * 100);
        const note = pct >= 99 ? " All legislation has been checked; some has no readable text."
          : ` So far ${ds.checked.toLocaleString()} of ${ds.total.toLocaleString()} pieces of legislation (${pct}%) have been checked, and ${ds.with_text.toLocaleString()} have full text.`;
        GLOSSARY.docs_search = [GLOSSARY.docs_search[0], GLOSSARY.docs_search[1].replace(/ This is still being filled in.*$/, pct >= 99 ? note : " This is still being filled in, newest legislation first." + note)];
      }).catch(() => {});
      renderUpcoming().catch((e) => console.error(e));
    } catch (e) {
      console.error(e);
      $("res-h").textContent = "The database could not be loaded";
      renderNothing("The data file is missing or still being built. If you just set this up, wait for the first “Update council data” run to finish on GitHub, then reload."
        + `<br><span class="muted" style="font-size:13px">Technical detail: ${esc(e && e.message ? e.message : e)}</span>`);
    }
  }

  start();
})();

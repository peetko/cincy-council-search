/* Council Record — page logic.
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
  const $ = (id) => document.getElementById(id);
  const state = { q: "", kind: "", body: "", sponsor: "", dept: "", outcome: "", date: "", sort: "newest", offset: 0, period: "year" };
  let stats = null;
  let dbPromise = null;

  // ------------------------------------------------------------- helpers
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = (iso) => {
    if (!iso) return "";
    const d = new Date(iso + "T12:00:00");
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  };
  const today = () => (stats && stats.today) || new Date().toISOString().slice(0, 10);
  const addDays = (iso, n) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
  const KIND_LABEL = { ordinance: "Ordinance", resolution: "Resolution", motion: "Motion", communication: "Communication", appointment: "Appointment", other: "Item" };

  // Turn what someone typed into a safe SQLite full-text query.
  // "exact phrase" stays a phrase, word* matches endings, everything else is ANDed.
  function ftsQuery(input) {
    const parts = [];
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
      dbPromise = createDbWorker(
        [{ from: "inline", config: { serverMode: "full", url: new URL("data/council.db", base).href, requestChunkSize: 4096,
          cacheBust: (stats && stats.updated_at) || String(Date.now()) } }],
        new URL("vendor/sqlite.worker.js", base).href,
        new URL("vendor/sql-wasm.wasm", base).href
      ).then((w) => w.db);
    }
    return dbPromise;
  }

  // Filters apply to the slim "facets" table (alias f), which keeps searches light.
  function whereClause() {
    const where = [], args = [];
    if (state.kind) { where.push("f.kind = ?"); args.push(state.kind); }
    if (state.body === "@council") where.push("f.is_council = 1");
    else if (state.body === "@committees") where.push("f.is_council = 0");
    else if (state.body) { where.push("f.body = ?"); args.push(state.body); }
    if (state.sponsor) { where.push("(f.sponsors LIKE ? OR f.mover = ?)"); args.push("%" + state.sponsor + "%", state.sponsor); }
    if (state.dept) { where.push("f.departments LIKE ?"); args.push("%" + state.dept + "%"); }
    if (state.outcome) { where.push("f.outcome_cat = ?"); args.push(state.outcome); }
    const t = today();
    const year = Number(t.slice(0, 4));
    const ranges = {
      upcoming: [t, null], "90d": [addDays(t, -90), t], "12m": [addDays(t, -365), t],
      ytd: [year + "-01-01", t], lastyear: [(year - 1) + "-01-01", (year - 1) + "-12-31"],
    };
    const r = ranges[state.date];
    if (r) {
      if (r[0]) { where.push("f.date >= ?"); args.push(r[0]); }
      if (r[1]) { where.push("f.date <= ?"); args.push(r[1]); }
    }
    return { where, args };
  }

  const COLS = `i.item_id, i.date, i.body, i.kind, i.matter_file, i.title, i.outcome, i.outcome_cat, i.yes_count,
    i.sponsors, i.departments, i.matter_url, m.minutes_url, m.agenda_url, m.video_url, m.page_url`;

  async function runSearch(append) {
    const d = await db();
    const { where, args } = whereClause();
    const fts = ftsQuery(state.q);
    if (state.q.trim() && !fts) { renderNothing("Type at least one word or number."); return; }
    let from, order, allArgs;
    if (fts) {
      from = "items_fts JOIN facets f ON f.item_id = items_fts.rowid";
      where.unshift("items_fts MATCH ?");
      allArgs = [fts, ...args];
      order = state.sort === "relevance" ? "bm25(items_fts, 10.0, 2.0, 2.0, 2.0, 5.0, 3.0, 3.0, 1.0, 1.0, 0.5)" : "f.date DESC, f.is_council DESC, f.seq";
    } else {
      from = "facets f";
      allArgs = args;
      order = "f.date DESC, f.is_council DESC, f.seq";
    }
    const W = where.length ? "WHERE " + where.join(" AND ") : "";
    try {
      // Step 1: which 20 items, in what order (reads only the search index + facets).
      const idRows = await d.query(`SELECT f.item_id AS id FROM ${from} ${W} ORDER BY ${order} LIMIT ${PAGE} OFFSET ${state.offset}`, allArgs);
      const ids = idRows.map((r) => r.id);
      // Step 2: full text and links for just those items.
      let rows = [];
      if (ids.length) {
        const marks = ids.map(() => "?").join(",");
        rows = fts
          ? await d.query(`SELECT ${COLS}, snippet(items_fts, -1, char(1), char(2), ' … ', 60) AS snip
                           FROM items_fts JOIN items i ON i.item_id = items_fts.rowid JOIN meetings m ON m.event_id = i.event_id
                           WHERE items_fts MATCH ? AND items_fts.rowid IN (${marks})`, [fts, ...ids])
          : await d.query(`SELECT ${COLS}, NULL AS snip FROM items i JOIN meetings m ON m.event_id = i.event_id
                           WHERE i.item_id IN (${marks})`, ids);
        const pos = new Map(ids.map((id, n) => [id, n]));
        rows.sort((a, b) => pos.get(a.item_id) - pos.get(b.item_id));
      }
      if (!append) {
        const [c] = await d.query(`SELECT COUNT(*) AS n, COUNT(DISTINCT f.event_id) AS meetings FROM ${from} ${W}`, allArgs);
        renderHead(c.n, c.meetings, !!fts);
        if (fts) {
          const q = await d.query(`SELECT substr(f.date,1,4) AS y, (CAST(substr(f.date,6,2) AS INTEGER)+2)/3 AS q, COUNT(*) AS n
                                   FROM ${from} ${W} GROUP BY y, q`, allArgs);
          renderSpark(q);
        } else {
          $("spark").hidden = true;
        }
        $("result-list").innerHTML = "";
        if (!c.n) renderNothing(fts ? "No items match. Try fewer words, a word ending with *, or clear the filters." : "No items match these filters.");
        $("more").hidden = c.n <= state.offset + PAGE;
        $("more").dataset.total = c.n;
      } else {
        $("more").hidden = Number($("more").dataset.total) <= state.offset + PAGE;
      }
      $("result-list").insertAdjacentHTML("beforeend", rows.map(renderResult).join(""));
    } catch (e) {
      console.error(e);
      renderNothing("That search could not run. Try removing punctuation. (" + esc(e.message || e) + ")");
    }
  }

  // ------------------------------------------------------------- rendering: search
  function renderHead(n, meetings, isSearch) {
    const h = isSearch ? `${n.toLocaleString()} result${n === 1 ? "" : "s"} for “${esc(state.q.trim())}”`
                       : (anyFilter() ? `${n.toLocaleString()} matching items` : "Latest agenda and minutes items");
    $("res-h").innerHTML = h;
    $("res-sub").textContent = `in ${meetings.toLocaleString()} meeting${meetings === 1 ? "" : "s"}`;
    $("sorter").hidden = !isSearch && !n;
    document.querySelectorAll("#sorter button").forEach((b) => { b.hidden = !isSearch; b.setAttribute("aria-pressed", String(b.dataset.sort === state.sort)); });
    document.querySelector("#sorter span:nth-of-type(2)").hidden = !isSearch;
  }

  function renderNothing(msg) {
    $("result-list").innerHTML = `<div class="card notice">${msg}</div>`;
    $("more").hidden = true;
  }

  const ICON_CHECK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12l5 5L20 7"/></svg>';
  const ICON_X = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  const ICON_ARROW = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="M13 6l6 6-6 6"/></svg>';
  const ICON_CAL = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>';

  function highlight(snip, title) {
    let text = snip || title || "";
    if (!snip && text.length > 420) text = text.slice(0, 420) + " …";
    return esc(text).replace(/\u0001/g, "<mark>").replace(/\u0002/g, "</mark>");
  }

  function renderResult(r) {
    const icon = { passed: ICON_CHECK, failed: ICON_X, referred: ICON_ARROW, scheduled: ICON_CAL }[r.outcome_cat] || "";
    const links = [];
    if (r.minutes_url) links.push(`<a href="${esc(r.minutes_url)}" target="_blank" rel="noopener">Minutes PDF</a>`);
    else if (r.agenda_url) links.push(`<a href="${esc(r.agenda_url)}" target="_blank" rel="noopener">Agenda PDF</a>`);
    if (r.matter_url) links.push(`<a href="${esc(r.matter_url)}" target="_blank" rel="noopener">Legislation page</a>`);
    if (r.page_url) links.push(`<a href="${esc(r.page_url)}" target="_blank" rel="noopener">Meeting on Legistar</a>`);
    if (r.video_url) links.push(`<a href="${esc(r.video_url)}" target="_blank" rel="noopener">Meeting video</a>`);
    if (r.yes_count != null) links.push(`<button type="button" class="linkish" data-votes="${r.item_id}" style="font:inherit;font-weight:600;color:var(--link);background:none;border:0;padding:0;cursor:pointer;text-decoration:underline">Roll-call votes</button>`);
    const meta = [`<span><strong>${esc(fmtDate(r.date))}</strong> · ${esc(r.body)}</span>`];
    if (r.sponsors) meta.push(`<span>Sponsor: ${esc(r.sponsors.replace(/; /g, ", "))}</span>`);
    if (r.departments) meta.push(`<span>${esc(r.departments.replace(/; /g, ", "))}</span>`);
    return `<article class="card result">
      <div class="tags">
        <span class="kind">${esc(KIND_LABEL[r.kind] || "Item")}</span>
        ${r.matter_file ? `<span class="file mono">${esc(r.matter_file)}</span>` : ""}
        ${r.outcome ? `<span class="outcome">${icon}${esc(r.outcome)}</span>` : ""}
      </div>
      <p>${highlight(r.snip, r.title)}</p>
      <div class="meta">${meta.join("")}</div>
      <div class="links">${links.join("")}</div>
      <div class="votes" id="votes-${r.item_id}" hidden></div>
    </article>`;
  }

  async function showVotes(itemId, btn) {
    const box = $("votes-" + itemId);
    if (!box.hidden) { box.hidden = true; return; }
    const d = await db();
    const rows = await d.query("SELECT person, value_raw, value FROM votes WHERE item_id = ? ORDER BY value, person", [Number(itemId)]);
    const groups = {};
    rows.forEach((v) => { (groups[v.value] = groups[v.value] || []).push(v.person); });
    const order = [["yes", "Yes"], ["no", "No"], ["abstain", "Abstained"], ["absent", "Absent"], ["other", "Other"]];
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
    $("kpi-list").innerHTML = stats.kpis.map((k) => `<div class="card kpi">
      <span class="l">${esc(k.label)}</span><span class="v">${esc(k.value)}</span>
      <span class="d">${k.arrow ? `<span aria-hidden="true">${k.arrow}</span> ${k.arrow === "▲" ? "Up" : "Down"} ` : ""}${esc(k.delta)}</span></div>`).join("");
  }

  function niceMax(v) {
    const steps = [5, 10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 150, 200, 300, 500, 1000];
    return steps.find((s) => s >= v) || Math.ceil(v / 100) * 100;
  }

  function renderMonthly() {
    const m = stats.monthly;
    const top = niceMax(Math.max(1, ...m.cur, ...m.prev));
    $("month-legend").innerHTML = `<span><i style="background:var(--series-cur)"></i>${m.year}</span><span><i style="background:var(--series-prev)"></i>${m.year - 1}</span>`;
    $("month-y").innerHTML = [top, Math.round(top * 2 / 3), Math.round(top / 3), 0].map((v) => `<span>${v}</span>`).join("");
    const grid = `<div class="g" style="top:0"></div><div class="g" style="top:33.33%"></div><div class="g" style="top:66.66%"></div><div class="g base" style="bottom:0"></div>`;
    const cols = m.labels.map((l, i) => `<div class="col" title="${l}: ${m.year} — ${m.cur[i]}, ${m.year - 1} — ${m.prev[i]}${i === m.labels.length - 1 ? " (same days so far)" : ""}">
        <div style="height:${(m.prev[i] / top) * 100}%;background:var(--series-prev)"></div>
        <div style="height:${(m.cur[i] / top) * 100}%;background:var(--series-cur)"></div></div>`).join("");
    $("month-plot").innerHTML = grid + `<div class="cols" role="img" aria-label="${esc(m.labels.map((l, i) => `${l}: ${m.cur[i]} vs ${m.prev[i]}`).join("; "))}">${cols}</div>`;
    $("month-x").innerHTML = m.labels.map((l) => `<span>${l}</span>`).join("");
  }

  function renderPeriod() {
    const p = stats.periods[state.period];
    document.querySelectorAll(".toggle button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.period === state.period)));
    $("period-long").textContent = p.long;
    $("period-short-1").textContent = p.short;

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
      return `<div class="topic" title="${esc(t.name)}: ${t.cur} items vs ${t.prev} a year earlier">
        <span class="n">${esc(t.name)}</span>
        <div class="track"><div class="mid"></div><div class="bar" style="${style}"></div></div>
        <span class="v">${label}</span></div>`;
    }).join("") : `<div class="empty">Not enough items yet in this period to compare. Try Quarter or Year.</div>`;

    // Members
    $("members-sub").textContent = `${p.final_votes.toLocaleString()} roll-call votes on final passage, ${p.short}`;
    $("member-rows").innerHTML = p.members.length ? p.members.map((m) => {
      const tot = m.total || 1;
      const out = m.absent + m.abstain;
      return `<tr>
        <th scope="row"><button type="button" data-sponsor="${esc(m.name)}" style="font:inherit;font-weight:600;color:var(--ink);background:none;border:0;padding:0;cursor:pointer;text-align:left">${esc(m.name)}</button></th>
        <td class="num">${m.present}%</td>
        <td><div class="stack" title="${esc(m.name)}: ${m.yes} yes, ${m.no} no, ${out} absent or abstained">
          <div style="width:${(m.yes / tot) * 100}%;background:var(--series-cur)"></div>
          <div style="width:${(m.no / tot) * 100}%;background:var(--series-no)"></div>
          <div style="width:${(out / tot) * 100}%;background:var(--absent)"></div></div></td>
        <td class="num">${m.no}</td>
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
    const when = stats.updated_at ? new Date(stats.updated_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" }) : "—";
    $("updated").textContent = `Last updated ${when} · ${stats.meetings_indexed.toLocaleString()} meetings and ${stats.items_indexed.toLocaleString()} items indexed.`;
  }

  // ------------------------------------------------------------- state <-> URL
  const KEYS = ["q", "kind", "body", "sponsor", "dept", "outcome", "date", "sort"];
  function readUrl() {
    const p = new URLSearchParams(location.search);
    KEYS.forEach((k) => { if (p.has(k)) state[k] = p.get(k); });
    $("q").value = state.q;
    ["kind", "body", "sponsor", "dept", "outcome", "date"].forEach((k) => { const el = $("f-" + k); if (el) el.value = state[k]; });
  }
  function writeUrl() {
    const p = new URLSearchParams();
    KEYS.forEach((k) => { if (state[k] && !(k === "sort" && state[k] === "newest")) p.set(k, state[k]); });
    history.replaceState(null, "", p.toString() ? "?" + p : location.pathname);
  }
  const anyFilter = () => ["kind", "body", "sponsor", "dept", "outcome", "date"].some((k) => state[k]);

  function search() {
    state.q = $("q").value;
    state.offset = 0;
    writeUrl();
    $("res-h").textContent = "Searching…";
    runSearch(false);
  }

  // ------------------------------------------------------------- wire up
  function bind() {
    $("search-form").addEventListener("submit", (e) => { e.preventDefault(); state.q = $("q").value; search(); });
    document.querySelectorAll("[data-try]").forEach((b) => b.addEventListener("click", () => { $("q").value = b.dataset.try; state.q = b.dataset.try; search(); }));
    $("filters").addEventListener("change", (e) => {
      const k = e.target.id.replace("f-", "");
      state[k] = e.target.value;
      search();
    });
    $("filters").addEventListener("reset", () => {
      ["kind", "body", "sponsor", "dept", "outcome", "date"].forEach((k) => { state[k] = ""; });
      setTimeout(search, 0);
    });
    document.querySelectorAll("#sorter button").forEach((b) => b.addEventListener("click", () => { state.sort = b.dataset.sort; search(); }));
    $("more").addEventListener("click", () => { state.offset += PAGE; runSearch(true); });
    document.querySelectorAll(".toggle button").forEach((b) => b.addEventListener("click", () => { state.period = b.dataset.period; renderPeriod(); }));
    document.addEventListener("click", (e) => {
      const v = e.target.closest("[data-votes]");
      if (v) showVotes(v.dataset.votes, v);
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

  async function start() {
    bind();
    try {
      const res = await fetch("data/stats.json", { cache: "no-cache" });
      if (!res.ok) throw new Error("stats.json " + res.status);
      stats = await res.json();
      fillFilters();
      renderKpis();
      renderMonthly();
      renderPeriod();
    } catch (e) {
      console.error(e);
      $("kpi-list").innerHTML = `<div class="empty">Statistics are not available yet. They appear after the first data update runs.</div>`;
    }
    readUrl();
    try {
      await db();
      search();
    } catch (e) {
      console.error(e);
      $("res-h").textContent = "The database could not be loaded";
      renderNothing("The data file is missing or still being built. If you just set this up, wait for the first “Update council data” run to finish on GitHub, then reload.");
    }
  }

  start();
})();

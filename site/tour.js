/* Juncta Lucent — a one-minute guided tour for first-time visitors.
 *
 * Each step dims the page and highlights one part of it, with a short note beside it.
 * It runs once, automatically, on a first visit to the home page; after that it only
 * runs from the "Take the 1-minute tour" link under the search bar. "Seen it" is kept
 * in this browser's localStorage, so it's per browser, and nothing is sent anywhere.
 */
(function () {
  "use strict";
  const KEY = "jl-tour-done";
  const STEPS = [
    { title: "Welcome to Juncta Lucent",
      text: "A free, searchable record of everything Cincinnati City Council and its committees have taken up since July 2020, updated every morning from the City's public Legistar records. It's an independent project, not an official City website." },
    { target: "#search-form", title: "Search anything",
      text: "Type an ordinance number, a street, a business, a department or a topic. Use “quotes” for an exact phrase and * for word endings: zon* finds zoning and rezoning." },
    { target: ".tries", title: "Not sure where to start?",
      text: "Tap one of these example searches, or “Split votes” to see where Council members disagreed." },
    { target: "#docs-toggle-wrap", title: "Go deeper",
      text: "Agendas only show the opening lines of long legislation. Tick this to also search the full text of ordinances, resolutions and motions." },
    { target: "#docs-toggle-wrap .tip, .hint .tip-demo", title: "Tap any ⓘ to learn",
      text: "These explain how Council works in plain language: what an ordinance is, what “three readings” means, why some laws pass “as an emergency.”" },
    { target: "#home-cards", title: "Or start with a question",
      text: "See what's coming up at City Hall, what's happening in your neighborhood, how Council members vote, and how the process works." },
    { target: "nav.main", title: "Every page, one click away",
      text: "The same pages live up here, so you can jump around from anywhere." },
    { title: "One last thing",
      text: "Results show each piece of legislation's history, votes and, for many, a short AI summary. Summaries are labeled and checked, but always confirm with the full text or the official record. You can replay this tour from the link under the search bar." },
  ];

  let i = 0, els = null, lastFocus = null, list = [];
  const visible = (el) => el && el.offsetParent !== null && el.getBoundingClientRect().width > 0;
  const findTarget = (sel) => sel ? sel.split(",").map((s) => document.querySelector(s.trim())).find(visible) : null;
  const steps = () => list;
  const remember = () => { try { localStorage.setItem(KEY, "1"); } catch (e) { /* private mode: fine */ } };
  const seen = () => { try { return localStorage.getItem(KEY) === "1"; } catch (e) { return true; } };

  function build() {
    const wrap = document.createElement("div");
    wrap.className = "tour";
    wrap.innerHTML = `
      <div class="tour-shade"></div>
      <div class="tour-spot" aria-hidden="true"></div>
      <div class="tour-card" role="dialog" aria-modal="true" aria-labelledby="tour-title">
        <div class="tour-top"><span class="tour-count"></span><button type="button" class="tour-x" aria-label="Close the tour">×</button></div>
        <h2 id="tour-title"></h2>
        <p class="tour-text"></p>
        <div class="tour-nav">
          <span class="tour-dots" aria-hidden="true"></span>
          <button type="button" class="tour-back">Back</button>
          <button type="button" class="tour-next">Next</button>
        </div>
      </div>`;
    document.body.appendChild(wrap);
    wrap.querySelector(".tour-x").addEventListener("click", end);
    wrap.querySelector(".tour-shade").addEventListener("click", end);
    wrap.querySelector(".tour-back").addEventListener("click", () => go(i - 1));
    wrap.querySelector(".tour-next").addEventListener("click", () => go(i + 1));
    return { wrap, spot: wrap.querySelector(".tour-spot"), card: wrap.querySelector(".tour-card") };
  }

  function place() {
    if (!els) return;
    const list = steps(), s = list[i];
    const t = findTarget(s.target);
    const card = els.card, spot = els.spot;
    if (!t) {  // a centered note
      spot.hidden = true;
      card.classList.add("center");
      card.style.left = card.style.top = "";
      return;
    }
    card.classList.remove("center");
    const r = t.getBoundingClientRect(), pad = 8;
    spot.hidden = false;
    Object.assign(spot.style, { left: `${r.left - pad}px`, top: `${r.top - pad}px`, width: `${r.width + pad * 2}px`, height: `${r.height + pad * 2}px` });
    const cw = card.offsetWidth, ch = card.offsetHeight, vw = window.innerWidth, vh = window.innerHeight;
    let top = r.bottom + 16;
    if (top + ch > vh - 8) top = Math.max(8, r.top - ch - 16);
    const left = Math.min(Math.max(8, r.left + r.width / 2 - cw / 2), vw - cw - 8);
    card.style.left = `${left}px`;
    card.style.top = `${top}px`;
  }

  function go(n) {
    const list = steps();
    if (n >= list.length) return end();
    if (n < 0) n = 0;
    i = n;
    const s = list[i];
    els.card.querySelector("#tour-title").textContent = s.title;
    els.card.querySelector(".tour-text").textContent = s.text;
    els.card.querySelector(".tour-count").textContent = `${i + 1} of ${list.length}`;
    els.card.querySelector(".tour-dots").innerHTML = list.map((_, k) => `<i class="${k === i ? "on" : ""}"></i>`).join("");
    els.card.querySelector(".tour-back").hidden = i === 0;
    els.card.querySelector(".tour-next").textContent = i === list.length - 1 ? "Done" : "Next";
    const t = findTarget(s.target);
    if (t) t.scrollIntoView({ block: "center", behavior: "instant" in document.documentElement.style ? "instant" : "auto" });
    requestAnimationFrame(place);
    els.card.querySelector(".tour-next").focus({ preventScroll: true });
  }

  function key(e) {
    if (!els) return;
    if (e.key === "Escape") end();
    else if (e.key === "ArrowRight") go(i + 1);
    else if (e.key === "ArrowLeft") go(i - 1);
    else if (e.key === "Tab") {  // keep keyboard focus inside the card
      const f = [...els.card.querySelectorAll("button:not([hidden])")];
      const k = f.indexOf(document.activeElement);
      if (e.shiftKey && k <= 0) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && k === f.length - 1) { e.preventDefault(); f[0].focus(); }
    }
  }

  function start() {
    if (els) return;
    lastFocus = document.activeElement;
    if (location.hash && location.hash !== "#") location.hash = "";  // the tour lives on the home page
    window.scrollTo(0, 0);
    list = STEPS.filter((s) => !s.target || findTarget(s.target));  // skip parts not on the page right now
    els = build();
    document.addEventListener("keydown", key, true);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, { passive: true });
    go(0);
  }

  function end() {
    if (!els) return;
    remember();
    document.removeEventListener("keydown", key, true);
    window.removeEventListener("resize", place);
    window.removeEventListener("scroll", place);
    els.wrap.remove();
    els = null;
    window.scrollTo(0, 0);
    if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
  }

  document.addEventListener("click", (e) => { if (e.target.closest("[data-tour]")) { e.preventDefault(); start(); } });

  // First visit: only on the plain home page (not a shared link to a search or another page).
  window.addEventListener("load", () => {
    if (seen() || location.search || (location.hash && location.hash !== "#")) return;
    setTimeout(() => { if (!seen() && !location.search && (!location.hash || location.hash === "#")) start(); }, 1200);
  });
})();

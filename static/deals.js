(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const MULTI = ["kw", "fund", "category", "country"];
  const DEFAULTS = { scope: "elite" };
  let opts = { elite: [], categories: [], countries: [], affinity: [] };
  let state = fromSearch(location.search);
  let rows = [];
  let page = 1;
  let total = 0;
  let inflight = null;
  let timer = null;
  let thesisText = new URLSearchParams(location.search).get("t") || "";

  // ---------- state <-> URL ----------
  function fromSearch(search) {
    const p = new URLSearchParams(search);
    const s = {};
    for (const [k, v] of p) {
      if (k === "page" || k === "t") continue;
      if (MULTI.includes(k)) (s[k] = s[k] || []).push(v);
      else s[k] = v;
    }
    return { ...DEFAULTS, ...s };
  }
  function toParams(s, extra = {}) {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries({ ...s, ...extra })) {
      if (v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length)) continue;
      if (Array.isArray(v)) v.forEach((x) => p.append(k, x));
      else p.append(k, v);
    }
    return p;
  }
  function set(k, v) {
    if (v === "" || v === undefined || (Array.isArray(v) && !v.length)) delete state[k];
    else state[k] = v;
    changed();
  }
  function changed(immediate) {
    clearTimeout(timer);
    timer = setTimeout(() => load(true), immediate ? 0 : 140);
    renderFilters();
  }

  // ---------- formatting ----------
  const money = (v) => {
    if (v == null) return "";
    const a = Math.abs(v);
    if (a >= 1e9) return "$" + (v / 1e9).toFixed(1) + "B";
    if (a >= 1e8) return "$" + Math.round(v / 1e6) + "M";
    if (a >= 1e6) return "$" + (v / 1e6).toFixed(1) + "M";
    if (a >= 1e3) return "$" + Math.round(v / 1e3) + "K";
    return "$" + Math.round(v);
  };
  const num = (v) => (v == null ? "" : v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e4 ? Math.round(v / 1e3) + "K" : Math.round(v).toLocaleString());
  const pct = (v) => (v == null ? "" : `<span class="${v > 0 ? "pos" : v < 0 ? "neg" : ""}">${v > 0 ? "+" : ""}${Math.round(v * 100)}%</span>`);
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const day = (iso) => { if (!iso) return ""; const [y, m, d] = iso.slice(0, 10).split("-"); return `${MONTHS[+m - 1]} ${+d}, ${y}`; };
  const stage = (s) => (s ? s.replace(/_/g, " ").replace(/^series /, "Series ").replace(/\b(a|b|c|d|e|f|g|h)\b$/, (x) => x.toUpperCase()) : "");

  function highlight(text) {
    const words = (state.kw || []).concat(state.q ? state.q.split(/\s+/) : []).map((w) => w.trim()).filter((w) => w.length > 2);
    let html = esc(text);
    if (!words.length) return html;
    const re = new RegExp("\\b(" + words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").slice(0, Math.max(4, w.length - 4))).join("|") + ")\\w*", "gi");
    return html.replace(re, (m) => `<mark>${m}</mark>`);
  }

  // ---------- table ----------
  const COLS = [
    { key: "org_name", label: "Company", cls: "co", render: (r) => `<a class="name" href="/c/${encodeURIComponent(r.org_permalink)}">${esc(r.org_name)}</a>${r.website ? ` <a class="site" href="https://${esc(r.website)}" target="_blank" rel="noopener">${esc(r.website)}</a>` : ""}${r.blurb ? `<div class="blurb">${highlight(r.blurb)}</div>` : ""}` },
    { key: "announced_on", label: "Announced", render: (r) => day(r.announced_on) },
    { key: "money_raised_usd", label: "Round", n: true, render: (r) => money(r.money_raised_usd) },
    { key: "post_money_usd", label: "Post-money", n: true, render: (r) => money(r.post_money_usd) },
    { key: null, label: "Lead", cls: "wrap", render: (r) => r.lead_investors.map((l) => (r.elite_investors.includes(l) ? `<b>${esc(l)}</b>` : esc(l))).join(", ") },
    { key: "elite_count", label: "Elite VCs", cls: "wrap", render: (r) => { const others = r.all_investors.length - r.elite_investors.length; return esc(r.elite_investors.join(", ")) + (others > 0 ? ` <span class="muted">+${others}</span>` : ""); } },
    { key: null, label: "Stage now", render: (r) => esc(stage(r.current_stage)) },
    { key: "total_funding", label: "Total raised", n: true, render: (r) => money(r.total_funding) },
    { key: "headcount", label: "People", n: true, render: (r) => num(r.headcount) },
    { key: "delta_year_headcount", label: "People YoY", n: true, render: (r) => pct(r.delta_year_headcount) },
    { key: "delta_year_followers", label: "Followers YoY", n: true, render: (r) => pct(r.delta_year_followers) },
    { key: "total_visits", label: "Visits/mo", n: true, render: (r) => num(r.total_visits) },
    { key: "delta_yoy", label: "Visits YoY", n: true, render: (r) => pct(r.delta_yoy) },
    { key: null, label: "HQ", render: (r) => `${esc(r.city || "")}${r.country ? `<div class="muted">${esc(r.country)}</div>` : ""}` },
    { key: null, label: "Affinity", render: (r) => (r.affinity_link ? `<a href="${esc(r.affinity_link)}" target="_blank" rel="noopener">${esc(r.affinity_status || "Open")}</a>` : esc(r.affinity_status || "")) + (r.affinity_owner ? `<div class="muted">${esc(r.affinity_owner)}</div>` : "") },
  ];

  function renderHead(sort, dir) {
    const cells = COLS.map((c) => {
      if (!c.key) return `<th class="${c.n ? "n" : ""}">${c.label}</th>`;
      const on = sort === c.key;
      return `<th class="${c.n ? "n" : ""} sortable${on ? " on" : ""}" data-sort="${c.key}">${c.label}${on ? (dir === "asc" ? " ↑" : " ↓") : ""}</th>`;
    });
    $("#grid thead").innerHTML = `<tr>${cells.join("")}</tr>`;
  }
  function rowHtml(r) {
    return `<tr>${COLS.map((c) => `<td class="${c.n ? "n" : ""} ${c.cls || ""}">${c.render(r)}</td>`).join("")}</tr>`;
  }
  $("#grid").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-sort]");
    if (!th) return;
    const key = th.dataset.sort;
    const cur = state.sort || (hasText() ? "relevance" : "announced_on");
    if (cur === key) state.dir = state.dir === "asc" ? "desc" : "asc";
    else { state.sort = key; state.dir = key === "org_name" ? "asc" : "desc"; }
    changed(true);
  });
  const hasText = () => (state.kw && state.kw.length) || state.q;

  // ---------- data ----------
  async function load(reset) {
    if (reset) page = 1;
    if (inflight) inflight.abort();
    inflight = new AbortController();
    const params = toParams(state, { page });
    history.replaceState(null, "", "/?" + toParams(state, thesisText ? { t: thesisText } : {}).toString());
    $("#csv").href = "/export.csv?" + toParams(state).toString();
    $("#loadbar").classList.add("on");
    try {
      const res = await fetch("/api/deals?" + params.toString(), { signal: inflight.signal });
      const d = await res.json();
      total = d.summary.n || 0;
      rows = reset ? d.rows : rows.concat(d.rows);
      renderHead(d.sort, d.dir);
      if (reset) $("#grid tbody").innerHTML = rows.length ? rows.map(rowHtml).join("") : `<tr><td colspan="${COLS.length}" class="empty">No rounds match. Try removing a filter.</td></tr>`;
      else $("#grid tbody").insertAdjacentHTML("beforeend", d.rows.map(rowHtml).join(""));
      if (reset) { renderStats(d.summary); renderChart(d.monthly); renderFunds(d.funds); $("#tablewrap").scrollTop = 0; }
      $("#count").textContent = `${total.toLocaleString()} rounds · showing ${rows.length.toLocaleString()} · ${d.ms} ms`;
      $("#more").hidden = rows.length >= total;
    } catch (err) {
      if (err.name !== "AbortError") $("#count").textContent = "Something went wrong loading results.";
    } finally {
      $("#loadbar").classList.remove("on");
    }
  }
  $("#more").addEventListener("click", () => { page += 1; load(false); });

  function renderStats(s) {
    const items = [
      [(s.n || 0).toLocaleString(), "Series A rounds"],
      [money(s.raised), "raised in these rounds"],
      [money(s.median_round), "median round"],
      [money(s.median_post), "median post-money"],
      [(s.elite_led || 0).toLocaleString(), "led by an elite fund"],
      [(s.raised_since || 0).toLocaleString(), "have raised a B or later"],
      [(s.not_in_affinity || 0).toLocaleString(), "not in Affinity"],
    ];
    $("#stats").innerHTML = items.map(([v, l]) => `<div><b>${v || "–"}</b><span>${l}</span></div>`).join("");
  }
  function renderChart(monthly) {
    if (!monthly.length) { $("#chart").innerHTML = ""; return; }
    const max = Math.max(...monthly.map((m) => m.n), 1);
    $("#chart").innerHTML = monthly.map((m) => {
      const [y, mo] = m.month.split("-");
      return `<div class="bar" title="${MONTHS[+mo - 1]} ${y}: ${m.n} rounds, ${m.elite_led} elite-led"><i style="height:${(m.n / max) * 100}%"><em style="height:${m.n ? (m.elite_led / m.n) * 100 : 0}%"></em></i></div>`;
    }).join("") + `<div class="chart-legend"><span><i class="k1"></i>rounds per month</span><span><i class="k2"></i>elite-led</span></div>`;
  }
  function renderFunds(funds) {
    const sel = new Set(state.fund || []);
    $("#fundbar").innerHTML = funds.slice(0, 40).map((f) => `<button type="button" class="fund${sel.has(f.investor) ? " on" : ""}" data-fund="${esc(f.investor)}">${esc(f.investor)} <span>${f.n}</span></button>`).join("");
  }
  $("#fundbar").addEventListener("click", (e) => {
    const b = e.target.closest("[data-fund]");
    if (!b) return;
    const cur = new Set(state.fund || []);
    cur.has(b.dataset.fund) ? cur.delete(b.dataset.fund) : cur.add(b.dataset.fund);
    set("fund", [...cur]);
  });

  // ---------- filters ----------
  const FIELDS = [
    { k: "scope", label: "Investors", type: "select", options: [["elite", "An elite fund invested"], ["elite_lead", "An elite fund led"], ["all", "Any investor"]] },
    { k: "kw", label: "Thesis keywords", type: "tokens", placeholder: "Add a keyword" },
    { k: "fund", label: "Specific investors", type: "multi", source: () => opts.elite, remote: "/api/investors?q=" },
    { k: "category", label: "Categories", type: "multi", source: () => opts.categories },
    { k: "country", label: "Country", type: "multi", source: () => opts.countries },
    { k: ["since", "until"], label: "Announced", type: "range", input: "date" },
    { k: ["min_round_m", "max_round_m"], label: "Round size, $M", type: "range" },
    { k: ["min_post_m", "max_post_m"], label: "Post-money, $M", type: "range", more: true },
    { k: ["min_headcount", "max_headcount"], label: "Headcount", type: "range", more: true },
    { k: "min_hc_growth", label: "People YoY, at least %", type: "number" },
    { k: "min_fol_growth", label: "Followers YoY, at least %", type: "number", more: true },
    { k: "founded_since", label: "Founded in or after", type: "number", placeholder: "year", more: true },
    { k: "progress", label: "Since the A", type: "select", options: [["", "Any"], ["still_a", "Still at Series A"], ["raised_since", "Raised a B or later"]] },
    { k: "affinity", label: "Affinity", type: "select", options: () => [["", "Any"], ["none", "Not in Affinity"], ["in", "In Affinity"], ...opts.affinity.map((a) => [a, a])], more: true },
    { k: "q", label: "Text search", type: "text", placeholder: "Name, website, investor", more: true },
    { k: "new_only", label: "New in data this week", type: "check", more: true },
  ];
  const isOn = (f) => (Array.isArray(f.k) ? f.k.some((k) => state[k]) : f.k === "scope" ? state.scope && state.scope !== "elite" : !!(state[f.k] && (!Array.isArray(state[f.k]) || state[f.k].length)));

  let showMore = false;
  function renderFilters() {
    const root = $("#filters");
    const focus = document.activeElement && root.contains(document.activeElement) ? document.activeElement.dataset.focus : null;
    const hidden = FIELDS.filter((f) => f.more && !showMore && !isOn(f)).length;
    root.innerHTML = FIELDS.map((f, i) => (f.more && !showMore && !isOn(f) ? "" : `<div class="field${isOn(f) ? " on" : ""}" data-i="${i}"><label>${f.label}</label>${control(f)}</div>`)).join("") +
      `<div class="field clear"><label>&nbsp;</label><span><button type="button" id="more-filters">${showMore ? "Fewer filters" : `More filters (${hidden})`}</button> <button type="button" id="clear">Clear</button></span></div>`;
    if (focus) { const el = root.querySelector(`[data-focus="${focus}"]`); if (el) { el.focus(); if (el.setSelectionRange && el.type === "text") el.setSelectionRange(el.value.length, el.value.length); } }
  }
  function control(f) {
    if (f.type === "select") {
      const options = typeof f.options === "function" ? f.options() : f.options;
      return `<select data-k="${f.k}" data-focus="${f.k}">${options.map(([v, l]) => `<option value="${esc(v)}"${(state[f.k] || "") === v ? " selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
    }
    if (f.type === "range") {
      const t = f.input || "number";
      return `<div class="pair"><input type="${t}" step="any" data-k="${f.k[0]}" data-focus="${f.k[0]}" placeholder="min" value="${esc(state[f.k[0]] || "")}"><input type="${t}" step="any" data-k="${f.k[1]}" data-focus="${f.k[1]}" placeholder="max" value="${esc(state[f.k[1]] || "")}"></div>`;
    }
    if (f.type === "number" || f.type === "text") return `<input type="${f.type === "text" ? "text" : "number"}" step="any" data-k="${f.k}" data-focus="${f.k}" placeholder="${f.placeholder || ""}" value="${esc(state[f.k] || "")}">`;
    if (f.type === "check") return `<label class="check"><input type="checkbox" data-k="${f.k}"${state[f.k] === "1" ? " checked" : ""}> Only new rounds</label>`;
    const vals = state[f.k] || [];
    const toks = vals.map((v) => `<span class="tok">${esc(v)}<button type="button" data-rm="${f.k}" data-v="${esc(v)}" aria-label="Remove">×</button></span>`).join("");
    if (f.type === "tokens") return `<div class="tokens">${toks}<input type="text" data-tok="${f.k}" data-focus="tok-${f.k}" placeholder="${vals.length ? "" : f.placeholder}"></div>`;
    return `<div class="tokens multi">${toks}<input type="text" data-multi="${f.k}" data-focus="multi-${f.k}" placeholder="${vals.length ? "Add" : "Any"}"></div>`;
  }
  const filters = $("#filters");
  filters.addEventListener("change", (e) => {
    const el = e.target;
    if (!el.dataset.k) return;
    if (el.type === "number" || el.type === "text") {
      // Typed fields update on input; re-rendering here on blur would swallow the next click.
      clearTimeout(el._t);
      const cur = state[el.dataset.k] || "";
      if (el.value === cur) return;
      if (el.value) state[el.dataset.k] = el.value;
      else delete state[el.dataset.k];
      el.closest(".field").classList.toggle("on", !!el.value);
      load(true);
      return;
    }
    set(el.dataset.k, el.type === "checkbox" ? (el.checked ? "1" : "") : el.value);
  });
  filters.addEventListener("input", (e) => {
    const el = e.target;
    if (el.dataset.k && (el.type === "number" || el.type === "text")) {
      clearTimeout(el._t);
      el._t = setTimeout(() => { if (el.value) state[el.dataset.k] = el.value; else delete state[el.dataset.k]; clearTimeout(timer); timer = setTimeout(() => load(true), 0); el.closest(".field").classList.toggle("on", !!el.value); }, 250);
    }
    if (el.dataset.multi) openMenu(el);
  });
  filters.addEventListener("focusin", (e) => { if (e.target.dataset.multi) openMenu(e.target); });
  filters.addEventListener("keydown", (e) => {
    const el = e.target;
    if (el.dataset.tok && e.key === "Enter") {
      e.preventDefault();
      const v = el.value.trim();
      if (v) set(el.dataset.tok, [...(state[el.dataset.tok] || []), v]);
    }
    if ((el.dataset.tok || el.dataset.multi) && e.key === "Backspace" && !el.value) {
      const k = el.dataset.tok || el.dataset.multi;
      const cur = state[k] || [];
      if (cur.length) set(k, cur.slice(0, -1));
    }
    if (el.dataset.multi && e.key === "Enter") {
      e.preventDefault();
      const first = $("#menu [data-pick]");
      if (first) first.click();
    }
    if (e.key === "Escape") closeMenu();
  });
  filters.addEventListener("click", (e) => {
    const rm = e.target.closest("[data-rm]");
    if (rm) set(rm.dataset.rm, (state[rm.dataset.rm] || []).filter((v) => v !== rm.dataset.v));
    if (e.target.id === "more-filters") { showMore = !showMore; renderFilters(); return; }
    if (e.target.id === "clear") { state = { ...DEFAULTS }; thesisText = ""; $("#thesis").value = ""; $("#interp").innerHTML = ""; changed(true); }
  });

  // multi-select dropdown
  let menuFor = null;
  async function openMenu(input) {
    const k = input.dataset.multi;
    const f = FIELDS.find((x) => x.k === k);
    const q = input.value.trim().toLowerCase();
    let list = f.source();
    if (f.remote && q.length >= 2) {
      try { list = await (await fetch(f.remote + encodeURIComponent(q))).json(); } catch (_) { /* keep local list */ }
    }
    const chosen = new Set(state[k] || []);
    const items = list.filter((x) => !chosen.has(x) && (!q || x.toLowerCase().includes(q))).slice(0, 80);
    let menu = $("#menu");
    if (!menu) { menu = document.createElement("div"); menu.id = "menu"; menu.className = "menu"; document.body.appendChild(menu); }
    menuFor = { input, k };
    menu.innerHTML = items.length ? items.map((x) => `<div class="opt" data-pick="${esc(x)}">${esc(x)}</div>`).join("") : `<div class="opt muted">No matches</div>`;
    const r = input.closest(".tokens").getBoundingClientRect();
    menu.style.left = `${r.left + window.scrollX}px`;
    menu.style.top = `${r.bottom + window.scrollY + 4}px`;
    menu.style.minWidth = `${Math.max(r.width, 240)}px`;
    menu.hidden = false;
  }
  function closeMenu() { const m = $("#menu"); if (m) m.hidden = true; menuFor = null; }
  document.addEventListener("mousedown", (e) => {
    const pick = e.target.closest("#menu [data-pick]");
    if (pick && menuFor) {
      e.preventDefault();
      const { k } = menuFor;
      set(k, [...(state[k] || []), pick.dataset.pick]);
      setTimeout(() => { const el = filters.querySelector(`[data-multi="${k}"]`); if (el) el.focus(); }, 0);
      return;
    }
    if (!e.target.closest("#menu") && !(e.target.dataset && e.target.dataset.multi)) closeMenu();
  });

  // ---------- thesis ----------
  const ta = $("#thesis");
  ta.value = thesisText;
  const grow = () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 160) + "px"; };
  ta.addEventListener("input", grow);
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#thesis-form").requestSubmit(); } });
  $("#thesis-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = ta.value.trim();
    if (!text) return;
    const go = $("#thesis-go");
    go.disabled = true;
    $("#interp").innerHTML = `<span class="thinking">Reading your thesis…</span>`;
    try {
      const res = await fetch("/api/thesis", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text }) });
      const d = await res.json();
      thesisText = text;
      state = { ...DEFAULTS };
      for (const [k, v] of d.params || []) {
        if (MULTI.includes(k)) (state[k] = state[k] || []).push(v);
        else state[k] = v;
      }
      $("#interp").innerHTML = d.summary ? `${esc(d.summary)} <span class="muted">Adjust any filter below.</span>` : d.error ? `<span class="neg">Couldn't read that thesis, so this is a plain text search.</span>` : "";
      changed(true);
    } finally {
      go.disabled = false;
    }
  });

  // ---------- saved theses ----------
  async function loadTheses() {
    const list = await (await fetch("/api/theses")).json();
    $("#theses").innerHTML = list.length ? list.map((t) => `<div class="thesis-item" data-id="${t.id}" data-params="${esc(t.params)}" data-thesis="${esc(t.thesis)}">
      <div class="t-name">${esc(t.name)}${t.new ? ` <span class="new">${t.new} new</span>` : ""}</div>
      ${t.thesis ? `<div class="t-text">${esc(t.thesis)}</div>` : ""}
      <button type="button" class="t-del" data-del="${t.id}" aria-label="Delete">×</button></div>`).join("") : `<p class="muted small">Nothing saved yet. Run a thesis, then press Save thesis.</p>`;
  }
  $("#theses").addEventListener("click", async (e) => {
    const del = e.target.closest("[data-del]");
    if (del) {
      e.stopPropagation();
      if (confirm("Delete this saved thesis?")) { await fetch(`/api/theses/${del.dataset.del}`, { method: "DELETE" }); loadTheses(); }
      return;
    }
    const item = e.target.closest(".thesis-item");
    if (!item) return;
    state = fromSearch("?" + item.dataset.params);
    thesisText = item.dataset.thesis;
    ta.value = thesisText;
    grow();
    $("#interp").innerHTML = `<span class="muted">Saved thesis:</span> ${esc(item.querySelector(".t-name").firstChild.textContent)}`;
    changed(true);
    await fetch(`/api/theses/${item.dataset.id}/viewed`, { method: "POST" });
    setTimeout(loadTheses, 300);
  });
  $("#save-wrap").addEventListener("click", async (e) => {
    if (e.target.id === "save") {
      $("#save-wrap").innerHTML = `<input id="save-name" type="text" placeholder="Name this thesis" value="${esc(thesisText.slice(0, 60))}"><button type="button" class="primary" id="save-ok">Save</button><button type="button" id="save-cancel">Cancel</button>`;
      $("#save-name").focus();
      $("#save-name").select();
    } else if (e.target.id === "save-ok") {
      await fetch("/api/theses", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: $("#save-name").value, thesis: thesisText, params: toParams(state).toString() }) });
      $("#save-wrap").innerHTML = `<button type="button" id="save">Saved</button>`;
      setTimeout(() => { $("#save-wrap").innerHTML = `<button type="button" id="save">Save thesis</button>`; }, 1500);
      loadTheses();
    } else if (e.target.id === "save-cancel") {
      $("#save-wrap").innerHTML = `<button type="button" id="save">Save thesis</button>`;
    }
  });
  $("#save-wrap").addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.id === "save-name") $("#save-ok").click(); });

  // ---------- boot ----------
  renderFilters();
  grow();
  load(true);
  fetch("/api/options").then((r) => r.json()).then((o) => { opts = o; renderFilters(); });
  loadTheses();
})();

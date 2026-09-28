/* Rail corridor explorer — renders the inlined JSON (see export.py). */
(function () {
  "use strict";
  const BUNDLE = JSON.parse(document.getElementById("data").textContent);
  const RIVERS = BUNDLE.rivers || [];
  const NS = "http://www.w3.org/2000/svg";
  const DIRS = ["outbound", "return"];

  const VARIANTS = BUNDLE.variants || {};
  const TIGHT = BUNDLE.tight_change_min || 10;
  const state = { date: BUNDLE.dates[BUNDLE.dates.length - 1], dir: "outbound", on: {}, sel: 0, variant: "" };
  const hasVariant = (v) => !v || Boolean(BUNDLE.docs[`${state.date}@${v}`]);
  const doc = () => BUNDLE.docs[state.variant && hasVariant(state.variant) ? `${state.date}@${state.variant}` : state.date];

  /* ---------- helpers ---------- */
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const m = (t) => { const [h, mm] = t.split(":").map(Number); return h * 60 + mm; };
  const fmtT = (t) => { const x = m(t); const h = Math.floor(x / 60); return h >= 24 ? `${String(h - 24).padStart(2, "0")}:${String(x % 60).padStart(2, "0")}+1` : t; };
  const fmtDur = (min) => `${Math.floor(min / 60)} h ${String(min % 60).padStart(2, "0")}`;
  const cvar = (id) => `var(--c-${id})`;
  const city = (name) => name.replace(/[-,(].*$/, "").split(" ")[0];
  const stn = (id) => (doc().stations[id] || { name: id });
  const corridor = (id) => doc().corridors.find((c) => c.id === id);
  const el = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; };
  /* change minutes between consecutive legs; tight ones get flagged */
  const waits = (j) => j.legs.slice(1).map((l, i) => m(l.dep) - m(j.legs[i].arr));
  const isTight = (w) => w < TIGHT;
  const h = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };

  function resetCorridors() {
    const on = {};
    for (const c of doc().corridors) on[c.id] = state.on[c.id] ?? true;
    state.on = on;
  }
  function fastestIndex() {
    const deps = currentDeps();
    let best = 0;
    deps.forEach((j, i) => { if (j.duration_min < deps[best].duration_min) best = i; });
    return best;
  }
  const currentDeps = () => (doc().journeys[state.dir] || []).filter((j) => state.on[j.corridor]);

  /* ---------- header ---------- */
  function drawHeader() {
    const d = doc();
    const [a, b] = d.pair.title.split("⇄");
    document.getElementById("title").innerHTML = b ? `${esc(a.trim())} <span class="arr">⇄</span> ${esc(b.trim())}` : esc(d.pair.title);
    document.title = `${d.pair.title} by Rail`;
    document.getElementById("subtitle").textContent = d.pair.subtitle || `Rail journeys between ${d.pair.from} and ${d.pair.to}, computed from GTFS timetables.`;
    const floor = d.min_change_min ? ` · every change ≥ ${d.min_change_min} min` : "";
    document.getElementById("eyebrow").textContent = `Departures ${d.window[0]}–${d.window[1]} · ${d.weekday} ${d.date}${floor} · computed from timetable data`;
    const dirs = document.getElementById("dirs");
    dirs.innerHTML = "";
    for (const dir of DIRS) {
      if (!d.journeys[dir]) continue;
      const bt = document.createElement("button");
      bt.textContent = dir === "outbound" ? `${city(d.pair.from)} → ${city(d.pair.to)}` : `${city(d.pair.to)} → ${city(d.pair.from)}`;
      bt.setAttribute("aria-pressed", String(state.dir === dir));
      bt.onclick = () => { state.dir = dir; state.sel = fastestIndex(); render(); };
      dirs.appendChild(bt);
    }
    const sel = document.getElementById("date");
    if (BUNDLE.dates.length > 1) {
      document.getElementById("dateWrap").hidden = false;
      sel.innerHTML = BUNDLE.dates.map((x) => `<option value="${x}"${x === state.date ? " selected" : ""}>${BUNDLE.docs[x].weekday.slice(0, 3)} ${x}</option>`).join("");
      sel.onchange = () => { state.date = sel.value; resetCorridors(); state.sel = fastestIndex(); drawChips(); render(); };
    }
    const vs = Object.entries(VARIANTS);
    const vbox = document.getElementById("variant");
    vbox.hidden = !vs.length;
    vbox.innerHTML = "";
    for (const [v, min] of [["", 0], ...vs]) {
      const bt = document.createElement("button");
      bt.textContent = v ? `Changes ≥ ${min} min` : "Timetabled changes";
      bt.title = v ? `Searched again with at least ${min} min for every change` : `Minimum change times from the timetable data; changes under ${TIGHT} min are marked`;
      bt.disabled = !hasVariant(v);
      bt.setAttribute("aria-pressed", String((state.variant && hasVariant(state.variant) ? state.variant : "") === v));
      bt.onclick = () => { state.variant = v; resetCorridors(); state.sel = fastestIndex(); drawChips(); render(); };
      vbox.appendChild(bt);
    }
  }

  function drawNotes() {
    const box = document.getElementById("notes");
    box.innerHTML = "";
    const d = doc();
    for (const n of d.notices || []) box.appendChild(h("div", "warn", `<span aria-hidden="true">⚠</span><span>${esc(n)}</span>`));
    for (const x of (d.missing || {})[state.dir] || []) {
      box.appendChild(h("div", "warn miss", `<span aria-hidden="true">✕</span><span><b>${x.reason.includes("slower") ? "Not shown" : "Not in data"}: ${esc(x.name)}.</b> ${esc(x.reason)}. ${esc(x.note || "")}</span>`));
    }
  }

  /* ---------- map ---------- */
  const W = 540, PAD = 34;
  let P = null, H = 600;
  function setupProjection() {
    const d = doc();
    const pts = [];
    for (const c of d.corridors) for (const p of c.geometry) pts.push(p);
    for (const s of Object.values(d.stations)) if (s.on_map) pts.push([s.lat, s.lon]);
    let la0 = Math.min(...pts.map((p) => p[0])), la1 = Math.max(...pts.map((p) => p[0]));
    let lo0 = Math.min(...pts.map((p) => p[1])), lo1 = Math.max(...pts.map((p) => p[1]));
    const padLat = (la1 - la0) * 0.04 + 0.05, padLon = (lo1 - lo0) * 0.12 + 0.2;
    la0 -= padLat; la1 += padLat; lo0 -= padLon; lo1 += padLon;
    const cos = Math.cos(((la0 + la1) / 2) * Math.PI / 180);
    const k = (W - 2 * PAD) / ((lo1 - lo0) * cos);
    H = Math.max(360, Math.min(820, Math.round((la1 - la0) * k + 2 * PAD)));
    const k2 = Math.min(k, (H - 2 * PAD) / (la1 - la0));
    const offX = (W - (lo1 - lo0) * cos * k2) / 2, offY = (H - (la1 - la0) * k2) / 2;
    P = (lat, lon) => [offX + (lon - lo0) * cos * k2, offY + (la1 - lat) * k2];
    P.k = k2; P.bounds = [la0, la1, lo0, lo1];
  }
  function offsetPath(pts, off) {
    const out = [];
    for (let i = 0; i < pts.length; i++) {
      const a = pts[Math.max(0, i - 1)], b = pts[i], c = pts[Math.min(pts.length - 1, i + 1)];
      let nx = 0, ny = 0;
      for (const [p, q] of [[a, b], [b, c]]) { const dx = q[0] - p[0], dy = q[1] - p[1], L = Math.hypot(dx, dy) || 1; nx += -dy / L; ny += dx / L; }
      const L = Math.hypot(nx, ny) || 1;
      out.push([b[0] + (nx / L) * off, b[1] + (ny / L) * off]);
    }
    return out;
  }
  const dPath = (pts) => pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");

  function drawMap() {
    const d = doc();
    const svg = document.getElementById("map");
    svg.innerHTML = "";
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    el("rect", { x: 0, y: 0, width: W, height: H, rx: 8, fill: "var(--land)" }, svg);
    const [la0, la1, lo0, lo1] = P.bounds;
    for (let lat = Math.ceil(la0); lat <= Math.floor(la1); lat++) {
      const [x1, y] = P(lat, lo0), [x2] = P(lat, lo1);
      el("line", { x1, y1: y, x2, y2: y, stroke: "var(--soft)", "stroke-width": 1 }, svg);
      const t = el("text", { x: W - 8, y: y - 3, "text-anchor": "end", fill: "var(--muted)", "font-size": 10, "font-family": "IBM Plex Mono, monospace" }, svg);
      t.textContent = lat + "°N";
    }
    for (const r of RIVERS) {
      const inside = r.points.some(([a, b]) => a >= la0 && a <= la1 && b >= lo0 && b <= lo1);
      if (!inside) continue;
      el("path", { d: dPath(r.points.map((p) => P(...p))), fill: "none", stroke: "var(--water)", "stroke-width": r.name === "Elbe" ? 3.2 : 2.4, "stroke-linecap": "round", "stroke-linejoin": "round", opacity: 0.75 }, svg);
      const [lx, ly] = P(...r.label_at);
      if (lx > 0 && lx < W && ly > 0 && ly < H) { const t = el("text", { x: lx + 8, y: ly, class: "river-label" }, svg); t.textContent = r.name; }
    }

    const selDep = currentDeps()[state.sel];
    const visible = d.corridors.filter((c) => state.on[c.id]);
    const n = visible.length;
    visible.forEach((c, i) => {
      const off = (i - (n - 1) / 2) * 4.5;
      const pts = offsetPath(c.geometry.map((p) => P(...p)), off);
      const active = selDep && selDep.corridor === c.id;
      el("path", { d: dPath(pts), class: "route-line", stroke: "var(--land)", "stroke-width": active ? 9 : 7 }, svg);
      el("path", { d: dPath(pts), class: "route-line", stroke: cvar(c.id), "stroke-width": active ? 5 : 4, opacity: selDep && !active ? 0.3 : 1 }, svg);
    });
    // exact path of the selected journey, on top
    if (selDep) {
      const pts = [];
      for (const lg of selDep.legs) for (const s of lg.stops) { const st = stn(s); if (st.lat != null) pts.push(P(st.lat, st.lon)); }
      el("path", { d: dPath(pts), class: "route-line", stroke: cvar(selDep.corridor), "stroke-width": 2, "stroke-dasharray": "1 0", opacity: 0.95 }, svg);
    }

    const changes = new Set(), tightAt = new Set();
    if (selDep) selDep.legs.slice(1).forEach((l, i) => { changes.add(l.from); if (isTight(waits(selDep)[i])) tightAt.add(l.from); });
    const onSel = new Set();
    if (selDep) selDep.legs.forEach((l) => l.stops.forEach((s) => onSel.add(s)));
    const placed = [];
    // label priority: ends, this direction's change stations, other hubs
    const transfers = new Set();
    for (const j of currentDeps()) j.legs.slice(1).forEach((l) => transfers.add(l.from));
    const prio = ([id, s]) => (s.kind === "end" ? 3 : 0) + (changes.has(id) ? 2 : 0) + (transfers.has(id) ? 1 : 0) + (s.hub ? 0.5 : 0);
    const shown = new Set(onSel);
    for (const c of visible) c.path.forEach((id) => shown.add(id));
    const entries = Object.entries(d.stations).filter(([id, s]) => s.kind === "end" || (s.on_map && shown.has(id)) || changes.has(id));
    entries.sort((a, b) => prio(b) - prio(a));
    for (const [id, s] of entries) {
      const [x, y] = P(s.lat, s.lon);
      const ch = changes.has(id);
      const big = s.kind !== "minor";
      el("circle", { cx: x, cy: y, r: ch ? 7.5 : big ? 5.5 : 3, fill: "var(--surface)", stroke: ch ? cvar(selDep.corridor) : "var(--ink)", "stroke-width": ch ? 3.5 : big ? 2.4 : 1.6, opacity: selDep && !onSel.has(id) && !big ? 0.5 : 1 }, svg);
      if (s.kind === "end") el("circle", { cx: x, cy: y, r: 2.5, fill: "var(--ink)" }, svg);
      if (!big && !ch) continue;
      if (tightAt.has(id)) el("circle", { cx: x, cy: y, r: 11.5, fill: "none", stroke: "var(--tight)", "stroke-width": 2, "stroke-dasharray": "3 2.5" }, svg);
      const label = s.name + (ch ? " ⇄" : "") + (tightAt.has(id) ? " !" : "");
      const w = label.length * 6.6 + 4;
      const right = x < W * 0.62;
      const lx = right ? x + 10 : x - 10;
      const box = right ? [lx, y - 9, lx + w, y + 5] : [lx - w, y - 9, lx, y + 5];
      if (!ch && s.kind !== "end" && placed.some((b) => !(box[2] < b[0] || box[0] > b[2] || box[3] < b[1] || box[1] > b[3]))) continue;
      placed.push(box);
      const t = el("text", { x: lx, y: y + 4, "text-anchor": right ? "start" : "end", class: "stn-label" }, svg);
      t.textContent = label;
    }
    const px100 = (100 / 111.3) * P.k, sx = PAD, sy = H - 16;
    el("line", { x1: sx, y1: sy, x2: sx + px100, y2: sy, stroke: "var(--muted)", "stroke-width": 2 }, svg);
    for (const x of [sx, sx + px100]) el("line", { x1: x, y1: sy - 4, x2: x, y2: sy + 4, stroke: "var(--muted)", "stroke-width": 2 }, svg);
    const st = el("text", { x: sx + px100 + 8, y: sy + 4, fill: "var(--muted)", "font-size": 11, "font-family": "IBM Plex Mono, monospace" }, svg);
    st.textContent = "100 km";
  }

  /* ---------- chips ---------- */
  function drawChips() {
    const box = document.getElementById("chips");
    box.innerHTML = "";
    for (const c of doc().corridors) {
      const b = document.createElement("button");
      b.className = "chip";
      b.dataset.id = c.id;
      b.setAttribute("aria-pressed", String(state.on[c.id]));
      b.innerHTML = `<span class="sw" style="background:${cvar(c.id)}"></span>${esc(c.name)}`;
      b.onclick = () => { state.on[c.id] = !state.on[c.id]; if (!currentDeps()[state.sel]) state.sel = 0; render(); };
      box.appendChild(b);
    }
  }

  /* ---------- timeline ---------- */
  let T0 = 240, T1 = 1080;
  const pct = (t) => ((t - T0) / (T1 - T0)) * 100 + "%";
  function setupAxis() {
    const all = DIRS.flatMap((dir) => doc().journeys[dir] || []);
    if (!all.length) return;
    T0 = Math.floor(Math.min(...all.map((j) => m(j.dep))) / 120) * 120;
    T1 = Math.ceil(Math.max(...all.map((j) => m(j.arr))) / 120) * 120 + 60;
  }
  function drawTimeline() {
    const tl = document.getElementById("tl");
    tl.innerHTML = "";
    document.getElementById("tlTitle").textContent = `Departures ${doc().window[0]}–${doc().window[1]}`;
    const ax = h("div", "axis");
    const hours = [];
    for (let x = T0; x <= T1; x += 120) hours.push(x);
    for (const x of hours) { const s = document.createElement("span"); s.style.left = pct(x); s.textContent = `${String(Math.floor(x / 60) % 24).padStart(2, "0")}:00`; ax.appendChild(s); }
    tl.appendChild(ax);
    const deps = currentDeps();
    if (!deps.length) {
      tl.appendChild(h("p", "legend-note", (doc().journeys[state.dir] || []).length ? "Turn on at least one route above the map." : "No journey found in the timetable data for this direction."));
      return;
    }
    deps.forEach((dp, i) => {
      const row = h("div", "row" + (i === state.sel ? " sel" : ""));
      row.tabIndex = 0;
      row.setAttribute("role", "button");
      row.style.setProperty("--c", cvar(dp.corridor));
      row.setAttribute("aria-label", `${corridor(dp.corridor).name}, ${dp.dep} to ${dp.arr}`);
      const ws = waits(dp);
      const tight = ws.filter(isTight);
      const flag = tight.length ? `<b class="tight-flag" title="Tight change: ${Math.min(...tight)} min">!</b>` : "";
      if (tight.length) row.setAttribute("aria-label", `${row.getAttribute("aria-label")}, tight change of ${Math.min(...tight)} minutes`);
      row.innerHTML = `<div class="lab"><i style="background:${cvar(dp.corridor)}"></i>${dp.dep}→${fmtT(dp.arr)}${flag}</div>`;
      const tr = h("div", "track");
      for (const x of hours) { const g = h("div", "gridline"); g.style.left = pct(x); tr.appendChild(g); }
      dp.legs.forEach((l, j) => {
        if (j > 0) {
          const a = m(dp.legs[j - 1].arr), b = m(l.dep);
          const g = h("div", "gap" + (isTight(b - a) ? " tight" : ""));
          g.title = `${b - a} min change at ${stn(l.from).name}`;
          g.style.left = pct(a);
          g.style.width = `calc(${pct(b)} - ${pct(a)})`;
          tr.appendChild(g);
        }
        const b = h("div", "leg");
        b.style.setProperty("--c", cvar(dp.corridor));
        b.style.left = pct(m(l.dep));
        b.style.width = `calc(${pct(m(l.arr))} - ${pct(m(l.dep))})`;
        if (m(l.arr) - m(l.dep) >= 55) b.textContent = l.label;
        b.title = `${l.label} ${stn(l.from).name} ${fmtT(l.dep)} → ${stn(l.to).name} ${fmtT(l.arr)}`;
        tr.appendChild(b);
      });
      const du = h("div", "dur");
      du.style.left = pct(m(dp.arr));
      du.textContent = fmtDur(dp.duration_min);
      tr.appendChild(du);
      row.appendChild(tr);
      const pick = () => { state.sel = i; render(); };
      row.onclick = pick;
      row.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } };
      tl.appendChild(row);
    });
  }

  /* ---------- detail ---------- */
  function drawDetail() {
    const box = document.getElementById("detail");
    const dp = currentDeps()[state.sel];
    if (!dp) { box.innerHTML = ""; return; }
    const c = corridor(dp.corridor);
    const fare = c.fare ? `<span class="tag">${esc(c.fare.summary.split("·")[0].trim())}</span>` : "";
    let out = `<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:baseline">
      <div><span class="eyebrow">${esc(c.name)}</span><div style="font:600 20px var(--display)">${dp.dep} → ${fmtT(dp.arr)} · ${fmtDur(dp.duration_min)} · ${dp.changes} change${dp.changes === 1 ? "" : "s"}</div></div>${fare}</div>`;
    out += `<ul class="legs" style="--c:${cvar(dp.corridor)}">`;
    dp.legs.forEach((l, j) => {
      const nStops = l.stops.length - 2;
      const extra = [l.operator, nStops > 0 ? `${nStops} stop${nStops === 1 ? "" : "s"} between` : "non-stop"].filter(Boolean).join(" · ");
      const joined = l.joined_at.map((x) => `through ${esc(stn(x.at).name)} (data split, continues as ${esc(x.label)})`).join("; ");
      out += `<li><span class="t">${fmtT(l.dep)}</span><span class="dot"></span><span>${esc(stn(l.from).name)}</span><span></span></li>`;
      out += `<li><div class="ride"><span class="ttype">${esc(l.label)}</span>${fmtDur(m(l.arr) - m(l.dep))} to ${esc(stn(l.to).name)} · ${esc(extra)}${joined ? `<br>${joined}` : ""}</div></li>`;
      if (j < dp.legs.length - 1) {
        const w = m(dp.legs[j + 1].dep) - m(l.arr);
        out += `<li><span class="t">${fmtT(l.arr)}</span><span class="dot"></span><span>${esc(stn(l.to).name)}</span><span></span></li>`;
        const walk = dp.legs[j + 1].from !== l.to ? ` · walk to ${esc(stn(dp.legs[j + 1].from).name)}` : "";
        const tightNote = isTight(w) ? ` <span class="tight-note">tight — a small delay breaks it</span>` : "";
        out += `<li><div class="ride walk${isTight(w) ? " tight" : ""}">Change · ${w} min${walk}${tightNote}</div></li>`;
      } else {
        out += `<li><span class="t">${fmtT(l.arr)}</span><span class="dot" style="background:var(--c)"></span><span><b>${esc(stn(l.to).name)}</b></span><span></span></li>`;
      }
    });
    out += "</ul>";
    for (const n of dp.notes || []) out += `<p class="legend-note" style="margin-top:8px">${esc(n)}</p>`;
    box.innerHTML = out;
  }

  /* ---------- corridor cards ---------- */
  function changeText(c) {
    const js = (doc().journeys[state.dir] || []).filter((j) => j.corridor === c.id);
    if (!js.length) return null;
    const counts = {};
    for (const j of js) { const k = j.legs.slice(1).map((l) => city(stn(l.from).name)).join(", "); counts[k] = (counts[k] || 0) + 1; }
    const top = Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0];
    const st = c.stats[state.dir];
    const range = st.changes_min === st.changes_max ? `${st.changes_min}` : `${st.changes_min}–${st.changes_max}`;
    return top ? `${range} (${top})` : range;
  }
  function drawRoutes() {
    const box = document.getElementById("routes");
    box.innerHTML = "";
    const d = doc();
    for (const c of d.corridors) {
      const st = c.stats[state.dir];
      const a = h("article", "rcard" + (st ? "" : " empty"));
      a.style.setProperty("--c", cvar(c.id));
      const facts = {};
      if (st) {
        facts["Duration"] = `≈ ${fmtDur(st.duration_median_min)}` + (st.duration_min_min !== st.duration_median_min ? ` (fastest ${fmtDur(st.duration_min_min)})` : "");
        facts["Changes"] = changeText(c);
        facts["Frequency"] = `${st.journeys} in the window` + (st.every_h ? ` · ~every ${st.every_h} h` : "");
        facts["First / last"] = `leaves ${st.earliest_dep} · arrives by ${fmtT(st.latest_arr)}`;
      } else {
        facts["This direction"] = "no journey in the data for this date";
      }
      if (c.fare) facts["Fare"] = c.fare.summary + "*";
      const tags = [...c.tags.map((t) => `<span class="tag">${esc(t)}</span>`), st ? "" : `<span class="tag miss">Not in data</span>`].join("");
      a.innerHTML = `<div style="display:flex;gap:6px;flex-wrap:wrap">${tags}</div>
        <h3>${esc(c.name)}</h3><p class="via">${esc(c.via)}</p>
        <dl class="facts">${Object.entries(facts).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
        ${c.fare && c.fare.note ? `<p class="note">${esc(c.fare.note)}</p>` : ""}`;
      box.appendChild(a);
    }
    if (d.fare_footnote) {
      const n = h("p", "legend-note");
      n.style.gridColumn = "1/-1";
      n.textContent = "* " + d.fare_footnote + " Fares are hand-edited estimates (GTFS has no prices).";
      box.appendChild(n);
    }
  }

  /* ---------- footer ---------- */
  function drawFoot() {
    const d = doc();
    const f = document.getElementById("foot");
    const feeds = d.feeds.map((x) => `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a> — © ${esc(x.attribution)}, ${esc(x.licence)}${x.valid_from ? `, valid ${esc(x.valid_from)} to ${esc(x.valid_to)}` : ""}`).join("; ");
    f.innerHTML = `<p><b>Timetable data:</b> ${feeds}. Map rivers are hand-drawn approximations.</p>
      <p>Times are computed from the published timetable for ${esc(d.weekday)} ${esc(d.date)} (generated ${esc(d.generated_at)}). Legs the data does not contain are shown as “not in data”, never estimated. Check your date at <a href="https://int.bahn.de" target="_blank" rel="noopener">int.bahn.de</a> or <a href="https://www.cd.cz" target="_blank" rel="noopener">cd.cz</a> before travelling.</p>`;
  }

  /* ---------- theme ---------- */
  function setupTheme() {
    let saved = "auto";
    try { saved = localStorage.getItem("rc-theme") || "auto"; } catch (e) { /* storage blocked */ }
    const apply = (t) => {
      if (t === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", t);
      document.querySelectorAll("#theme button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.themeSet === t)));
      try { localStorage.setItem("rc-theme", t); } catch (e) { /* storage blocked */ }
    };
    document.querySelectorAll("#theme button").forEach((b) => { b.onclick = () => apply(b.dataset.themeSet); });
    apply(saved);
  }

  function render() {
    drawHeader();
    drawNotes();
    document.querySelectorAll("#chips .chip").forEach((b) => b.setAttribute("aria-pressed", String(state.on[b.dataset.id])));
    setupProjection();
    setupAxis();
    drawMap();
    drawTimeline();
    drawDetail();
    drawRoutes();
    drawFoot();
  }

  setupTheme();
  resetCorridors();
  state.sel = fastestIndex();
  drawChips();
  render();
})();

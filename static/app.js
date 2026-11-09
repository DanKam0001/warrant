const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v) => (v == null ? "–" : "$" + Number(v).toFixed(2));
const COLOR = { false: "green", true: "red", unverifiable: "amber", ignored: "grey", noted: "blue" };
const VERDICT = { false: "Contradicted", true: "Holds", unverifiable: "Unverifiable", ignored: "Ignored", noted: "Request noted" };
const ACTION_LABEL = { concede: "refund in full", partial: "partial refund", fight: "fight", review: "needs you" };

let state = null, current = null, queueApi = null, boardApi = null, animating = false, statusFilter = null, searchText = "", policyDirty = false, boardSet = "heldout2";

function ago(iso) {
  if (!iso) return "";
  const sec = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (sec < 60) return "just now";
  if (sec < 3600) return Math.floor(sec / 60) + " min ago";
  if (sec < 86400) return Math.floor(sec / 3600) + " h ago";
  return Math.floor(sec / 86400) + " d ago";
}
const ACTION_WORD = { concede: "Refunded", partial: "Partly refunded", fight: "Evidence sent", waiting_on_paypal: "Waiting on PayPal", analysed: "Checked", auto_skipped: "Auto left it", auto_failed: "Auto failed", policy_changed: "Policy changed", auto_on: "Auto on", auto_off: "Auto off", kill_switch: "Kill switch" };

async function api(path, opts = {}) {
  const r = await fetch(path, { method: opts.method || "GET", headers: { "Content-Type": "application/json" }, body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (!r.ok) { const t = await r.text(); let m = t; try { m = JSON.parse(t).detail || t; } catch (e) { /* plain text */ } throw new Error(String(m).slice(0, 300)); }
  return r.json();
}

// ---- tabs -------------------------------------------------------------------------------------------------------
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === b.dataset.tab));
  if (b.dataset.tab === "board") renderBoard();
  if (b.dataset.tab === "policy") { renderPolicy(true); loadAllLog(); }
}));

// ---- queue ------------------------------------------------------------------------------------------------------
function initQueue() {
  queueApi = agGrid.createGrid($("#queueGrid"), {
    suppressColumnVirtualisation: true,
    columnDefs: [
      { field: "status_rank", headerName: "Status", width: 145, sort: "asc", sortIndex: 0, cellRenderer: (p) => `<span class="chip s-${p.data.status}">${esc(p.data.status_label)}</span>${p.data.auto_handled ? ' <span class="chip auto">auto</span>' : ""}` },
      { field: "order", headerName: "Order", width: 100 },
      { field: "amount", headerName: "Amount", width: 84, valueFormatter: (p) => money(p.value) },
      { field: "decision", headerName: "Warrant", width: 92, cellRenderer: (p) => (p.value ? `<span class="chip ${p.value}">${p.value === "review" ? "review" : p.value}</span>` : "") },
      { field: "last_at", headerName: "Last action", flex: 1, minWidth: 130, sort: "desc", sortIndex: 1, cellRenderer: (p) => (p.data.last_text ? `${esc(p.data.last_text)} <span class="muted">${esc(ago(p.value))}</span>` : '<span class="muted">none yet</span>') },
    ],
    isExternalFilterPresent: () => !!statusFilter || !!searchText,
    doesExternalFilterPass: (node) => {
      const d = node.data;
      if (statusFilter === "auto" ? !d.auto_handled : statusFilter && d.status !== statusFilter) return false;
      return !searchText || `${d.order} ${d.reason} ${d.last_text} ${d.status_label}`.toLowerCase().includes(searchText);
    },
    rowData: [],
    rowSelection: "single",
    getRowId: (p) => p.data.dispute_id,
    onRowClicked: (e) => openCase(e.data.dispute_id),
    defaultColDef: { resizable: true, sortable: true },
  });
}

function queueRows() {
  return state.cases.map((c) => {
    const o = state.orders[c.capture_id] || {};
    const la = c.last_action;
    return {
      dispute_id: c.dispute_id, order: o.evidence?.purchase_record?.order_ref, reason: (c.reason || "").replace("MERCHANDISE_OR_SERVICE_", "").replaceAll("_", " ").toLowerCase(),
      amount: c.amount, stage: c.stage, decision: c.analysis?.decision?.action, paypal_status: c.paypal_status,
      status: c.status, status_label: c.status_label, status_rank: c.status_rank, auto_handled: c.auto_handled,
      last_text: la ? (ACTION_WORD[la.action] || la.action) + (la.by === "auto" ? " (auto)" : "") : "", last_at: la?.at ? (/([zZ]|[+-]\d\d:\d\d)$/.test(la.at) ? la.at : la.at + "Z") : "",
    };
  });
}

function renderStrip() {
  const s = state.summary;
  const chip = (key, label, count, usd, extra = "") => `<button class="schip ${statusFilter === key ? "on" : ""} ${extra}" data-s="${key ?? ""}"><span class="n">${count}</span><span class="l">${esc(label)}</span><span class="u">${money(usd)}</span></button>`;
  $("#chips").innerHTML = chip(null, "All", s.total, s.total_usd) +
    s.statuses.filter((x) => x.count || x.key === "needs_review").map((x) => chip(x.key, x.label, x.count, x.usd, "s-" + x.key + (x.key === "needs_review" && x.count ? " hot" : ""))).join("") +
    chip("auto", "Handled by auto", s.auto_handled.count, s.auto_handled.usd, "auto");
  $("#chips").querySelectorAll(".schip").forEach((b) => b.addEventListener("click", () => { statusFilter = b.dataset.s || null; renderStrip(); queueApi.onFilterChanged(); }));
  $("#stripTotal").textContent = `${s.total} dispute${s.total === 1 ? "" : "s"}, ${money(s.total_usd)} in total`;
}

function renderAuto() {
  const a = state.auto;
  $("#autoToggle").checked = a.enabled;
  $("#autoLabel").textContent = a.enabled ? "Auto mode: ON" : "Auto mode: off";
  document.querySelector(".auto").classList.toggle("on", a.enabled);
  $("#killBtn").hidden = !a.enabled;
  $("#autoNote").textContent = a.enabled ? `Auto limits: refund up to ${money(a.limits.max_refund)}, ${money(a.spent_today)} of ${money(a.limits.daily_refund_cap)} spent today, fights up to ${money(a.limits.max_fight_amount)}.` : a.killed_at ? "Auto mode was stopped by the kill switch." : "";
}

function renderPolicy(force) {
  const pp = state.policy, f = $("#policyForm");
  if (!force && policyDirty) return;
  $("#floorDisc").textContent = pp.disclaimer;
  $("#floorList").innerHTML = pp.principles.map((x) => `<li><b>${esc(x.id)}</b> ${esc(x.text)}</li>`).join("");
  $("#fightNote").textContent = pp.fight_note;
  $("#pJur").innerHTML = Object.entries(pp.jurisdictions).map(([k, v]) => `<option value="${k}">${k}: ${esc(v)}</option>`).join("");
  const p = pp.policy;
  const get = (n) => n.split(".").reduce((o, k) => o[k], p);
  f.querySelectorAll("[name]").forEach((el) => { const v = get(el.name); el.value = Array.isArray(v) ? v.join(", ") : v; });
  policyDirty = false;
}

function readPolicy() {
  const f = $("#policyForm"), out = { cost: {}, auto: {} };
  f.querySelectorAll("[name]").forEach((el) => {
    const n = el.name;
    const v = el.type === "number" ? Number(el.value) : n.startsWith("final_sale") ? el.value.split(",").map((x) => x.trim()).filter(Boolean) : el.value;
    const [a, b] = n.split(".");
    if (b) out[a][b] = v; else out[a] = v;
  });
  return out;
}

$("#policyForm").addEventListener("input", () => { policyDirty = true; $("#policyNote").textContent = "unsaved changes"; });
$("#policyForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/policy", { method: "PUT", body: readPolicy() });
    policyDirty = false;
    await refresh();
    renderPolicy(true);
    $("#policyNote").textContent = "saved; open disputes re-checked";
    loadAllLog();
  } catch (err) { $("#policyNote").textContent = "not saved: " + err.message; }
});

const logLine = (withOrder) => (r) => `<li><span class="t">${esc(r.at.slice(5, 16).replace("T", " "))}</span><span class="by ${esc(r.by)}">${esc(r.by)}</span>${withOrder && r.order ? `<b>${esc(r.order)}</b> ` : ""}${esc(r.reason)}</li>`;
async function loadAllLog() {
  const { activity } = await api("/api/activity?limit=80");
  $("#allLog").innerHTML = activity.map(logLine(true)).join("") || '<li class="muted">Nothing yet.</li>';
}
async function loadOrderLog(id) {
  const { activity } = await api("/api/activity?dispute_id=" + encodeURIComponent(id) + "&limit=30");
  if (id !== current) return;
  $("#orderLog").innerHTML = activity.map(logLine(false)).join("") || '<li class="muted">Nothing done yet.</li>';
}

$("#autoToggle").addEventListener("change", async (e) => {
  try { await api("/api/auto", { method: "POST", body: { enabled: e.target.checked } }); } catch (err) { alert(err.message); }
  refresh();
});
$("#killBtn").addEventListener("click", async () => { await api("/api/auto/kill", { method: "POST" }); refresh(); });
$("#search").addEventListener("input", (e) => { searchText = e.target.value.trim().toLowerCase(); queueApi.onFilterChanged(); });

async function refresh() {
  state = await api("/api/state");
  queueApi.setGridOption("rowData", queueRows());
  renderStrip();
  renderAuto();
  if (!policyDirty) renderPolicy(false);
  const m = state.model;
  const ms = $("#modelStatus");
  const usedFallback = state.cases.some((c) => c.analysis?.split?.source === "fallback");
  ms.textContent = m.name ? `splitter: ${m.name.split("/").pop()}` : usedFallback ? "splitter: keyword fallback (model offline)" : "splitter: Nemotron (Nebius)";
  ms.className = "pill " + (usedFallback ? "warn" : m.name ? "ok" : "");
  const seed = state.seeding;
  $("#seedNote").textContent = seed && !seed.done ? "Creating sandbox orders and disputes…" : seed?.error ? "Seeding failed: " + seed.error : state.cases.length < Object.keys(state.orders).length ? "Waiting for PayPal to list new disputes…" : "";
  $("#events").innerHTML = state.events.map((e) => `<li><span>${esc(e.at.slice(11, 19))}</span>${esc(e.text)}</li>`).join("");
  if (current && !animating) renderCase(false);
}

// ---- case view --------------------------------------------------------------------------------------------------
function caseObj() { return state.cases.find((c) => c.dispute_id === current); }

function openCase(id) {
  current = id;
  $("#caseEmpty").hidden = true;
  $("#case").hidden = false;
  renderCase(false);
}

function highlightClaim(claim, checked, sentences) {
  // Rule marks from the code classifier: injections struck through, non-injections underlined; exact phrase highlighted.
  const marks = [];
  (sentences || []).forEach((x) => x.marks.forEach((m) => marks.push(m)));
  marks.sort((a, b) => a.start - b.start || b.end - a.end);
  let out = "", pos = 0;
  for (const m of marks) {
    if (m.start < pos || m.end > claim.length) continue;
    out += esc(claim.slice(pos, m.start));
    out += `<mark class="${m.kind === "injection" ? "inj" : "ok"} r-${m.rule}" data-rule="${m.rule}" title="${esc(m.rule + " " + m.name + ": " + m.why)}">${esc(claim.slice(m.start, m.end))}</mark>`;
    pos = m.end;
  }
  out += esc(claim.slice(pos));
  if (marks.length) return out;
  let html = esc(claim);
  (checked || []).filter((c) => c.verdict === "ignored").forEach((c) => {
    const q = esc(c.quote);
    if (q && html.includes(q)) html = html.replace(q, `<mark class="inj" title="instruction: ignored">${q}</mark>`);
  });
  return html;
}

const RULE_NOTE = { N3: "checked against the shipping record", N4: "counts against the claim", N1: "noted, never obeyed", N2: "recorded, changes nothing", N5: "recorded, changes nothing", I1: "ignored, not evidence", I2: "ignored, not evidence", I3: "ignored, not evidence", I4: "ignored, not evidence" };
function renderRuleChips(a) {
  const box = $("#ruleChips");
  const chips = [];
  (a?.split?.sentences || []).forEach((x) => x.marks.forEach((m) => chips.push(
    `<span class="rchip ${m.kind === "injection" ? "inj" : "ok"}" data-rule="${m.rule}" title="${esc(m.why)}"><b>${m.rule} ${esc(m.name)}</b>: "${esc(m.phrase)}" <i>${esc(RULE_NOTE[m.rule] || "")}</i></span>`)));
  box.innerHTML = chips.join("");
  box.hidden = !chips.length;
}

let ruleLegendLoaded = false;
async function loadRuleLegend() {
  if (ruleLegendLoaded) return;
  ruleLegendLoaded = true;
  try {
    const t = await api("/api/injection");
    const ids = Object.entries(t.rules).map(([k, v]) => `<tr class="${v.kind === "injection" ? "inj" : "ok"}"><td><b>${k}</b></td><td>${esc(v.name)}</td><td>${v.kind === "injection" ? "injection: ignored" : "not an injection"}</td><td>${esc(v.text)}</td></tr>`).join("");
    const pair = (s) => `<div class="nm ${s.injection ? "inj" : "ok"}">"${esc(s.text)}" ${s.marks.map((m) => `<span class="rchip ${m.kind === "injection" ? "inj" : "ok"}"><b>${m.rule}</b></span>`).join("")} <span class="muted">${s.injection ? "injection" : "not an injection"}</span></div>`;
    const pairs = t.near_miss.map((p) => `<div class="nmpair" data-pair>${pair(p.ok)}${pair(p.bad)}<div class="muted">${esc(p.why)}</div></div>`).join("");
    $("#ruleLegendBody").innerHTML = `<p><b>Tie-break:</b> ${esc(t.tie_break)}</p><table class="ruletable" id="ruleTable">${ids}</table><h4>Near-miss pairs (classified live by the code)</h4><div id="nearMiss">${pairs}</div>`;
  } catch (e) { $("#ruleLegendBody").textContent = "could not load: " + e.message; }
}
$("#ruleLegend").addEventListener("toggle", (e) => { if (e.target.open) loadRuleLegend(); });

function renderCase(animate) {
  const c = caseObj();
  if (!c) return;
  const o = state.orders[c.capture_id];
  const ev = o.evidence, rec = ev.purchase_record, a = c.analysis;
  $("#cOrder").textContent = `${rec.order_ref} · ${rec.buyer.name} · ${rec.items.map((i) => i.name).join(", ")}`;
  $("#cTitle").textContent = `${money(c.amount)} · ${(c.reason || "").replaceAll("_", " ").toLowerCase()}`;
  $("#cIds").innerHTML = `dispute ${esc(c.dispute_id)} · order ${esc(ev.paypal.order_id)} · capture ${esc(ev.paypal.capture_id)} · intake: ${esc(c.intake)}`;
  $("#cClaim").innerHTML = highlightClaim(c.claim, a?.checked, a?.split?.sentences);
  renderRuleChips(a);
  const review = a?.decision?.action === "review" && c.stage !== "answered";
  $("#respondBtn").disabled = !a || c.stage === "answered" || review;
  $("#respondBtn").textContent = c.stage === "answered" ? "Answered" : review ? "Decide below" : a?.decision?.action === "fight" ? "Send evidence to PayPal" : a ? (a.decision.action === "partial" ? "Partial refund via PayPal" : "Refund via PayPal") : "Send answer to PayPal";
  $("#reviewBox").hidden = !review || animating;
  if (review) {
    $("#reviewWhy").textContent = (a.decision.review_reasons || []).join("; ") + ". " + a.decision.summary;
    if ($("#partialAmt").dataset.d !== c.dispute_id) { $("#partialAmt").value = (a.decision.proposed_refund || 0).toFixed(2); $("#partialAmt").dataset.d = c.dispute_id; }
    $("#partialAmt").max = c.amount;
  }
  loadOrderLog(c.dispute_id);
  $("#splitSource").textContent = a ? (a.split.source === "model" ? `split by ${a.split.model}` : `split by keyword fallback (model unavailable: ${a.split.error?.slice(0, 60)})`) : "";
  renderStatements(a, ev, animate);
  renderDecision(a);
  renderPayPal(c, ev);
}

function evidenceItems(a, ev) {
  // Evidence column: every item referenced by a check, plus the core bundle so the board is never empty.
  const items = new Map();
  const add = (e) => { const k = e.kind + "|" + e.ref; if (!items.has(k)) items.set(k, e); return k; };
  const pr = ev.purchase_record;
  add({ kind: "paypal_order", ref: ev.paypal.order_id, detail: `custom_id ${ev.paypal.custom_id} = hash of purchase record` });
  if (ev.shipment) add({ kind: "paypal_tracker", ref: ev.shipment.paypal_tracker_id || ev.shipment.tracking_number, detail: `${ev.shipment.carrier} ${ev.shipment.tracking_number} registered with PayPal` });
  add({ kind: "purchase_record", ref: pr.order_ref, detail: `${pr.items.map((i) => `${i.qty}× ${i.sku} @ ${money(i.unit_price)}`).join(", ")}; approved via ${pr.approval.method}` });
  const links = [];
  (a?.checked || []).forEach((c, i) => c.evidence.forEach((e) => links.push([i, add(e), COLOR[c.verdict]])));
  return { items: [...items.entries()], links };
}

const ROLE_LABEL = { listing: "Our listing photo", outbound: "Our dispatch photo", buyer: "Buyer's photo" };
const CHK_CLASS = { ok: "ok", supports: "bad", contradicts: "good", flag: "warn", info: "info", fail: "bad" };

function renderPhotos(a, ev) {
  const box = $("#photoPanel");
  const st = (a?.checked || []).find((c) => c.photo_report);
  if (!st) { box.hidden = true; box.innerHTML = ""; return; }
  const r = st.photo_report;
  const fig = (role) => {
    const p = r.photos[role];
    if (!p) return `<div class="ph none"><div class="phrole">${ROLE_LABEL[role]}</div><div class="muted">no photo</div></div>`;
    const o = p.obs;
    const seen = !o ? "not described" : o.error ? "could not be described" : [`${o.main_colour || "?"} ${o.object || "?"}`, o.visible_damage ? `damage visible (${o.damage_kind || "unspecified"})` : "no damage visible", o.legible ? "" : "not legible", o.catalogue_style ? "catalogue-style shot" : "", o.looks_like_screen ? "looks like a screen" : "", `confidence ${o.confidence}`].filter(Boolean).join(", ");
    return `<div class="ph" data-role="${role}"><div class="phrole">${ROLE_LABEL[role]}</div>${p.url ? `<img src="${esc(p.url)}" alt="${ROLE_LABEL[role]}">` : ""}
      <div class="phseen"><b>Model saw:</b> ${esc(seen)}</div>
      <div class="phmeta muted">hash ${esc(p.dhash || "?")}${p.exif_at ? " · taken " + esc(p.exif_at.replace("T", " ")) : " · no capture time"}</div></div>`;
  };
  box.hidden = false;
  box.innerHTML = `<h3>Photos: what the model saw, what the code decided</h3>
    <div class="phrow">${["listing", "outbound", "buyer"].map(fig).join("")}</div>
    <div class="phchecks">${r.checks.map((k) => `<div class="phchk ${CHK_CLASS[k.status] || "info"}" data-check="${esc(k.id)}"><span class="phtag">${esc(k.status)}</span> <b>${esc(k.title)}</b> ${esc(k.text)}</div>`).join("")}</div>
    ${st.carrier_claim ? `<div class="carrierNote"><b>Carrier claim:</b> the item left us intact, so the carrier is liable. Refund the buyer and file a claim with ${esc(ev?.shipment?.carrier || "the carrier")} using our dispatch photo.</div>` : ""}
    <div class="muted small">The model only describes each picture. The checks above are plain code.</div>`;
}

function renderStatements(a, ev, animate) {
  renderPhotos(a, ev);
  const { items, links } = evidenceItems(a, ev);
  $("#evid").innerHTML = items.map(([k, e]) => `<div class="evi" data-k="${esc(k)}"><div class="k">${esc(e.kind.replaceAll("_", " "))}${e.kind === "carrier_scan" || e.kind === "packing_record" ? ' <span class="muted">(shop fixture)</span>' : ""}</div><div class="r">${esc(e.ref)}</div><div class="d">${esc(e.detail)}</div></div>`).join("");
  if (!a) {
    $("#stmts").innerHTML = `<div class="muted">Press <b>Check claim</b>: Nemotron splits the claim into statements; code checks each one against the evidence on the right.</div>`;
    drawLinks([]);
    return;
  }
  $("#stmts").innerHTML = a.checked.map((c, i) => `<div class="stmt ${animate ? "pending" : COLOR[c.verdict]}" data-i="${i}">
      <span class="verdict">${animate ? "checking…" : VERDICT[c.verdict]}</span>
      <div class="type">${esc(c.type.replaceAll("_", " "))}${c.flagged_by === "code" ? " · flagged by code" : ""}${c.type === "request" && c.remedy ? " · " + esc(c.remedy.replaceAll("_", " ")) : ""}</div>
      <div class="q">“${esc(c.quote)}”</div>
      <div class="why">${animate ? "" : esc(c.reason)}</div></div>`).join("");
  if (!animate) { drawLinks(links); return; }
  animating = true;
  drawLinks([]);
  a.checked.forEach((c, i) => setTimeout(() => {
    const el = document.querySelector(`.stmt[data-i="${i}"]`);
    el.className = "stmt " + COLOR[c.verdict];
    el.querySelector(".verdict").textContent = VERDICT[c.verdict];
    el.querySelector(".why").textContent = c.reason;
    drawLinks(links.filter((l) => l[0] <= i));
    if (i === a.checked.length - 1) { animating = false; $("#costCard").hidden = false; }
  }, 700 * (i + 1)));
}

function drawLinks(links) {
  const svg = $("#links"), box = $("#linkBoard").getBoundingClientRect();
  svg.innerHTML = links.map(([i, k, color]) => {
    const s = document.querySelector(`.stmt[data-i="${i}"]`), e = document.querySelector(`.evi[data-k="${CSS.escape(k)}"]`);
    if (!s || !e) return "";
    const a = s.getBoundingClientRect(), b = e.getBoundingClientRect();
    const x1 = a.right - box.left, y1 = a.top + a.height / 2 - box.top, x2 = b.left - box.left, y2 = b.top + b.height / 2 - box.top;
    const stroke = { green: "#12805c", red: "#c4320a", amber: "#b54708", grey: "#8a93a3" }[color];
    return `<path d="M${x1},${y1} C${x1 + 45},${y1} ${x2 - 45},${y2} ${x2},${y2}" stroke="${stroke}" stroke-width="2" fill="none" opacity=".75"/><circle cx="${x2}" cy="${y2}" r="3.5" fill="${stroke}"/>`;
  }).join("");
}
window.addEventListener("resize", () => current && renderCase(false));

function renderDecision(a) {
  $("#costCard").hidden = !a || animating;
  if (!a) return;
  const d = a.decision;
  $("#decAction").textContent = ACTION_LABEL[d.action] || d.action;
  const layerName = { floor: "consumer-protection floor", policy: "merchant policy", evidence: "evidence" };
  $("#trace").innerHTML = (d.trace || []).map((t) => `<div class="tr ${esc(t.layer)}"><span class="lay">${esc(layerName[t.layer] || t.layer)}</span><span class="rule">${esc(t.rule)}</span> ${esc(t.text)} <span class="muted">→ ${esc(t.effect)}</span></div>`).join("");
  $("#decAction").className = "act-" + d.action;
  $("#decSummary").textContent = d.summary;
  $("#costRows").innerHTML = d.options.map((o) => `<tr class="${o.action === d.action ? "chosen" : ""}"><td>${o.action}${o.refund ? " (" + money(o.refund) + ")" : ""}${o.blocked ? ' <span class="tag warn">blocked by policy</span>' : ""}</td><td>${money(o.cost)}</td><td class="f">${esc(o.formula)}</td></tr>`).join("");
  const p = d.params;
  $("#costParams").textContent = `Merchant settings: dispute fee ${money(p.dispute_fee)}, ${p.handling_hours} h handling at ${money(p.hourly_rate)}/h. P(lose) by weakest evidence: strong ${p.p_lose.strong}, medium ${p.p_lose.medium}, weak/unverifiable ${p.p_lose.weak}, claim holds ${p.p_lose.true}.`;
}

function renderPayPal(c, ev) {
  const r = c.response;
  $("#ppInfo").innerHTML = `<dt>Dispute</dt><dd>${esc(c.dispute_id)}</dd><dt>Status</dt><dd>${esc(c.paypal_status)} / ${esc(c.paypal_state || "")}</dd>
    <dt>Stage</dt><dd>${esc(c.dispute_life_cycle_stage || "CHARGEBACK")}</dd>
    <dt>Refunds</dt><dd>${ev.refunds.length ? ev.refunds.map((x) => `${esc(x.refund_id)} ${money(x.amount)}`).join(", ") : "none"}</dd>`;
  $("#ppSteps").innerHTML = r ? `<div class="muted small">PayPal offered: ${esc(r.offered_by_paypal.join(", ") || "no actions")}</div>` + r.steps.map((s) =>
    `<div class="step"><span class="${s.ok ? "ok" : "bad"}">${s.ok ? "✓" : "✗"}</span> POST ${esc(s.call)} ${s.ok ? "" : esc(s.status + " " + (s.error || "").slice(0, 120))}</div>`).join("") +
    `<div class="step">after: ${esc(r.status_after)} / ${esc(r.state_after)}</div>` + (r.pending_note ? `<div class="small bad">${esc(r.pending_note)}</div>` : "") : `<div class="muted small">Not answered yet.</div>`;
}

$("#analyseBtn").addEventListener("click", async () => {
  $("#analyseBtn").disabled = true;
  $("#analyseBtn").textContent = "Splitting claim…";
  try {
    await api(`/api/cases/${current}/analyse`, { method: "POST" });
    state = await api("/api/state");
    queueApi.setGridOption("rowData", queueRows());
    renderCase(true);
  } catch (e) { alert(e.message); }
  $("#analyseBtn").disabled = false;
  $("#analyseBtn").textContent = "Check claim";
});

$("#respondBtn").addEventListener("click", async () => {
  $("#respondBtn").disabled = true;
  $("#respondBtn").textContent = "Calling PayPal…";
  try { await api(`/api/cases/${current}/respond`, { method: "POST" }); } catch (e) { alert(e.message); }
  await refresh();
});

$("#reviewBox").querySelectorAll("button[data-act]").forEach((b) => b.addEventListener("click", async () => {
  const act = b.dataset.act, body = { action: act };
  if (act === "partial") body.refund = Number($("#partialAmt").value);
  const c = caseObj();
  if (!confirm(`${act === "fight" ? "Send the evidence to PayPal" : act === "concede" ? "Refund " + money(c.amount) : "Refund " + money(body.refund)} for this dispute?`)) return;
  try { await api(`/api/cases/${current}/respond`, { method: "POST", body }); } catch (e) { alert(e.message); }
  await refresh();
}));

$("#tryBtn").addEventListener("click", async () => {
  const c = caseObj();
  $("#tryOut").innerHTML = "<span class='muted'>checking…</span>";
  try {
    const a = await api("/api/try", { method: "POST", body: { capture_id: c.capture_id, claim: $("#tryText").value, reason: c.reason } });
    $("#tryOut").innerHTML = a.checked.map((x) => `<div class="stmt ${COLOR[x.verdict]}"><span class="verdict">${VERDICT[x.verdict]}</span><div class="type">${esc(x.type)}</div><div class="q">“${esc(x.quote)}”</div><div class="why">${esc(x.reason)}</div></div>`).join("") +
      `<p><b>Decision: ${esc(a.decision.action)}</b> ${a.decision.refund_amount ? money(a.decision.refund_amount) : ""} — ${esc(a.decision.summary)} <span class="muted">(${esc(a.split.source)})</span></p>`;
  } catch (e) { $("#tryOut").textContent = e.message; }
});

$("#resetBtn").addEventListener("click", async () => {
  if (!confirm("Wipe local cases and create fresh sandbox orders + disputes?")) return;
  current = null; $("#case").hidden = true; $("#caseEmpty").hidden = false;
  try { await api("/api/reset?photos=true", { method: "POST" }); } catch (e) { alert(e.message); }
  refresh();
});

// ---- results board ----------------------------------------------------------------------------------------------
const BOT_NAMES = { naive: "Refund-everything bot", llm_alone: "LLM decides alone", warrant_keyword: "Warrant, keyword splitter", warrant: "Warrant" };
const SETS = { heldout2: "Held-out set 2 (clean)", dev: "Dev set (tuned against)" };

function renderBoard() {
  const ev = state?.eval;
  if (!ev || !ev[boardSet]) { $("#kpis").innerHTML = `<div class="kpi">No eval results yet. Run <code>python scripts/run_eval.py</code>.</div>`; return; }
  const set = ev[boardSet], s = set.summary, n = set.total;
  $("#setSeg").innerHTML = Object.entries(SETS).map(([k, v]) => `<button class="${k === boardSet ? "on" : ""}" data-k="${k}">${v}</button>`).join("");
  $("#setSeg").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { boardSet = b.dataset.k; renderBoard(); }));
  $("#boardTitle").textContent = `${n} eval cases`;
  const others = (key, fmt) => Object.keys(BOT_NAMES).filter((k) => k !== "warrant" && s[k]).map((k) => `${BOT_NAMES[k]}: <b>${fmt(s[k][key], s[k])}</b>`).join("<br>");
  const kpi = (label, key, fmt) => `<div class="kpi"><div class="l">${label}</div><div class="v">${fmt(s.warrant[key], s.warrant)}</div><div class="cmp">${others(key, fmt)}</div></div>`;
  $("#kpis").innerHTML =
    kpi("Wrong refunds paid out (Warrant)", "wrong_refund_usd", money) +
    kpi("Owed refunds withheld (Warrant)", "owed_withheld_usd", money) +
    kpi("Correct decisions of " + n, "correct", (v) => v) +
    kpi("Sent to a person", "review_sent", (v, x) => `${v} (${x.review_expected} needed one)`) +
    kpi("Injected claims resisted", "injection_resisted", (v, x) => `${v} of ${x.injection_total}`) +
    kpi("Polite requests wrongly flagged", "benign_wrongly_flagged", (v, x) => (v == null ? "n/a" : `${v} of ${x.benign_total}`));
  $("#evalMeta").textContent = `generated ${ev.generated_at} · splitter ${ev.splitter} · ${ev.model_note || ""}`;
  const cols = [
    { field: "id", width: 70 }, { field: "kind", width: 130 }, { field: "title", flex: 1, minWidth: 220 },
    { field: "amount", width: 90, valueFormatter: (p) => money(p.value) },
    { headerName: "Truth", valueGetter: (p) => `${p.data.truth.action}${p.data.truth.refund ? " " + money(p.data.truth.refund) : ""}`, width: 130 },
  ];
  for (const k of Object.keys(BOT_NAMES)) {
    if (!set.cases[0][k]) continue;
    cols.push({ headerName: BOT_NAMES[k], width: 175, valueGetter: (p) => p.data[k], cellRenderer: (p) => p.value ? `<span class="chip ${p.value.action}">${p.value.action}</span> ${p.value.refund ? money(p.value.refund) : ""} ${p.value.correct ? "✓" : "✗"}` : "" });
  }
  if (boardApi) { boardApi.setGridOption("columnDefs", cols); boardApi.setGridOption("rowData", set.cases); }
  else boardApi = agGrid.createGrid($("#boardGrid"), { columnDefs: cols, rowData: set.cases, defaultColDef: { resizable: true, sortable: true, filter: true } });
}

initQueue();
fetch("/api/health").then((r) => r.json()).then((h) => { if (h.demo) $("#demoBanner").hidden = false; }).catch(() => {});
refresh();
setInterval(refresh, 4000);

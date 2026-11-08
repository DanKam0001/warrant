const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v) => (v == null ? "–" : "$" + Number(v).toFixed(2));
const COLOR = { false: "green", true: "red", unverifiable: "amber", ignored: "grey" };
const VERDICT = { false: "Contradicted", true: "Holds", unverifiable: "Unverifiable", ignored: "Ignored" };

let state = null, current = null, queueApi = null, boardApi = null, animating = false;

async function api(path, opts = {}) {
  const r = await fetch(path, { method: opts.method || "GET", headers: { "Content-Type": "application/json" }, body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (!r.ok) throw new Error((await r.text()).slice(0, 300));
  return r.json();
}

// ---- tabs -------------------------------------------------------------------------------------------------------
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === b.dataset.tab));
  if (b.dataset.tab === "board") renderBoard();
}));

// ---- queue ------------------------------------------------------------------------------------------------------
function initQueue() {
  queueApi = agGrid.createGrid($("#queueGrid"), {
    columnDefs: [
      { field: "order", headerName: "Order", width: 90 },
      { field: "reason", headerName: "Reason", flex: 1, minWidth: 110 },
      { field: "amount", headerName: "Amount", width: 85, valueFormatter: (p) => money(p.value) },
      { field: "stage", headerName: "Stage", width: 110, cellRenderer: (p) => `<span class="chip ${p.value}">${p.value.replaceAll("_", " ")}</span>` },
      { field: "decision", headerName: "Warrant", width: 90, cellRenderer: (p) => (p.value ? `<span class="chip ${p.value}">${p.value}</span>` : "") },
    ],
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
    return {
      dispute_id: c.dispute_id, order: o.evidence?.purchase_record?.order_ref, reason: (c.reason || "").replace("MERCHANDISE_OR_SERVICE_", "").replaceAll("_", " ").toLowerCase(),
      amount: c.amount, stage: c.stage, decision: c.analysis?.decision?.action, paypal_status: c.paypal_status,
    };
  });
}

async function refresh() {
  state = await api("/api/state");
  queueApi.setGridOption("rowData", queueRows());
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

function highlightClaim(claim, checked) {
  let html = esc(claim);
  (checked || []).filter((c) => c.verdict === "ignored").forEach((c) => {
    const q = esc(c.quote);
    if (q && html.includes(q)) html = html.replace(q, `<mark title="instruction: ignored">${q}</mark>`);
  });
  return html;
}

function renderCase(animate) {
  const c = caseObj();
  if (!c) return;
  const o = state.orders[c.capture_id];
  const ev = o.evidence, rec = ev.purchase_record, a = c.analysis;
  $("#cOrder").textContent = `${rec.order_ref} · ${rec.buyer.name} · ${rec.items.map((i) => i.name).join(", ")}`;
  $("#cTitle").textContent = `${money(c.amount)} · ${(c.reason || "").replaceAll("_", " ").toLowerCase()}`;
  $("#cIds").innerHTML = `dispute ${esc(c.dispute_id)} · order ${esc(ev.paypal.order_id)} · capture ${esc(ev.paypal.capture_id)} · intake: ${esc(c.intake)}`;
  $("#cClaim").innerHTML = highlightClaim(c.claim, a?.checked);
  $("#respondBtn").disabled = !a || c.stage === "answered";
  $("#respondBtn").textContent = c.stage === "answered" ? "Answered" : a?.decision?.action === "fight" ? "Send evidence to PayPal" : a ? "Refund via PayPal" : "Send answer to PayPal";
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

function renderStatements(a, ev, animate) {
  const { items, links } = evidenceItems(a, ev);
  $("#evid").innerHTML = items.map(([k, e]) => `<div class="evi" data-k="${esc(k)}"><div class="k">${esc(e.kind.replaceAll("_", " "))}${e.kind === "carrier_scan" || e.kind === "packing_record" ? ' <span class="muted">(shop fixture)</span>' : ""}</div><div class="r">${esc(e.ref)}</div><div class="d">${esc(e.detail)}</div></div>`).join("");
  if (!a) {
    $("#stmts").innerHTML = `<div class="muted">Press <b>Check claim</b>: Nemotron splits the claim into statements; code checks each one against the evidence on the right.</div>`;
    drawLinks([]);
    return;
  }
  $("#stmts").innerHTML = a.checked.map((c, i) => `<div class="stmt ${animate ? "pending" : COLOR[c.verdict]}" data-i="${i}">
      <span class="verdict">${animate ? "checking…" : VERDICT[c.verdict]}</span>
      <div class="type">${esc(c.type.replaceAll("_", " "))}${c.flagged_by === "code" ? " · flagged by code" : ""}</div>
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
  $("#decAction").textContent = d.action === "concede" ? "refund in full" : d.action === "partial" ? "partial refund" : "fight";
  $("#decAction").className = "act-" + d.action;
  $("#decSummary").textContent = d.summary;
  $("#costRows").innerHTML = d.options.map((o) => `<tr class="${o.action === d.action ? "chosen" : ""}"><td>${o.action}${o.refund ? " (" + money(o.refund) + ")" : ""}</td><td>${money(o.cost)}</td><td class="f">${esc(o.formula)}</td></tr>`).join("");
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
  try { await api("/api/reset", { method: "POST" }); } catch (e) { alert(e.message); }
  refresh();
});

// ---- results board ----------------------------------------------------------------------------------------------
function renderBoard() {
  const ev = state?.eval;
  if (!ev) { $("#kpis").innerHTML = `<div class="kpi">No eval results yet. Run <code>python scripts/run_eval.py</code>.</div>`; return; }
  const s = ev.summary, names = { naive: "Refund-everything bot", llm_alone: "LLM decides alone", warrant_keyword: "Warrant, keyword splitter", warrant: "Warrant" };
  const kpi = (label, key, fmt, better) => `<div class="kpi"><div class="l">${label}</div><div class="v">${fmt(s.warrant[key])}</div>
    <div class="cmp">${Object.keys(names).filter((k) => k !== "warrant" && s[k]).map((k) => `${names[k]}: <b>${fmt(s[k][key])}</b>`).join("<br>")}</div></div>`;
  $("#kpis").innerHTML =
    kpi("Wrong refunds paid out (Warrant)", "wrong_refund_usd", money) +
    kpi("Owed refunds withheld (Warrant)", "under_refund_usd", money) +
    kpi("Correct decisions of " + ev.cases.length, "correct", (v) => v) +
    kpi("Injected claims resisted", "injection_resisted", (v) => `${v} of ${ev.injection_total}`);
  $("#evalMeta").textContent = `generated ${ev.generated_at} · splitter ${ev.splitter} · ${ev.model_note || ""}`;
  const cols = [
    { field: "id", width: 70 }, { field: "kind", width: 100 }, { field: "title", flex: 1, minWidth: 220 },
    { field: "amount", width: 90, valueFormatter: (p) => money(p.value) },
    { headerName: "Truth", valueGetter: (p) => `${p.data.truth.action}${p.data.truth.refund ? " " + money(p.data.truth.refund) : ""}`, width: 130 },
  ];
  for (const k of Object.keys(names)) {
    if (!ev.cases[0][k]) continue;
    cols.push({ headerName: names[k], width: 170, valueGetter: (p) => p.data[k], cellRenderer: (p) => p.value ? `<span class="chip ${p.value.action}">${p.value.action}</span> ${p.value.refund ? money(p.value.refund) : ""} ${p.value.correct ? "✓" : "✗"}` : "" });
  }
  if (boardApi) boardApi.setGridOption("columnDefs", cols), boardApi.setGridOption("rowData", ev.cases);
  else boardApi = agGrid.createGrid($("#boardGrid"), { columnDefs: cols, rowData: ev.cases, defaultColDef: { resizable: true, sortable: true, filter: true } });
}

initQueue();
refresh();
setInterval(refresh, 4000);

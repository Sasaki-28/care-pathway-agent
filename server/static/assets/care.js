/* Shared helpers for the staff screens (clinic, doctor, lab). */
const $ = (id) => document.getElementById(id);

const ORDER = ["INTAKE", "TESTS_ORDERED", "RESULTS_RECEIVED", "AWAITING_DOCTOR_REVIEW", "DOCTOR_APPROVED", "TREATMENT_ACTIVE", "FOLLOW_UP_DUE", "END"];
const TRAIL = ["Admitted", "Tests", "Results", "Doctor review", "Approved", "Treatment", "Check-in", "Closed"];
const GATE = { TESTS_ORDERED: "results", AWAITING_DOCTOR_REVIEW: "doctor", TREATMENT_ACTIVE: "checkin" };
const STAMP = {
  INTAKE:                 ["grey",  "New admission",          "being registered"],
  TESTS_ORDERED:          ["blue",  "Awaiting lab results",   "asleep"],
  RESULTS_RECEIVED:       ["amber", "Results in, not yet read", "coordinator interrupted"],
  AWAITING_DOCTOR_REVIEW: ["red",   "Awaiting doctor review", "patient not informed · asleep"],
  DOCTOR_APPROVED:        ["amber", "Plan approved",          "patient not yet told"],
  TREATMENT_ACTIVE:       ["green", "On treatment",           "asleep until next check-in"],
  FOLLOW_UP_DUE:          ["amber", "Check-in received",      "not yet assessed"],
  ESCALATED:              ["red solid", "Escalated to doctor", "a warning sign was reported"],
  COMPLETED:              ["green", "Pathway completed",      "file closed"],
};
const STEP_TEXT = { INTAKE: "Admitting", TESTS_ORDERED: "Awaiting lab", RESULTS_RECEIVED: "Results in", AWAITING_DOCTOR_REVIEW: "Awaiting doctor",
  DOCTOR_APPROVED: "Plan approved", TREATMENT_ACTIVE: "On treatment", FOLLOW_UP_DUE: "Check-in", ESCALATED: "Escalated", COMPLETED: "Closed" };
const STEP_COLOR = { TESTS_ORDERED: "var(--blue)", AWAITING_DOCTOR_REVIEW: "var(--amber)", TREATMENT_ACTIVE: "var(--green)", ESCALATED: "var(--red)", COMPLETED: "var(--ink-3)" };
const ACTOR = { agent: "Coordinator", lab_agent: "Lab agent", imaging_agent: "Imaging agent", webhook: "Outside event", guard: "Safety guard", email: "Email" };
const AUTHOR = { care_pathway_coordinator: "Coordinator", lab_agent: "Lab agent", imaging_agent: "Imaging agent" };
const KIND = { instructions: ["Test instructions", ""], doctor_approved_plan: ["From your doctor", "ok"], follow_up: ["Check-in reply", ""], escalation_notice: ["Care team", "alert"] };
const CHECKIN_LABELS = { on_track: "On track", warning: "Warning signs", finished: "Course finished" };

let PATHWAYS = {};
const pw = (id) => PATHWAYS[id] || null;
const pathwayLabel = (p) => p ? p.name.replace(/^Suspected /, "").replace(/^./, c => c.toUpperCase()) : "—";
async function loadPathways() { for (const p of await api("/api/pathways").catch(() => [])) PATHWAYS[p.id] = p; return PATHWAYS; }

function esc(s) { return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
const T = (ts) => { const d = new Date(typeof ts === "number" ? ts * 1000 : ts); return isNaN(d) ? null : d; };
function hm(ts) { const d = T(ts); return d ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }) : ""; }
function dayTime(ts) { const d = T(ts); return d ? d.toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }) : ""; }
function since(sec) {
  if (sec < 60) return `${Math.floor(sec)} s`;
  const m = Math.floor(sec / 60); if (m < 60) return `${m} m ${String(Math.floor(sec % 60)).padStart(2, "0")} s`;
  const h = Math.floor(m / 60); if (h < 24) return `${h} h ${String(m % 60).padStart(2, "0")} m`;
  return `${Math.floor(h / 24)} d ${h % 24} h`;
}
function ago(ts) { const d = T(ts); if (!d) return ""; const s = (Date.now() - d) / 1000; if (s < 60) return "under a minute"; const m = Math.floor(s / 60); if (m < 60) return `${m} min`; const h = Math.floor(m / 60); return h < 24 ? `${h} h ${m % 60} min` : `${Math.floor(h / 24)} d`; }
function md(text) {
  const lines = esc(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").split("\n");
  let out = "", list = false;
  for (const l of lines) {
    const m = l.match(/^\s*[-*]\s+(.*)/);
    if (m) { if (!list) { out += "<ul>"; list = true; } out += `<li>${m[1]}</li>`; }
    else { if (list) { out += "</ul>"; list = false; } if (l.trim()) out += `<p>${l}</p>`; }
  }
  return out + (list ? "</ul>" : "");
}
async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = body.detail;
    throw new Error(Array.isArray(d) ? d.map(x => x.msg.replace(/^Value error, /, "")).join(" ") : d || res.statusText);
  }
  return body;
}
const postJSON = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
function friendly(msg) {
  if (/503|UNAVAILABLE/.test(msg)) return "Gemini is overloaded right now (503). Nothing was lost: the file is saved at its last step. Press Continue in a minute.";
  if (/429|RESOURCE_EXHAUSTED/.test(msg)) return "The free-tier limit was reached (about 15 requests a minute). Wait a minute, then press Continue.";
  if (/API key/i.test(msg)) return "No Gemini API key found. Add GOOGLE_API_KEY to the .env file and restart the server.";
  return "The coordinator stopped with an error: " + msg;
}

/* ---------------- top bar shared by the staff screens ---------------- */
const ICONS = {
  clinic: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 4v16"/></svg>`,
  doctor: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 3v6a6 6 0 0 0 12 0V3"/><path d="M12 15v2a4 4 0 0 0 8 0v-3"/><circle cx="20" cy="12" r="2"/></svg>`,
  lab: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M9 3h6M10 3v6L4 19a1.5 1.5 0 0 0 1.3 2h13.4a1.5 1.5 0 0 0 1.3-2L14 9V3"/><path d="M7 15h10"/></svg>`,
  patient: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="6" y="2" width="12" height="20" rx="3"/><path d="M11 18h2"/></svg>`,
};
const ROLE_TAG = { clinic: "Clinic staff", doctor: "Doctor", lab: "Lab" };
function topbar(active, roleLine) {
  if (active) document.body.dataset.role = active;
  const link = (href, key, label) => `<a href="${href}" data-k="${key}" ${active === key ? 'aria-current="page"' : ""}>${ICONS[key]}${label}</a>`;
  $("topbar").innerHTML = `<a class="brand" href="/">Care Pathway<small>City Clinic · AI coordinator, research prototype</small></a>
    <nav class="roles" aria-label="Screens">${link("/clinic", "clinic", "Clinic desk")}${link("/doctor", "doctor", "Doctor")}${link("/lab", "lab", "Lab")}</nav>
    ${roleLine ? `<div class="who-am-i"><span class="tag">${ROLE_TAG[active] || ""}</span>${roleLine}</div>` : ""}`;
}

/* ---------------- case file pieces (used by the clinic and doctor screens) ---------------- */
function stampHTML(step, busy, sinceTs) {
  if (busy) return ["stamp grey", `<span class="writing"><svg class="pen" viewBox="0 0 16 16" aria-hidden="true"><path d="M2 14l1-4 8-8 3 3-8 8z" fill="currentColor"/></svg>Coordinator writing…</span>`];
  const [cls, title, sub] = STAMP[step] || ["grey", step, ""];
  const clock = GATE[step] && sinceTs ? ` <span class="clock" data-since="${sinceTs}">${since((Date.now() - sinceTs) / 1000)}</span>` : "";
  return ["stamp " + cls, `${esc(title)}<small>${esc(sub)}${clock}</small>`];
}
setInterval(() => document.querySelectorAll(".clock[data-since]").forEach(c => { c.textContent = since((Date.now() - +c.dataset.since) / 1000); }), 1000);

function trailHTML(step) {
  const end = ["ESCALATED", "COMPLETED"].includes(step);
  const idx = end ? ORDER.length - 1 : ORDER.indexOf(step);
  return TRAIL.map((t, i) => `<span class="${i < idx ? "done" : i === idx ? "now" : ""}">${i === ORDER.length - 1 && step === "ESCALATED" ? "Escalated" : t}</span>`).join("");
}
function fieldsHTML(st) {
  const p = st.patient || {};
  return `<div><small>Age / sex</small>${esc(p.age ?? "—")} / ${esc((p.sex || "—").charAt(0))}</div>
    <div><small>Pathway</small>${esc(pw(st.pathway_id)?.name || "Not set")}</div><div><small>Phone</small>${esc(p.phone || "—")}</div>
    <div><small>Email updates</small>${st.has_email ? "On" : "Not given"}</div>`;
}
function labHTML(st, sid) {
  const lab = st.lab_findings || {}, tests = st.tests_ordered || [];
  let html = tests.length ? `<p>Ordered: ${tests.map(esc).join(", ")}.</p>` : `<p class="pending">No tests ordered yet.</p>`;
  if (st.lab_report_path) html += `<p><a class="filelink" href="/api/cases/${sid}/lab_report" target="_blank" rel="noopener">Open the lab report the lab uploaded ↗</a></p>`;
  if (lab.summary) {
    const tone = ["positive", "abnormal"].includes(lab.result_flag) ? "red" : lab.result_flag === "indeterminate" ? "grey" : "green";
    html += `<div class="typed">${(lab.key_values || []).map(esc).join("\n")}</div><p><span class="flag ${tone}">${esc(lab.result_flag)}</span>${esc(lab.summary)}</p>
      <p style="font:12.5px var(--sans);color:var(--blue)">Read by the lab agent, ${esc(dayTime(lab.ts))}</p>`;
  } else if (tests.length) html += `<p class="pending">Awaiting results from the lab.</p>`;
  return html;
}
function imagingHTML(st, sid, big) {
  const im = st.imaging_findings || {};
  const src = `/api/cases/${sid}/xray?v=${encodeURIComponent(st.xray_path || "")}`;
  const img = st.xray_path ? `<a href="${src}" target="_blank" rel="noopener"><img class="xray${big ? " big" : ""}" src="${src}" alt="Chest X-ray for this patient"></a>` : "";
  if (!im.impression) return img + `<p class="pending">${st.xray_path ? "X-ray received, not read yet." : "Awaiting chest X-ray."}</p><div class="clear"></div>`;
  const tone = { high: "red", moderate: "amber", low: "green" }[im.suspicion_level] || "grey";
  const label = { high: "high", moderate: "moderate", low: "low", unable_to_assess: "could not assess" }[im.suspicion_level] || im.suspicion_level;
  return `${img}<p>${esc(im.impression)}</p><ul class="findings">${(im.key_findings || []).map(f => `<li>${esc(f)}</li>`).join("")}</ul>
    <p>Suspicion: <span class="flag ${tone}">${esc(label)}</span> Image quality: ${esc(im.image_quality)}</p>
    <p style="font:12.5px var(--sans);color:var(--blue)">Read by the imaging agent, ${esc(dayTime(im.ts))}</p><div class="clear"></div>`;
}
function needsImaging(st) { return (pw(st.pathway_id)?.requires_imaging ?? true) || !!st.xray_path; }
function summaryHTML(st) {
  const s = st.doctor_summary || {};
  return s.summary ? `<p>${esc(s.summary)}</p><p style="font:14px var(--sans);color:var(--ink-2)">Suggested for the doctor to consider: ${esc(s.suggested_next_step)}</p>`
    : `<p class="pending">Written once the results have been read.</p>`;
}
function planHTML(st) {
  const d = st.doctor_decision || {};
  if (d.decision === "approve") return `<div class="signed"><p>${esc(d.notes || "Approved without notes.")}</p><div class="sig">Approved — ${esc(d.doctor || "Doctor")}</div></div>`;
  if (d.decision === "more_tests") return `<p>Further tests requested by ${esc(d.doctor || "the doctor")}: ${esc(d.notes || "")}</p>`;
  return `<p class="pending">Not signed yet. Nothing has been released to the patient.</p>`;
}
function followUpHTML(st) {
  const fus = st.follow_ups || [];
  const tone = { on_track: ["green", "on track"], escalate: ["red", "escalated"], completed: ["green", "completed"] };
  return fus.length ? fus.slice().reverse().map(f => {
    const [c, t] = tone[f.outcome] || ["grey", f.outcome];
    return `<div class="fu"><small>${esc(dayTime(f.ts))}</small><q>${esc(f.check_in)}</q><p style="margin-top:4px"><span class="flag ${c}">${t}</span>${esc(f.summary)}</p></div>`;
  }).join("") : `<p class="pending">${st.current_step === "TREATMENT_ACTIVE" ? "Waiting for the first check-in." : "Starts once treatment begins."}</p>`;
}
function conversationHTML(transcript) {
  return transcript.map(m => {
    if (m.author === "user") {
      if (/^New patient at the front desk/.test(m.text)) return `<div class="event">Patient admitted · ${hm(m.ts)}</div>`;
      if (/^SYSTEM EVENT:/.test(m.text)) {
        const label = /results have arrived/i.test(m.text) ? "Lab results arrived" : /approved a plan/i.test(m.text) ? "Doctor approved the plan"
          : /more tests/i.test(m.text) ? "Doctor requested further tests" : /check-in/i.test(m.text) ? "Patient checked in" : m.text.replace(/^SYSTEM EVENT:\s*/, "");
        return `<div class="event">${esc(label)} · ${hm(m.ts)}</div>`;
      }
      return `<div class="entry staff"><span class="who">Clinic staff · ${hm(m.ts)}</span><p>${esc(m.text)}</p></div>`;
    }
    const tools = m.tool_calls.length ? `<div class="tools">${m.tool_calls.map(t => `<span class="tool">${esc(t)}</span>`).join("")}</div>` : "";
    return `<div class="entry agent"><span class="who">${esc(AUTHOR[m.author] || m.author)} · ${hm(m.ts)}</span>${m.text ? md(m.text) : ""}${tools}</div>`;
  }).join("") || `<p class="pending">Nothing yet.</p>`;
}
function auditHTML(log) {
  return (log || []).slice().reverse().map(a =>
    `<tr class="${a.actor === "guard" ? "guard" : ""}"><td>${esc(hm(a.ts))}</td><td>${esc(ACTOR[a.actor] || a.actor)}</td><td>${esc(a.event)}</td></tr>`
  ).join("") || `<tr><td colspan="3">Nothing yet.</td></tr>`;
}
function inboxHTML(st) {
  const inbox = st ? st.patient_inbox || [] : [];
  const name = st?.patient?.name;
  const approved = st?.doctor_decision?.decision === "approve";
  const lock = st && st.tests_ordered?.length && !approved && !["ESCALATED", "COMPLETED"].includes(st.current_step)
    ? `<div class="locked"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>Results appear here after your doctor reviews them.</div>` : "";
  return `<div class="notch"></div><div class="sms-from">City Clinic${name ? " → " + esc(name.split(" ")[0]) : ""}</div>` +
    inbox.map(m => { const [k, c] = KIND[m.kind] || [m.kind, ""]; return `<div class="sms ${c}"><span class="k">${k} · ${esc(dayTime(m.ts))}</span>${esc(m.text)}</div>`; }).join("") +
    (lock || (inbox.length ? "" : `<p style="text-align:center;color:#77817F;font-size:13px;padding-top:30px">No messages yet.</p>`));
}

/* A small summary card for lists and the board. */
function ticketHTML(r, opts = {}) {
  const chips = [];
  if (r.lab_flag) chips.push(`<span class="chip ${["positive", "abnormal"].includes(r.lab_flag) ? "red" : r.lab_flag === "indeterminate" ? "" : "green"}">lab ${esc(r.lab_flag)}</span>`);
  if (r.imaging_level) chips.push(`<span class="chip ${{ high: "red", moderate: "amber", low: "green" }[r.imaging_level] || ""}">X-ray ${esc(r.imaging_level.replace(/_/g, " "))}</span>`);
  if (r.has_email) chips.push(`<span class="chip blue">email</span>`);
  const asleep = GATE[r.current_step] && r.since;
  const wait = opts.wait ?? (asleep ? "" : STEP_TEXT[r.current_step]);
  const sleep = asleep ? `${opts.wait ? "" : `<span class="zz">${MOON}asleep ${esc(ago(r.since))}</span>`}<span class="sleepbar" title="How long the case has been asleep"><i style="width:${sleepPct(r.since)}%"></i></span>` : "";
  return `<button class="ticket ${opts.cls || ""}" data-id="${esc(r.session_id)}" style="--c:${STEP_COLOR[r.current_step] || "var(--amber)"}" ${opts.current ? 'aria-current="true"' : ""}>
    <span class="n">${esc(r.name === "(intake)" ? "Unregistered" : r.name)}</span>
    <span class="m">${esc(r.pathway_short || "—")}${r.age != null ? ` · ${esc(r.age)} ${esc((r.sex || "").charAt(0))}` : ""}</span>
    ${wait ? `<span class="w">${esc(wait)}</span>` : ""}${sleep}${r.alert ? `<span class="a">${esc(r.alert)}</span>` : ""}
    ${chips.length ? `<span class="chips">${chips.join("")}</span>` : ""}</button>`;
}

/* ---------------- sleep bars ---------------- */
const MOON = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>`;
function sleepPct(ts) {   // log scale: 1 min ≈ 10%, 1 h ≈ 50%, 1 day ≈ 80%, a week fills the bar
  const d = T(ts); if (!d) return 0;
  const min = Math.max(0, (Date.now() - d) / 60000);
  return Math.min(100, 6 + 94 * Math.log10(1 + min) / Math.log10(1 + 10080)).toFixed(1);
}

/* ---------------- tick for successful actions ---------------- */
const TICK = `<svg viewBox="0 0 28 28" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="14" cy="14" r="12"/><path d="M8.5 14.5l3.7 3.7 7.3-8"/></svg>`;
function sayDone(el, text) { el.classList.add("tick"); el.innerHTML = TICK + `<span>${esc(text)}</span>`; }
function clearDone(el) { el.classList.remove("tick"); el.textContent = ""; }

/* ---------------- live activity strip (clinic desk) ---------------- */
const ACTOR_COLOR = { agent: "var(--blue)", lab_agent: "var(--amber)", imaging_agent: "var(--amber)", guard: "var(--red)", email: "var(--green)", webhook: "var(--ink-3)" };
let activitySeen = null;
function activityHTML(rows) {
  const items = rows.flatMap(r => (r.recent || []).map(a => ({ ...a, sid: r.session_id, name: r.name === "(intake)" ? "New patient" : r.name })))
    .sort((a, b) => (b.ts || "").localeCompare(a.ts || "")).slice(0, 14);
  const keys = items.map(a => a.sid + a.ts + a.actor);
  const first = activitySeen === null; const prev = activitySeen || new Set();
  activitySeen = new Set(keys);
  if (!items.length) return `<li class="empty">Nothing yet. Admit a patient to see the agents at work.</li>`;
  return items.map((a, i) => `<li data-id="${esc(a.sid)}" class="${!first && !prev.has(keys[i]) ? "fresh" : ""}" style="--c:${ACTOR_COLOR[a.actor] || "var(--rule)"}"
    title="${esc(a.event)}"><b>${esc(hm(a.ts))}</b> · ${esc(ACTOR[a.actor] || a.actor)} · ${esc(a.name)}: ${esc(a.event)}</li>`).join("");
}

/* ---------------- agents at work: a live view of one agent run ---------------- */
const FLOW_ICONS = {
  agent: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4M5 5l2.5 2.5M16.5 16.5L19 19M5 19l2.5-2.5M16.5 7.5L19 5"/></svg>`,
  lab_agent: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M9 3h6M10 3v6L4 19a1.5 1.5 0 0 0 1.3 2h13.4a1.5 1.5 0 0 0 1.3-2L14 9V3"/></svg>`,
  imaging_agent: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M12 6v12M8 9c1 1 7 1 8 0M8 13c1 1 7 1 8 0"/></svg>`,
  guard: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/></svg>`,
  email: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>`,
};
const FLOW_ROWS = [
  ["agent", "Coordinator", "active", "Reading the case from saved state…"],
  ["lab_agent", "Lab agent", "", "Waits to be called"],
  ["imaging_agent", "Imaging agent", "", "Waits to be called"],
  ["guard", "Safety guard", "watch", "Checks every action against the current step"],
  ["email", "Patient email", "", "Only for messages the patient may see"],
];
function agentFlow(el, sid, title, baseline) {
  const st = Object.fromEntries(FLOW_ROWS.map(([k, , c, t]) => [k, { c, t }]));
  el.innerHTML = `<div class="flow-panel live"><h3><span class="live-dot"></span>${esc(title)}</h3>
    <p class="sub">Live: each agent lights up as it acts.</p><ol class="agents">${FLOW_ROWS.map(([k, n]) =>
      `<li class="fagent" data-k="${k}"><span class="orb">${FLOW_ICONS[k]}</span><span><span class="nm">${n}</span><span class="st"></span></span></li>`).join("")}</ol>
    <ul class="flow-log"></ul><div class="flow-result" role="status"></div></div>`;
  const panel = el.firstElementChild, log = panel.querySelector(".flow-log");
  const seen = new Set(); let calls = 0, stopped = false, timer = null;
  const key = (a) => a.actor + "|" + a.ts + "|" + a.event;
  const paint = () => FLOW_ROWS.forEach(([k]) => {
    const li = panel.querySelector(`[data-k="${k}"]`); li.className = "fagent " + st[k].c; li.querySelector(".st").textContent = st[k].t;
  });
  const set = (k, c, t) => { st[k] = { c, t }; };
  const take = (d) => {
    const s = d.state || {};
    (d.transcript || []).slice(calls).forEach(m => (m.tool_calls || []).forEach(t => {
      if (t === "lab_agent") set("lab_agent", "active", "Reading the lab report…");
      else if (t === "imaging_agent") set("imaging_agent", "active", "Looking at the chest X-ray…");
      else if (st.agent.c === "active") set("agent", "active", "Using " + t.replace(/_/g, " ") + "…");
    }));
    calls = (d.transcript || []).length;
    for (const a of s.audit_log || []) {
      if (seen.has(key(a))) continue; seen.add(key(a));
      if (a.actor === "agent") set("agent", "active", a.event);
      else if (a.actor === "lab_agent") set("lab_agent", "done", a.event);
      else if (a.actor === "imaging_agent") set("imaging_agent", "done", a.event);
      else if (a.actor === "guard") set("guard", "alert", a.event);
      else if (a.actor === "email") set("email", /not sent/i.test(a.event) ? "alert" : "done", a.event);
      else if (a.actor === "webhook") set("agent", "active", "Woken by an outside event");
      log.insertAdjacentHTML("beforeend", `<li class="${a.actor === "guard" ? "guard" : ""}"><span>${esc(hm(a.ts))}</span><b>${esc(ACTOR[a.actor] || a.actor)}</b><span>${esc(a.event)}</span></li>`);
      log.scrollTop = log.scrollHeight;
    }
    paint();
    return s;
  };
  const poll = async () => { try { return take(await api(`/api/cases/${sid}`)); } catch { return null; } };
  const loop = async () => { if (stopped) return; await poll(); timer = setTimeout(loop, 800); };
  const ready = (async () => {
    let d = baseline;
    if (!d) d = await api(`/api/cases/${sid}`).catch(() => null);
    if (d) { (d.state?.audit_log || []).forEach(a => seen.add(key(a))); calls = (d.transcript || []).length; }
    paint(); timer = setTimeout(loop, 600);
  })();
  return {
    ready,
    async finish(error) {
      stopped = true; clearTimeout(timer);
      const s = await poll();
      if (error) set("agent", "alert", "Stopped: " + String(error).slice(0, 120));
      else set("agent", "done", st.agent.t.endsWith("…") ? "Finished" : st.agent.t);
      for (const k of ["lab_agent", "imaging_agent"]) if (st[k].c === "") set(k, "skip", "Not needed in this step");
      if (st.guard.c === "watch") set("guard", "done", "Nothing was blocked");
      if (st.email.c === "") set("email", "skip", "Nothing new for the patient");
      paint(); panel.classList.remove("live");
      const step = s?.current_step;
      panel.querySelector(".flow-result").textContent = error ? "" : step ? `Case is now: ${(STAMP[step] || [, step])[1]}` : "";
    },
  };
}

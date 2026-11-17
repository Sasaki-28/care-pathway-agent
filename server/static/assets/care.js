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
function topbar(active, roleLine) {
  const link = (href, key, label) => `<a href="${href}" ${active === key ? 'aria-current="page"' : ""}>${ICONS[key]}${label}</a>`;
  $("topbar").innerHTML = `<a class="brand" href="/">Care Pathway<small>City Clinic · AI coordinator, research prototype</small></a>
    <nav class="roles" aria-label="Screens">${link("/clinic", "clinic", "Clinic desk")}${link("/doctor", "doctor", "Doctor")}${link("/lab", "lab", "Lab")}</nav>
    ${roleLine ? `<div class="who-am-i">${roleLine}</div>` : ""}`;
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
  const wait = opts.wait ?? (GATE[r.current_step] ? `waiting ${ago(r.since)}` : STEP_TEXT[r.current_step]);
  return `<button class="ticket" data-id="${esc(r.session_id)}" style="--c:${STEP_COLOR[r.current_step] || "var(--amber)"}" ${opts.current ? 'aria-current="true"' : ""}>
    <span class="n">${esc(r.name === "(intake)" ? "Unregistered" : r.name)}</span>
    <span class="m">${esc(r.pathway_short || "—")}${r.age != null ? ` · ${esc(r.age)} ${esc((r.sex || "").charAt(0))}` : ""}</span>
    <span class="w">${esc(wait)}</span>${r.alert ? `<span class="a">${esc(r.alert)}</span>` : ""}
    ${chips.length ? `<span class="chips">${chips.join("")}</span>` : ""}</button>`;
}

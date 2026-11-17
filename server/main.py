"""FastAPI server: role-based screens, case API, webhooks, patient portal, ADK dev UI.

Run from the project root:
    uvicorn server.main:app --port 8000
Then open http://127.0.0.1:8000/  (pick a role; ADK's own dev UI is at /dev-ui/)

Screens:
    /                 role picker
    /clinic           clinic dashboard + case files + coordinator chat (staff)
    /doctor           doctor's review queue (approve / ask for more tests)
    /lab              lab system simulator (sends results)
    /patient/<case>   the patient's own page, opened from the link in their email
    /clinic/single    the earlier one-screen demo console (fallback)

Settings (environment variables or .env):
    GOOGLE_API_KEY     Gemini API key
    SESSION_DB_URI     database for cases; SQLite file by default, or a Supabase
                       postgresql://... URI (Session pooler) when deployed
    DEMO_PASSWORD      if set, staff screens ask for this password (patient pages use their link)
    RESEND_API_KEY, EMAIL_FROM, PUBLIC_BASE_URL   patient emails (see server/notify.py)
"""

import base64
import logging
import os
import re
import secrets
import sys
import uuid
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / "agents"
DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
SAMPLE_DIR = ROOT / "sample_data"
STATIC_DIR = ROOT / "server" / "static"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(AGENTS_DIR / "care_pathway" / ".env")
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(AGENTS_DIR))

from google.adk.cli.fast_api import get_fast_api_app  # noqa: E402
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import DatabaseSessionService  # noqa: E402

from care_pathway.agent import root_agent  # noqa: E402
from care_pathway.pathways import PATHWAYS, get_pathway  # noqa: E402
from care_pathway.state_schema import CareStep, initial_state  # noqa: E402
from care_pathway.tools import AUDIT_PREFIX, merged_audit  # noqa: E402
from care_pathway.triage import suggest_pathway  # noqa: E402
from server import notify  # noqa: E402
from server.resume_handler import CaseRunner, SignalRejected  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

APP_NAME = "care_pathway"      # must match the agent folder name so /dev-ui sees the same cases
USER_ID = "clinic"             # one clinic user; each session is one patient case


def _normalise_db_uri(uri: str) -> str:
    """Accept the plain postgresql:// URI Supabase shows and use the async driver."""
    for prefix in ("postgresql://", "postgres://"):
        if uri.startswith(prefix):
            return "postgresql+asyncpg://" + uri[len(prefix):]
    return uri


SESSION_DB_URI = _normalise_db_uri(os.getenv(
    "SESSION_DB_URI", f"sqlite+aiosqlite:///{(DATA_DIR / 'sessions.db').as_posix()}"
))
# Postgres (Supabase): small pools, and no server-side prepared statements so it also
# works through Supabase's connection pooler.
DB_KWARGS = (
    {"connect_args": {"statement_cache_size": 0}, "pool_size": 3, "max_overflow": 2}
    if SESSION_DB_URI.startswith("postgresql") else {}
)

# ADK's FastAPI app (agent API + dev UI) backed by the same persistent database.
app: FastAPI = get_fast_api_app(
    agents_dir=str(AGENTS_DIR),
    session_service_uri=SESSION_DB_URI,
    session_db_kwargs=DB_KWARGS or None,
    web=True,
)
# ADK's app sends "/" to its dev UI; our role picker takes "/" instead (dev UI stays at /dev-ui/).
app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) != "/"]
app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

# ------------------------------------------------------------------ demo password
# When DEMO_PASSWORD is set (always set it on a public deployment), staff screens ask for
# it via the browser's built-in login prompt. Any username works. Patient pages are
# reached by their private link instead (checked against the case's token).
DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "")
PUBLIC_PREFIXES = ("/patient/", "/api/patient/", "/assets/")


@app.middleware("http")
async def protect(request: Request, call_next):
    if DEMO_PASSWORD and not request.url.path.startswith(PUBLIC_PREFIXES):
        supplied = ""
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                supplied = base64.b64decode(header[6:]).decode("utf-8").partition(":")[2]
            except (ValueError, UnicodeDecodeError):
                supplied = ""
        if not secrets.compare_digest(supplied.encode(), DEMO_PASSWORD.encode()):
            return Response("Password required.", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="Care Pathway demo"'})
    return await call_next(request)


session_service = DatabaseSessionService(db_url=SESSION_DB_URI, **DB_KWARGS)
runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)
cases = CaseRunner(runner, session_service, APP_NAME, USER_ID)


# ------------------------------------------------------------------ helpers

def _transcript(session) -> list[dict]:
    out = []
    for ev in session.events or []:
        if not ev.content or not ev.content.parts:
            continue
        text = "".join(p.text or "" for p in ev.content.parts if p.text).strip()
        calls = [c.name for c in (ev.get_function_calls() or [])]
        if text or calls:
            out.append({"author": ev.author, "text": text, "tool_calls": calls,
                        "ts": ev.timestamp})
    return out


async def _require_case(session_id: str):
    session = await cases.get(session_id)
    if session is None:
        raise HTTPException(404, f"No case {session_id}")
    return session


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80]


def _summary_row(session) -> dict:
    """What lists and boards need about one case, without the full record."""
    st = session.state
    log = merged_audit(st)
    patient = st.get("patient") or {}
    pw = get_pathway(st.get("pathway_id", ""))
    alerts = st.get("doctor_alerts") or []
    return {
        "session_id": session.id,
        "name": patient.get("name", "(intake)"),
        "age": patient.get("age"),
        "sex": patient.get("sex", ""),
        "pathway_id": st.get("pathway_id", ""),
        "pathway_short": pw.short_name if pw else "",
        "current_step": st.get("current_step", CareStep.INTAKE),
        "since": log[-1]["ts"] if log else None,
        "opened": log[0]["ts"] if log else None,
        "lab_flag": (st.get("lab_findings") or {}).get("result_flag", ""),
        "imaging_level": (st.get("imaging_findings") or {}).get("suspicion_level", ""),
        "alert": alerts[-1]["summary"] if alerts else "",
        "has_email": bool((st.get("contact") or {}).get("email")),
        "last_update": session.last_update_time,
    }


def _public_state(st: dict) -> dict:
    """Full case for staff screens: audit merged, internal keys and contact details left out."""
    state = {k: v for k, v in st.items()
             if not k.startswith("_") and not k.startswith(AUDIT_PREFIX)
             and k not in ("contact", "patient_token")}
    state["audit_log"] = merged_audit(st)
    state["has_email"] = bool((st.get("contact") or {}).get("email"))
    return state


def _page(name: str):
    return FileResponse(STATIC_DIR / name, headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ screens

@app.get("/", include_in_schema=False)
async def home():
    return _page("home.html")


@app.get("/clinic", include_in_schema=False)
async def clinic_ui():
    return _page("clinic.html")


@app.get("/doctor", include_in_schema=False)
async def doctor_ui():
    return _page("doctor.html")


@app.get("/lab", include_in_schema=False)
async def lab_ui():
    return _page("lab.html")


@app.get("/patient/{session_id}", include_in_schema=False)
async def patient_ui(session_id: str):
    return _page("patient.html")


@app.get("/clinic/single", include_in_schema=False)
async def clinic_single():
    """The earlier one-screen demo console, kept as a fallback."""
    return _page("single.html")


@app.get("/clinic/classic", include_in_schema=False)
async def clinic_ui_classic():
    return _page("classic.html")


# ------------------------------------------------------------------ clinic (staff) API

class NewCase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    age: int = Field(ge=0, le=120)
    sex: str
    phone: str = ""
    email: str = ""
    symptoms: str = Field(min_length=1)
    pathway: str = "tb"

    @field_validator("pathway")
    @classmethod
    def known_pathway(cls, value: str) -> str:
        if value not in PATHWAYS:
            raise ValueError(f"Unknown pathway '{value}'. Options: {', '.join(PATHWAYS)}")
        return value

    @field_validator("email")
    @classmethod
    def plausible_email(cls, value: str) -> str:
        value = value.strip()
        if value and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("That email address doesn't look right.")
        return value


@app.post("/api/cases")
async def create_case(body: NewCase):
    state = initial_state()
    state["patient_token"] = secrets.token_urlsafe(16)
    state["contact"] = {"email": body.email} if body.email else {}
    session = await session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID, state=state,
        session_id=f"case-{uuid.uuid4().hex[:8]}",
    )
    # The email address is never sent to the AI; it stays with the server for notifications.
    message = (
        "New patient at the front desk. "
        f"Name: {body.name}. Age: {body.age}. Sex: {body.sex}. Phone: {body.phone or 'not given'}. "
        f"Symptoms: {body.symptoms}. Suspected pathway: {body.pathway}. "
        "Please register the patient and order the tests."
    )
    result = await cases.run(session.id, message)
    return {"session_id": session.id, **result}


@app.get("/api/cases")
async def list_cases():
    listing = await session_service.list_sessions(app_name=APP_NAME, user_id=USER_ID)
    rows = []
    for s in listing.sessions:
        full = await cases.get(s.id)
        if full is not None:
            rows.append(_summary_row(full))
    rows.sort(key=lambda r: r["last_update"] or 0, reverse=True)
    return rows


@app.get("/api/cases/{session_id}")
async def get_case(session_id: str):
    session = await _require_case(session_id)
    return {"session_id": session_id, "state": _public_state(session.state),
            "transcript": _transcript(session), "steps": CareStep.ALL}


@app.get("/api/cases/{session_id}/patient-link")
async def patient_link(session_id: str):
    """Staff can open or copy the patient's private page (the same link the email carries)."""
    session = await _require_case(session_id)
    token = session.state.get("patient_token", "")
    return {"path": f"/patient/{session_id}?t={token}",
            "email_configured": notify.configured(),
            "has_email": bool((session.state.get("contact") or {}).get("email"))}


class ChatMessage(BaseModel):
    message: str


@app.post("/api/cases/{session_id}/chat")
async def chat(session_id: str, body: ChatMessage):
    """Clinic staff talk to the coordinator (e.g. to try to skip a gate during the demo)."""
    await _require_case(session_id)
    return await cases.run(session_id, body.message)


class TriageRequest(BaseModel):
    symptoms: str


@app.post("/api/triage")
async def triage(body: TriageRequest):
    """Suggest a pathway from the presenting complaint. Staff confirm or change it."""
    return await suggest_pathway(body.symptoms)


@app.get("/api/pathways")
async def pathways():
    """Everything the screens need to offer a pathway. Adding one to pathways.json is enough."""
    return [p.public() for p in PATHWAYS.values()]


@app.get("/api/samples")
async def samples():
    xrays = sorted(p.name for p in (SAMPLE_DIR / "xrays").glob("*")
                   if p.suffix.lower() in {".png", ".jpg", ".jpeg"})
    reports = sorted(p.name for p in (SAMPLE_DIR / "lab_reports").glob("*.txt"))
    return {"xrays": xrays, "lab_reports": reports}


@app.get("/api/samples/lab_reports/{name}")
async def sample_report(name: str):
    path = SAMPLE_DIR / "lab_reports" / _safe_name(name)
    if not path.exists():
        raise HTTPException(404)
    return {"text": path.read_text(encoding="utf-8")}


@app.get("/api/cases/{session_id}/xray", include_in_schema=False)
async def case_xray(session_id: str):
    session = await _require_case(session_id)
    path = session.state.get("xray_path")
    if not path or not Path(path).exists():
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/api/cases/{session_id}/lab_report", include_in_schema=False)
async def case_lab_report(session_id: str):
    session = await _require_case(session_id)
    path = session.state.get("lab_report_path")
    if not path or not Path(path).exists():
        raise HTTPException(404)
    return FileResponse(path)


# ------------------------------------------------------------------ doctor API

@app.get("/api/doctor/queue")
async def doctor_queue():
    """Cases waiting for the doctor's decision, plus escalations needing the doctor."""
    listing = await session_service.list_sessions(app_name=APP_NAME, user_id=USER_ID)
    review, alerts, recent = [], [], []
    for s in listing.sessions:
        full = await cases.get(s.id)
        if full is None:
            continue
        row = _summary_row(full)
        step = row["current_step"]
        if step == CareStep.AWAITING_DOCTOR_REVIEW:
            review.append(row)
        elif step == CareStep.ESCALATED:
            alerts.append(row)
        elif (full.state.get("doctor_decision") or {}).get("decision"):
            recent.append(row)
    review.sort(key=lambda r: r["since"] or "")             # longest waiting first
    alerts.sort(key=lambda r: r["since"] or "", reverse=True)
    recent.sort(key=lambda r: r["last_update"] or 0, reverse=True)
    return {"review": review, "alerts": alerts, "recent": recent[:10]}


# ------------------------------------------------------------------ patient portal API
# Reached by the private link in the patient's email: /patient/<case>?t=<token>.
# Shows only what the patient is allowed to see: their own messages and a plain status.

PATIENT_STATUS = {
    CareStep.INTAKE: "We are registering you.",
    CareStep.TESTS_ORDERED: "Please get your tests done. We will contact you once your doctor has reviewed the results.",
    CareStep.RESULTS_RECEIVED: "Your results have reached the clinic. Your doctor will review them.",
    CareStep.AWAITING_DOCTOR_REVIEW: "Your results have reached the clinic. Your doctor is reviewing them.",
    CareStep.DOCTOR_APPROVED: "Your doctor has reviewed your results. A message is on its way.",
    CareStep.TREATMENT_ACTIVE: "You are on your treatment plan. Please check in below.",
    CareStep.FOLLOW_UP_DUE: "Thanks, your check-in has been received.",
    CareStep.ESCALATED: "Your care team has been alerted and a doctor will contact you.",
    CareStep.COMPLETED: "Your care pathway is complete. Take care!",
}


async def _patient_case(session_id: str, token: str):
    session = await cases.get(session_id)
    expected = session.state.get("patient_token", "") if session else ""
    if not session or not expected or not secrets.compare_digest(expected.encode(), (token or "").encode()):
        raise HTTPException(404, "This link is not valid. Please use the link from your email.")
    return session


@app.get("/api/patient/{session_id}")
async def patient_view(session_id: str, t: str = ""):
    session = await _patient_case(session_id, t)
    st = session.state
    step = st.get("current_step", CareStep.INTAKE)
    pw = get_pathway(st.get("pathway_id", ""))
    first = ((st.get("patient") or {}).get("name") or "").split(" ")[0]
    return {
        "first_name": first,
        "care_plan": pw.name.replace("Suspected ", "").capitalize() if pw else "",
        "status": PATIENT_STATUS.get(step, ""),
        "step": step,
        "messages": st.get("patient_inbox", []),
        "can_check_in": step == CareStep.TREATMENT_ACTIVE,
        "check_in_examples": pw.example_check_ins if pw else {},
    }


class PatientCheckIn(BaseModel):
    check_in: str = Field(min_length=2, max_length=2000)


@app.post("/api/patient/{session_id}/check-in")
async def patient_check_in(session_id: str, body: PatientCheckIn, t: str = ""):
    await _patient_case(session_id, t)
    try:
        result = await cases.resume(
            session_id, signal="follow_up_due",
            state_delta={"current_step": CareStep.FOLLOW_UP_DUE, "latest_check_in": body.check_in},
            message="SYSTEM EVENT: A patient check-in has arrived. Assess it.",
        )
    except SignalRejected:
        raise HTTPException(409, "Check-ins are not open right now.")
    return {"ok": result.get("error") is None,
            "error": "The care team could not process your check-in right now. Please try again shortly."
            if result.get("error") else None}


# ------------------------------------------------------------------ webhooks (external events)

REPORT_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}


@app.post("/webhooks/results_ready")
async def results_ready(
    session_id: str = Form(...),
    lab_report_text: str = Form(""),
    sample_xray: str = Form(""),
    xray: UploadFile | None = File(None),
    lab_report_file: UploadFile | None = File(None),
):
    """Called by the lab system (or the lab simulator) when test results are available."""
    await _require_case(session_id)
    xray_path = ""
    if xray is not None and xray.filename:
        if Path(xray.filename).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise HTTPException(422, "The X-ray must be a PNG, JPG or WEBP image.")
        dest = UPLOAD_DIR / f"{session_id}_xray_{_safe_name(xray.filename)}"
        dest.write_bytes(await xray.read())
        xray_path = str(dest)
    elif sample_xray:
        candidate = SAMPLE_DIR / "xrays" / _safe_name(sample_xray)
        if candidate.exists():
            xray_path = str(candidate)

    report_path = ""
    if lab_report_file is not None and lab_report_file.filename:
        if Path(lab_report_file.filename).suffix.lower() not in REPORT_TYPES:
            raise HTTPException(422, "The lab report must be a photo (PNG, JPG, WEBP) or a PDF.")
        dest = UPLOAD_DIR / f"{session_id}_report_{_safe_name(lab_report_file.filename)}"
        dest.write_bytes(await lab_report_file.read())
        report_path = str(dest)
    if not lab_report_text.strip() and not report_path:
        raise HTTPException(422, "Send the lab report as text or as a photo/PDF.")

    try:
        return await cases.resume(
            session_id,
            signal="results_ready",
            state_delta={
                "current_step": CareStep.RESULTS_RECEIVED,
                "lab_report_text": lab_report_text,
                "lab_report_path": report_path,
                "xray_path": xray_path,
                "imaging_findings": {},
                "lab_findings": {},
            },
            message="SYSTEM EVENT: Test results have arrived. Continue the pathway.",
        )
    except SignalRejected as exc:
        raise HTTPException(409, str(exc))


class DoctorDecision(BaseModel):
    session_id: str
    decision: Literal["approve", "more_tests"]
    notes: str = ""
    doctor_name: str = "Doctor"


@app.post("/webhooks/doctor_decision")
async def doctor_decision(body: DoctorDecision):
    """Called when the doctor signs off (human-in-the-loop approval gate)."""
    decision = {"decision": body.decision, "notes": body.notes, "doctor": body.doctor_name}
    if body.decision == "approve":
        delta = {"current_step": CareStep.DOCTOR_APPROVED, "doctor_decision": decision}
        message = "SYSTEM EVENT: The doctor has approved a plan. Continue the pathway."
    else:
        # Loop back to the results gate: the case sleeps again until new results arrive.
        delta = {
            "current_step": CareStep.TESTS_ORDERED,
            "pending_signals": ["results_ready"],
            "doctor_decision": decision,
            "imaging_findings": {},
            "lab_findings": {},
            "doctor_summary": {},
            "lab_report_path": "",
        }
        message = ("SYSTEM EVENT: The doctor requested more tests. The case is waiting "
                   "for new results. Acknowledge briefly.")
    try:
        return await cases.resume(body.session_id, "doctor_decision", delta, message)
    except SignalRejected as exc:
        raise HTTPException(409, str(exc))


class CheckIn(BaseModel):
    session_id: str
    check_in: str


@app.post("/webhooks/follow_up_due")
async def follow_up_due(body: CheckIn):
    """Called by a scheduler (weekly/monthly) with the patient's check-in answers."""
    try:
        return await cases.resume(
            body.session_id,
            signal="follow_up_due",
            state_delta={"current_step": CareStep.FOLLOW_UP_DUE, "latest_check_in": body.check_in},
            message="SYSTEM EVENT: A patient check-in has arrived. Assess it.",
        )
    except SignalRejected as exc:
        raise HTTPException(409, str(exc))

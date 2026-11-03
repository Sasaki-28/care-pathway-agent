"""FastAPI server: ADK dev UI + clinic demo UI + webhooks.

Run from the project root:
    uvicorn server.main:app --port 8000
Then open http://127.0.0.1:8000/clinic  (ADK's own dev UI is at /dev-ui/)

Settings (environment variables or .env):
    GOOGLE_API_KEY     Gemini API key
    SESSION_DB_URI     database for cases; SQLite file by default, or a Supabase
                       postgresql://... URI (Session pooler) when deployed
    DEMO_PASSWORD      if set, the whole site asks for this password
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
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / "agents"
DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
SAMPLE_DIR = ROOT / "sample_data"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(AGENTS_DIR / "care_pathway" / ".env")
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(AGENTS_DIR))

from google.adk.cli.fast_api import get_fast_api_app  # noqa: E402
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import DatabaseSessionService  # noqa: E402

from care_pathway.agent import root_agent  # noqa: E402
from care_pathway.pathways import PATHWAYS  # noqa: E402
from care_pathway.state_schema import CareStep, initial_state  # noqa: E402
from care_pathway.tools import AUDIT_PREFIX, merged_audit  # noqa: E402
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

# ------------------------------------------------------------------ demo password
# When DEMO_PASSWORD is set (always set it on a public deployment), the whole site asks
# for it via the browser's built-in login prompt. Any username works.
DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "")


@app.middleware("http")
async def protect_and_route(request: Request, call_next):
    if DEMO_PASSWORD:
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
    if request.url.path == "/":
        return RedirectResponse("/clinic")
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


# ------------------------------------------------------------------ demo UI + case API

@app.get("/clinic/classic", include_in_schema=False)
async def clinic_ui_classic():
    """The previous console design, kept as a fallback."""
    return FileResponse(ROOT / "server" / "static" / "classic.html")


@app.get("/clinic", include_in_schema=False)
async def clinic_ui():
    return FileResponse(ROOT / "server" / "static" / "index.html")


class NewCase(BaseModel):
    name: str
    age: int = Field(ge=0, le=120)
    sex: str
    phone: str
    symptoms: str
    pathway: str = "tb"

    @field_validator("pathway")
    @classmethod
    def known_pathway(cls, value: str) -> str:
        if value not in PATHWAYS:
            raise ValueError(f"Unknown pathway '{value}'. Options: {', '.join(PATHWAYS)}")
        return value


@app.post("/api/cases")
async def create_case(body: NewCase):
    session = await session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID, state=initial_state(),
        session_id=f"case-{uuid.uuid4().hex[:8]}",
    )
    message = (
        "New patient at the front desk. "
        f"Name: {body.name}. Age: {body.age}. Sex: {body.sex}. Phone: {body.phone}. "
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
        if full is None:
            continue
        st = full.state
        rows.append({
            "session_id": s.id,
            "name": st.get("patient", {}).get("name", "(intake)"),
            "pathway_id": st.get("pathway_id", ""),
            "current_step": st.get("current_step", CareStep.INTAKE),
            "last_update": full.last_update_time,
        })
    rows.sort(key=lambda r: r["last_update"] or 0, reverse=True)
    return rows


@app.get("/api/cases/{session_id}")
async def get_case(session_id: str):
    session = await _require_case(session_id)
    state = {k: v for k, v in session.state.items()
             if not k.startswith("_") and not k.startswith(AUDIT_PREFIX)}
    state["audit_log"] = merged_audit(session.state)
    return {"session_id": session_id, "state": state, "transcript": _transcript(session),
            "steps": CareStep.ALL}


class ChatMessage(BaseModel):
    message: str


@app.post("/api/cases/{session_id}/chat")
async def chat(session_id: str, body: ChatMessage):
    """Clinic staff talk to the coordinator (e.g. to try to skip a gate during the demo)."""
    await _require_case(session_id)
    return await cases.run(session_id, body.message)


@app.get("/api/pathways")
async def pathways():
    """Everything the UI needs to offer a pathway. Adding one to pathways.py is enough."""
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


# ------------------------------------------------------------------ webhooks (external events)

@app.post("/webhooks/results_ready")
async def results_ready(
    session_id: str = Form(...),
    lab_report_text: str = Form(""),
    sample_xray: str = Form(""),
    xray: UploadFile | None = File(None),
):
    """Called by the lab system (or the demo UI) when test results are available."""
    await _require_case(session_id)
    xray_path = ""
    if xray is not None and xray.filename:
        dest = UPLOAD_DIR / f"{session_id}_{_safe_name(xray.filename)}"
        dest.write_bytes(await xray.read())
        xray_path = str(dest)
    elif sample_xray:
        candidate = SAMPLE_DIR / "xrays" / _safe_name(sample_xray)
        if candidate.exists():
            xray_path = str(candidate)

    try:
        return await cases.resume(
            session_id,
            signal="results_ready",
            state_delta={
                "current_step": CareStep.RESULTS_RECEIVED,
                "lab_report_text": lab_report_text,
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

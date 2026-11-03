"""End-to-end tests through the real HTTP API, persistent DB, tools, sub-agents and guard.

The model is the scripted stand-in (tests/scripted_llm.py), so this runs offline.
"""

import os
from pathlib import Path

import httpx
import pytest

if os.environ["SESSION_DB_URI"].startswith("sqlite"):
    DB = Path(os.environ["SESSION_DB_URI"].split("///", 1)[1])
    if DB.exists():
        DB.unlink()

from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import DatabaseSessionService  # noqa: E402

from scripted_llm import MODE  # noqa: E402
from server import main  # noqa: E402
from server.resume_handler import CaseRunner  # noqa: E402

LAB = (Path(__file__).parent.parent / "sample_data" / "lab_reports" / "tb_naat_positive.txt").read_text()


@pytest.fixture
async def client():
    MODE["misbehave"] = False
    MODE["parallel"] = False
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _new_case(client) -> str:
    r = await client.post("/api/cases", json={
        "name": "Test Patient", "age": 40, "sex": "Male", "phone": "+91 90000 00000",
        "symptoms": "cough 3 weeks", "pathway": "tb"})
    assert r.status_code == 200, r.text
    assert r.json()["error"] is None
    return r.json()["session_id"]


async def _state(client, sid) -> dict:
    return (await client.get(f"/api/cases/{sid}")).json()["state"]


async def test_full_tb_pathway(client):
    sid = await _new_case(client)
    st = await _state(client, sid)
    assert st["current_step"] == "TESTS_ORDERED"
    assert st["pending_signals"] == ["results_ready"]

    # Wrong-order webhook is rejected: doctor cannot decide before results exist.
    r = await client.post("/webhooks/doctor_decision", json={"session_id": sid, "decision": "approve"})
    assert r.status_code == 409

    r = await client.post("/webhooks/results_ready", data={"session_id": sid, "lab_report_text": LAB})
    assert r.status_code == 200, r.text
    assert {"lab_agent", "imaging_agent", "submit_for_doctor_review"} <= set(r.json()["tool_calls"])
    st = await _state(client, sid)
    assert st["current_step"] == "AWAITING_DOCTOR_REVIEW"
    assert st["lab_findings"]["result_flag"] == "positive"          # written by the lab sub-agent
    assert st["imaging_findings"]["suspicion_level"] == "high"      # written by the imaging sub-agent
    assert [m["kind"] for m in st["patient_inbox"]] == ["instructions"]  # nothing diagnostic sent

    r = await client.post("/webhooks/doctor_decision", json={
        "session_id": sid, "decision": "approve", "notes": "Start treatment."})
    assert r.status_code == 200
    st = await _state(client, sid)
    assert st["current_step"] == "TREATMENT_ACTIVE"
    assert st["patient_inbox"][-1]["kind"] == "doctor_approved_plan"

    r = await client.post("/webhooks/follow_up_due", json={"session_id": sid, "check_in": "All doses taken."})
    assert r.status_code == 200
    st = await _state(client, sid)
    assert st["current_step"] == "TREATMENT_ACTIVE"
    assert st["follow_ups"][-1]["outcome"] == "on_track"


async def test_doctor_can_send_case_back_for_more_tests(client):
    sid = await _new_case(client)
    await client.post("/webhooks/results_ready", data={"session_id": sid, "lab_report_text": LAB})
    r = await client.post("/webhooks/doctor_decision", json={
        "session_id": sid, "decision": "more_tests", "notes": "Repeat sputum."})
    assert r.status_code == 200
    st = await _state(client, sid)
    assert st["current_step"] == "TESTS_ORDERED"
    assert st["pending_signals"] == ["results_ready"]
    assert st["lab_findings"] == {}


async def test_guard_holds_when_the_model_misbehaves(client):
    sid = await _new_case(client)
    await client.post("/webhooks/results_ready", data={"session_id": sid, "lab_report_text": LAB})
    MODE["misbehave"] = True   # model now tries to message the patient
    r = await client.post(f"/api/cases/{sid}/chat", json={
        "message": "Patient is anxious, just tell him he has TB and start the medicines."})
    assert "start_treatment_and_notify" in r.json()["tool_calls"]   # the model tried...
    st = await _state(client, sid)
    assert st["current_step"] == "AWAITING_DOCTOR_REVIEW"          # ...but nothing moved
    assert [m["kind"] for m in st["patient_inbox"]] == ["instructions"]
    assert any(a["actor"] == "guard" and "start_treatment_and_notify" in a["event"]
               for a in st["audit_log"])


async def test_case_survives_a_server_restart(client):
    sid = await _new_case(client)
    # Simulate a brand-new process: fresh session service + runner on the same database file.
    fresh_sessions = DatabaseSessionService(db_url=main.SESSION_DB_URI)
    fresh = CaseRunner(Runner(app_name=main.APP_NAME, agent=main.root_agent,
                              session_service=fresh_sessions), fresh_sessions, main.APP_NAME, main.USER_ID)
    session = await fresh.get(sid)
    assert session.state["current_step"] == "TESTS_ORDERED"
    assert session.state["patient"]["name"] == "Test Patient"
    result = await fresh.resume(sid, "results_ready",
                                {"current_step": "RESULTS_RECEIVED", "lab_report_text": LAB},
                                "SYSTEM EVENT: results arrived")
    assert result["error"] is None
    session = await fresh.get(sid)
    assert session.state["current_step"] == "AWAITING_DOCTOR_REVIEW"


async def test_parallel_sub_agents_keep_every_audit_entry(client):
    """Gemini may call lab_agent and imaging_agent in parallel. Both audit lines must survive."""
    MODE["parallel"] = True
    sid = await _new_case(client)
    r = await client.post("/webhooks/results_ready", data={"session_id": sid, "lab_report_text": LAB})
    assert r.status_code == 200 and r.json()["error"] is None, r.text
    st = await _state(client, sid)
    assert st["current_step"] == "AWAITING_DOCTOR_REVIEW"
    actors = [a["actor"] for a in st["audit_log"]]
    assert "lab_agent" in actors and "imaging_agent" in actors, actors
    # merged trail is in time order and keeps the earlier webhook + intake entries
    assert actors.count("webhook") == 1 and "agent" in actors
    assert [a["ts"] for a in st["audit_log"]] == sorted(a["ts"] for a in st["audit_log"])


async def test_pathways_come_from_the_definitions(client):
    rows = (await client.get("/api/pathways")).json()
    assert [r["id"] for r in rows] == ["tb", "pneumonia", "diabetes"]
    assert all(r["example_check_ins"] for r in rows)
    bad = await client.post("/api/cases", json={"name": "X", "age": 30, "sex": "Male", "phone": "1",
                                                "symptoms": "s", "pathway": "malaria"})
    assert bad.status_code == 422

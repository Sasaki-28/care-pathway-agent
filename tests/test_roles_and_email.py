"""Role screens, patient portal, patient emails, lab report uploads, triage and pathway config.

Runs offline with the scripted model (tests/scripted_llm.py); emails are captured, not sent.
"""

import json
from pathlib import Path

import httpx
import pytest

from care_pathway.pathways import load_pathways
from care_pathway.triage import suggest_by_keywords
from scripted_llm import MODE, SEEN_FILES
from server import main, notify

LAB = (Path(__file__).parent.parent / "sample_data" / "lab_reports" / "tb_naat_positive.txt").read_text()
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.fixture
async def client():
    MODE["misbehave"] = False
    MODE["parallel"] = False
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def outbox(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    sent = []

    async def fake_send(to, subject, text, page):
        sent.append({"to": to, "subject": subject, "text": text, "html": page})
        return True, "sent"
    monkeypatch.setattr(notify, "send_email", fake_send)
    return sent


async def _new_case(client, email="") -> str:
    r = await client.post("/api/cases", json={
        "name": "Test Patient", "age": 40, "sex": "Male", "phone": "+91 90000 00000",
        "email": email, "symptoms": "cough 3 weeks", "pathway": "tb"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


async def _raw_state(sid) -> dict:
    return (await main.cases.get(sid)).state


async def _to_treatment(client, sid):
    await client.post("/webhooks/results_ready", data={"session_id": sid, "lab_report_text": LAB})
    r = await client.post("/webhooks/doctor_decision", json={"session_id": sid, "decision": "approve", "notes": "Start treatment."})
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ screens

@pytest.mark.parametrize("path", ["/", "/clinic", "/doctor", "/lab", "/patient/case-x", "/clinic/single",
                                  "/clinic/classic", "/assets/care.css", "/assets/care.js"])
async def test_every_screen_loads(client, path):
    r = await client.get(path)
    assert r.status_code == 200, path


async def test_home_is_the_role_picker_not_adk(client):
    assert "Choose a role" in (await client.get("/")).text


# ------------------------------------------------------------------ email + contact privacy

async def test_email_is_kept_away_from_the_ai_and_staff_api(client, outbox):
    sid = await _new_case(client, email="patient@example.com")
    st = await _raw_state(sid)
    assert st["contact"] == {"email": "patient@example.com"}
    assert len(st["patient_token"]) >= 20
    case = (await client.get(f"/api/cases/{sid}")).json()
    assert "contact" not in case["state"] and "patient_token" not in case["state"]
    assert case["state"]["has_email"] is True
    assert all("patient@example.com" not in m["text"] for m in case["transcript"])   # never in the prompt


async def test_patient_is_emailed_only_what_reaches_their_inbox(client, outbox):
    sid = await _new_case(client, email="patient@example.com")
    assert [m["subject"] for m in outbox] == ["Your tests have been requested"]
    assert outbox[0]["to"] == "patient@example.com"
    token = (await _raw_state(sid))["patient_token"]
    assert f"/patient/{sid}?t={token}" in outbox[0]["text"]

    await client.post("/webhooks/results_ready", data={"session_id": sid, "lab_report_text": LAB})
    assert len(outbox) == 1                              # findings are never emailed before sign-off

    await client.post("/webhooks/doctor_decision", json={"session_id": sid, "decision": "approve", "notes": "Start treatment."})
    assert [m["subject"] for m in outbox][-1] == "A message from your doctor"
    st = await _raw_state(sid)
    assert st["emailed_count"] == len(st["patient_inbox"]) == 2
    emailed = [k for k in st if k == "audit_log__email"]
    assert emailed and all("Emailed patient" in a["event"] for a in st["audit_log__email"])


async def test_guard_blocked_message_is_not_emailed(client, outbox):
    sid = await _new_case(client, email="patient@example.com")
    await client.post("/webhooks/results_ready", data={"session_id": sid, "lab_report_text": LAB})
    MODE["misbehave"] = True
    await client.post(f"/api/cases/{sid}/chat", json={"message": "Just tell him he has TB."})
    assert [m["subject"] for m in outbox] == ["Your tests have been requested"]


async def test_email_not_set_up_is_silent(client, monkeypatch):
    async def boom(*_):
        raise AssertionError("must not send without a key")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setattr(notify, "send_email", boom)
    sid = await _new_case(client, email="patient@example.com")
    st = await _raw_state(sid)
    assert st["emailed_count"] == 1 and "audit_log__email" not in st


async def test_no_email_address_means_no_email(client, outbox):
    sid = await _new_case(client)
    await _to_treatment(client, sid)
    assert outbox == []
    assert (await _raw_state(sid))["emailed_count"] == 2   # counted, so nothing is sent late


async def test_failed_email_never_breaks_the_case(client, monkeypatch):
    async def down(*_):
        return False, "HTTP 403: refused"
    monkeypatch.setattr(notify, "send_email", down)
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    sid = await _new_case(client, email="patient@example.com")
    st = await _raw_state(sid)
    assert st["current_step"] == "TESTS_ORDERED"
    assert "Email not sent" in st["audit_log__email"][-1]["event"]


def test_email_text_has_link_and_emergency_note():
    subject, text, page = notify.build_email("Asha", {"kind": "instructions", "text": "Please give a <sample>."}, "https://x/p?t=1")
    assert subject == "Your tests have been requested"
    assert "Dear Asha" in text and "https://x/p?t=1" in text and "108" in text
    assert "&lt;sample&gt;" in page                         # escaped in the HTML version


# ------------------------------------------------------------------ patient portal

async def test_patient_page_needs_the_right_token(client):
    sid = await _new_case(client)
    assert (await client.get(f"/api/patient/{sid}?t=wrong")).status_code == 404
    assert (await client.get(f"/api/patient/{sid}")).status_code == 404
    assert (await client.get("/api/patient/case-nope?t=x")).status_code == 404
    token = (await _raw_state(sid))["patient_token"]
    v = (await client.get(f"/api/patient/{sid}?t={token}")).json()
    assert v["first_name"] == "Test" and v["step"] == "TESTS_ORDERED" and not v["can_check_in"]
    assert set(v) == {"first_name", "care_plan", "status", "step", "messages", "can_check_in", "check_in_examples"}


async def test_patient_sees_no_findings_before_sign_off(client):
    sid = await _new_case(client)
    await client.post("/webhooks/results_ready", data={"session_id": sid, "lab_report_text": LAB})
    token = (await _raw_state(sid))["patient_token"]
    body = (await client.get(f"/api/patient/{sid}?t={token}")).text
    assert "MTB" not in body and "opacity" not in body
    assert [m["kind"] for m in json.loads(body)["messages"]] == ["instructions"]


async def test_patient_checks_in_from_their_page(client):
    sid = await _new_case(client)
    token = (await _raw_state(sid))["patient_token"]
    early = await client.post(f"/api/patient/{sid}/check-in?t={token}", json={"check_in": "Fine"})
    assert early.status_code == 409                       # no check-ins before treatment
    await _to_treatment(client, sid)
    assert (await client.post(f"/api/patient/{sid}/check-in?t=bad", json={"check_in": "Fine"})).status_code == 404
    r = await client.post(f"/api/patient/{sid}/check-in?t={token}", json={"check_in": "All doses taken."})
    assert r.status_code == 200 and r.json()["ok"], r.text
    st = await _raw_state(sid)
    assert st["follow_ups"][-1]["outcome"] == "on_track"
    assert st["patient_inbox"][-1]["kind"] == "follow_up"


async def test_password_protects_staff_screens_but_not_the_patient_link(client, monkeypatch):
    sid = await _new_case(client)
    token = (await _raw_state(sid))["patient_token"]
    monkeypatch.setattr(main, "DEMO_PASSWORD", "s3cret")
    assert (await client.get("/api/cases")).status_code == 401
    assert (await client.get("/doctor")).status_code == 401
    assert (await client.get("/api/cases", auth=("any", "s3cret"))).status_code == 200
    assert (await client.get(f"/api/patient/{sid}?t={token}")).status_code == 200
    assert (await client.get(f"/patient/{sid}")).status_code == 200
    assert (await client.get("/assets/care.css")).status_code == 200


# ------------------------------------------------------------------ doctor queue

async def test_doctor_queue_shows_cases_waiting_for_sign_off(client):
    sid = await _new_case(client)
    q = (await client.get("/api/doctor/queue")).json()
    assert sid not in [r["session_id"] for r in q["review"]]
    await client.post("/webhooks/results_ready", data={"session_id": sid, "lab_report_text": LAB})
    q = (await client.get("/api/doctor/queue")).json()
    row = next(r for r in q["review"] if r["session_id"] == sid)
    assert row["lab_flag"] == "positive" and row["imaging_level"] == "high"
    await client.post("/webhooks/doctor_decision", json={"session_id": sid, "decision": "approve", "notes": "Start."})
    q = (await client.get("/api/doctor/queue")).json()
    assert sid not in [r["session_id"] for r in q["review"]]
    assert sid in [r["session_id"] for r in q["recent"]]


# ------------------------------------------------------------------ lab report upload

async def test_lab_report_photo_is_shown_to_the_lab_agent(client):
    sid = await _new_case(client)
    SEEN_FILES.clear()
    r = await client.post("/webhooks/results_ready", data={"session_id": sid},
                          files={"lab_report_file": ("report.png", PNG, "image/png")})
    assert r.status_code == 200 and r.json()["error"] is None, r.text
    assert ("lab", "image/png") in SEEN_FILES                 # the photo reached the lab agent's request
    st = await _raw_state(sid)
    assert st["lab_report_path"].endswith("report.png")
    got = await client.get(f"/api/cases/{sid}/lab_report")
    assert got.status_code == 200 and got.content == PNG
    # sending the case back for more tests clears the old report
    await client.post("/webhooks/doctor_decision", json={"session_id": sid, "decision": "more_tests", "notes": "Repeat."})
    assert (await _raw_state(sid))["lab_report_path"] == ""


async def test_lab_report_must_be_text_or_a_supported_file(client):
    sid = await _new_case(client)
    r = await client.post("/webhooks/results_ready", data={"session_id": sid},
                          files={"lab_report_file": ("report.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 422
    r = await client.post("/webhooks/results_ready", data={"session_id": sid, "lab_report_text": "  "})
    assert r.status_code == 422
    assert (await _raw_state(sid))["current_step"] == "TESTS_ORDERED"


# ------------------------------------------------------------------ triage + pathway config

def test_keyword_triage_suggests_and_admits_uncertainty():
    assert suggest_by_keywords("High fever, severe headache, pain behind the eyes, rash")["pathway_id"] == "dengue"
    assert suggest_by_keywords("Cough for 3 weeks, night sweats, weight loss")["pathway_id"] == "tb"
    assert suggest_by_keywords("BP 170/105 at the pharmacy, headaches")["pathway_id"] == "hypertension"
    none = suggest_by_keywords("sprained ankle")
    assert none["pathway_id"] == "" and none["source"] == "rules"


async def test_triage_endpoint_falls_back_to_rules_without_a_key(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    r = (await client.post("/api/triage", json={"symptoms": "Excessive thirst, frequent urination, tired"})).json()
    assert r["pathway_id"] == "diabetes" and r["source"] == "rules"


@pytest.mark.parametrize("change, message", [
    (lambda p: p.pop("tests"), "missing"),
    (lambda p: p.update(colour="red"), "unknown fields"),
    (lambda p: p.pop("imaging_focus"), "imaging_focus"),
])
def test_broken_pathway_config_is_rejected(tmp_path, change, message):
    raw = json.loads((Path(load_pathways.__globals__["CONFIG_FILE"])).read_text())
    tb = raw["pathways"][0]
    change(tb)
    f = tmp_path / "pathways.json"
    f.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match=message):
        load_pathways(f)


def test_duplicate_pathway_ids_are_rejected(tmp_path):
    raw = json.loads((Path(load_pathways.__globals__["CONFIG_FILE"])).read_text())
    raw["pathways"].append(raw["pathways"][0])
    f = tmp_path / "pathways.json"
    f.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="Duplicate"):
        load_pathways(f)


async def test_screen_can_choose_the_case_id_and_board_gets_recent_activity(client):
    r = await client.post("/api/cases", json={
        "name": "Id Patient", "age": 30, "sex": "Female", "symptoms": "cough", "pathway": "tb",
        "session_id": "case-0badc0de"})
    assert r.status_code == 200 and r.json()["session_id"] == "case-0badc0de"
    again = await client.post("/api/cases", json={
        "name": "X", "age": 30, "sex": "Female", "symptoms": "cough", "pathway": "tb", "session_id": "case-0badc0de"})
    assert again.status_code == 409
    bad = await client.post("/api/cases", json={
        "name": "X", "age": 30, "sex": "Female", "symptoms": "cough", "pathway": "tb", "session_id": "../etc"})
    assert bad.status_code == 422
    row = next(r for r in (await client.get("/api/cases")).json() if r["session_id"] == "case-0badc0de")
    assert row["recent"] and row["recent"][-1]["event"].startswith("Ordered tests")

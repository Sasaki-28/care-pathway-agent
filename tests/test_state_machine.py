"""Unit tests: tools and the code-level guard, no model involved."""

from types import SimpleNamespace

from care_pathway.guards import enforce_step_permissions
from care_pathway.state_schema import ALLOWED_TOOLS, CareStep, initial_state
from care_pathway import tools


def ctx(**overrides):
    state = initial_state()
    state.update(overrides)
    return SimpleNamespace(state=state)


def tool(name):
    return SimpleNamespace(name=name)


def test_every_step_has_a_permission_entry():
    assert set(ALLOWED_TOOLS) == set(CareStep.ALL)


def test_intake_registers_and_orders_tests():
    c = ctx()
    assert tools.register_patient("Asha", 35, "Female", "+91", "cough", "tb", c)["status"] == "success"
    r = tools.order_tests(c)
    assert r["status"] == "success"
    assert c.state["current_step"] == CareStep.TESTS_ORDERED
    assert c.state["pending_signals"] == ["results_ready"]
    assert "Chest X-ray (PA view)" in c.state["tests_ordered"]
    assert c.state["patient_inbox"][0]["kind"] == "instructions"


def test_unknown_pathway_is_rejected():
    c = ctx()
    assert tools.register_patient("Asha", 35, "Female", "+91", "cough", "cancer", c)["status"] == "error"


def test_cannot_order_tests_before_registration():
    assert tools.order_tests(ctx())["status"] == "error"


def test_guard_blocks_everything_while_sleeping():
    for step in (CareStep.TESTS_ORDERED, CareStep.AWAITING_DOCTOR_REVIEW, CareStep.TREATMENT_ACTIVE):
        c = ctx(current_step=step)
        for name in ("submit_for_doctor_review", "start_treatment_and_notify", "imaging_agent"):
            result = enforce_step_permissions(tool(name), {}, c)
            assert result["status"] == "blocked", (step, name)
        assert any(a["actor"] == "guard" for a in tools.merged_audit(c.state))


def test_guard_blocks_patient_message_before_doctor_approval():
    c = ctx(current_step=CareStep.RESULTS_RECEIVED)
    assert enforce_step_permissions(tool("start_treatment_and_notify"), {}, c)["status"] == "blocked"


def test_guard_allows_the_right_tool():
    c = ctx(current_step=CareStep.RESULTS_RECEIVED)
    assert enforce_step_permissions(tool("lab_agent"), {}, c) is None


def test_submit_requires_findings_for_tb():
    c = ctx(current_step=CareStep.RESULTS_RECEIVED, pathway_id="tb")
    r = tools.submit_for_doctor_review("s", "n", c)
    assert r["status"] == "error" and "imaging" in r["message"]
    tools.record_lab_findings("MTB detected", "positive", ["MTB: DETECTED"], c)
    assert tools.submit_for_doctor_review("s", "n", c)["status"] == "error"  # still no imaging
    tools.record_imaging_findings("opacity", "high", ["RUL opacity"], "ok", c)
    assert tools.submit_for_doctor_review("s", "n", c)["status"] == "success"
    assert c.state["current_step"] == CareStep.AWAITING_DOCTOR_REVIEW


def test_diabetes_does_not_need_imaging():
    c = ctx(current_step=CareStep.RESULTS_RECEIVED, pathway_id="diabetes")
    tools.record_lab_findings("HbA1c 8.2%", "abnormal", ["HbA1c 8.2 %"], c)
    assert tools.submit_for_doctor_review("s", "n", c)["status"] == "success"


def test_treatment_tool_double_checks_approval():
    # Even if the guard were misconfigured, the tool itself refuses without approval.
    c = ctx(current_step=CareStep.DOCTOR_APPROVED, doctor_decision={})
    assert tools.start_treatment_and_notify("hi", c)["status"] == "error"
    c.state["doctor_decision"] = {"decision": "approve", "notes": "start"}
    assert tools.start_treatment_and_notify("hi", c)["status"] == "success"
    assert c.state["current_step"] == CareStep.TREATMENT_ACTIVE


def test_follow_up_outcomes():
    for outcome, expected in (("on_track", CareStep.TREATMENT_ACTIVE),
                              ("escalate", CareStep.ESCALATED),
                              ("completed", CareStep.COMPLETED)):
        c = ctx(current_step=CareStep.FOLLOW_UP_DUE, latest_check_in="text")
        tools.record_follow_up(outcome, "summary", "msg", c)
        assert c.state["current_step"] == expected
    c = ctx(current_step=CareStep.FOLLOW_UP_DUE)
    tools.record_follow_up("escalate", "missed doses", "We'll call you", c)
    assert c.state["doctor_alerts"]


def test_invalid_enum_values_rejected():
    c = ctx()
    assert tools.record_imaging_findings("x", "certain", [], "ok", c)["status"] == "error"
    assert tools.record_lab_findings("x", "maybe", [], c)["status"] == "error"
    assert tools.record_follow_up("fine", "x", "y", c)["status"] == "error"


def test_escalation_message_is_fixed_not_model_written():
    c = ctx(current_step=CareStep.FOLLOW_UP_DUE, latest_check_in="eyes yellow")
    tools.record_follow_up("escalate", "possible liver injury",
                           "Keep taking your medicines until you speak with the team.", c)
    sent = c.state["patient_inbox"][-1]
    assert sent["kind"] == "escalation_notice"
    assert sent["text"] == tools.ESCALATION_PATIENT_MESSAGE
    assert "medicines" not in sent["text"].lower()


# ---------------------------------------------------------------- pathways are data

from care_pathway.pathways import PATHWAYS  # noqa: E402


def test_every_pathway_is_complete():
    for p in PATHWAYS.values():
        assert p.tests and p.lab_focus and p.escalation_rules and p.completion_rule, p.id
        assert p.requires_imaging == bool(p.imaging_focus), p.id
        assert set(p.example_check_ins) == {"on_track", "warning", "finished"}, p.id
        assert p.example_symptoms and p.example_doctor_notes, p.id


def test_pneumonia_pathway_runs_on_the_same_tools():
    c = ctx()
    assert tools.register_patient("Asha", 35, "Female", "+91", "fever, cough", "pneumonia", c)["status"] == "success"
    tools.order_tests(c)
    assert "C-reactive protein (CRP)" in c.state["tests_ordered"]
    c.state["current_step"] = CareStep.RESULTS_RECEIVED
    tools.record_lab_findings("WBC and CRP raised", "abnormal", ["WBC 16.8", "CRP 142"], c)
    assert tools.submit_for_doctor_review("s", "n", c)["status"] == "error"   # pneumonia needs the X-ray read
    tools.record_imaging_findings("RLL consolidation", "high", ["right lower lobe consolidation"], "ok", c)
    assert tools.submit_for_doctor_review("s", "n", c)["status"] == "success"

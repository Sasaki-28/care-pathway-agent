"""Generic care-pathway state machine.

The step names are deliberately disease-agnostic. Every pathway (TB, diabetes, ...)
moves through the same checkpoints; only the tests, specialist sub-agents and
follow-up rules differ (see pathways.py).
"""


class CareStep:
    INTAKE = "INTAKE"                                  # collecting patient details
    TESTS_ORDERED = "TESTS_ORDERED"                    # idle gate: waiting for results
    RESULTS_RECEIVED = "RESULTS_RECEIVED"              # specialist sub-agents analyse results
    AWAITING_DOCTOR_REVIEW = "AWAITING_DOCTOR_REVIEW"  # human gate: doctor must decide
    DOCTOR_APPROVED = "DOCTOR_APPROVED"                # doctor signed off on a plan
    TREATMENT_ACTIVE = "TREATMENT_ACTIVE"              # idle gate: waiting for next check-in
    FOLLOW_UP_DUE = "FOLLOW_UP_DUE"                    # assess a patient check-in
    ESCALATED = "ESCALATED"                            # handed back to the doctor (terminal for the agent)
    COMPLETED = "COMPLETED"                            # pathway finished

    ALL = [
        INTAKE, TESTS_ORDERED, RESULTS_RECEIVED, AWAITING_DOCTOR_REVIEW,
        DOCTOR_APPROVED, TREATMENT_ACTIVE, FOLLOW_UP_DUE, ESCALATED, COMPLETED,
    ]


# Steps where the agent must do nothing but wait for an external event (webhook).
IDLE_STEPS = {
    CareStep.TESTS_ORDERED: "results_ready",
    CareStep.AWAITING_DOCTOR_REVIEW: "doctor_decision",
    CareStep.TREATMENT_ACTIVE: "follow_up_due",
}

# The single source of truth for which tools may run in which step.
# Enforced in code by guards.enforce_step_permissions, not just by the prompt.
ALLOWED_TOOLS = {
    CareStep.INTAKE: {"register_patient", "order_tests"},
    CareStep.TESTS_ORDERED: set(),
    CareStep.RESULTS_RECEIVED: {
        "imaging_agent", "lab_agent",
        "record_imaging_findings", "record_lab_findings",
        "submit_for_doctor_review",
    },
    CareStep.AWAITING_DOCTOR_REVIEW: set(),
    CareStep.DOCTOR_APPROVED: {"start_treatment_and_notify"},
    CareStep.TREATMENT_ACTIVE: set(),
    CareStep.FOLLOW_UP_DUE: {"record_follow_up"},
    CareStep.ESCALATED: set(),
    CareStep.COMPLETED: set(),
}

# Webhook signal -> the step the case must be in for that signal to be accepted.
SIGNAL_EXPECTED_STEP = {signal: step for step, signal in IDLE_STEPS.items()}


def initial_state() -> dict:
    """Default values for every key the agent reads. Missing keys never crash a run."""
    return {
        "current_step": CareStep.INTAKE,
        "pathway_id": "",
        "patient": {},
        "tests_ordered": [],
        "pending_signals": [],
        "xray_path": "",
        "lab_report_text": "",
        "imaging_findings": {},
        "lab_findings": {},
        "doctor_summary": {},
        "doctor_decision": {},
        "follow_ups": [],
        "latest_check_in": "",
        "patient_inbox": [],
        "doctor_alerts": [],
        "lab_report_path": "",       # uploaded photo/PDF of the lab report, if any
        # Set by the server, never shown to the AI: patient contact details and portal token.
        "contact": {},
        "patient_token": "",
        "emailed_count": 0,          # how many patient_inbox messages have been emailed
        # audit entries live in per-actor keys "audit_log__<actor>" (see tools.audit)
    }

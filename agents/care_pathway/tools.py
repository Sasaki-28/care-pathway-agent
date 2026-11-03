"""Tools that advance the state machine.

Every tool writes its checkpoint into session state (ToolContext.state). With a
persistent session service, each tool call is a durable checkpoint: if the server
dies right after a tool runs, the case resumes from the new step.

Rules every tool follows:
- lists in state are reassigned, never mutated in place, so ADK records the change;
- patient-facing text is only written by tools that are allowed after doctor approval
  (order_tests sends a fixed, non-diagnostic instruction to get tests done).
"""

from datetime import datetime, timezone

from google.adk.tools import ToolContext

from .pathways import get_pathway, pathway_ids
from .state_schema import CareStep


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# Each actor (agent, lab_agent, imaging_agent, webhook, guard) appends to its OWN list.
# Gemini can call sub-agents in parallel; ADK merges their state changes key by key,
# so two writers appending to one shared list would overwrite each other.
AUDIT_PREFIX = "audit_log__"


def audit(state, actor: str, event: str) -> None:
    """Append to the case's audit trail (who did what, when)."""
    key = AUDIT_PREFIX + actor
    state[key] = list(state.get(key, [])) + [{"ts": _now(), "actor": actor, "event": event}]


def audit_entry_delta(state, actor: str, event: str) -> dict:
    """The same append, as a state_delta (used by webhooks before the agent runs)."""
    key = AUDIT_PREFIX + actor
    return {key: list(state.get(key, [])) + [{"ts": _now(), "actor": actor, "event": event}]}


def merged_audit(state) -> list[dict]:
    """All actors' entries in time order (also reads the old single 'audit_log' key)."""
    entries = list(state.get("audit_log", []))
    for key, value in state.items():
        if key.startswith(AUDIT_PREFIX) and isinstance(value, list):
            entries.extend(value)
    return sorted(entries, key=lambda e: e.get("ts", ""))


def _send_patient_message(state, text: str, kind: str) -> None:
    state["patient_inbox"] = list(state.get("patient_inbox", [])) + [
        {"ts": _now(), "kind": kind, "text": text}
    ]


def _error(message: str) -> dict:
    return {"status": "error", "message": message}


# ---------------------------------------------------------------- INTAKE

def register_patient(
    name: str,
    age: int,
    sex: str,
    phone: str,
    symptoms: str,
    pathway_id: str,
    tool_context: ToolContext,
) -> dict:
    """Registers the patient and the suspected care pathway for this case.

    Args:
        name: Patient's full name.
        age: Age in years.
        sex: Patient's sex as stated by staff.
        phone: Contact number for patient messages.
        symptoms: Presenting symptoms in the staff's words.
        pathway_id: The suspected pathway id, e.g. "tb", "pneumonia" or "diabetes".
    """
    state = tool_context.state
    pathway = get_pathway(pathway_id)
    if pathway is None:
        return _error(f"Unknown pathway '{pathway_id}'. Use one of: {pathway_ids()}.")
    state["patient"] = {
        "name": name, "age": age, "sex": sex, "phone": phone, "symptoms": symptoms,
    }
    state["pathway_id"] = pathway.id
    audit(state, "agent", f"Registered {name} on pathway '{pathway.name}'")
    return {
        "status": "success",
        "message": f"{name} registered for pathway: {pathway.name}. Next: order_tests.",
    }


def order_tests(tool_context: ToolContext) -> dict:
    """Orders the pathway's diagnostic tests and puts the case to sleep until results arrive."""
    state = tool_context.state
    pathway = get_pathway(state.get("pathway_id", ""))
    if not state.get("patient") or pathway is None:
        return _error("Register the patient (register_patient) before ordering tests.")

    state["tests_ordered"] = pathway.tests
    state["current_step"] = CareStep.TESTS_ORDERED
    state["pending_signals"] = ["results_ready"]
    _send_patient_message(
        state,
        "Your doctor has requested the following tests: "
        + ", ".join(pathway.tests)
        + ". Please get them done at the clinic lab. We will contact you once "
        "the doctor has reviewed the results.",
        kind="instructions",
    )
    audit(state, "agent", "Ordered tests: " + ", ".join(pathway.tests))
    return {
        "status": "success",
        "message": "Tests ordered. The case is now paused until the results webhook fires.",
        "tests": pathway.tests,
    }


# ---------------------------------------------------------------- RESULTS (sub-agents)

def record_imaging_findings(
    impression: str,
    suspicion_level: str,
    key_findings: list[str],
    image_quality: str,
    tool_context: ToolContext,
) -> dict:
    """Stores the imaging sub-agent's structured findings.

    Args:
        impression: One or two sentence overall impression of the image.
        suspicion_level: One of "high", "moderate", "low", "unable_to_assess".
        key_findings: Short list of observed findings, each a few words.
        image_quality: Short note on image quality / limitations.
    """
    level = suspicion_level.lower().strip()
    if level not in {"high", "moderate", "low", "unable_to_assess"}:
        return _error("suspicion_level must be high, moderate, low or unable_to_assess.")
    state = tool_context.state
    state["imaging_findings"] = {
        "impression": impression,
        "suspicion_level": level,
        "key_findings": key_findings,
        "image_quality": image_quality,
        "ts": _now(),
    }
    audit(state, "imaging_agent", f"Imaging analysed: suspicion {level}")
    return {"status": "success"}


def record_lab_findings(
    summary: str,
    result_flag: str,
    key_values: list[str],
    tool_context: ToolContext,
) -> dict:
    """Stores the lab sub-agent's structured reading of the lab report.

    Args:
        summary: One or two sentence plain-language summary of the report.
        result_flag: One of "positive", "negative", "abnormal", "normal", "indeterminate".
        key_values: The key result lines copied from the report, e.g. "MTB DETECTED LOW".
    """
    flag = result_flag.lower().strip()
    if flag not in {"positive", "negative", "abnormal", "normal", "indeterminate"}:
        return _error("result_flag must be positive, negative, abnormal, normal or indeterminate.")
    state = tool_context.state
    state["lab_findings"] = {
        "summary": summary,
        "result_flag": flag,
        "key_values": key_values,
        "ts": _now(),
    }
    audit(state, "lab_agent", f"Lab report read: {flag}")
    return {"status": "success"}


def submit_for_doctor_review(
    summary_for_doctor: str,
    suggested_next_step: str,
    tool_context: ToolContext,
) -> dict:
    """Packages the findings for the doctor and pauses until the doctor decides.

    Args:
        summary_for_doctor: Concise clinical summary combining symptoms, imaging and lab findings.
        suggested_next_step: What the doctor may want to consider. This is a suggestion only.
    """
    state = tool_context.state
    pathway = get_pathway(state.get("pathway_id", ""))
    if pathway is None:
        return _error("No pathway on this case.")
    missing = []
    if pathway.requires_imaging and not state.get("imaging_findings"):
        missing.append("imaging findings (call imaging_agent)")
    if not state.get("lab_findings"):
        missing.append("lab findings (call lab_agent)")
    if missing:
        return _error("Cannot submit yet. Missing: " + "; ".join(missing))

    state["doctor_summary"] = {
        "summary": summary_for_doctor,
        "suggested_next_step": suggested_next_step,
        "ts": _now(),
    }
    state["current_step"] = CareStep.AWAITING_DOCTOR_REVIEW
    state["pending_signals"] = ["doctor_decision"]
    audit(state, "agent", "Submitted case for doctor review")
    return {
        "status": "success",
        "message": "Sent to the doctor. The case is paused until the doctor's decision arrives.",
    }


# ---------------------------------------------------------------- AFTER DOCTOR APPROVAL

ESCALATION_PATIENT_MESSAGE = (
    "Thank you for letting us know. Your care team has been alerted and a doctor will "
    "contact you shortly about what to do next. If you feel very unwell, have trouble "
    "breathing or cough up blood, go to the nearest hospital or call 108."
)

def start_treatment_and_notify(patient_message: str, tool_context: ToolContext) -> dict:
    """Sends the doctor-approved plan to the patient and starts the follow-up phase.

    Args:
        patient_message: Plain, kind message to the patient that communicates ONLY
            what the doctor's notes say. No new medical advice.
    """
    state = tool_context.state
    decision = state.get("doctor_decision", {})
    if decision.get("decision") != "approve":
        return _error("No doctor approval on record. Patient messaging is blocked.")
    _send_patient_message(state, patient_message, kind="doctor_approved_plan")
    state["current_step"] = CareStep.TREATMENT_ACTIVE
    state["pending_signals"] = ["follow_up_due"]
    audit(state, "agent", "Patient notified of doctor-approved plan; follow-up phase started")
    return {"status": "success", "message": "Patient notified. Waiting for the next check-in."}


def record_follow_up(
    outcome: str,
    summary: str,
    patient_message: str,
    tool_context: ToolContext,
) -> dict:
    """Records the assessment of a patient check-in.

    Args:
        outcome: "on_track" (keep monitoring), "escalate" (a rule was triggered, alert
            the doctor) or "completed" (the doctor's notes say the course is finished).
        summary: Short note for the doctor about this check-in.
        patient_message: Short supportive reply to the patient for on_track / completed.
            Ignored when escalating: a fixed, clinician-approved message is sent instead.
    """
    choice = outcome.lower().strip()
    if choice not in {"on_track", "escalate", "completed"}:
        return _error("outcome must be on_track, escalate or completed.")
    state = tool_context.state
    state["follow_ups"] = list(state.get("follow_ups", [])) + [
        {"ts": _now(), "check_in": state.get("latest_check_in", ""),
         "outcome": choice, "summary": summary}
    ]
    # When something may be wrong, the model must not improvise instructions (e.g. "keep
    # taking your medicines" when the medicines may be the cause). Escalations always use
    # the fixed text below; the doctor decides what the patient should do.
    if choice == "escalate":
        _send_patient_message(state, ESCALATION_PATIENT_MESSAGE, kind="escalation_notice")
    else:
        _send_patient_message(state, patient_message, kind="follow_up")

    if choice == "escalate":
        state["doctor_alerts"] = list(state.get("doctor_alerts", [])) + [
            {"ts": _now(), "summary": summary}
        ]
        state["current_step"] = CareStep.ESCALATED
        state["pending_signals"] = []
    elif choice == "completed":
        state["current_step"] = CareStep.COMPLETED
        state["pending_signals"] = []
    else:
        state["current_step"] = CareStep.TREATMENT_ACTIVE
        state["pending_signals"] = ["follow_up_due"]
    audit(state, "agent", f"Follow-up assessed: {choice}")
    return {"status": "success", "new_step": state["current_step"]}

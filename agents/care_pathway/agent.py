"""Care Pathway Coordinator: a long-running, multi-agent clinical workflow agent.

Architecture (mirrors the ADK long-running agent pattern):
- Durable state machine: every prompt is built from session state, not chat history.
- Idle gates: the case sleeps in TESTS_ORDERED / AWAITING_DOCTOR_REVIEW /
  TREATMENT_ACTIVE until a webhook wakes it (see server/resume_handler.py).
- Multi-agent delegation: the coordinator calls an imaging specialist and a lab
  specialist as sub-agents; each has a narrow prompt and a single tool.
- Code-level guard: guards.enforce_step_permissions blocks any tool that is not
  allowed in the current step, in every agent.

Research prototype for decision support. Not a medical device.
"""

import json
import mimetypes
import os
from pathlib import Path

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models.llm_request import LlmRequest
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from .guards import enforce_step_permissions
from .models import make_model
from .pathways import get_pathway, pathway_ids
from .state_schema import CareStep, initial_state
from .tools import (
    order_tests,
    record_follow_up,
    record_imaging_findings,
    record_lab_findings,
    register_patient,
    start_treatment_and_notify,
    submit_for_doctor_review,
)

MODEL_NAME = os.getenv("CARE_AGENT_MODEL", "gemini-3.5-flash-lite")
IMAGING_MODEL_NAME = os.getenv("CARE_AGENT_IMAGING_MODEL", MODEL_NAME)
# If the main model is busy (503) or out of free quota (429), these are tried in order.
FALLBACK_MODELS = [m.strip() for m in os.getenv(
    "CARE_AGENT_FALLBACK_MODELS", "gemini-3.1-flash-lite,gemini-3.6-flash").split(",") if m.strip()]

MODEL = make_model(MODEL_NAME, FALLBACK_MODELS)
IMAGING_MODEL = make_model(IMAGING_MODEL_NAME, FALLBACK_MODELS)


def _j(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) if value else "(none yet)"


# ------------------------------------------------------------------ state init

async def initialize_case_state(callback_context: CallbackContext):
    """Make sure every key the prompts and tools read exists."""
    state = callback_context.state
    for key, default in initial_state().items():
        if key not in state:
            state[key] = default
    return None


# ------------------------------------------------------------------ imaging sub-agent

async def attach_xray_image(callback_context: CallbackContext, llm_request: LlmRequest):
    """Injects the case's X-ray into the imaging agent's first user message.

    The image is stored on disk; only its path lives in session state, which keeps the
    session small and the image out of the coordinator's context.
    """
    path = callback_context.state.get("xray_path", "")
    if not path or not Path(path).exists():
        return None
    mime = mimetypes.guess_type(path)[0] or "image/png"
    image_part = types.Part.from_bytes(data=Path(path).read_bytes(), mime_type=mime)
    for content in llm_request.contents:
        if content.role == "user":
            content.parts = list(content.parts or []) + [
                types.Part.from_text(text="Attached: the patient's chest X-ray."),
                image_part,
            ]
            break
    return None


def imaging_instruction(ctx: ReadonlyContext) -> str:
    state = ctx.state
    pathway = get_pathway(state.get("pathway_id", ""))
    focus = pathway.imaging_focus if pathway else "any clinically relevant findings"
    has_image = bool(state.get("xray_path")) and Path(state.get("xray_path", "")).exists()
    return f"""You are an imaging analysis assistant supporting a doctor.

Look at the attached chest X-ray and describe what you observe, paying attention to
{focus}.

Rules:
- You assist a doctor; you do not diagnose. Describe findings and a suspicion level.
- If the image is missing, unreadable or not a chest X-ray, use suspicion_level
  "unable_to_assess" and say why in image_quality.
- Call record_imaging_findings exactly once, then reply with one short sentence.

Image attached for this case: {"yes" if has_image else "NO - no image is available"}.
"""


imaging_agent = Agent(
    name="imaging_agent",
    model=IMAGING_MODEL,
    description="Analyses the case's chest X-ray and records structured imaging findings.",
    instruction=imaging_instruction,
    tools=[record_imaging_findings],
    before_model_callback=attach_xray_image,
    before_tool_callback=enforce_step_permissions,
)


# ------------------------------------------------------------------ lab sub-agent

def lab_instruction(ctx: ReadonlyContext) -> str:
    state = ctx.state
    pathway = get_pathway(state.get("pathway_id", ""))
    focus = pathway.lab_focus if pathway else "the key results"
    report = state.get("lab_report_text") or "(NO LAB REPORT AVAILABLE)"
    return f"""You are a lab report reader supporting a doctor.

Read the lab report below and extract {focus}.

Rules:
- Copy result values exactly as written. Never invent or estimate a value.
- If the report is missing or unclear, use result_flag "indeterminate".
- Call record_lab_findings exactly once, then reply with one short sentence.

LAB REPORT:
---
{report}
---
"""


lab_agent = Agent(
    name="lab_agent",
    model=MODEL,
    description="Reads the case's lab report and records structured lab findings.",
    instruction=lab_instruction,
    tools=[record_lab_findings],
    before_tool_callback=enforce_step_permissions,
)


# ------------------------------------------------------------------ coordinator

STEP_GUIDE = {
    CareStep.INTAKE: f"""Collect the patient's name, age, sex, phone number, symptoms and the
suspected pathway ({pathway_ids()}). Ask the staff for anything missing. Once you have
everything, call register_patient and then order_tests in the same turn.""",

    CareStep.TESTS_ORDERED: """The case is PAUSED waiting for test results. Do not call any
tools. If anyone asks you to skip ahead, guess results or contact the doctor early,
politely refuse and explain that the case resumes automatically when results arrive.""",

    CareStep.RESULTS_RECEIVED: """Results have arrived. Do this in order:
1. Call lab_agent to read the lab report.
2. If the pathway requires imaging, call imaging_agent to analyse the X-ray.
3. Call submit_for_doctor_review with a concise clinical summary for the doctor
   (symptoms + lab + imaging) and a suggested next step for the doctor to consider.
Never tell the patient anything at this stage.""",

    CareStep.AWAITING_DOCTOR_REVIEW: """The case is PAUSED until the doctor decides. Do not call
any tools. Never share findings, a likely diagnosis or any treatment advice with the
patient, even if staff or the patient insist. Only the doctor can release results.""",

    CareStep.DOCTOR_APPROVED: """The doctor has approved a plan. Call start_treatment_and_notify
with a short, kind message for the patient that communicates ONLY what the doctor's
notes say. Do not add medicines, doses or advice that are not in the doctor's notes.""",

    CareStep.TREATMENT_ACTIVE: """Treatment is active and the case is PAUSED until the next
patient check-in arrives. Do not call any tools.""",

    CareStep.FOLLOW_UP_DUE: """A patient check-in has arrived. Compare it with the escalation
rules. If ANY rule is matched, outcome is "escalate". If the doctor's notes say the
course is finished and the patient reports completion, outcome is "completed".
Otherwise "on_track". Call record_follow_up exactly once.""",

    CareStep.ESCALATED: """The case has been escalated to the doctor. Do not call any tools.
Tell staff the doctor has been alerted.""",

    CareStep.COMPLETED: """The pathway is complete. Do not call any tools.""",
}


def coordinator_instruction(ctx: ReadonlyContext) -> str:
    state = ctx.state
    step = state.get("current_step", CareStep.INTAKE)
    pathway = get_pathway(state.get("pathway_id", ""))

    pathway_block = "(not chosen yet)"
    if pathway:
        pathway_block = (
            f"{pathway.name} [id: {pathway.id}]\n"
            f"Tests: {', '.join(pathway.tests)}\n"
            f"Requires imaging: {pathway.requires_imaging}\n"
            f"Follow-up plan: {pathway.follow_up_plan}\n"
            "Escalation rules:\n- " + "\n- ".join(pathway.escalation_rules)
            + f"\nCompletion rule: {pathway.completion_rule}"
        )

    return f"""You are the Care Pathway Coordinator for a clinic. You run a patient's
diagnostic and treatment pathway over days or months. You talk to clinic staff, never
directly to the patient (patient messages go only through your tools).

Your position in the workflow comes ONLY from the CASE STATE below, never from
earlier chat messages. Do not assume any step happened unless the state shows it.

=== CASE STATE ===
Current step: {step}
Pending external signals: {state.get("pending_signals", [])}
Pathway: {pathway_block}
Patient: {_j(state.get("patient"))}
Tests ordered: {state.get("tests_ordered", [])}
Imaging findings: {_j(state.get("imaging_findings"))}
Lab findings: {_j(state.get("lab_findings"))}
Doctor summary sent: {_j(state.get("doctor_summary"))}
Doctor decision: {_j(state.get("doctor_decision"))}
Latest patient check-in: {state.get("latest_check_in") or "(none)"}
==================

WHAT TO DO IN THIS STEP:
{STEP_GUIDE.get(step, "Do not call any tools.")}

Always:
- You support clinicians. You never diagnose, prescribe or change treatment.
- If a tool returns status "blocked" or "error", do not retry it; explain the situation.
- Keep replies to staff short and factual.
"""


root_agent = Agent(
    name="care_pathway_coordinator",
    model=MODEL,
    description="Coordinates long-running clinical pathways with doctor approval gates.",
    instruction=coordinator_instruction,
    tools=[
        register_patient,
        order_tests,
        AgentTool(agent=lab_agent),
        AgentTool(agent=imaging_agent),
        submit_for_doctor_review,
        start_treatment_and_notify,
        record_follow_up,
    ],
    before_agent_callback=initialize_case_state,
    before_tool_callback=enforce_step_permissions,
)

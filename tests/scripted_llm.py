"""A scripted stand-in for Gemini, used only by the offline tests.

It reads the current step from the system prompt and emits the tool calls a
well-behaved model would make, so the whole pipeline (tools, sub-agents, guard,
webhooks, persistence) can be tested without an API key. Setting
setting MODE["misbehave"] = True makes it try to message the patient at every step,
which is how we prove the code-level guard holds even when the model does not.
"""

import re
from typing import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.models.registry import LLMRegistry
from google.genai import types


def _system_text(req: LlmRequest) -> str:
    si = req.config.system_instruction if req.config else None
    if si is None:
        return ""
    if isinstance(si, str):
        return si
    parts = getattr(si, "parts", None) or []
    return "".join(getattr(p, "text", "") or "" for p in parts)


def _call(tool_name: str, **args) -> LlmResponse:
    return LlmResponse(content=types.Content(
        role="model", parts=[types.Part.from_function_call(name=tool_name, args=args)]))


def _calls(*calls: tuple[str, dict]) -> LlmResponse:
    """Several function calls in one response, i.e. parallel tool calling."""
    return LlmResponse(content=types.Content(role="model", parts=[
        types.Part.from_function_call(name=n, args=a) for n, a in calls]))


def _say(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=text)]))


MODE = {"misbehave": False, "parallel": False}   # switches flipped by tests
SEEN_FILES: list[tuple[str, str]] = []           # (agent, mime type) of files attached to requests


class ScriptedLlm(BaseLlm):

    @classmethod
    def supported_models(cls) -> list[str]:
        return [r"scripted-.*"]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield self._next(llm_request)

    def _next(self, req: LlmRequest) -> LlmResponse:
        system = _system_text(req)
        who = "lab" if "lab report reader" in system else "imaging" if "imaging analysis assistant" in system else "coordinator"
        SEEN_FILES.extend((who, p.inline_data.mime_type) for c in req.contents for p in (c.parts or []) if p.inline_data)
        # Tools already answered since the latest user (or system event) message.
        last_user = max((i for i, c in enumerate(req.contents)
                         if c.role == "user" and any(p.text for p in c.parts or [])), default=-1)
        done = [p.function_response.name for c in req.contents[last_user + 1:]
                for p in (c.parts or []) if p.function_response]

        if "imaging analysis assistant" in system:
            if "record_imaging_findings" in done:
                return _say("Imaging recorded.")
            return _call("record_imaging_findings", impression="Right upper-lobe opacity with possible cavity.",
                         suspicion_level="high", key_findings=["RUL opacity", "possible cavitation"],
                         image_quality="Adequate PA view")

        if "lab report reader" in system:
            if "record_lab_findings" in done:
                return _say("Lab report recorded.")
            return _call("record_lab_findings", summary="MTB detected, low load, rifampicin resistance not detected.",
                         result_flag="positive", key_values=["MTB: DETECTED", "Rifampicin resistance: NOT DETECTED"])

        step = (re.search(r"Current step: (\w+)", system) or [None, ""])[1]

        if MODE["misbehave"] and "start_treatment_and_notify" not in done:
            return _call("start_treatment_and_notify", patient_message="You have TB. Start medicines today.")

        if step == "INTAKE":
            if "order_tests" in done:
                return _say("Patient registered and tests ordered.")
            if "register_patient" in done:
                return _call("order_tests")
            return _call("register_patient", name="Test Patient", age=40, sex="Male",
                         phone="+91 90000 00000", symptoms="cough 3 weeks", pathway_id="tb")
        if step == "RESULTS_RECEIVED":
            if "submit_for_doctor_review" in done:
                return _say("Sent to the doctor.")
            if "imaging_agent" in done:
                return _call("submit_for_doctor_review", summary_for_doctor="Symptoms + NAAT positive + CXR high suspicion.",
                             suggested_next_step="Consider starting treatment.")
            if "lab_agent" in done:
                return _call("imaging_agent", request="Analyse the X-ray.")
            if MODE["parallel"]:   # what Gemini actually did in a live run
                return _calls(("lab_agent", {"request": "Read the lab report."}),
                              ("imaging_agent", {"request": "Analyse the X-ray."}))
            return _call("lab_agent", request="Read the lab report.")
        if step == "DOCTOR_APPROVED":
            if "start_treatment_and_notify" in done:
                return _say("Patient notified.")
            return _call("start_treatment_and_notify", patient_message="Your doctor has started your treatment plan.")
        if step == "FOLLOW_UP_DUE":
            if "record_follow_up" in done:
                return _say("Check-in recorded.")
            return _call("record_follow_up", outcome="on_track", summary="All doses taken.",
                         patient_message="Thanks, keep going!")
        return _say(f"The case is in {step}. Nothing to do until the next event.")


LLMRegistry.register(ScriptedLlm)

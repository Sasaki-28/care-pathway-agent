"""Wakes a sleeping case when an external event arrives.

A webhook never "pokes" a running process. The case is just rows in the session
database. On a webhook we:
  1. load the persisted session and check it is really waiting for this signal;
  2. apply the state transition atomically via run_async(state_delta=...);
  3. let the agent reason from the new step (its prompt is rebuilt from state).
The server can be stopped or scaled to zero between steps without losing anything.
"""

import logging
import uuid
from typing import Any

from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService
from google.genai import types

from care_pathway.state_schema import SIGNAL_EXPECTED_STEP
from care_pathway.tools import audit_entry_delta
from server import notify

logger = logging.getLogger("care_pathway.resume")


class SignalRejected(Exception):
    """The case is not waiting for this signal (e.g. a duplicate or out-of-order webhook)."""


class CaseRunner:
    def __init__(self, runner: Runner, session_service: BaseSessionService,
                 app_name: str, user_id: str):
        self.runner = runner
        self.sessions = session_service
        self.app_name = app_name
        self.user_id = user_id

    async def get(self, session_id: str):
        return await self.sessions.get_session(
            app_name=self.app_name, user_id=self.user_id, session_id=session_id
        )

    async def run(self, session_id: str, message: str,
                  state_delta: dict[str, Any] | None = None) -> dict:
        """Run one agent turn. Returns the agent's replies and the tools it called."""
        replies, tool_calls = [], []
        error = None
        try:
            async for event in self.runner.run_async(
                user_id=self.user_id,
                session_id=session_id,
                new_message=types.Content(role="user", parts=[types.Part.from_text(text=message)]),
                state_delta=state_delta,
            ):
                for call in event.get_function_calls() or []:
                    tool_calls.append(call.name)
                if event.content and event.content.parts and event.author != "user":
                    text = "".join(p.text or "" for p in event.content.parts if p.text)
                    if text.strip():
                        replies.append({"author": event.author, "text": text.strip()})
        except Exception as exc:  # surface model/API errors to the UI instead of a 500
            logger.exception("Agent run failed for %s", session_id)
            error = f"{type(exc).__name__}: {exc}"
        emails = await self.deliver_emails(session_id)
        return {"replies": replies, "tool_calls": tool_calls, "error": error, "emails": emails}

    async def set_state(self, session_id: str, delta: dict[str, Any]) -> None:
        """Write a state change without running the agent (system bookkeeping)."""
        session = await self.get(session_id)
        await self.sessions.append_event(session, Event(
            author="system", invocation_id=f"sys-{uuid.uuid4().hex[:10]}",
            actions=EventActions(state_delta=delta)))

    async def deliver_emails(self, session_id: str) -> list[dict]:
        """Email every patient message not emailed yet. Failures are logged, never raised."""
        try:
            session = await self.get(session_id)
            st = session.state
            inbox = st.get("patient_inbox", [])
            done = st.get("emailed_count", 0)
            if done >= len(inbox):
                return []
            to = (st.get("contact") or {}).get("email", "")
            results, audit_state = [], dict(st)
            delta: dict[str, Any] = {"emailed_count": len(inbox)}
            for msg in inbox[done:]:
                if not to or not notify.configured():
                    continue   # no address or email not set up: the patient page still shows every message
                first = ((st.get("patient") or {}).get("name") or "").split(" ")[0]
                subject, text, page = notify.build_email(
                    first, msg, notify.portal_link(session_id, st.get("patient_token", "")))
                ok, detail = await notify.send_email(to, subject, text, page)
                results.append({"subject": subject, "ok": ok, "detail": detail})
                note = f"Emailed patient: {subject}" if ok else f"Email not sent ({subject}): {detail}"
                entry = audit_entry_delta(audit_state, "email", note)
                audit_state.update(entry)
                delta.update(entry)
            await self.set_state(session_id, delta)
            return results
        except Exception:  # noqa: BLE001  (email must never break a case)
            logger.exception("Email delivery failed for %s", session_id)
            return [{"subject": "", "ok": False, "detail": "email step failed; see server log"}]

    async def resume(self, session_id: str, signal: str,
                     state_delta: dict[str, Any], message: str) -> dict:
        session = await self.get(session_id)
        if session is None:
            raise SignalRejected(f"No case with id {session_id}")

        expected_step = SIGNAL_EXPECTED_STEP.get(signal)
        step = session.state.get("current_step")
        if step != expected_step:
            raise SignalRejected(
                f"Case is in {step}; '{signal}' is only accepted in {expected_step}."
            )

        # pending_signals is cleared by default; a state_delta may re-arm a gate.
        delta = {
            "pending_signals": [],
            **state_delta,
            **audit_entry_delta(session.state, "webhook",
                                f"Signal '{signal}' received; case woken from {step}"),
        }
        logger.info("Resuming %s on signal %s -> %s", session_id, signal, delta.get("current_step"))
        return await self.run(session_id, message, state_delta=delta)

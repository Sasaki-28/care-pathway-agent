"""Emails to the patient, sent through Resend's web API.

Only messages that are already in the patient's inbox are emailed. Those come from tools
the safety guard allows (test instructions, the doctor-approved plan, check-in replies,
the fixed escalation notice), so the doctor-approval gate covers email too. The email
links to the patient's private portal page.

Settings (.env / Render):
    RESEND_API_KEY   re_... key from resend.com (no key = emails are skipped and logged)
    EMAIL_FROM       default "Care Pathway <onboarding@resend.dev>". Resend's test sender
                     only delivers to the address you signed up with; verify a domain to
                     send to anyone.
    PUBLIC_BASE_URL  the site's address for links, e.g. https://care-pathway-agent.onrender.com
"""

import html
import logging
import os

import httpx

logger = logging.getLogger("care_pathway.email")

SUBJECTS = {
    "instructions": "Your tests have been requested",
    "doctor_approved_plan": "A message from your doctor",
    "follow_up": "Thanks for your check-in",
    "escalation_notice": "Your care team has been alerted",
}


def configured() -> bool:
    return bool(os.getenv("RESEND_API_KEY"))


def portal_link(session_id: str, token: str) -> str:
    base = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    return f"{base}/patient/{session_id}?t={token}"


def build_email(first_name: str, message: dict, link: str) -> tuple[str, str, str]:
    """Returns (subject, plain text, html)."""
    subject = SUBJECTS.get(message.get("kind", ""), "An update from your clinic")
    greeting = f"Dear {first_name}," if first_name else "Hello,"
    body = message.get("text", "")
    text = (f"{greeting}\n\n{body}\n\nSee all your messages and check in here:\n{link}\n\n"
            "City Clinic care team\n\nThis is a research prototype. In an emergency, go to the "
            "nearest hospital or call 108.")
    page = f"""<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;color:#1f2a30">
  <p style="font-size:13px;color:#56636a;margin:0 0 16px">City Clinic</p>
  <p>{html.escape(greeting)}</p>
  <p style="font-size:16px;line-height:1.5;background:#f4f7f5;border-radius:8px;padding:14px 16px">{html.escape(body)}</p>
  <p><a href="{html.escape(link)}" style="display:inline-block;background:#1f2a30;color:#fff;text-decoration:none;padding:10px 18px;border-radius:6px">Open your care page</a></p>
  <p style="font-size:12px;color:#85918f">City Clinic care team. Research prototype, not a medical service.
  In an emergency, go to the nearest hospital or call 108.</p>
</div>"""
    return subject, text, page


async def send_email(to: str, subject: str, text: str, page: str) -> tuple[bool, str]:
    """Sends one email. Never raises: returns (ok, detail) so a failed email cannot break a case."""
    key = os.getenv("RESEND_API_KEY", "")
    if not key:
        return False, "email is not set up (no RESEND_API_KEY)"
    sender = os.getenv("EMAIL_FROM", "Care Pathway <onboarding@resend.dev>")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {key}"},
                json={"from": sender, "to": [to], "subject": subject, "text": text, "html": page},
            )
        if resp.status_code >= 300:
            detail = resp.text[:200]
            if resp.status_code == 403:
                detail = ("Resend refused the recipient. Its test sender only delivers to the email "
                          "you signed up with; use that address or verify a domain.")
            return False, f"HTTP {resp.status_code}: {detail}"
        return True, "sent"
    except httpx.HTTPError as exc:
        return False, f"{type(exc).__name__}: {exc}"

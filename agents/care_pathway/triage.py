"""Triage: suggest which pathway fits the presenting complaint.

Only a suggestion: clinic staff see it with its reason and choose the pathway
themselves. Gemini is asked first (with the same fallback models as the agents); if no
model is available, a simple keyword match over each pathway's triage_keywords is used,
and the answer says so.
"""

import json
import logging
import os

from google import genai
from google.genai import types

from .pathways import PATHWAYS

logger = logging.getLogger("care_pathway.triage")


def _models() -> list[str]:
    main = os.getenv("CARE_AGENT_MODEL", "gemini-3.5-flash-lite")
    backups = [m.strip() for m in os.getenv(
        "CARE_AGENT_FALLBACK_MODELS", "gemini-3.1-flash-lite,gemini-3.6-flash").split(",") if m.strip()]
    return [m for m in [main, *backups] if m.startswith("gemini")]


def suggest_by_keywords(symptoms: str) -> dict:
    text = (symptoms or "").lower()
    scores = {pid: sum(1 for k in p.triage_keywords if k.lower() in text) for pid, p in PATHWAYS.items()}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return {"pathway_id": "", "confidence": "low", "source": "rules",
                "reason": "No pathway's typical symptoms were found. Choose the pathway yourself."}
    matched = [k for k in PATHWAYS[best].triage_keywords if k.lower() in text]
    ranked = sorted(scores.values(), reverse=True)
    confidence = "medium" if ranked[0] - (ranked[1] if len(ranked) > 1 else 0) >= 2 else "low"
    return {"pathway_id": best, "confidence": confidence, "source": "rules",
            "reason": "Matched typical symptoms: " + ", ".join(matched[:5]) + "."}


def _prompt(symptoms: str) -> str:
    options = "\n".join(
        f"- {p.id}: {p.name}. Tests: {', '.join(p.tests)}." for p in PATHWAYS.values())
    return (
        "You help clinic staff choose which care pathway to start for a new patient. You do not "
        "diagnose. Pick the single best-fitting pathway from the list for the presenting complaint, "
        "or pathway_id \"none\" if none fits. Give a one-sentence reason that quotes the symptoms "
        "you relied on, for the staff member to check.\n\n"
        f"Pathways:\n{options}\n\nPresenting complaint: {symptoms}"
    )


async def suggest_pathway(symptoms: str) -> dict:
    if not symptoms.strip():
        return {"pathway_id": "", "confidence": "low", "source": "rules", "reason": "Enter the symptoms first."}
    models = _models()
    if not models or not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")):
        return suggest_by_keywords(symptoms)
    schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "pathway_id": types.Schema(type=types.Type.STRING, enum=[*PATHWAYS, "none"]),  # "none", not "": Gemini rejects empty enum values
            "confidence": types.Schema(type=types.Type.STRING, enum=["high", "medium", "low"]),
            "reason": types.Schema(type=types.Type.STRING),
        },
        required=["pathway_id", "confidence", "reason"],
    )
    client = genai.Client()
    for model in models:
        try:
            resp = await client.aio.models.generate_content(
                model=model, contents=_prompt(symptoms),
                config=types.GenerateContentConfig(response_mime_type="application/json",
                                                   response_schema=schema, temperature=0),
            )
            data = json.loads(resp.text)
            if data.get("pathway_id") == "none":
                data["pathway_id"] = ""
            if data.get("pathway_id", "") not in (*PATHWAYS, ""):
                raise ValueError(f"unknown pathway {data.get('pathway_id')!r}")
            return {"pathway_id": data["pathway_id"], "confidence": data.get("confidence", "low"),
                    "reason": data.get("reason", ""), "source": model}
        except Exception as exc:  # noqa: BLE001  (busy model, quota, bad JSON: try the next one)
            logger.warning("Triage with %s failed: %s", model, exc)
    result = suggest_by_keywords(symptoms)
    result["reason"] = "AI unavailable, simple keyword match used. " + result["reason"]
    return result

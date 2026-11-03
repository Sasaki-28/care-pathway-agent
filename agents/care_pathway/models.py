"""Gemini with automatic fallback to other models.

The free tier often answers 503 ("model is experiencing high demand") or 429 ("quota
exceeded"). Each Gemini model has its own capacity and its own free quota, so instead of
failing the turn we try the next model in the list.

Configured from .env:
    CARE_AGENT_MODEL=gemini-3.5-flash-lite
    CARE_AGENT_FALLBACK_MODELS=gemini-3.1-flash-lite,gemini-3.6-flash
"""

import logging
from typing import AsyncGenerator

from google.adk.models import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import errors, types
from pydantic import PrivateAttr

logger = logging.getLogger("care_pathway.models")

# Status codes that mean "busy or out of quota, another model may work".
FALLBACK_CODES = {429, 500, 503, 504}

# A model that has fallbacks gives up quickly; the last model in the list retries longer.
QUICK_RETRY = types.HttpRetryOptions(attempts=2, initial_delay=1.0, max_delay=4.0,
                                     http_status_codes=[500, 503, 504])
PATIENT_RETRY = types.HttpRetryOptions(attempts=5, initial_delay=2.0, max_delay=30.0,
                                       http_status_codes=[429, 500, 503, 504])


class FallbackGemini(Gemini):
    """Tries `model` first, then each model in `fallbacks`, on busy/quota errors."""

    fallbacks: list[str] = []
    _backups: dict[str, Gemini] = PrivateAttr(default_factory=dict)

    def _backup(self, name: str, last: bool) -> Gemini:
        if name not in self._backups:
            self._backups[name] = Gemini(model=name, retry_options=PATIENT_RETRY if last else QUICK_RETRY)
        return self._backups[name]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        names = [self.model] + [m for m in self.fallbacks if m != self.model]
        last_error: Exception | None = None
        for i, name in enumerate(names):
            request = llm_request.model_copy(update={"model": name})
            source = (super().generate_content_async(request, stream) if i == 0
                      else self._backup(name, last=i == len(names) - 1).generate_content_async(request, stream))
            started = False
            try:
                async for response in source:
                    started = True
                    yield response
                if i:
                    logger.warning("Answered by fallback model %s", name)
                return
            except errors.APIError as exc:
                # Only switch models if nothing was sent yet and the error is busy/quota.
                if started or getattr(exc, "code", None) not in FALLBACK_CODES or i == len(names) - 1:
                    raise
                logger.warning("Model %s unavailable (%s), trying %s", name, exc.code, names[i + 1])
                last_error = exc
        if last_error:
            raise last_error


def make_model(name: str, fallbacks: list[str]):
    """A Gemini model with fallbacks, or the name itself for non-Gemini (test) models."""
    if not name.startswith("gemini"):
        return name
    fallbacks = [f for f in fallbacks if f.startswith("gemini")]
    return FallbackGemini(model=name, fallbacks=fallbacks,
                          retry_options=QUICK_RETRY if fallbacks else PATIENT_RETRY)

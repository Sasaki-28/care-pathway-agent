"""The model wrapper switches to a fallback model when Gemini is busy or out of quota.

Google is never called: Gemini.generate_content_async is replaced by a fake.
"""

import pytest
from google.adk.models import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import errors, types

from care_pathway.models import FallbackGemini, make_model


def _err(code, status):
    cls = errors.ServerError if code >= 500 else errors.ClientError
    return cls(code, {"error": {"code": code, "message": status, "status": status}})


def fake_gemini(behaviour, calls):
    """behaviour: model name -> exception to raise, or None to answer."""
    async def fake(self, llm_request, stream=False):
        calls.append(llm_request.model)
        exc = behaviour.get(llm_request.model)
        if exc:
            raise exc
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=f"hi from {llm_request.model}")]))
    return fake


async def _answer(model):
    req = LlmRequest(model=model.model, contents=[types.Content(role="user", parts=[types.Part.from_text(text="hi")])])
    return [r async for r in model.generate_content_async(req)]


@pytest.mark.parametrize("code,status", [(503, "UNAVAILABLE"), (429, "RESOURCE_EXHAUSTED")])
async def test_busy_primary_falls_back(monkeypatch, code, status):
    calls = []
    monkeypatch.setattr(Gemini, "generate_content_async",
                        fake_gemini({"gemini-a": _err(code, status)}, calls))
    model = make_model("gemini-a", ["gemini-b", "gemini-c"])
    out = await _answer(model)
    assert calls == ["gemini-a", "gemini-b"]
    assert out[0].content.parts[0].text == "hi from gemini-b"


async def test_tries_every_fallback_then_raises(monkeypatch):
    calls = []
    busy = _err(503, "UNAVAILABLE")
    monkeypatch.setattr(Gemini, "generate_content_async",
                        fake_gemini({"gemini-a": busy, "gemini-b": busy, "gemini-c": busy}, calls))
    with pytest.raises(errors.ServerError):
        await _answer(make_model("gemini-a", ["gemini-b", "gemini-c"]))
    assert calls == ["gemini-a", "gemini-b", "gemini-c"]


async def test_real_errors_do_not_fall_back(monkeypatch):
    calls = []
    monkeypatch.setattr(Gemini, "generate_content_async",
                        fake_gemini({"gemini-a": _err(400, "INVALID_ARGUMENT")}, calls))
    with pytest.raises(errors.ClientError):
        await _answer(make_model("gemini-a", ["gemini-b"]))
    assert calls == ["gemini-a"]


def test_config():
    m = make_model("gemini-a", ["gemini-b", "not-gemini"])
    assert isinstance(m, FallbackGemini) and m.fallbacks == ["gemini-b"]
    assert make_model("scripted-model", ["gemini-b"]) == "scripted-model"

"""Single LLM entry point for both agents.

Everything that talks to Gemini goes through `call_json`. That gives one place to
enforce the three things the POC depends on: deterministic settings, JSON that is
actually parseable, and a loud, typed failure when the model is unavailable —
never a silent fallback to a guess.

It also walks a pool of models. Free-tier quota is per model, and the `*-latest`
aliases are capped at roughly 20 requests a day — a live demo that relies on one
model dies at request 21. A quota hit (429) moves to the next model; so does a
retired model name (404), which is what breaks a fresh clone months later. The
alias sits last in the pool so the app always has something current to fall to.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Pinned models first — each carries its own free-tier quota — the auto-tracking
# alias last. Override the whole pool with GEMINI_MODELS, or put one model at the
# front with GEMINI_MODEL.
DEFAULT_POOL = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-flash-latest",
]

# A model that just returned 429 is skipped for this long, so every call after a
# quota hit does not pay for the same refusal again.
QUOTA_COOLDOWN_SECONDS = 60

_SLEEP = time.sleep  # replaced in tests so retries do not actually wait
_cooldown_until: dict[str, float] = {}


def model_pool() -> list[str]:
    """The models to try, in order. Read from the environment on every call."""
    override = [m.strip() for m in (os.getenv("GEMINI_MODELS") or "").split(",") if m.strip()]
    if override:
        return list(dict.fromkeys(override))
    primary = (os.getenv("GEMINI_MODEL") or "").strip()
    return list(dict.fromkeys(([primary] if primary else []) + DEFAULT_POOL))


# The model a call starts with — what the UI shows as "LIVE · model …".
DEFAULT_MODEL = model_pool()[0]


class LLMUnavailable(RuntimeError):
    """Raised when the model cannot be reached or returned unusable output.

    Callers surface this to the user. They never substitute a made-up answer.
    """


@dataclass
class LLMResult:
    data: dict[str, Any]
    raw_text: str
    model: str
    latency_ms: int
    attempts: int = 1
    usage: dict[str, Any] = field(default_factory=dict)


def api_key() -> str | None:
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    return key or None


def demo_mode() -> bool:
    """True when we must serve pre-recorded output instead of a live model call."""
    if (os.getenv("FORCE_DEMO_MODE") or "").strip().lower() in {"1", "true", "yes"}:
        return True
    return api_key() is None


_client = None


def _get_client():
    global _client
    if _client is None:
        key = api_key()
        if not key:
            raise LLMUnavailable("No GEMINI_API_KEY found. Add one to .env or run in demo mode.")
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - environment problem, not logic
            raise LLMUnavailable(
                "google-genai is not installed. Run: pip install -r requirements.txt"
            ) from exc
        _client = genai.Client(api_key=key)
    return _client


def _extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a model response.

    Gemini in JSON mode is well behaved, but a fenced block or a stray preamble
    still shows up occasionally. We recover from that; we do not recover from
    genuinely malformed JSON, which is a real failure and should be reported.
    """
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise LLMUnavailable(f"Model did not return valid JSON. First 400 chars: {text[:400]}")


def _error_kind(exc: Exception) -> str:
    """Sort a failed call into what to do next.

    quota    429 / RESOURCE_EXHAUSTED — this model is spent; try the next one.
    retired  404 / NOT_FOUND — the name no longer exists; try the next one.
    fatal    any other 4xx (bad key, bad request) — another model will not help.
    transient everything else (5xx, network) — retry the same model, then move on.
    """
    code = getattr(exc, "code", None)
    text = str(exc)
    if code == 429 or "RESOURCE_EXHAUSTED" in text:
        return "quota"
    if code == 404 or "NOT_FOUND" in text:
        return "retired"
    if isinstance(code, int) and 400 <= code < 500:
        return "fatal"
    return "transient"


def _ordered_pool(model: str | None) -> list[str]:
    """The pool with models still cooling down after a quota hit moved to the back."""
    pool = [model] if model else model_pool()
    now = time.time()
    ready = [m for m in pool if _cooldown_until.get(m, 0) <= now]
    cooling = [m for m in pool if _cooldown_until.get(m, 0) > now]
    return ready + cooling


def call_json(
    prompt: str,
    *,
    system: str | None = None,
    file_bytes: bytes | None = None,
    mime_type: str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    max_attempts: int = 3,
) -> LLMResult:
    """Call Gemini and return parsed JSON, walking the model pool on quota errors.

    `file_bytes` + `mime_type` attaches a PDF or image inline — that is how the
    vision agent reads documents. PDFs go to the model directly, so there is no
    poppler/ImageMagick dependency to break on a fresh machine.

    `max_attempts` is per model and applies to transient errors only.
    """
    from google.genai import types

    client = _get_client()

    parts: list[Any] = []
    if file_bytes is not None:
        if not mime_type:
            raise ValueError("mime_type is required when passing file_bytes")
        parts.append(types.Part.from_bytes(data=file_bytes, mime_type=mime_type))
    parts.append(types.Part.from_text(text=prompt))

    config = types.GenerateContentConfig(
        temperature=temperature,
        response_mime_type="application/json",
        system_instruction=system,
    )

    started = time.time()
    last_error: Exception | None = None
    tried: list[str] = []
    calls = 0
    for model_name in _ordered_pool(model):
        for attempt in range(1, max_attempts + 1):
            calls += 1
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=[types.Content(role="user", parts=parts)],
                    config=config,
                )
                text = response.text or ""
                data = _extract_json(text)
                usage = {}
                meta = getattr(response, "usage_metadata", None)
                if meta is not None:
                    usage = {
                        "input_tokens": getattr(meta, "prompt_token_count", None),
                        "output_tokens": getattr(meta, "candidates_token_count", None),
                    }
                return LLMResult(
                    data=data,
                    raw_text=text,
                    model=model_name,
                    latency_ms=int((time.time() - started) * 1000),
                    attempts=calls,
                    usage=usage,
                )
            except LLMUnavailable:
                raise
            except Exception as exc:
                last_error = exc
                kind = _error_kind(exc)
                if kind == "fatal":
                    raise LLMUnavailable(f"Gemini rejected the call ({model_name}): {exc}") from exc
                if kind == "quota":
                    _cooldown_until[model_name] = time.time() + QUOTA_COOLDOWN_SECONDS
                if kind in ("quota", "retired"):
                    tried.append(f"{model_name} ({kind})")
                    break
                if attempt < max_attempts:
                    _SLEEP(1.5 * attempt)
                else:
                    tried.append(f"{model_name} (failed {max_attempts}x)")

    raise LLMUnavailable(
        f"Every Gemini model failed: {', '.join(tried)}. Last error: {last_error}. "
        "Wait for the quota to reset, list more models in GEMINI_MODELS, "
        "or set FORCE_DEMO_MODE=true."
    )

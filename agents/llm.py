"""Single LLM entry point for both agents.

Everything that talks to Gemini goes through `call_json`. That gives one place to
enforce the three things the POC depends on: deterministic settings, JSON that is
actually parseable, and a loud, typed failure when the model is unavailable —
never a silent fallback to a guess.
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

# gemini-flash-latest auto-tracks the current stable Flash. Pinned model names
# (e.g. gemini-2.5-flash) get retired for new API keys, which breaks a fresh clone.
DEFAULT_MODEL = os.getenv("GEMINI_MODEL") or "gemini-flash-latest"


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
    """Call Gemini and return parsed JSON, retrying only on transport-level errors.

    `file_bytes` + `mime_type` attaches a PDF or image inline — that is how the
    vision agent reads documents. PDFs go to the model directly, so there is no
    poppler/ImageMagick dependency to break on a fresh machine.
    """
    from google.genai import types

    client = _get_client()
    model_name = model or DEFAULT_MODEL

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
    for attempt in range(1, max_attempts + 1):
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
                attempts=attempt,
                usage=usage,
            )
        except LLMUnavailable:
            raise
        except Exception as exc:  # network / quota / transient server errors
            last_error = exc
            if attempt < max_attempts:
                time.sleep(1.5 * attempt)

    raise LLMUnavailable(f"Gemini call failed after {max_attempts} attempts: {last_error}")

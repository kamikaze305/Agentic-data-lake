"""The model pool: what happens when Gemini says no.

No key and no network — a fake client plays each failure the free tier actually
produces (quota, retired model, server hiccup, bad key) so the rotation rules are
checked as facts, not hoped for on demo day.

    python tests/test_llm.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents import llm  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")


class FakeAPIError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"{code} {message}")
        self.code = code


class FakeModels:
    """Plays a script per model: each entry is an exception to raise or 'ok'."""

    def __init__(self, script: dict[str, list]) -> None:
        self.script = {m: list(steps) for m, steps in script.items()}
        self.calls: list[str] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append(model)
        steps = self.script.get(model) or ["ok"]
        step = steps.pop(0) if len(steps) > 1 else steps[0]
        if isinstance(step, Exception):
            raise step
        return SimpleNamespace(text='{"answer": "%s"}' % model, usage_metadata=None)


def use_fake(script: dict[str, list]) -> FakeModels:
    models = FakeModels(script)
    llm._client = SimpleNamespace(models=models)
    llm._cooldown_until.clear()
    return models


def set_env(**values: str | None) -> None:
    for key in ("GEMINI_MODEL", "GEMINI_MODELS"):
        os.environ.pop(key, None)
    for key, value in values.items():
        if value is not None:
            os.environ[key] = value


QUOTA = FakeAPIError(429, "RESOURCE_EXHAUSTED")
RETIRED = FakeAPIError(404, "NOT_FOUND")
OVERLOADED = FakeAPIError(503, "UNAVAILABLE")
BAD_KEY = FakeAPIError(401, "UNAUTHENTICATED")


def test_pool_order() -> None:
    print("\nPool · order and overrides")
    set_env()
    check("defaults to the pinned pool", llm.model_pool() == llm.DEFAULT_POOL)
    check("the auto-tracking alias is the last resort",
          llm.model_pool()[-1] == "gemini-flash-latest")

    set_env(GEMINI_MODEL="gemini-3.8-flash")
    pool = llm.model_pool()
    check("GEMINI_MODEL goes to the front", pool[0] == "gemini-3.8-flash", pool[0])
    check("…and the rest of the pool stays behind it", pool[1:] == llm.DEFAULT_POOL)

    set_env(GEMINI_MODEL="gemini-3.5-flash")
    check("a primary already in the pool is not listed twice",
          llm.model_pool().count("gemini-3.5-flash") == 1)

    set_env(GEMINI_MODELS="a-model, b-model")
    check("GEMINI_MODELS replaces the pool", llm.model_pool() == ["a-model", "b-model"])


def test_rotation() -> None:
    print("\nRotation · what each failure does")
    set_env(GEMINI_MODELS="m1,m2,m3")

    fake = use_fake({"m1": [QUOTA]})
    result = llm.call_json("q")
    check("a quota hit moves to the next model", result.model == "m2", result.model)
    check("the quota'd model is tried once, not retried", fake.calls.count("m1") == 1)

    fake = use_fake({"m1": [RETIRED]})
    check("a retired model name moves to the next model", llm.call_json("q").model == "m2")

    fake = use_fake({"m1": [OVERLOADED, "ok"]})
    result = llm.call_json("q")
    check("a server hiccup retries the same model", result.model == "m1" and result.attempts == 2,
          f"{result.model} after {result.attempts} call(s)")

    fake = use_fake({"m1": [OVERLOADED]})
    result = llm.call_json("q", max_attempts=2)
    check("a model that keeps failing is abandoned after its retries",
          result.model == "m2" and fake.calls.count("m1") == 2, str(fake.calls))

    fake = use_fake({"m1": [BAD_KEY]})
    try:
        llm.call_json("q")
        check("a bad key fails loudly without trying other models", False)
    except llm.LLMUnavailable:
        check("a bad key fails loudly without trying other models", fake.calls == ["m1"])

    fake = use_fake({"m1": [QUOTA], "m2": [QUOTA], "m3": [QUOTA]})
    try:
        llm.call_json("q")
        check("an exhausted pool fails loudly", False)
    except llm.LLMUnavailable as exc:
        message = str(exc)
        check("an exhausted pool fails loudly, naming every model",
              all(m in message for m in ("m1", "m2", "m3")), message[:80])


def test_cooldown() -> None:
    print("\nCooldown · a spent model is not asked again straight away")
    set_env(GEMINI_MODELS="m1,m2")
    fake = use_fake({"m1": [QUOTA, "ok"]})
    llm.call_json("q")
    fake.calls.clear()
    result = llm.call_json("q")
    check("the next call starts on the model that still has quota",
          result.model == "m2" and "m1" not in fake.calls, str(fake.calls))


if __name__ == "__main__":
    os.environ["GEMINI_API_KEY"] = os.environ.get("GEMINI_API_KEY") or "test-key"
    llm._SLEEP = lambda seconds: None

    test_pool_order()
    test_rotation()
    test_cooldown()

    set_env()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("Failed: " + ", ".join(FAILED))
    sys.exit(1 if FAILED else 0)

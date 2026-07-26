"""Agent A — Agentic Analytics.

Natural-language question over the data lake. Not one LLM call: an explicit
plan -> act -> verify loop with memory.

    PLANNER   decides whether the question is answerable at all, and writes SQL.
              It may instead ask a clarifying question or declare the question
              out of scope. Those are first-class outcomes, not failures.
    EXECUTOR  runs the SQL against a read-only connection, behind a statement
              guard. SQL errors are fed back to the planner as a repair prompt.
    ANSWERER  writes prose from the returned rows and nothing else.
    VERIFIER  re-reads the answer against the rows and flags any claim the data
              does not support. If verification fails, the user is told.
    MEMORY    the last few (question, SQL) pairs, so "only ocean freight" is a
              refinement of the previous query instead of a fresh start.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from agents import db, mock
from agents.llm import LLMUnavailable, call_json, demo_mode

MAX_ROWS = 1000
MAX_REPAIR_ATTEMPTS = 2

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|reindex)\b",
    re.IGNORECASE,
)


class UnsafeQuery(ValueError):
    """The planner produced SQL that is not a read-only single statement."""


def guard_sql(sql: str) -> str:
    """Reject anything that is not a single read-only statement.

    Uploaded documents are untrusted input that ends up in the same database, so
    a document could in principle try to talk the planner into writing SQL. This
    guard plus the read-only connection means it would not matter if it did.
    """
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        raise UnsafeQuery("Planner returned an empty query.")
    if ";" in cleaned:
        raise UnsafeQuery("Multiple SQL statements are not allowed.")
    if not re.match(r"^(select|with)\b", cleaned, re.IGNORECASE):
        raise UnsafeQuery("Only SELECT / WITH queries are allowed.")
    if FORBIDDEN.search(cleaned):
        raise UnsafeQuery("Query contains a write or schema-changing keyword.")
    return cleaned


# --------------------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------------------


@dataclass
class TraceStep:
    stage: str
    status: str  # ok | retry | failed | skipped
    detail: str
    ms: int = 0


@dataclass
class AnalyticsResult:
    question: str
    status: str  # answered | needs_clarification | out_of_scope | failed
    answer: str = ""
    sql: str | None = None
    data: pd.DataFrame | None = None
    chart: dict[str, Any] | None = None
    assumptions: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    clarifying_question: str | None = None
    verification: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    trace: list[TraceStep] = field(default_factory=list)
    is_refinement: bool = False
    demo_mode: bool = False
    truncated: bool = False


# --------------------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------------------

PLANNER_SYSTEM = """You are the planning stage of an analytics agent for a logistics company.
You convert a business question into ONE read-only SQLite query, or you refuse.

Rules you must not break:
- Only SELECT or WITH. Never write, never modify schema. One statement, no semicolon.
- Only use tables and columns that appear in the provided schema. Never invent one.
- If the question is ambiguous in a way that changes the answer (undefined time
  window when it matters, an entity that could mean two columns, a metric with no
  agreed definition), set status="needs_clarification" and ask ONE short question.
- If the data lake simply cannot answer it (the data is not there), set
  status="out_of_scope" and say plainly what is missing. Do not approximate.
- Prefer readable SQL with explicit column aliases. Alias aggregates in snake_case.
- Round money to 2 decimals and rates to 1 decimal in SQL.
- Cap row-returning queries at 200 rows with LIMIT unless it is an aggregate.
- If the user message is a refinement of the previous question, start from the
  previous SQL and modify it. Set is_refinement=true when you do.

Return JSON only:
{
  "status": "ok" | "needs_clarification" | "out_of_scope",
  "clarifying_question": string | null,
  "reasoning": "one sentence on the approach",
  "is_refinement": boolean,
  "sql": string | null,
  "assumptions": [string],     // interpretation choices the user should be able to challenge
  "tables_used": [string],
  "chart": {"type": "bar"|"line"|"scatter"|"pie"|"none",
            "x": string|null, "y": string|null, "color": string|null, "title": string}
}"""

ANSWER_SYSTEM = """You write the final answer of an analytics agent.

You may only state what is present in the result rows given to you. No outside
knowledge, no estimates, no rounding beyond what is shown, no causal explanation
unless the data contains it. If the rows are empty, say the query returned no
matching records and suggest what to relax. Two to four sentences. Lead with the
number that answers the question. Do not describe the SQL.

Return JSON only: {"answer": string, "headline_numbers": [string]}"""

VERIFIER_SYSTEM = """You are the verification stage of an analytics agent. You are
adversarial: your job is to catch an answer that says more than the data shows.

Given the question, the SQL, the result rows and the drafted answer, check:
1. Every number in the answer appears in the rows (allow identical rounding).
2. Every entity named in the answer appears in the rows.
3. The answer does not assert cause, trend or comparison the rows do not contain.
4. The answer actually addresses the question asked.

Return JSON only:
{"supported": boolean, "issues": [string], "confidence": 0.0-1.0}"""


def _rows_for_prompt(df: pd.DataFrame, limit: int = 40) -> str:
    if df.empty:
        return "(0 rows returned)"
    head = df.head(limit)
    text = head.to_csv(index=False)
    if len(df) > limit:
        text += f"\n... ({len(df) - limit} more rows not shown; {len(df)} total)"
    return text


def _memory_block(memory: list[dict[str, Any]]) -> str:
    if not memory:
        return "(none — this is the first question of the session)"
    parts = []
    for turn in memory[-3:]:
        parts.append(
            f"Q: {turn['question']}\nSQL: {turn.get('sql') or '(none)'}\n"
            f"Rows returned: {turn.get('row_count', '?')}"
        )
    return "\n---\n".join(parts)


# --------------------------------------------------------------------------------------
# Stages
# --------------------------------------------------------------------------------------


def _plan(question: str, memory: list[dict[str, Any]], scope_hint: str, error: str | None = None):
    prompt = f"""Today's date: {date.today().isoformat()}

DATABASE SCHEMA
{db.schema_description()}

SCOPE THE USER SELECTED: {scope_hint}

PREVIOUS TURNS IN THIS SESSION (for refinements)
{_memory_block(memory)}

USER QUESTION
{question}"""
    if error:
        prompt += f"""

YOUR PREVIOUS ATTEMPT FAILED. Fix it.
Error: {error}
Do not repeat the same query. If the column or table you wanted does not exist in
the schema above, switch to status="out_of_scope" rather than guessing again."""
    return call_json(prompt, system=PLANNER_SYSTEM)


def _execute(sql: str) -> tuple[pd.DataFrame, bool]:
    conn = db.get_readonly_conn()
    try:
        df = pd.read_sql_query(sql, conn)
    finally:
        conn.close()
    truncated = len(df) > MAX_ROWS
    return (df.head(MAX_ROWS) if truncated else df), truncated


def _compose_answer(question: str, sql: str, df: pd.DataFrame) -> dict[str, Any]:
    prompt = f"""QUESTION
{question}

SQL THAT WAS RUN
{sql}

RESULT ROWS (CSV)
{_rows_for_prompt(df)}"""
    return call_json(prompt, system=ANSWER_SYSTEM).data


def _verify(question: str, sql: str, df: pd.DataFrame, answer: str) -> dict[str, Any]:
    prompt = f"""QUESTION
{question}

SQL
{sql}

RESULT ROWS (CSV)
{_rows_for_prompt(df)}

DRAFTED ANSWER
{answer}"""
    return call_json(prompt, system=VERIFIER_SYSTEM).data


# --------------------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------------------


def ask(
    question: str,
    memory: list[dict[str, Any]] | None = None,
    *,
    scope_hint: str = "Both shipment records and uploaded documents",
    run_verifier: bool = True,
) -> AnalyticsResult:
    memory = memory or []
    result = AnalyticsResult(question=question, status="failed", demo_mode=demo_mode())

    if result.demo_mode:
        return mock.analytics_answer(question, scope_hint)

    plan_data: dict[str, Any] = {}
    sql: str | None = None
    df: pd.DataFrame | None = None
    error: str | None = None

    # --- plan / execute / repair loop -------------------------------------------------
    for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
        started = time.time()
        try:
            plan = _plan(question, memory, scope_hint, error)
        except LLMUnavailable as exc:
            result.trace.append(TraceStep("planner", "failed", str(exc)))
            result.status = "failed"
            result.answer = f"The planning model is unavailable, so no answer was produced. {exc}"
            return result

        plan_data = plan.data
        ms = plan.latency_ms
        status = (plan_data.get("status") or "ok").lower()

        if status == "needs_clarification":
            result.trace.append(
                TraceStep("planner", "ok", "Question is ambiguous — asking the user.", ms)
            )
            result.status = "needs_clarification"
            result.clarifying_question = (
                plan_data.get("clarifying_question")
                or "Could you narrow that down — which time period and which metric?"
            )
            return result

        if status == "out_of_scope":
            result.trace.append(
                TraceStep("planner", "ok", "Data lake cannot answer this.", ms)
            )
            result.status = "out_of_scope"
            result.answer = (
                plan_data.get("clarifying_question")
                or plan_data.get("reasoning")
                or "The data needed to answer this is not in the data lake."
            )
            return result

        try:
            sql = guard_sql(plan_data.get("sql") or "")
            df, result.truncated = _execute(sql)
            result.trace.append(
                TraceStep(
                    "planner",
                    "ok",
                    plan_data.get("reasoning") or "Query planned.",
                    ms,
                )
            )
            result.trace.append(
                TraceStep("executor", "ok", f"{len(df)} row(s) returned.", 0)
            )
            break
        except (UnsafeQuery, Exception) as exc:  # noqa: BLE001 - sqlite raises broadly
            error = f"{type(exc).__name__}: {exc}"
            result.trace.append(
                TraceStep(
                    "executor",
                    "retry" if attempt < MAX_REPAIR_ATTEMPTS else "failed",
                    error,
                    int((time.time() - started) * 1000),
                )
            )
            sql, df = None, None

    if df is None or sql is None:
        result.status = "failed"
        result.answer = (
            "I could not build a query I trust for that question, so I am not going to "
            f"answer it. Last error: {error}"
        )
        result.warnings.append("No answer was produced. Nothing here is a guess.")
        return result

    result.sql = sql
    result.data = df
    result.assumptions = [str(a) for a in (plan_data.get("assumptions") or [])]
    result.sources = [str(t) for t in (plan_data.get("tables_used") or [])]
    result.is_refinement = bool(plan_data.get("is_refinement"))
    chart = plan_data.get("chart") or {}
    result.chart = chart if chart.get("type") not in (None, "none") else None

    # --- answer ------------------------------------------------------------------------
    try:
        composed = _compose_answer(question, sql, df)
        result.answer = composed.get("answer") or ""
        result.trace.append(TraceStep("answerer", "ok", "Answer drafted from result rows.", 0))
    except LLMUnavailable as exc:
        result.status = "answered"
        result.answer = (
            "The query ran, but the answer-writing step failed. The result table below is "
            f"the raw output — read it directly rather than trusting a summary. ({exc})"
        )
        result.warnings.append("Answer text unavailable; table shown instead.")
        return result

    # --- verify ------------------------------------------------------------------------
    if run_verifier and result.answer:
        try:
            verdict = _verify(question, sql, df, result.answer)
            result.verification = verdict
            if verdict.get("supported"):
                result.trace.append(
                    TraceStep("verifier", "ok", "Answer is supported by the returned rows.", 0)
                )
            else:
                issues = "; ".join(str(i) for i in (verdict.get("issues") or []))
                result.trace.append(TraceStep("verifier", "failed", issues, 0))
                result.warnings.append(
                    "The verifier could not support part of this answer: "
                    + (issues or "unspecified mismatch")
                    + ". Treat the table below as the source of truth."
                )
        except LLMUnavailable as exc:
            result.trace.append(TraceStep("verifier", "skipped", str(exc), 0))
            result.warnings.append("Verification step did not run — answer is unchecked.")

    if df.empty:
        result.warnings.append(
            "The query returned zero rows. The answer reflects an empty result, not a finding."
        )

    result.status = "answered"
    return result

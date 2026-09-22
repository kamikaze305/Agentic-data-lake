"""Badges and renderers shared by more than one tab."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from agents import analytics_agent
from agents.vision_agent import HIGH, MEDIUM

VERDICT_BADGE = {
    "match": "✅ matched",
    "mismatch": "❌ mismatch",
    "uncertain": "🟠 uncertain",
    "missing": "⬜ missing",
}
STATUS_BADGE = {
    "awaiting_cg": "📥 awaiting CG",
    "approval_sent": "✅ approval sent",
    "amendment_sent": "✉️ amendment sent",
}


def confidence_badge(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= HIGH:
        return f"🟢 {value:.2f}"
    if value >= MEDIUM:
        return f"🟡 {value:.2f}"
    return f"🔴 {value:.2f}"


def format_age(minutes: float | None) -> str:
    if minutes is None or pd.isna(minutes):
        return "—"
    minutes = max(0.0, minutes)
    if minutes < 60:
        return f"{minutes:.0f}m"
    hours, rem = divmod(minutes, 60)
    return f"{hours:.0f}h {rem:.0f}m"


def render_chart(spec: dict, df: pd.DataFrame) -> None:
    ctype, x, y = spec.get("type"), spec.get("x"), spec.get("y")
    color = spec.get("color")
    if not x or not y or x not in df.columns or y not in df.columns:
        st.caption("The agent proposed a chart, but the columns it named are not in the result.")
        return
    if color and color not in df.columns:
        color = None
    plot = df.head(40)
    title = spec.get("title") or ""
    try:
        if ctype == "line":
            fig = px.line(plot, x=x, y=y, color=color, markers=True, title=title)
        elif ctype == "scatter":
            fig = px.scatter(plot, x=x, y=y, color=color, title=title)
        elif ctype == "pie":
            fig = px.pie(plot, names=x, values=y, title=title)
        else:
            fig = px.bar(plot, x=x, y=y, color=color, title=title)
        fig.update_layout(height=380, margin=dict(l=10, r=10, t=45, b=10))
        st.plotly_chart(fig, width="stretch")
    except Exception as exc:  # a bad spec must not take the answer down with it
        st.caption(f"Chart could not be rendered ({exc}). The table above is unaffected.")


def render_trace(trace: list) -> None:
    icons = {"ok": "✅", "retry": "🔁", "failed": "❌", "skipped": "⏭️"}
    for step in trace:
        stage = getattr(step, "stage", None) or step.get("stage", "")
        status = getattr(step, "status", None) or step.get("status", "")
        detail = getattr(step, "detail", None) or step.get("detail", "")
        ms = getattr(step, "ms", None) or (step.get("ms") if isinstance(step, dict) else 0)
        timing = f" · {ms} ms" if ms else ""
        st.markdown(f"{icons.get(status, '•')} **{stage}**{timing} — {detail}")


def render_result(result: analytics_agent.AnalyticsResult) -> None:
    if result.status == "needs_clarification":
        st.warning(f"**I need one thing before I answer:** {result.clarifying_question}")
        st.caption(
            "The agent stopped rather than pick an interpretation for you. "
            "Answer in the box below and it will continue."
        )
        with st.expander("Agent trace"):
            render_trace(result.trace)
        return

    if result.status == "out_of_scope":
        st.warning("**Not answerable from this data lake.**")
        st.write(result.answer)
        with st.expander("Agent trace"):
            render_trace(result.trace)
        return

    if result.status == "failed":
        st.error(result.answer or "The agent could not produce an answer.")
        with st.expander("Agent trace", expanded=True):
            render_trace(result.trace)
        return

    if result.is_refinement:
        st.caption("🔗 Refined the previous query rather than starting over.")
    st.markdown(result.answer)

    for warning in result.warnings:
        st.warning(warning)

    verdict = result.verification
    if verdict is not None:
        if verdict.get("supported"):
            st.caption(
                f"✅ Verifier: every claim above is present in the returned rows "
                f"(confidence {float(verdict.get('confidence') or 0):.0%})."
            )
        else:
            st.caption("⚠️ Verifier: see the warning above — trust the table, not the prose.")

    if result.assumptions:
        with st.expander(f"Assumptions the agent made ({len(result.assumptions)}) — challenge these"):
            for item in result.assumptions:
                st.markdown(f"- {item}")

    if result.data is not None:
        st.dataframe(result.data, width="stretch", height=min(360, 45 + 35 * min(len(result.data), 9)))
        caption = f"{len(result.data)} row(s)"
        if result.sources:
            caption += f" · source: {', '.join(result.sources)}"
        if result.truncated:
            caption += f" · truncated to {analytics_agent.MAX_ROWS} rows"
        st.caption(caption)

    if result.chart and result.data is not None and not result.data.empty:
        render_chart(result.chart, result.data)

    col1, col2 = st.columns(2)
    with col1.expander("SQL the agent ran"):
        st.code(result.sql or "", language="sql")
    with col2.expander("Agent trace (plan → execute → answer → verify)"):
        render_trace(result.trace)

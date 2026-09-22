"""Flows A + C — ask the data lake, including about documents just stored."""

from __future__ import annotations

import streamlit as st

from agents import analytics_agent
from ui.common import render_result

SAMPLE_QUESTIONS = [
    "Which destination port had the most delayed shipments in the last 6 months?",
    "How does each carrier's on-time rate compare to its contractual target?",
    "Which customers need the most document amendment cycles per shipment?",
    "What is the customs hold rate by commodity?",
    "Show me the total declared weight and value across all uploaded documents",
    "Do the uploaded documents match our shipment records on weight and consignee?",
    "How many documents are pending CG review right now?",
    "What is the average verification turnaround time by verdict?",
]


def render(run_verifier: bool) -> None:
    st.markdown("#### Ask a question in plain English")
    st.caption(
        "Every answer shows the SQL it ran and the rows it read. If the question is "
        "ambiguous the agent asks instead of guessing; if the data cannot answer it, "
        "it says so."
    )

    left, right = st.columns([3, 1])
    with right:
        scope = st.radio(
            "Scope",
            [
                "Both shipment records and uploaded documents",
                "Shipment records only",
                "Uploaded documents only",
            ],
            index=0,
            help="A hint to the planner about where to look. It still shows you which "
                 "tables it used.",
        )
    with left:
        st.markdown("**Try one of these**")
        cols = st.columns(2)
        for i, question in enumerate(SAMPLE_QUESTIONS):
            if cols[i % 2].button(question, key=f"sample_{i}", width="stretch"):
                st.session_state.pending_question = question
                st.rerun()

    st.divider()

    for turn in st.session_state.turns:
        with st.chat_message("user"):
            st.markdown(turn["question"])
        with st.chat_message("assistant"):
            render_result(turn["result"])

    typed = st.chat_input("Ask about shipments, or about documents you have uploaded…")
    question = typed or st.session_state.pending_question
    if question:
        st.session_state.pending_question = None
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            memory = [
                {
                    "question": t["question"],
                    "sql": t["result"].sql,
                    "row_count": 0 if t["result"].data is None else len(t["result"].data),
                }
                for t in st.session_state.turns
            ]
            with st.spinner("Planning → querying → verifying…"):
                result = analytics_agent.ask(
                    question, memory, scope_hint=scope, run_verifier=run_verifier
                )
            render_result(result)
        st.session_state.turns.append({"question": question, "result": result})

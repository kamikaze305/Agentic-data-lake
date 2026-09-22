"""Agentic Data Lake — ask a logistics data lake questions, extract trade documents
into it, and verify supplier documents against customer rules.

    Flow A  Ask a question of the shipment data lake.
    Flow B  Upload a trade document, review what the agent extracted, store it.
    Flow C  Ask a question of the data that document just created — same agent, same store.
    Flow V  An SU email arrives → the agent extracts the attached document, compares it
            against the customer rule set, flags every discrepancy and drafts the reply.
            The CG validator reviews and sends. The agent never sends.

This file is the page shell — setup, sidebar, tabs. Each tab lives in `ui/`.

Run:  python -m streamlit run app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

from agents import config, db
from agents.llm import demo_mode, model_pool
from ui import about_tab, ask_tab, documents_tab, extract_tab, verify_tab

st.set_page_config(page_title="Agentic Data Lake", page_icon="🚢", layout="wide")


# --------------------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------------------

@st.cache_resource
def _boot() -> bool:
    db.ensure_db()
    return True


_boot()

for key, default in [
    ("turns", []),
    ("pending_question", None),
    ("extraction", None),
    ("last_stored", None),
    ("verify_traces", {}),
    ("selected_verification", None),
    ("last_sent", None),
]:
    st.session_state.setdefault(key, default)


# --------------------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### 🚢 Agentic Data Lake")
    st.caption(
        "Analytics · document extraction · supplier-document verification. "
        "One agentic system, one store."
    )

    if demo_mode():
        st.error(
            "**DEMO MODE** — no `GEMINI_API_KEY` in `.env`.\n\n"
            "SQL still runs for real against SQLite, but language understanding, "
            "live extraction and verification are off. Responses are labelled."
        )
    else:
        pool = model_pool()
        st.success(f"**LIVE** · model `{pool[0]}`")
        if len(pool) > 1:
            st.caption(f"Falls back to {len(pool) - 1} more model(s) if this one runs out of quota.")

    run_verifier = st.toggle(
        "Run verifier stage",
        value=True,
        help="A second model pass that re-reads the answer against the returned rows "
             "and flags anything the data does not support. Leave on.",
    )

    st.divider()
    st.markdown("**Data lake contents**")
    counts = db.table_row_counts()
    c1, c2 = st.columns(2)
    c1.metric("Shipments", f"{counts.get('shipments', 0):,}")
    c2.metric("Documents", f"{counts.get('documents', 0):,}")
    st.caption(
        f"{counts.get('v_trade_documents', 0)} confirmed document(s) queryable · "
        f"{counts.get('document_fields', 0)} extracted fields · "
        f"{counts.get('v_verifications', 0)} verification(s) on record"
    )

    with st.expander("Reset"):
        if st.button("Clear stored documents", width="stretch"):
            db.reset_documents()
            st.session_state.extraction = None
            st.cache_resource.clear()
            st.rerun()
        if st.button("Clear conversation", width="stretch"):
            st.session_state.turns = []
            st.rerun()
        if st.button("Clear verifications & inbox", width="stretch"):
            db.reset_verifications()
            for f in config.su_inbox().iterdir():
                if f.is_file():
                    f.unlink()
            st.session_state.verify_traces = {}
            st.session_state.selected_verification = None
            st.rerun()


# --------------------------------------------------------------------------------------
# Tabs
# --------------------------------------------------------------------------------------

tab_verify, tab_ask, tab_extract, tab_docs, tab_about = st.tabs(
    [
        "📬  Verify documents",
        "💬  Ask the data lake",
        "📄  Extract a document",
        "🗂️  Stored documents",
        "🧭  How it works",
    ]
)

with tab_verify:
    verify_tab.render()
with tab_ask:
    ask_tab.render(run_verifier)
with tab_extract:
    extract_tab.render()
with tab_docs:
    documents_tab.render()
with tab_about:
    about_tab.render()

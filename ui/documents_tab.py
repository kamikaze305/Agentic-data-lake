"""Stored documents — what is in the data lake, and what the analytics agent can see."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from agents import db
from ui.common import confidence_badge


def render() -> None:
    st.markdown("#### Documents in the data lake")
    docs = db.list_documents()
    if docs.empty:
        st.info("Nothing stored yet. Extract and confirm a document on the previous tab.")
        return

    st.dataframe(docs, width="stretch", hide_index=True)
    chosen = st.selectbox("Inspect a document", docs["doc_id"].tolist())
    if chosen:
        fields = db.document_fields(chosen)
        fields["confidence"] = fields["confidence"].map(confidence_badge)
        fields["needs_review"] = fields["needs_review"].map({1: "⚑ yes", 0: ""})
        fields["edited_by_user"] = fields["edited_by_user"].map({1: "✎ corrected", 0: ""})
        st.dataframe(fields, width="stretch", hide_index=True)
        if st.button("Delete this document"):
            db.delete_document(chosen)
            st.rerun()

    st.markdown("**`v_trade_documents` — what the analytics agent sees**")
    conn = db.get_readonly_conn()
    try:
        st.dataframe(
            pd.read_sql_query("SELECT * FROM v_trade_documents", conn),
            width="stretch",
            hide_index=True,
        )
    finally:
        conn.close()
    st.caption(
        "Confirmed documents only. An extraction nobody reviewed cannot appear in "
        "an analytics answer — the rule is enforced by the view, not by convention."
    )

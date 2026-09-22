"""Flow B — upload a trade document, review what the agent extracted, store it."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from agents import config, db, vision_agent
from agents.llm import DEFAULT_MODEL
from ui.common import confidence_badge, render_trace


def render() -> None:
    st.markdown("#### Upload a trade document")
    st.caption(
        "PDF or image. The agent classifies it, extracts the canonical trade fields with "
        "a confidence and a quoted evidence snippet for each, then runs deterministic "
        "format and cross-field checks. Nothing reaches the data lake until you confirm."
    )

    sample_dir = config.PROJECT_ROOT / "sample_docs"
    samples = sorted(sample_dir.glob("*.pdf")) if sample_dir.exists() else []

    source = st.radio(
        "Where is the document?",
        ["Upload a file", "Load from folder", "Use a bundled sample"],
        horizontal=True,
        label_visibility="collapsed",
    )

    file_bytes: bytes | None = None
    filename = ""

    if source == "Upload a file":
        uploaded = st.file_uploader(
            "Document", type=["pdf", "png", "jpg", "jpeg", "webp"], label_visibility="collapsed"
        )
        if uploaded is not None:
            file_bytes, filename = uploaded.getvalue(), uploaded.name

    elif source == "Load from folder":
        inbox = config.doc_inbox()
        st.caption(
            f"Reading from **`{inbox}`** — change `DOC_INBOX` in `.env` to point "
            "anywhere. Drop documents in that folder and they appear here."
        )
        inbox_files = config.list_inbox_documents()
        c_pick, c_refresh = st.columns([4, 1])
        with c_refresh:
            st.button("🔄 Refresh", width="stretch")
        if not inbox_files:
            st.info(f"No PDF/PNG/JPG/WEBP files in `{inbox}` yet. Add some and hit Refresh.")
        else:
            with c_pick:
                chosen = st.selectbox(
                    "File", [p.name for p in inbox_files], label_visibility="collapsed"
                )
            picked = next((p for p in inbox_files if p.name == chosen), None)
            if picked is not None:
                file_bytes, filename = picked.read_bytes(), picked.name

    else:  # Use a bundled sample
        if not samples:
            st.info("No bundled samples found. Run `python tools/make_sample_docs.py`.")
        else:
            chosen_sample = st.selectbox("Sample", [p.name for p in samples])
            path = sample_dir / chosen_sample
            file_bytes, filename = path.read_bytes(), chosen_sample

    if file_bytes and st.button("Extract fields", type="primary"):
        with st.spinner("Classifying → extracting → verifying…"):
            st.session_state.extraction = vision_agent.extract(file_bytes, filename)
        st.session_state.last_stored = None

    extraction = st.session_state.extraction
    if extraction is not None:
        _render_extraction(extraction)

    if st.session_state.last_stored:
        st.success(
            f"Stored **{st.session_state.last_stored}**. It is queryable now — go to "
            "**Ask the data lake** and try *“Show me the total declared weight and value "
            "across all uploaded documents”*."
        )


def _render_extraction(extraction: vision_agent.VisionResult) -> None:
    """Review, correct and store one extraction."""
    if extraction.failed:
        st.error(f"**Extraction failed.** {extraction.error}")
        st.caption("Nothing was stored. Fix the input and try again.")
        return

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Document type", extraction.doc_type.replace("_", " ").title())
    m2.metric("Type confidence", f"{extraction.doc_type_confidence:.0%}")
    m3.metric("Mean field confidence", f"{extraction.overall_confidence:.0%}")
    m4.metric("Flagged for review", extraction.review_count)

    if extraction.demo_mode:
        st.info("Replayed from a pre-recorded run — no live model call was made.")

    if extraction.issues:
        with st.expander(
            f"⚠️ {len(extraction.issues)} verification issue(s) — read before confirming",
            expanded=True,
        ):
            for issue in extraction.issues:
                st.markdown(f"- {issue}")

    st.markdown("**Review and correct before storing**")
    st.caption(
        "Edit any value directly in the table. Red and amber rows are the agent "
        "telling you it is unsure — they are not approved until you say so."
    )

    editable = pd.DataFrame(
        [
            {
                "Field": f["name"],
                "Value": f["value"],
                "Confidence": confidence_badge(f["confidence"]),
                "Review": "⚑ yes" if f["needs_review"] else "",
                "Required": "✱" if f["required"] else "",
                "Evidence (quoted from the document)": f["evidence"] or "— no quote —",
            }
            for f in extraction.fields
        ]
    )
    edited = st.data_editor(
        editable,
        width="stretch",
        hide_index=True,
        disabled=["Field", "Confidence", "Review", "Required", "Evidence (quoted from the document)"],
        column_config={
            "Field": st.column_config.TextColumn(width="medium"),
            "Value": st.column_config.TextColumn(width="medium"),
            "Confidence": st.column_config.TextColumn(width="small"),
            "Review": st.column_config.TextColumn(width="small"),
            "Required": st.column_config.TextColumn(width="small"),
        },
        key=f"editor_{extraction.doc_id}",
    )

    for field_row, new_value in zip(extraction.fields, edited["Value"].tolist()):
        new_value = "" if new_value is None else str(new_value).strip()
        if new_value != field_row["value"]:
            field_row["value"] = new_value
            field_row["edited_by_user"] = True
            field_row["needs_review"] = False
            field_row["confidence"] = 1.0
            field_row["evidence"] = "Corrected by reviewer"

    flagged = [f["name"] for f in extraction.fields if f["needs_review"]]
    acknowledged = True
    if flagged:
        acknowledged = st.checkbox(
            f"I have checked the {len(flagged)} flagged field(s) against the "
            f"document: {', '.join(flagged)}",
            key=f"ack_{extraction.doc_id}",
        )

    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("Confirm & store", type="primary", disabled=not acknowledged):
        db.store_document(
            doc_id=extraction.doc_id,
            filename=extraction.filename,
            doc_type=extraction.doc_type,
            doc_type_confidence=extraction.doc_type_confidence,
            fields=extraction.fields,
            overall_confidence=extraction.overall_confidence,
            model=extraction.model or DEFAULT_MODEL,
            trace=extraction.trace,
        )
        st.session_state.last_stored = extraction.doc_id
        st.session_state.extraction = None
        st.rerun()

    if b2.button("Discard"):
        st.session_state.extraction = None
        st.rerun()

    if not acknowledged:
        st.caption(
            "Storing is blocked until the flagged fields are acknowledged. "
            "An uncertain extraction never becomes a confident row."
        )

    with st.expander("Agent trace (classify → extract → repair → verify)"):
        render_trace(extraction.trace)

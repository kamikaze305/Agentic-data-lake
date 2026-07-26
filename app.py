"""GoComet Agentic Data Lake — Part 1 POC.

Flow A  Ask a question of the shipment data lake.
Flow B  Upload a trade document, review what the agent extracted, store it.
Flow C  Ask a question of the data that document just created — same agent, same store.

Run:  streamlit run app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import plotly.express as px
import streamlit as st

from agents import analytics_agent, config, db, vision_agent
from agents.llm import DEFAULT_MODEL, demo_mode
from agents.vision_agent import HIGH, MEDIUM

st.set_page_config(page_title="GoComet Agentic Data Lake", page_icon="🚢", layout="wide")

SAMPLE_QUESTIONS = [
    "Which destination port had the most delayed shipments in the last 6 months?",
    "How does each carrier's on-time rate compare to its contractual target?",
    "Which customers need the most document amendment cycles per shipment?",
    "What is the customs hold rate by commodity?",
    "Show me the total declared weight and value across all uploaded documents",
    "Do the uploaded documents match our shipment records on weight and consignee?",
]


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
]:
    st.session_state.setdefault(key, default)


def confidence_badge(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= HIGH:
        return f"🟢 {value:.2f}"
    if value >= MEDIUM:
        return f"🟡 {value:.2f}"
    return f"🔴 {value:.2f}"


# --------------------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### 🚢 GoComet Agentic Data Lake")
    st.caption("Part 1 POC · agentic analytics + vision extraction, one store")

    if demo_mode():
        st.error(
            "**DEMO MODE** — no `GEMINI_API_KEY` in `.env`.\n\n"
            "SQL still runs for real against SQLite, but language understanding, "
            "live extraction and verification are off. Responses are labelled."
        )
    else:
        st.success(f"**LIVE** · model `{DEFAULT_MODEL}`")

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
        f"{counts.get('document_fields', 0)} extracted fields"
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


# --------------------------------------------------------------------------------------
# Rendering helpers
# --------------------------------------------------------------------------------------

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


# --------------------------------------------------------------------------------------
# Tabs
# --------------------------------------------------------------------------------------

tab_ask, tab_extract, tab_docs, tab_about = st.tabs(
    ["💬  Ask the data lake", "📄  Extract a document", "🗂️  Stored documents", "🧭  How it works"]
)


# ---- Flow A + C -----------------------------------------------------------------------
with tab_ask:
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


# ---- Flow B ---------------------------------------------------------------------------
with tab_extract:
    st.markdown("#### Upload a trade document")
    st.caption(
        "PDF or image. The agent classifies it, extracts the canonical trade fields with "
        "a confidence and a quoted evidence snippet for each, then runs deterministic "
        "format and cross-field checks. Nothing reaches the data lake until you confirm."
    )

    sample_dir = Path(__file__).resolve().parent / "sample_docs"
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
        if extraction.failed:
            st.error(f"**Extraction failed.** {extraction.error}")
            st.caption("Nothing was stored. Fix the input and try again.")
        else:
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

    if st.session_state.last_stored:
        st.success(
            f"Stored **{st.session_state.last_stored}**. It is queryable now — go to "
            "**Ask the data lake** and try *“Show me the total declared weight and value "
            "across all uploaded documents”*."
        )


# ---- Stored documents -----------------------------------------------------------------
with tab_docs:
    st.markdown("#### Documents in the data lake")
    docs = db.list_documents()
    if docs.empty:
        st.info("Nothing stored yet. Extract and confirm a document on the previous tab.")
    else:
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


# ---- How it works ---------------------------------------------------------------------
with tab_about:
    st.markdown(
        """
#### The chain

```
        Flow A                          Flow B                        Flow C
  ┌──────────────────┐          ┌────────────────────┐        ┌──────────────────┐
  │ NL question      │          │ PDF / image        │        │ NL question over │
  │      ↓           │          │      ↓             │        │ extracted data   │
  │ PLANNER  → SQL   │          │ CLASSIFIER         │        │      ↓           │
  │      ↓           │          │      ↓             │        │ same planner,    │
  │ EXECUTOR (ro)    │          │ EXTRACTOR + conf   │        │ same executor,   │
  │      ↓  ↑ repair │          │      ↓  ↑ repair   │        │ same verifier    │
  │ ANSWERER         │          │ RULE VERIFIER      │        │      ↓           │
  │      ↓           │          │      ↓             │        │ answer + table   │
  │ VERIFIER         │          │ HUMAN REVIEW       │        │ + chart          │
  └────────┬─────────┘          └─────────┬──────────┘        └────────▲─────────┘
           │                              │                            │
           └──────────────►  SQLite data lake  ◄───────────────────────┘
                     shipments + carriers + customers
                     documents + document_fields + v_trade_documents
```

**Why one store.** Flow B does not write to a document silo. It writes to the same
SQLite file the shipment data lives in, in the same field vocabulary. That is the
whole of Flow C: no new agent, no new pipeline — a document uploaded ten seconds
ago is joinable to a shipment booked last quarter.

#### Trust, built in rather than bolted on

| Failure the demo has to survive | What stops it |
|---|---|
| Confident nonsense in an answer | Verifier re-reads the answer against the rows and flags unsupported claims |
| Silent guessing on a vague question | Planner returns `needs_clarification` and asks one question |
| Answering something the data cannot support | Planner returns `out_of_scope` and names what is missing |
| Invented SQL columns | Schema with sample values is in the prompt; SQL errors are fed back for repair, twice, then it gives up loudly |
| A prompt injection inside an uploaded PDF | Read-only connection + single-statement SELECT guard |
| A misread field becoming fact | Per-field confidence + quoted evidence + deterministic rules; flagged fields block storage until acknowledged |
| Unreviewed extractions leaking into answers | `v_trade_documents` filters to `status = 'confirmed'` |
| No API key / quota exhausted | Demo mode: real SQL, replayed extraction, labelled on every response |

#### Deliberately not built
Multi-turn autonomous planning, a vector store, OCR fallback for handwriting,
role-based access, multi-page/multi-document sets, streaming ingestion, and any
kind of write-back to the source systems. Each is a real requirement in production
and none of them is what a 24-hour proof of the A→B→C chain needs to demonstrate.
"""
    )
    st.markdown("**Live schema handed to the planner**")
    with st.expander("Schema"):
        st.code(db.schema_description(), language="text")

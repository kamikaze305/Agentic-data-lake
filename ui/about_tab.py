"""How it works — the architecture, in the app itself."""

from __future__ import annotations

import streamlit as st

from agents import db

ABOUT = """
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

#### Verification — the same chain, pointed at a real workflow

```
  SU email arrives (watched folder)
       ↓
  TRIGGER      new envelope detected, processed exactly once
       ↓
  EXTRACTOR    the Flow B vision agent, unmodified (confidence + evidence per field)
       ↓
  COMPARATOR   deterministic checks vs the customer rule set (rules/*.json)
               match / mismatch / uncertain / missing — no model in the verdict
       ↓
  DRAFTER      approval or amendment email, rendered FROM the check table
       ↓
  CG VALIDATOR reviews, edits, and sends — the only send in the system
       ↓
  One store    verifications + checks + emails → queryable via Flow A
```

The three-party workflow does not change: SU sends, CG validates, the customer
receives one clean doc set. What disappears is the manual reading and typing —
not the human. On approval the document enters `v_trade_documents`; every
verification (pending or done) is queryable in `v_verifications`, which is where
the north-star metric — turnaround from SU email to CG reply — lives.

#### Trust, built in rather than bolted on

| Failure the system has to survive | What stops it |
|---|---|
| Confident nonsense in an answer | Verifier re-reads the answer against the rows and flags unsupported claims |
| Silent guessing on a vague question | Planner returns `needs_clarification` and asks one question |
| Answering something the data cannot support | Planner returns `out_of_scope` and names what is missing |
| Invented SQL columns | Schema with sample values is in the prompt; SQL errors are fed back for repair, twice, then it gives up loudly |
| A prompt injection inside an uploaded PDF | Read-only connection + single-statement SELECT guard |
| A misread field becoming fact | Per-field confidence + quoted evidence + deterministic rules; flagged fields block storage until acknowledged |
| Unreviewed extractions leaking into answers | `v_trade_documents` filters to `status = 'confirmed'` |
| A model out of quota | The call moves down a pool of models; each has its own free-tier quota |
| No API key / every model exhausted | Demo mode: real SQL, replayed extraction, labelled on every response |

#### Deliberately not built
Multi-turn autonomous planning, a vector store, OCR fallback for handwriting,
role-based access, multi-page/multi-document sets, streaming ingestion, and any
kind of write-back to the source systems. Each is a real requirement in production;
none of them is needed to prove the A→B→C chain works.
"""


def render() -> None:
    st.markdown(ABOUT)
    st.markdown("**Live schema handed to the planner**")
    with st.expander("Schema"):
        st.code(db.schema_description(), language="text")

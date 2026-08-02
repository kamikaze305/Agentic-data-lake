# PRD — Agentic Data Lake for Trade Operations

**Part 1 · Agentic Data Lake** · Swapnil · 2026-07-26
**Status:** POC shipped and running (`README.md` → 5-minute demo path). This document is the reasoning behind it.

---

## 1. Problem statement

A trade operations team runs on two bodies of information, and cannot use either one well.


**The warehouse too technical to access** 1. Wait for the analyst to send report and by the time it arrives, it is too late to ask second level questions. 
2. Sit with the analyst and wait for them to execute what you are looking for. 

**The documents nobody has ever queried.** The other half of the truth is in PDFs — commercial invoices, bills of lading, packing lists, certificates of origin. Every field on them is read by a human, once, under time pressure, and then never enters a queryable system. Nobody can ask "what weight did we actually declare last month" because that number has never been data. It has only ever been ink.

**Why the current flows fail — four named causes:**

| Cause | What it looks like | Consequence |
|---|---|---|
| **Analyst dependency** | Every non-standard question is a ticket | Questions get rationed; curiosity is expensive |
| **Context switching** | Answer in a BI tool, evidence in a PDF, decision in email | The join happens in someone's head, undocumented |
| **Low trust** | A dashboard number with no traceable derivation | Users re-verify manually, or quietly ignore it |
| **Slow iteration** | Each refinement is a new request | Analysis stops one question short of the insight |

And LLM tools that fix the first three tend to make trust *worse*. A confident, unsourced, occasionally wrong answer is more dangerous in trade compliance than no answer, because a wrong HS code or consignee becomes a customs hold, a demurrage bill, or a penalty.

**Success in the user's first five minutes.** They ask a question in their own words and get an answer *with the SQL that produced it*. They refine it once and watch the query change rather than restart. They ask something vague and get a clarifying question instead of a confident guess. They drop in a Bill of Lading, see each field with a confidence and the quoted text it came from, correct the one field flagged, and store it. Then they ask a question about that document's data — and in the shipped POC, that question surfaces a real discrepancy: the invoice declares 18,720 kg, the B/L and the shipment record say 18,960 kg. Nobody read a PDF to find it.

---

## 2. Users and jobs to be done

**Primary persona — Meera, Trade Documentation Executive (the validator).** Spends most of the day opening attachments and checking fields against what a customer requires. Knows the rules by memory and by scar tissue; the rules are not written down anywhere. Success for her is a clean document set with no amendment round trips. She is not technical, does not write SQL, and does not trust software that cannot show its work. *She is the same person Part 2 is built for.*

**Primary persona — Rohan, Logistics Ops Lead (the decision-maker).** Owns lane performance, carrier mix and cost. Needs answers in the middle of a conversation, not two days later. Comfortable reading a table, not writing a query. Judges a tool by whether he can defend its number in front of a customer.

**Affected non-user — the data analyst.** Currently the bottleneck. The goal is not to remove them; it's to stop spending them on questions that shouldn't need a human.

| # | Job to be done | Testable as |
|---|---|---|
| 1 | When I need a number mid-conversation, I want to ask in plain English and get a data-backed answer with its query visible, so that I can defend it without an analyst. | Question → answer + SQL + table + chart, unaided, < 30s |
| 2 | When the first answer raises a second question, I want to refine it without starting over, so that I reach the insight instead of stopping one step short. | Follow-up modifies prior SQL; agent labels it a refinement |
| 3 | When my question is ambiguous or the data can't support it, I want to be told, so that I never act on an invented answer. | Vague question → one clarifying question; unanswerable → explicit refusal naming what's missing |
| 4 | When a trade document arrives, I want its fields extracted into something queryable, so that I stop being the only index of what we declared. | Upload → structured fields → stored → answerable by question |
| 5 | When the agent is unsure about a field, I want it flagged with the text it read, so that I check one field in seconds instead of re-reading the page. | Every field carries confidence + verbatim evidence; low confidence blocks storage until acknowledged |
| 6 | When I correct a machine-read field, I want the correction recorded as mine, so that there is an audit trail when the value is challenged. | Corrected fields stored with `edited_by_user`, visible in the document view |

---

## 3. Product scope

**In scope (built in 24 hours, all running):**

- **A — Agentic analytics.** NL question over 421 shipments × 14 months plus carrier and customer reference data. Answer, the SQL, the result table, a chart, and multi-turn refinement.
- **B — Vision document agent.** PDF or image in; classification, canonical trade fields with per-field confidence and quoted evidence, deterministic rule checks, human review and correction, then storage.
- **C — Linkage.** One SQLite store, one field vocabulary. Extracted documents are queryable by the same agent and joinable to shipment records on B/L or invoice number.

**Explicitly out of scope, and why:**

| Cut | Why |
|---|---|
| Multi-page / multi-document sets | One document proves extraction; batching is throughput work, not proof |
| Vector search / RAG over document text | The value here is *structured fields*, not passage retrieval |
| OCR fallback for handwriting and low-dpi scans | Real production need; adds a dependency chain that can't be validated in 24h |
| Role-based access, tenant isolation | Necessary for pilot, irrelevant to whether the chain works |
| Write-back to source systems | The data lake stays read-only downstream. Nothing this POC does is irreversible |
| Autonomous multi-step planning | The loop is deliberately bounded at two repairs. Unbounded agents fail in ways a 24h demo can't characterise |
| Scheduled / streaming ingestion | Upload is manual and deliberate — *this is the gap Iteration 1 closes* |

**Assumptions.** Documents are digitally generated, not photographed; one customer's field vocabulary is representative enough to model canonically; a validator accepts a review step that takes seconds, not minutes; a free-tier vision model is accurate enough to be worth *reviewing* rather than replacing.

**Constraints.** 24 hours; one API key; the demo must survive an unreliable network on the evaluator's machine — hence a labelled demo mode that replays recorded extraction while still executing real SQL. The seeded dataset is synthetic, shaped to the pain in the brief; every number in §6 is a pilot target, not a result claimed here.

---

## 4. Key flows

**Flow A — ask → answer → visualise → follow-up.**
Question → **planner** (SQL, or `needs_clarification` / `out_of_scope` — both first-class outcomes, not errors) → **executor** (read-only, single-statement guard; SQL errors fed back for two repairs, then a loud failure) → **answerer** (prose from the returned rows and nothing else) → **verifier** (§5) → answer + SQL + table + chart. Memory holds the last three (question, SQL) pairs, so "now only ocean freight" edits the previous query rather than restarting.

**Flow B — upload → extract → review → store → query later.**
Document → **classifier** (type selects the field set) → **extractor** (each field with a confidence *and a verbatim quote*) → **deterministic verifier** (no model: HS digit count, Incoterm and ISO currency membership, numeric and date parsing, net ≤ gross, required fields — a failure caps confidence and flags the field regardless of what the model claimed) → **repair pass** for missing required fields → **human review** (edit inline; flagged fields block storage until acknowledged) → stored.

**Flow C — the join.** Flow B writes into the same store, in the same vocabulary. `v_trade_documents` exposes confirmed documents as a flat table the planner sees like any other, so *"do the uploaded documents match our shipment records?"* is one question over both halves. No second pipeline.

---

## 5. Trust, safety and failure handling

The design rule: **the agent is never the last line of defence, and it never fails quietly.**

| Failure mode | Control |
|---|---|
| Confident nonsense | Verifier re-reads the answer against the rows; unsupported claims are surfaced to the user, and the table is named as the source of truth |
| Silent guessing on a vague question | Planner returns `needs_clarification` and asks exactly one question |
| Answering beyond the data | Planner returns `out_of_scope` and names what is missing |
| Invented columns / broken SQL | Live schema with sample values in the prompt; errors fed back for two repairs, then a loud failure and no answer |
| Prompt injection inside an uploaded PDF | Read-only connection **and** a single-statement `SELECT`/`WITH` guard — two independent controls |
| A misread field becoming fact | Per-field confidence + quoted evidence + deterministic rules; flagged fields cannot be stored until explicitly acknowledged |
| Unreviewed extraction leaking into an answer | `v_trade_documents` filters to `status='confirmed'` — enforced by schema, not convention |
| Empty result dressed as a finding | Explicit "zero rows — this is not a finding" warning |
| Model down, quota exhausted, no key | Demo mode: real SQL, replayed extraction, labelled on every response |

Two deliberate UX consequences: a clarifying question is a **success**, not friction; and an uncertain field is never approved by default — the human's acknowledgement is a required, recorded act.

---

## 6. Metrics

**North star — verified self-serve answers per active user per week.**
An answer counts only if the user asked it themselves, the verifier confirmed support, and no analyst was involved. Read off the session log — no instrumentation work needed. If people aren't getting *trustworthy* answers *themselves*, nothing else matters.

| # | Supporting metric | Target | Reads on |
|---|---|---|---|
| 1 | Verifier pass rate | ≥ 95% | Answer quality |
| 2 | Clarification rate | 10–25% | Calibration — below 10% it's guessing, above 25% it's friction |
| 3 | Median follow-ups per session | ≥ 2 | The second question is actually getting asked |
| 4 | **Flag precision:** reviewer edits landing on a field the agent had already flagged | ≥ 80% | Whether confidence means anything |
| 5 | Reviewer edits per document | ≤ 1.5 | Extraction accuracy in the only unit that matters |
| 6 | Median upload → confirmed-stored | ≤ 90 seconds | Review is seconds, not minutes |
| 7 | Silent approvals (field stored below threshold without acknowledgement) | **0, hard gate** | The trust invariant |

**Go / No-Go for pilot.** Two weeks, 3 validators and 2 ops leads, one customer's document set. **Go** if: north star ≥ 10 verified self-serve answers per user per week; metrics 1, 4 and 7 all met; and the system catches at least one discrepancy the manual process missed. **No-Go** if flag precision < 60% (confidence is noise and the review UX is theatre) or if any silent approval occurs (the invariant is broken — the design needs revisiting, not the threshold).

---

## 7. Next two iterations

**Iteration 1 (2 weeks) — event-triggered verification against a customer rule set.**
Today a document enters the system because someone chose to upload it. That is the gap. Next: the agent watches the inbox, and when a supplier emails a document set, it extracts, compares each field against that customer's written rule set, and produces a per-field verdict — matched, mismatched, or **uncertain, which is never treated as approved**. On a clean pass it drafts an approval reply; on issues, an amendment email listing field, found, and expected. **The validator reviews and sends. The agent never sends.** The three-party structure — supplier, validator, customer — does not change; what disappears is the manual reading and typing, not the human. Verified output lands in the same store, so §6's metrics keep working. Everything this needs — extraction, confidence, evidence, the confirmed-only store — shipped in Part 1.

*Why this is next and not something else:* the shipped POC already proves the expensive half. The remaining work is a trigger, a rule set, and a drafting step.

**Iteration 2 (4 weeks) — rule learning, queue visibility, and cross-document consistency.**
Three things the first iteration exposes. **(a) Rules come out of people's heads:** mine the amendment history to propose candidate rules a lead approves — new hires stop erring for weeks. **(b) Nobody can see the queue:** a pending view with age and SLA, plus a full audit trail of every verdict, correction and send. **(c) Documents disagree with each other,** not just with the rules: invoice vs B/L vs packing list consistency checks — precisely the 240 kg gap the Part 1 demo surfaces by hand today. At that point the operating metric shifts to **first-pass approval rate** and **amendment cycles per shipment** — the brief's own numbers (2–4 cycles, 4–24 hours each), and a number a validator's team lead can check on Day 14.

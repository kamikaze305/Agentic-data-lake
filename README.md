# Agentic Data Lake — Part 1 + Part 2 POC

Ask a logistics data lake questions in plain English. Drop in a trade document and
have its fields extracted, reviewed and stored. Then ask questions about the data
that document just created — same agent, same store, no new pipeline.

That last sentence is the point of the whole build. **A → B → C is one chain.**

```
A  "Which customers need the most document amendment cycles?"  → answer + SQL + table + chart
B  drop in a Bill of Lading                                     → fields + confidence + evidence → you review → stored
C  "Do the uploaded documents match our shipment records?"      → answer over data that did not exist 30 seconds ago
```

**Part 2 points that chain at a real workflow** — the SU → CG document verification
loop. An SU email arrives, the agent extracts the attached trade document (Part 1
vision agent, unmodified), compares every field against the customer's rule set,
flags mismatches and uncertainty, and drafts the reply. The CG validator reviews and
sends — **the agent has no send button**. Verified output lands in the same store,
so the Part 1 analytics layer can answer "how many documents are pending?" and
"what's our verification turnaround?" from the audit trail itself.

```
V  SU email arrives → extract → compare vs rules → flag → draft → 👤 CG reviews & sends → queryable
```

See [PRD_Part2.md](PRD_Part2.md) (1 page) and [demo_script_part2.md](demo_script_part2.md).

---

## Setup (3 commands, ~2 minutes)

```bash
pip install -r requirements.txt
```

```bash
cp .env.example .env        # then paste a free Gemini key from https://aistudio.google.com/apikey
```

```bash
python -m streamlit run app.py
```

The database seeds itself on first run (421 shipments, 14 months) and the two sample
documents are already in `sample_docs/`. Nothing else to download or configure.

> **No API key? It still runs.** The app starts in **demo mode**: SQL executes for
> real against SQLite, extraction is replayed from a recorded run of the two sample
> documents, and every response says so on its face. Full language understanding
> needs a key — demo mode matches keywords and refuses anything outside
> `sample_questions.md` rather than approximating.

> On Windows use `python -m streamlit run app.py`; the bare `streamlit` command is
> often not on PATH.

**Verify the whole chain without touching the UI:**

```bash
python tests/test_end_to_end.py
```

```bash
python tests/test_verification.py
```

The first covers the Part 1 flows, the SQL guard, extraction rules and a headless
render of the app; the second runs the entire Part 2 loop across all three scenarios
(clean / mismatch / incomplete), the uncertain-never-approved rule, and the
analytics linkage. No API key or network required for either.

---

## The 5-minute demo path

| # | Do this | What to look at |
|---|---------|-----------------|
| 1 | **Ask the data lake** tab → click *"Which destination port had the most delayed shipments in the last 6 months?"* | The answer, then open **SQL the agent ran** and **Agent trace**. The query is the citation. |
| 2 | Type a follow-up: **"now only ocean freight"** | "🔗 Refined the previous query" — the agent edits the previous SQL rather than starting over. |
| 3 | Type something vague: **"how are we doing?"** | It asks a clarifying question instead of inventing a metric. |
| 4 | Type something unanswerable: **"what's the weather at Rotterdam?"** | It says the data lake cannot answer that. No approximation. |
| 5 | **Extract a document** tab → pick `bill_of_lading_MAEU778213.pdf` → **Extract fields** | Per-field confidence and a **quoted evidence snippet**. `hs_code` is 🔴 — the carrier stamp overprints it on the page. |
| 6 | Try to store it | Blocked until you tick the acknowledgement for the flagged field. Correct the value inline; the row turns into a reviewer-confirmed value. |
| 7 | **Confirm & store**, then repeat for the invoice | Sidebar counters move. |
| 8 | Back to **Ask** → *"Do the uploaded documents match our shipment records on weight and consignee?"* | **The chain.** The invoice declares 18,720 kg; the B/L and the ERP say 18,960 kg. A −240 kg discrepancy found by a question, not by a person reading a PDF. |

Step 8 is the thing to watch. It is also the thing Part 2 automates.

---

## The 2-minute Part 2 demo path

All of it works with or without an API key (demo mode replays the recorded
extractions for the bundled `Testdocs/`; comparison, drafting and storage always run
for real).

| # | Do this | What to look at |
|---|---------|-----------------|
| 1 | **📬 Verify (Part 2)** tab → pick `su_email_2_hs_mismatch_bl.json` → **✉️ Simulate this SU email arriving** | The trigger fires: extract → compare → flag → draft, and the email appears in **Incoming** with a verdict. |
| 2 | **Verification result** | ❌ 1 of 8 checks needs attention. Field-by-field verdicts with found vs required, confidence, and the quoted evidence. |
| 3 | **Discrepancy detail** → `hs_code` | Found **1006.40**, customer requires **1006.30** — read at 95% confidence, so it's a confident mismatch, not a guess. |
| 4 | **Draft reply** | The amendment email lists field / found / expected — rendered from the check table, so it cannot claim anything the checks didn't record. Edit it if you like. |
| 5 | **📤 Send reply (as CG)** | The only send button in the system, and it's yours. The reply is written to `cg_outbox/`. |
| 6 | Try `su_email_3_incomplete_invoice.json` | Missing and uncertain fields **block approval** — uncertain is never treated as approved. |
| 7 | Try `su_email_1_clean_invoice.json` | All checks match → approval draft; on send, the document becomes queryable in `v_trade_documents`. |
| 8 | **Ask the data lake** → *"What is the average verification turnaround time by verdict?"* | **The linkage.** Part 2's audit trail answered by Part 1's analytics agent — this is the north-star metric. |

You can also drop any PDF/image or `.json` email envelope into `su_inbox/` and click
**📥 Check inbox now** — the folder is the simulated mailbox.

---

## Architecture

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

Neither agent is a single LLM call.

**Agent A — analytics** ([agents/analytics_agent.py](agents/analytics_agent.py))
- **Planner** decides whether the question is answerable at all. `needs_clarification`
  and `out_of_scope` are first-class outcomes, not errors.
- **Executor** runs the SQL on a read-only connection behind a single-statement guard.
  A SQL error is fed back to the planner as a repair prompt, twice, then it gives up
  out loud.
- **Answerer** writes prose from the returned rows and nothing else.
- **Verifier** independently re-reads the answer against the rows and flags any number,
  entity or causal claim the data does not support. The user sees the verdict.
- **Memory** carries the last three (question, SQL) pairs so follow-ups refine.

**Agent B — vision extraction** ([agents/vision_agent.py](agents/vision_agent.py))
- **Classifier** picks the document type, which selects the field set.
- **Extractor** returns each field with a confidence **and a verbatim evidence snippet**.
  A value it cannot quote is a value it cannot claim.
- **Verifier** is deterministic — no model. HS code digit count, Incoterm 2020 and ISO
  currency membership, numeric parsing, date format, net ≤ gross, required-field
  presence. A failure caps confidence and flags the field regardless of what the model
  claimed.
- **Repair** takes one focused second pass at missing required fields; anything only
  found on the second look is capped at 0.75 confidence and flagged.

**Linkage** ([agents/db.py](agents/db.py)) — Flow B writes into the same SQLite file
as the shipment data, in the same field vocabulary. `v_trade_documents` pivots the
extracted fields into a flat table filtered to `status = 'confirmed'`. Flow C needs no
new component: the planner sees that view in its schema like any other table, and can
join it to `shipments` on `bl_number` or `invoice_number`.

**Agent V — verification (Part 2)** ([agents/verification_agent.py](agents/verification_agent.py))
- **Trigger** polls the simulated mailbox (`su_inbox/`) and processes each email
  exactly once. The plumbing is mocked, per the brief; the activation logic is real.
- **Extractor** is the Part 1 vision agent, called unmodified — perception only.
- **Comparator** is deterministic: each field checked against the customer rule set
  ([rules/sunpeak_foods.json](rules/sunpeak_foods.json)) → match / mismatch /
  **uncertain** / missing. No model in the verdict path, so a verdict is always
  auditable — and uncertain counts against approval exactly like a mismatch.
- **Drafter** renders the approval or amendment email *from the check table*. It
  cannot claim anything the comparator did not record, and it works without a key.
- **CG send** (`cg_send`) is only reachable from the human's button in the UI. It
  stamps the audit trail, writes the reply to `cg_outbox/`, and stores the document —
  `confirmed` (queryable) on approval, `rejected` (audit-only) on amendment.
- Every verification is recorded the moment it happens in `verifications` /
  `verification_checks` / `su_emails`, exposed to the analytics agent via
  `v_verifications` — including `turnaround_minutes`, the north-star metric.

---

## Trust and failure handling

| Failure the demo has to survive | What stops it |
|---|---|
| Confident nonsense in an answer | Verifier re-reads the answer against the rows and surfaces unsupported claims |
| Silent guessing on a vague question | Planner returns `needs_clarification` and asks exactly one question |
| Answering what the data cannot support | Planner returns `out_of_scope` and names what is missing |
| Invented SQL columns | Schema with live sample values in the prompt; SQL errors fed back for repair, twice, then a loud failure |
| Prompt injection inside an uploaded PDF | Read-only connection + single-statement `SELECT`/`WITH` guard |
| A misread field becoming fact | Per-field confidence + quoted evidence + deterministic rules; flagged fields block storage until acknowledged |
| Unreviewed extractions leaking into answers | `v_trade_documents` filters to `confirmed` — enforced by the schema, not by convention |
| Model down, quota gone, no key | Demo mode: real SQL, replayed extraction, labelled on every single response |
| Answer text generated but query returned nothing | Explicit "zero rows — this is not a finding" warning |
| **(P2)** A wrong field shown as approved | Verdicts are deterministic rule checks; a low-confidence read is `uncertain`, and uncertain blocks approval like a mismatch |
| **(P2)** The amendment email claiming something false | The reply is rendered from the recorded check table — it cannot state what the comparator didn't find |
| **(P2)** The agent emailing the supplier on its own | It can't. `cg_send` is only invoked by the CG validator's button; the "sent" artifact records the human action |

Every answer shows its SQL. Every extracted field shows its confidence and its quote.
Nothing uncertain is ever stored quietly.

---

## Deliberately not built

Out of scope for a 24-hour proof of the chain, and each one is a real production
requirement rather than an oversight:

- Multi-page and multi-document sets (one document, one page-set, one extraction)
- Vector search / RAG over document text — the value here is structured fields
- OCR fallback for handwriting and scans below ~150 dpi
- Role-based access, audit log, tenant isolation
- Write-back to any source system; the data lake is read-only downstream
- Streaming or scheduled ingestion — upload is manual and deliberate
- Autonomous multi-step planning; the loop is bounded at two repairs by design

---

## Repo map

```
PRD.md                        Part 1 product reasoning (Deliverable 1)
PRD_Part2.md                  Part 2 PRD — one page, the SU → CG verification loop
app.py                        Streamlit UI — Verify (Part 2) + the Part 1 flows
agents/analytics_agent.py     Agent A: planner → executor → answerer → verifier + memory
agents/vision_agent.py        Agent B: classifier → extractor → rule verifier → repair
agents/verification_agent.py  Agent V: trigger → extract → compare → flag → draft (Part 2)
agents/db.py                  The data lake: schema, read-only access, documents + verifications
agents/llm.py                 Single Gemini entry point; typed failure, never a silent guess
agents/mock.py                Demo mode: real SQL, replayed extractions, always labelled
rules/sunpeak_foods.json      The customer rule set the comparator checks against
data/seed.py                  Generates 421 shipments across 14 months (deterministic)
tools/make_sample_docs.py     Generates the two sample trade documents
tests/test_end_to_end.py      Part 1 checks across A, B, C, guards and a headless UI render
tests/test_verification.py    Part 2 checks: all three scenarios, trust rules, linkage
sample_docs/                  Commercial Invoice + Bill of Lading for the same shipment
sample_emails/                Three SU emails: clean pass, HS mismatch, incomplete doc
Testdocs/                     The trade documents those emails attach (T1 / T2 / T3)
sample_questions.md           Questions to try, including the ones designed to fail well
demo_script.md                Part 1 walkthrough · demo_script_part2.md — Part 2 (2 min)
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `streamlit: command not found` | `python -m streamlit run app.py` |
| Sidebar says DEMO MODE with a key set | Key goes in `.env` (not `.env.example`); check `FORCE_DEMO_MODE=false` |
| `429` / quota errors from Gemini | Free tier rate limit — wait a minute, or unset the key to fall back to demo mode |
| Want a clean slate | Delete `data/datalake.db`; it reseeds on next run |
| Sample documents missing | `python tools/make_sample_docs.py` |

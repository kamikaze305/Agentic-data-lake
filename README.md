# Agentic Data Lake

[![tests](https://github.com/kamikaze305/Agentic-data-lake/actions/workflows/tests.yml/badge.svg)](https://github.com/kamikaze305/Agentic-data-lake/actions/workflows/tests.yml)

<!-- Demo video: replace the placeholder link below once the recording is uploaded. -->
**▶ [Watch the 4-minute demo](https://example.com/agentic-data-lake-demo)** · walkthrough in [demo_script.md](demo_script.md)

Ask a logistics data lake questions in plain English. Drop in a trade document and
have its fields extracted, reviewed and stored. Then ask questions about the data
that document just created — same agent, same store, no new pipeline.

That last sentence is the point of the whole build. **A → B → C is one chain.**

```
A  "Which customers need the most document amendment cycles?"  → answer + SQL + table + chart
B  drop in a Bill of Lading                                     → fields + confidence + evidence → you review → stored
C  "Do the uploaded documents match our shipment records?"      → answer over data that did not exist 30 seconds ago
```

**The same chain, pointed at a real workflow — supplier document verification.** A
supplier (SU) emails a document; the agent extracts it (the Flow B vision agent,
unmodified), compares every field against the customer's written rule set, flags
mismatches and uncertainty, and drafts the reply. The CG validator reviews and sends —
**the agent has no send button**. Every verification lands in the same store, so the
analytics agent can answer "how many documents are pending?" and "what's our
verification turnaround?" from the audit trail itself.

```
V  SU email arrives → extract → compare vs rules → flag → draft → 👤 CG reviews & sends → queryable
```

Product reasoning: [docs/PRD.md](docs/PRD.md) (analytics & extraction) and
[docs/PRD_verification.md](docs/PRD_verification.md) (the verification loop, one page).

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

The database seeds itself on first run (421 shipments, 14 months) and the sample
documents are already in `sample_docs/` and `Testdocs/`. Nothing else to download.

> **No API key? It still runs.** The app starts in **demo mode**: SQL executes for
> real against SQLite, extraction is replayed from a recorded run of the bundled
> documents, and every response says so on its face. Full language understanding
> needs a key — demo mode matches keywords and refuses anything outside
> [docs/sample_questions.md](docs/sample_questions.md) rather than approximating.

> On Windows use `python -m streamlit run app.py`; the bare `streamlit` command is
> often not on PATH.

**Verify everything without touching the UI** — no API key or network needed:

```bash
python tests/test_end_to_end.py
```

```bash
python tests/test_verification.py
```

```bash
python tests/test_llm.py
```

| Script | Covers |
|---|---|
| `test_end_to_end.py` | Flows A, B, C, the SQL guard, extraction rules, and a headless render of the app |
| `test_verification.py` | The verification loop across all three scenarios (clean / mismatch / incomplete), the uncertain-never-approved rule, and the analytics linkage |
| `test_llm.py` | The model pool — what a quota hit, a retired model, a server error and a bad key each do |

All three run on every push via GitHub Actions.

---

## The demo path

The full 5-minute script, with what to say at each step, is in
[demo_script.md](demo_script.md). The short version:

| # | Do this | What to look at |
|---|---------|-----------------|
| 1 | **💬 Ask the data lake** → click *"Which destination port had the most delayed shipments in the last 6 months?"* | The answer, then **SQL the agent ran** and **Agent trace**. The query is the citation. |
| 2 | Type **"now only ocean freight"**, then **"how are we doing?"** | It refines the previous query; then it asks a clarifying question instead of inventing a metric. |
| 3 | **📄 Extract a document** → `bill_of_lading_MAEU778213.pdf` → **Extract fields** | Per-field confidence and a **quoted evidence snippet**. `hs_code` is 🔴 — the carrier stamp overprints it. Storing is blocked until you acknowledge or correct it. |
| 4 | Store it and the invoice, then ask *"Do the uploaded documents match our shipment records on weight and consignee?"* | **The chain.** The invoice declares 18,720 kg; the B/L and the ERP say 18,960 kg — a −240 kg discrepancy found by a question, not by a person reading a PDF. |
| 5 | **📬 Verify documents** → `su_email_2_hs_mismatch_bl.json` → **✉️ Simulate this SU email arriving** | Extract → compare → flag → draft fires; the **📋 Pending queue** shows count, oldest wait and SLA breaches. |
| 6 | Open the result → **Discrepancy detail** → `hs_code` | Found **1006.40**, customer requires **1006.30**, read at 95% confidence — a confident mismatch, not a guess. |
| 7 | **📤 Send reply (as CG)** | The only send in the system, and it's the human's. The reply is written to `cg_outbox/`. |
| 8 | **Ask** → *"What is the average verification turnaround time by verdict?"* | The verification audit trail answered by the analytics agent — the north-star metric. |

`su_email_3_incomplete_invoice.json` shows missing and uncertain fields **blocking
approval**; `su_email_1_clean_invoice.json` shows a clean pass. You can also drop any
PDF/image or `.json` email envelope into `su_inbox/` and click **📥 Check inbox now** —
the folder is the simulated mailbox.

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
                     verifications + checks + emails → v_verifications
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

**Agent V — verification** ([agents/verification_agent.py](agents/verification_agent.py))
- **Trigger** polls the simulated mailbox (`su_inbox/`) and processes each email
  exactly once. The email plumbing is mocked; the activation logic is real.
- **Extractor** is the Flow B vision agent, called unmodified — perception only.
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
- **Pending queue** — count awaiting CG review, oldest wait time, and SLA breaches
  (`QUEUE_SLA_MINUTES`, default 60), read straight off `v_verifications`.

**One LLM entry point** ([agents/llm.py](agents/llm.py)) — every model call goes
through `call_json`, which walks a **pool of models**. Free-tier quota is per model, so
a 429 moves to the next one (and parks the spent model for a minute); a retired model
name (404) moves on too; a server error retries the same model first; a bad key fails
loudly at once. The auto-tracking `gemini-flash-latest` alias sits last, so a fresh
clone always has a current model to fall to.

---

## Trust and failure handling

| Failure the system has to survive | What stops it |
|---|---|
| Confident nonsense in an answer | Verifier re-reads the answer against the rows and surfaces unsupported claims |
| Silent guessing on a vague question | Planner returns `needs_clarification` and asks exactly one question |
| Answering what the data cannot support | Planner returns `out_of_scope` and names what is missing |
| Invented SQL columns | Schema with live sample values in the prompt; SQL errors fed back for repair, twice, then a loud failure |
| Prompt injection inside an uploaded PDF | Read-only connection + single-statement `SELECT`/`WITH` guard |
| A misread field becoming fact | Per-field confidence + quoted evidence + deterministic rules; flagged fields block storage until acknowledged |
| Unreviewed extractions leaking into answers | `v_trade_documents` filters to `confirmed` — enforced by the schema, not by convention |
| A wrong field shown as approved | Verdicts are deterministic rule checks; a low-confidence read is `uncertain`, and uncertain blocks approval like a mismatch |
| The amendment email claiming something false | The reply is rendered from the recorded check table — it cannot state what the comparator didn't find |
| The agent emailing the supplier on its own | It can't. `cg_send` is only invoked by the CG validator's button; the "sent" artifact records the human action |
| One model out of quota mid-demo | The model pool moves to the next model, each with its own quota |
| No key, or every model exhausted | Demo mode: real SQL, replayed extraction, labelled on every single response |
| Answer text generated but query returned nothing | Explicit "zero rows — this is not a finding" warning |

Every answer shows its SQL. Every extracted field shows its confidence and its quote.
Nothing uncertain is ever stored quietly.

---

## Configuration

Everything lives in `.env` (copy [.env.example](.env.example)). Only the key matters
for a first run.

| Variable | Default | What it does |
|---|---|---|
| `GEMINI_API_KEY` | — | Free key from AI Studio. Without it the app runs in demo mode |
| `GEMINI_MODEL` | — | Put one model at the front of the default pool |
| `GEMINI_MODELS` | — | Replace the pool entirely (comma-separated, tried in order) |
| `FORCE_DEMO_MODE` | `false` | Force demo mode even with a key |
| `DOC_INBOX` | `Testdocs` | Folder the Extract tab's *Load from folder* reads |
| `SU_INBOX` | `su_inbox` | The watched folder standing in for the CG mailbox |
| `CG_OUTBOX` | `cg_outbox` | Where sent replies are written |
| `QUEUE_SLA_MINUTES` | `60` | When a pending document counts as an SLA breach |

Customer rules are plain JSON in [rules/](rules/) — edit, save, and the next
verification uses them; no restart.

---

## Deliberately not built

Each is a real production requirement rather than an oversight:

- Multi-page and multi-document sets (one document, one page-set, one extraction)
- Vector search / RAG over document text — the value here is structured fields
- OCR fallback for handwriting and scans below ~150 dpi
- Role-based access, tenant isolation
- Write-back to any source system; the data lake is read-only downstream
- Real email integration (IMAP/Graph) and scheduled ingestion — the mailbox is a watched folder
- Autonomous multi-step planning; the loop is bounded at two repairs by design

Next on the roadmap ([docs/PRD.md](docs/PRD.md) §7, Iteration 2): cross-document
consistency (invoice vs B/L vs packing list), rule learning from amendment history,
and a full audit-trail view.

---

## Repo map

```
app.py                        Page shell: setup, sidebar, tabs
ui/                           One module per tab (verify, ask, extract, documents, about) + common.py
agents/analytics_agent.py     Agent A: planner → executor → answerer → verifier + memory
agents/vision_agent.py        Agent B: classifier → extractor → rule verifier → repair
agents/verification_agent.py  Agent V: trigger → extract → compare → flag → draft
agents/db.py                  The data lake: schema, read-only access, documents + verifications
agents/llm.py                 Single Gemini entry point: model pool, typed failure, never a silent guess
agents/mock.py                Demo mode: real SQL, replayed extractions, always labelled
agents/config.py              Folders and settings read from .env
rules/sunpeak_foods.json      The customer rule set the comparator checks against
data/seed.py                  Generates 421 shipments across 14 months (deterministic)
sample_docs/                  Commercial Invoice + Bill of Lading for the same shipment
sample_emails/                Three SU emails: clean pass, HS mismatch, incomplete doc
Testdocs/                     The trade documents those emails attach (T1 / T2 / T3)
tests/                        End-to-end, verification-loop and model-pool checks (run in CI)
tools/make_sample_docs.py     Regenerates the two sample trade documents
tools/make_user_guide_docx.py Regenerates docs/USER_GUIDE.docx
docs/                         PRDs, the non-technical user guide, sample questions
demo_script.md                The 5-minute walkthrough — what to click, what to say
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `streamlit: command not found` | `python -m streamlit run app.py` |
| Sidebar says DEMO MODE with a key set | Key goes in `.env` (not `.env.example`); check `FORCE_DEMO_MODE=false` |
| "Every Gemini model failed" | All models in the pool are out of free-tier quota — wait for the reset, add models via `GEMINI_MODELS`, or set `FORCE_DEMO_MODE=true` |
| Simulating an email does nothing | Each email is processed once. Sidebar → **Reset → Clear verifications & inbox** |
| Want a clean slate | Delete `data/datalake.db`; it reseeds on next run |
| Sample documents missing | `python tools/make_sample_docs.py` |

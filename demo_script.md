# Demo script — 5 minutes, all four flows

Setup before starting: app running, sidebar showing LIVE (or DEMO MODE — every step
below works either way), both sample documents **not** yet stored, conversation
cleared, and **Reset → Clear verifications & inbox** clicked so the Verify tab is empty.

**The one line to land:** *"A −240 kg mismatch found by asking a question, not by
reading a PDF — and then caught before a human ever opens the email."*

---

## Part A — ask, extract, join (2 minutes)

### 0:00–0:15 · The frame

> "A logistics ops lead has two problems that are actually one problem. Half their
> data is in a warehouse they need an analyst to query, and the other half is in PDFs
> nobody has ever queried at all. This connects them."

### 0:15–0:50 · Flow A — agentic analytics

**💬 Ask the data lake** → click **"Which customers need the most document amendment
cycles per shipment?"**

> "Plain English question, data-backed answer. But the answer isn't the interesting
> part —"

Open **SQL the agent ran**.

> "— this is. Every answer shows the query it ran. You don't trust me, you read the SQL."

Open **Agent trace**.

> "And it isn't one model call. Planner writes the SQL, executor runs it read-only,
> a separate verifier re-reads the answer against the rows it actually got back and
> tells you if the prose says more than the data does."

Type **"now only ocean freight"** → *"Follow-ups refine the previous query instead of
starting from scratch."*

Type **"how are we doing?"** → *"And when the question is ambiguous it asks. It does
not pick a metric for you and hope."*

### 0:50–1:25 · Flow B — vision document agent

**📄 Extract a document** → *Use a bundled sample* → `bill_of_lading_MAEU778213.pdf` →
**Extract fields**.

> "Bill of Lading in. Fields out — with a confidence and the exact text each one was
> read from. That quote is what makes review take ten seconds instead of two minutes."

Point at the red `hs_code` row → *"The carrier's stamp prints across the HS code line,
and the agent says so rather than guessing the digits."*

**Confirm & store** is disabled → *"Uncertain is not approved."* Correct the field,
tick the acknowledgement, **Confirm & store**. Repeat quickly for the invoice.

### 1:25–2:00 · Flow C — the join

Back to **💬 Ask the data lake** → **"Do the uploaded documents match our shipment
records on weight and consignee?"**

Point at `weight_difference_kg`.

> "The invoice declares 18,720 kilos. The Bill of Lading and our own record both say
> 18,960. A 240-kilo mismatch on a live shipment — the kind that holds a container at
> customs. Nobody read a PDF to find it. But somebody still had to ask."

---

## Part B — stop waiting for someone to ask (2 minutes)

### 2:00–2:20 · The workflow

> "Three people: the supplier (SU) sends documents, the CG validator checks every field
> against what the customer requires, the customer needs one clean set. Today that
> check is a human reading every field and typing every amendment — 2 to 4 cycles per
> shipment. We keep all three humans and remove the reading and the typing."

### 2:20–3:30 · Flow V — the verification loop, live

**📬 Verify documents** → pick **su_email_2_hs_mismatch_bl.json** → **✉️ Simulate this SU
email arriving**.

1. *"An email just landed with a Bill of Lading attached. The agent triggers, extracts
   it with the same vision agent you just saw, compares it against Sunpeak Foods' rule
   set, and drafts the reply."* Point at **📋 Pending queue** — count, oldest wait, SLA.
2. **Verification result:** *"1 of 8 checks needs attention. Every verdict shows found,
   required, confidence and the quoted evidence. Verdicts are rules, not model
   judgment — and uncertain never counts as approved."*
3. **Discrepancy detail** → `hs_code`: *"Found 1006.40, customer requires 1006.30 — the
   exact error class behind customs holds. Read at 95% confidence, so it's a confident
   mismatch, not a guess."*
4. **Draft reply:** *"Field, found, expected — rendered from the check table, so it
   cannot claim anything the checks didn't record. And this Send button is the only
   send in the system. It's mine, not the agent's."* Click **📤 Send reply (as CG)**.

### 3:30–4:00 · The close

**💬 Ask the data lake** → *"What is the average verification turnaround time by
verdict?"*

> "The verification I just sent is already queryable — same store, same agent. That's
> the north-star metric a team lead checks on Day 14, straight off the audit trail."

If time allows: **su_email_3_incomplete_invoice.json** shows missing and uncertain fields
blocking approval; **su_email_1_clean_invoice.json** shows the clean-pass approval.

---

## If something goes wrong

| Problem | Say this, do this |
|---|---|
| Gemini is slow or rate-limited | "Free-tier quota is per model — watch it fall to the next one." If every model is spent, set `FORCE_DEMO_MODE=true`, restart, and run the same script; the SQL and the checks are still real. |
| Extraction returns a surprise field | Do not hide it. "That's the flag doing its job" — correct it live. |
| A question returns zero rows | The warning says so explicitly: "It tells you that's an empty result, not a finding." |
| Simulating an email does nothing | It was already processed (by design, once only). Sidebar → **Reset → Clear verifications & inbox**, then simulate again. |
| The whole app fails to start | `python tests/test_end_to_end.py` — no network, proves the chain independently of the UI. |

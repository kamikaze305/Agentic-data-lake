# Demo script — 2 minutes, all three flows

Setup before recording: app running, both sample documents **not** yet uploaded,
conversation cleared, sidebar showing LIVE.

---

### 0:00–0:15 · The frame

> "A logistics ops lead has two problems that are actually one problem. Half their
> data is in a warehouse they need an analyst to query, and the other half is in PDFs
> nobody has ever queried at all. This connects them."

---

### 0:15–0:50 · Flow A — agentic analytics

Click **"Which customers need the most document amendment cycles per shipment?"**

> "Plain English question, data-backed answer. But the answer isn't the interesting
> part —"

Open **SQL the agent ran**.

> "— this is. Every answer shows the query it ran. You don't trust me, you read the SQL."

Open **Agent trace**.

> "And it isn't one model call. Planner writes the SQL, executor runs it read-only,
> a separate verifier re-reads the answer against the rows it actually got back and
> tells you if the prose says more than the data does."

Type **"now only ocean freight"**.

> "Follow-ups refine the previous query instead of starting from scratch."

Type **"how are we doing?"**

> "And when the question is ambiguous it asks. It does not pick a metric for you and
> hope. This is the behaviour I care most about."

---

### 0:50–1:25 · Flow B — vision document agent

**Extract a document** tab → `bill_of_lading_MAEU778213.pdf` → **Extract fields**.

> "Bill of Lading in. Fields out — with two things next to each one: a confidence, and
> the exact text it was read from. That quote is what makes review take ten seconds
> instead of two minutes."

Point at the red `hs_code` row.

> "This one's flagged. On this document the carrier's stamp prints straight across the
> HS code line, and the agent says so rather than guessing the digits."

Click **Confirm & store** — it is disabled.

> "I can't store it. Uncertain is not approved. I acknowledge the flag, or I fix the
> value —"

Correct the field, tick the acknowledgement, **Confirm & store**.

> "— and now it's a reviewer-confirmed value, recorded as corrected by a human."

Repeat quickly for the invoice.

---

### 1:25–1:55 · Flow C — the linkage

Back to **Ask the data lake**:
**"Do the uploaded documents match our shipment records on weight and consignee?"**

> "Now the payoff. This is one question over both halves — the shipment record from
> the ERP, and the fields that came out of a PDF ninety seconds ago. Same agent, same
> store, no second pipeline."

Point at `weight_difference_kg`.

> "The invoice declares 18,720 kilos. The Bill of Lading and our own record both say
> 18,960. That's a 240-kilo mismatch on a live shipment — the kind that holds a
> container at customs. Nobody read a PDF to find it."

---

### 1:55–2:00 · The hand-off to what's next

> "Today a person finds that by opening every attachment and checking every field by
> hand. The next iteration is to stop waiting for someone to ask: trigger on the
> email, check the fields against that customer's rule set, and draft the amendment
> reply for a human to send. The extraction and the store for that already exist —
> they're what you just watched."

---

## If something goes wrong on the day

| Problem | Say this, do this |
|---|---|
| Gemini is slow or rate-limited | "Free tier is rate limited — this is exactly why there's a fallback." Unset the key, restart, run the same script in demo mode; the SQL and numbers are still real. |
| Extraction returns a surprise field | Do not hide it. "That's the flag doing its job" — correct it live. A flagged field handled well is a better demo than a clean one. |
| A question returns zero rows | The warning says so explicitly. Point at it: "It tells you that's an empty result, not a finding." |
| The whole app fails to start | `python tests/test_end_to_end.py` — 34 checks, no network, proves the chain independently of the UI. |

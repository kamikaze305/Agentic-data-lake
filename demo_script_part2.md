# Part 2 demo script — 2 minutes

> Audience: a GoComet evaluator who may not have seen Part 1 run.
> Open `streamlit run app.py` on the **📬 Verify (Part 2)** tab before starting.

---

## 0:00 — The 60-second Part 1 recap (say, don't show)

"Part 1 built two capabilities on one store: an analytics agent that answers
plain-English questions over a shipment data lake — always showing its SQL — and a
vision agent that extracts trade-document fields with a confidence score and a quoted
evidence snippet for every field. Part 1's PRD said Iteration 1 would be event-triggered
verification against a customer rule set. **This is that iteration** — nothing was
rebuilt, the extraction and the store are the Part 1 code paths."

## 0:20 — PRD thinking (30 sec)

"Three people in this workflow: SU sends documents, CG validates every field against
what the customer requires, the customer needs one clean set. Today that validation is
a human reading every field and typing every amendment — 2 to 4 cycles per shipment.
We keep all three humans and remove the reading and the typing. North star: turnaround
from SU email to CG reply, measured from the tool's own audit trail. The one failure
mode we designed against: a false approval — so verdicts are deterministic rules, not
model judgment, and *uncertain never counts as approved*."

## 0:50 — UI walkthrough + agent live (45 sec)

1. Pick **su_email_2_hs_mismatch_bl.json** → click **✉️ Simulate this SU email
   arriving**. *"An SU email just landed with a Bill of Lading attached — the agent
   triggers, extracts with the Part 1 vision agent, compares against Sunpeak Foods'
   rule set, and drafts the reply."*
2. **State 1 — Incoming:** the queue row appears with the verdict.
3. **State 2 — Verification result:** "❌ 1 of 8 checks needs attention. Every verdict
   shows the found value, the required value, the confidence, and the quoted evidence."
4. **State 3 — Discrepancy detail:** open `hs_code` — *"found 1006.40, customer
   requires 1006.30 — this is the exact error class that causes customs holds. The
   agent read it at 95% confidence, so it's a confident mismatch, not a guess."*
5. **State 4 — Draft reply:** *"The amendment email lists field, found, expected —
   rendered from the check table, so it cannot claim anything the checks didn't
   record. I can edit it. And this Send button is the only send in the system — it's
   mine, not the agent's."* Click **📤 Send reply (as CG)**.

## 1:35 — The linkage close (25 sec)

Switch to **💬 Ask the data lake** → click *"What is the average verification
turnaround time by verdict?"* — *"The verification I just did is already queryable
through the Part 1 analytics agent — same store, same chain. That's the north-star
metric a CG team lead checks on Day 14, and it's coming from the audit trail, not a
spreadsheet."*

(If time allows: run **su_email_1_clean_invoice.json** for the clean-pass approval, or
**su_email_3_incomplete_invoice.json** to show missing + uncertain fields blocking
approval.)

---

**Insurance:** everything above runs identically with no API key — extraction replays
the recorded run for the bundled Testdocs (labelled), and comparison, drafting, storage
and analytics-over-verifications are deterministic/real.

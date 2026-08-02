# PRD — SU → CG Trade Document Verification Agent (Part 2)

*One page. Builds on the Part 1 POC: same vision extraction, same store, same analytics. v1.0 · 2026-08-02*

---

## Problem

Every shipment's documents are validated by a CG (Control Group) team member who opens each attachment, reads every field, and mentally checks it against what the customer requires — then types out what's wrong. Rules live in people's heads, so a new hire errs for weeks, nobody can see how many documents are pending, and there is no audit trail when a dispute surfaces. The result is 2–4 amendment cycles per shipment at 4–24 hours each, with CG bandwidth capping how fast shipments clear.

## Personas

**Meera — CG validator (3 yrs).** Checks ~40 documents a day across a dozen customers' unwritten rule sets. Judged on one thing: errors that reach the customer (a wrong HS code = customs hold = penalty). Cares about: catching every discrepancy without re-reading every field, and never being the reason a shipment sat for a day.

**Rahul — SU documentation executive (supplier side).** His job feels done when the doc-set email is sent. Judged on dispatch speed; every amendment email is unplanned rework. Cares about: knowing *exactly* what to fix, in one pass — not "please recheck the invoice" ping-pong.

## Jobs to be done

1. **(Meera)** When an SU document lands in my inbox, I want a field-by-field verdict against the customer's written requirements before I open the attachment, so that I spend my time only on the fields that are actually wrong.
2. **(Rahul)** When my documents have an issue, I want an amendment request that lists each field with what I sent and what was expected, so that I can fix everything in one cycle instead of three.

## The flow — every human touchpoint marked 👤

```
👤 SU emails the document  →  agent detects it (trigger)  →  agent extracts fields
(Part 1 vision agent)  →  agent compares vs customer rule set (deterministic)  →
agent flags: match / mismatch / uncertain / missing  →  agent drafts the reply  →
👤 CG opens the verification result  →  👤 CG inspects flagged fields (found vs
expected, with quoted evidence)  →  👤 CG edits the draft if needed  →  👤 CG SENDS
→  👤 SU fixes and resends (loop) · clean pass → docs go to the customer
```

The three-party structure is untouched — SU sends, CG validates, the customer receives one clean set. The agent removes the reading and the typing, not the humans. **It has no send capability at all.**

## North-star metric

**Median turnaround: SU email arrival → CG reply sent (minutes).** Computed from the system's own audit trail (`v_verifications.turnaround_minutes`) — a CG team lead can check it on Day 14 with one query, against the manual baseline (hours). Guardrail so speed never buys errors: % of agent-approved documents later amended (target: 0).

## The failure mode, and how it is stopped

**Worst case: a false approval** — the agent shows a wrong field as ✅ matched, Meera trusts the green tick, and a bad document reaches customs. Stopped four ways: **(1)** verdicts are deterministic rule checks against a written rule set — no model judgment can "reason away" a mismatch; **(2)** any field below the confidence bar is *uncertain*, and uncertain blocks approval exactly like a mismatch — the agent never silently approves what it could not read; **(3)** every verdict carries the verbatim evidence quote, so CG can spot-check any field in seconds; **(4)** the agent cannot send — an approval only goes out through Meera's button, and the reply email is rendered from the recorded check table, so it cannot claim anything the comparator didn't find.

---

*Out of scope for this iteration (deliberately): real email integration (trigger is a watched folder, per brief), multi-customer rule packs, rule learning from amendment history, cross-document consistency (invoice vs B/L) — the last two are Iteration 2 in the Part 1 PRD.*

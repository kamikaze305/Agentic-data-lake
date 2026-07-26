# Sample questions

Questions marked **✅ works in demo mode** run without an API key (keyword-matched to a
fixed query, executed for real against SQLite). Everything else needs a Gemini key.

---

## Flow A — questions of the shipment data lake

| Question | What to notice |
|---|---|
| Which destination port had the most delayed shipments in the last 6 months? **✅** | Rotterdam and Hamburg lead — EU congestion is baked into the data |
| How does each carrier's on-time rate compare to its contractual target? **✅** | ONE misses its 88% target by a wide margin; the others cluster near theirs |
| Which customers need the most document amendment cycles per shipment? **✅** | Enterprise customers churn most — this is the Part 2 problem, visible in Part 1 |
| What is the customs hold rate by commodity? **✅** | Pharmaceutical formulations (HS 3004.90) hold at ~3x the rest |
| Show me monthly freight spend **✅** | Line chart, 14 months |
| What is the average transit time from Nhava Sheva to Rotterdam? | Date arithmetic across two columns |
| Which shipments are still in transit and already past their ETA? | Filtering on a NULL arrival plus a date comparison |
| Break down delay days by carrier and month for ocean freight only | Two-dimensional grouping, chart with a colour series |

## Follow-ups — proving it refines rather than restarts

Ask one of the above, then send:

- **"now only ocean freight"**
- **"just the last quarter"**
- **"group that by customer instead"**
- **"show me the same thing as a percentage"**

Look for the **🔗 Refined the previous query rather than starting over** caption, and
compare the new SQL to the old one in the expander.

## Flow C — questions of the data the documents created

Upload and confirm both sample documents first.

| Question | What to notice |
|---|---|
| Show me the total declared weight and value across all uploaded documents **✅** | Answers over data that did not exist a minute ago |
| Do the uploaded documents match our shipment records on weight and consignee? **✅** | **The punchline.** Invoice 18,720 kg vs B/L and ERP 18,960 kg — a −240 kg gap |
| What HS codes appear on the uploaded documents, and do they match the shipment record? | Joins `v_trade_documents` to `shipments` |
| Which uploaded document has the lowest extraction confidence? | Confidence is data, not just decoration |
| List every field a reviewer corrected by hand | `document_fields.edited_by_user` — an audit trail from day one |

## Questions designed to fail well

These are the interesting ones. A demo that only shows the happy path is not showing
you anything about trust.

| Question | Expected behaviour |
|---|---|
| How are we doing? | **needs_clarification** — asks which metric and which period |
| Show me the delays | **needs_clarification** — delays for whom, over what window? |
| What's the weather at Rotterdam right now? **✅** | **out_of_scope** — names what it does not have |
| Which carrier will be late next month? | **out_of_scope** — no forecast in the data, and it will not improvise one |
| Why did shipments to Hamburg slip in December? | Answers with *what* the data shows and refuses to assert a cause the data does not contain |
| Delete all shipments for Acme | Blocked by the SQL guard before it reaches the database |

## Document review — what to test on the Extract tab

1. Extract `bill_of_lading_MAEU778213.pdf`. `hs_code` comes back 🔴 (~0.54) — the
   "CLEAN ON BOARD" stamp overprints that line on the page.
2. Try **Confirm & store** without ticking the acknowledgement. It is disabled.
3. Type the correct value into the table. The row becomes reviewer-confirmed
   (confidence 1.00, evidence "Corrected by reviewer") and the block clears.
4. Extract `commercial_invoice_INV-2026-0847.pdf` — clean, all fields 🟢/🟡.
5. Upload a file that is not a trade document. It reports what it could not do
   rather than returning empty fields dressed up as an extraction.

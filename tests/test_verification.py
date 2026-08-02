"""End-to-end test of the Part 2 verification loop.

Runs in demo mode against a throwaway inbox/outbox, so it needs no API key and no
network. Covers the three scenarios the demo stands on (clean / mismatch /
incomplete), the trust rules (uncertain never approved, agent never sends), the
CG send path, and the linkage back into the Part 1 analytics layer.

    python tests/test_verification.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["FORCE_DEMO_MODE"] = "true"
_TMP = tempfile.mkdtemp(prefix="datalake_p2_")
os.environ["SU_INBOX"] = str(Path(_TMP) / "su_inbox")
os.environ["CG_OUTBOX"] = str(Path(_TMP) / "cg_outbox")

from agents import analytics_agent, db, verification_agent as va  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")


def by_scenario(results):
    def find(fragment):
        return next(r for r in results if fragment in r.extraction.filename.lower())
    return find("happy"), find("hsn"), find("incomplete")


def test_trigger_and_pipeline():
    print("\nTrigger · watched inbox activates the agent")
    samples = va.list_sample_emails()
    check("three sample SU emails ship with the repo", len(samples) == 3,
          ", ".join(p.name for p in samples))
    for sample in samples:
        va.simulate_email_arrival(sample)
    results = va.check_inbox_and_process()
    check("every email is processed", len(results) == 3)
    check("re-polling processes nothing twice", va.check_inbox_and_process() == [])
    return results


def test_clean_pass(clean):
    print("\nScenario T1 · clean pass")
    check("verdict is clean", clean.verdict == "clean")
    check("every check matched", clean.count("match") == len(clean.checks))
    check("approval draft is ready", clean.draft_subject.startswith("APPROVED"))
    check("draft names the customer", "Sunpeak Foods BV" in clean.draft_subject)


def test_hs_mismatch(mismatch):
    print("\nScenario T2 · confident HS-code mismatch")
    check("verdict is amend", mismatch.verdict == "amend")
    hs = next(c for c in mismatch.checks if c["field"] == "hs_code")
    check("hs_code is a mismatch, not a guess", hs["verdict"] == "mismatch")
    check("found vs expected recorded", hs["found"] == "1006.40" and hs["expected"] == "1006.30")
    check("amendment draft lists the field, found and expected",
          all(s in mismatch.draft_body for s in ("hs_code", "1006.40", "1006.30")))
    check("draft subject is an amendment request",
          mismatch.draft_subject.startswith("AMENDMENT REQUIRED"))


def test_incomplete(incomplete):
    print("\nScenario T3 · incomplete document — uncertainty is loud")
    check("verdict is amend", incomplete.verdict == "amend")
    check("missing required fields detected", incomplete.count("missing") >= 3,
          f"{incomplete.count('missing')} missing")
    check("low-confidence field is uncertain, not approved",
          any(c["field"] == "invoice_date" and c["verdict"] == "uncertain"
              for c in incomplete.checks))
    check("uncertain/missing block approval even with zero mismatches",
          incomplete.count("mismatch") == 0 and incomplete.verdict != "clean")
    check("draft asks SU to supply what is missing", "missing" in incomplete.draft_body)


def test_cg_send(clean, mismatch):
    print("\nCG send · the human's action, recorded")
    before = db.list_verifications()
    check("all verifications await CG before any send",
          (before["status"] == "awaiting_cg").all())

    out = va.cg_send(clean.verification_id, clean.draft_subject, clean.draft_body)
    check("approval send is recorded as the CG's action", out["action"] == "approval_sent")
    check("the sent reply lands in the outbox", Path(out["outbox_file"]).exists())
    check("outbox artifact names the human sender",
          "Sent-by: CG validator (human)" in Path(out["outbox_file"]).read_text(encoding="utf-8"))

    edited_body = mismatch.draft_body + "\n\nPS: please expedite — vessel cutoff is tomorrow."
    out2 = va.cg_send(mismatch.verification_id, mismatch.draft_subject, edited_body)
    check("amendment send is recorded", out2["action"] == "amendment_sent")
    check("a CG edit before sending is flagged in the audit trail", out2["edited"] is True)


def test_storage_and_linkage(clean, mismatch, incomplete):
    print("\nLinkage · Part 2 output is Part 1 queryable")
    conn = db.get_readonly_conn()
    try:
        approved = conn.execute(
            "SELECT COUNT(*) FROM v_trade_documents WHERE doc_id = ?",
            (clean.extraction.doc_id,),
        ).fetchone()[0]
        rejected = conn.execute(
            "SELECT COUNT(*) FROM v_trade_documents WHERE doc_id = ?",
            (mismatch.extraction.doc_id,),
        ).fetchone()[0]
        turnaround = conn.execute(
            "SELECT MIN(turnaround_minutes) FROM v_verifications "
            "WHERE cg_actioned_at IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    check("approved document becomes queryable in v_trade_documents", approved == 1)
    check("amended document stays out of analytics answers", rejected == 0)
    check("north-star turnaround is computable and non-negative",
          turnaround is not None and turnaround >= 0, f"{turnaround} min")

    r = analytics_agent.ask("How many documents are pending CG review right now?")
    check("analytics answers the queue question", r.status == "answered")
    check("exactly the un-actioned verification is pending",
          r.data is not None and len(r.data) == 1
          and r.data.iloc[0]["verification_id"] == incomplete.verification_id)

    r2 = analytics_agent.ask("What is the average verification turnaround time by verdict?")
    check("analytics answers the north-star question",
          r2.status == "answered" and r2.data is not None and not r2.data.empty)


def test_failure_paths():
    print("\nFailure handling · loud, never silent")
    bad = va.config.su_inbox() / "unreadable_scan.pdf"
    bad.write_bytes(b"not a real document")
    results = va.check_inbox_and_process()
    check("an unprocessable document is a failed verification, not an approval",
          len(results) == 1 and results[0].verdict == "failed")
    check("a failed verification carries its error", bool(results[0].error))
    try:
        va.cg_send(results[0].verification_id, "s", "b")
        check("a failed verification has no reply to send", False)
    except ValueError:
        check("a failed verification has no reply to send", True)


if __name__ == "__main__":
    db.ensure_db()
    db.reset_verifications()
    db.reset_documents()

    results = test_trigger_and_pipeline()
    clean, mismatch, incomplete = by_scenario(results)
    test_clean_pass(clean)
    test_hs_mismatch(mismatch)
    test_incomplete(incomplete)
    test_cg_send(clean, mismatch)
    test_storage_and_linkage(clean, mismatch, incomplete)
    test_failure_paths()

    db.reset_verifications()
    db.reset_documents()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("Failed: " + ", ".join(FAILED))
    sys.exit(1 if FAILED else 0)

"""End-to-end test of the A -> B -> C chain, plus a headless run of the UI.

Runs in demo mode, so it needs no API key and no network. It is the check that
"the demo works on a clean machine" is a fact rather than a hope.

    python tests/test_end_to_end.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["FORCE_DEMO_MODE"] = "true"

from agents import analytics_agent, db, vision_agent  # noqa: E402
from agents.analytics_agent import UnsafeQuery, guard_sql  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")


def test_flow_a() -> None:
    print("\nFlow A · agentic analytics")
    r = analytics_agent.ask("Which destination port had the most delayed shipments?")
    check("answers a question about shipments", r.status == "answered")
    check("shows the SQL it ran", bool(r.sql), (r.sql or "")[:40])
    check("returns rows", r.data is not None and not r.data.empty)
    check("proposes a visualisation", r.chart is not None)
    check("exposes an agent trace", len(r.trace) >= 2)

    r2 = analytics_agent.ask("What is the average rainfall in Bangalore?")
    check("refuses questions the data cannot answer", r2.status == "out_of_scope")


def test_flow_b() -> tuple[vision_agent.VisionResult, vision_agent.VisionResult]:
    print("\nFlow B · vision document extraction")
    inv_path = ROOT / "sample_docs" / "commercial_invoice_INV-2026-0847.pdf"
    bl_path = ROOT / "sample_docs" / "bill_of_lading_MAEU778213.pdf"
    check("sample documents exist", inv_path.exists() and bl_path.exists())

    inv = vision_agent.extract(inv_path.read_bytes(), inv_path.name)
    check("classifies the invoice", inv.doc_type == "commercial_invoice", inv.doc_type)
    check("extracts required invoice fields",
          all(f["value"] for f in inv.fields if f["required"]))
    check("every populated field carries evidence",
          all(f["evidence"] for f in inv.fields if f["value"]))

    bl = vision_agent.extract(bl_path.read_bytes(), bl_path.name)
    check("classifies the bill of lading", bl.doc_type == "bill_of_lading", bl.doc_type)
    flagged = [f["name"] for f in bl.fields if f["needs_review"]]
    check("flags the low-confidence field instead of accepting it",
          "hs_code" in flagged, f"flagged={flagged}")

    bad = vision_agent.extract(b"not a document", "notes.txt")
    check("rejects unsupported file types loudly", bad.failed and bool(bad.error))
    return inv, bl


def test_verifier_rules() -> None:
    print("\nVision verifier · deterministic rules")
    fields = [
        {"name": "hs_code", "value": "10", "confidence": 0.99, "evidence": "HS: 10",
         "required": False, "needs_review": False},
        {"name": "gross_weight_kg", "value": "1000", "confidence": 0.9, "evidence": "1000",
         "required": False, "needs_review": False},
        {"name": "net_weight_kg", "value": "2000", "confidence": 0.9, "evidence": "2000",
         "required": False, "needs_review": False},
        {"name": "incoterm", "value": "XYZ", "confidence": 0.95, "evidence": "XYZ",
         "required": True, "needs_review": False},
    ]
    issues = vision_agent.verify_fields("commercial_invoice", fields)
    by_name = {f["name"]: f for f in fields}
    check("catches a malformed HS code", by_name["hs_code"]["needs_review"])
    check("catches net weight above gross", by_name["net_weight_kg"]["needs_review"])
    check("catches an invalid Incoterm", by_name["incoterm"]["needs_review"])
    check("caps confidence on failed fields", by_name["hs_code"]["confidence"] <= 0.5)
    check("reports missing required fields", any("was not found" in i for i in issues))


def test_flow_c(inv, bl) -> None:
    print("\nFlow C · linkage — query the data the documents created")
    for r in (inv, bl):
        db.store_document(
            doc_id=r.doc_id, filename=r.filename, doc_type=r.doc_type,
            doc_type_confidence=r.doc_type_confidence, fields=r.fields,
            overall_confidence=r.overall_confidence, model=r.model, trace=r.trace,
        )
    counts = db.table_row_counts()
    check("documents are queryable after confirmation", counts["v_trade_documents"] == 2)

    r = analytics_agent.ask("Show me the total declared weight across uploaded documents")
    check("answers over extracted document data", r.status == "answered" and not r.data.empty)

    r = analytics_agent.ask("Do the uploaded documents match our shipment records?")
    check("joins documents to shipments", r.status == "answered" and not r.data.empty)
    diff = r.data["weight_difference_kg"].dropna().abs().max()
    check("surfaces the invoice/B-L weight discrepancy", diff == 240.0, f"{diff} kg")


def test_unconfirmed_stays_invisible() -> None:
    print("\nTrust · unreviewed extractions never reach an answer")
    doc = ROOT / "sample_docs" / "commercial_invoice_INV-2026-0847.pdf"
    res = vision_agent.extract(doc.read_bytes(), doc.name)
    db.store_document(
        doc_id="DOC-PENDING", filename=res.filename, doc_type=res.doc_type,
        doc_type_confidence=res.doc_type_confidence, fields=res.fields,
        overall_confidence=res.overall_confidence, model=res.model,
        trace=res.trace, status="pending_review",
    )
    conn = db.get_readonly_conn()
    try:
        visible = conn.execute(
            "SELECT COUNT(*) FROM v_trade_documents WHERE doc_id = 'DOC-PENDING'"
        ).fetchone()[0]
    finally:
        conn.close()
    check("pending_review document is excluded from the analytics view", visible == 0)
    db.delete_document("DOC-PENDING")


def test_sql_guard() -> None:
    print("\nTrust · SQL guard")
    for bad in [
        "DROP TABLE shipments",
        "SELECT 1; DELETE FROM shipments",
        "UPDATE shipments SET status = 'x'",
        "ATTACH DATABASE 'x.db' AS x",
        "",
    ]:
        try:
            guard_sql(bad)
            check(f"blocks: {bad[:32] or '(empty)'}", False)
        except UnsafeQuery:
            check(f"blocks: {bad[:32] or '(empty)'}", True)
    check("allows a plain SELECT", guard_sql("SELECT 1").startswith("SELECT"))


def test_ui_renders() -> None:
    """Run app.py headlessly. Catches anything that would blow up on the evaluator."""
    print("\nUI · headless render of app.py")
    try:
        from streamlit.testing.v1 import AppTest
    except ImportError:
        print("  SKIP  streamlit.testing unavailable")
        return

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=90)
    at.run()
    check("app.py runs without an exception", not at.exception, str(at.exception)[:200])
    check("all five tabs render", len(at.tabs) == 5, f"{len(at.tabs)} tabs")

    sample = next((b for b in at.button if "amendment" in (b.label or "")), None)
    check("sample question buttons are present", sample is not None)
    if sample is not None:
        at = sample.click().run()
        check("asking a question does not raise", not at.exception, str(at.exception)[:200])
        check("an answer is rendered", len(at.dataframe) >= 1)


if __name__ == "__main__":
    db.ensure_db()
    db.reset_documents()

    test_flow_a()
    inv, bl = test_flow_b()
    test_verifier_rules()
    test_flow_c(inv, bl)
    test_unconfirmed_stays_invisible()
    test_sql_guard()
    test_ui_renders()

    db.reset_documents()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("Failed: " + ", ".join(FAILED))
    sys.exit(1 if FAILED else 0)

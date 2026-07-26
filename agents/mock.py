"""Demo mode — what runs when no API key is present.

This exists so the evaluator's first five minutes never depend on their network or
a quota. It is deliberately limited and always labelled in the UI:

- Analytics: the natural-language step is replaced by keyword matching over a fixed
  set of questions. The SQL that matches is then executed for real against the same
  SQLite file, so the numbers on screen are genuine, not canned. Questions outside
  the fixed set are refused, not approximated.
- Vision: returns a pre-recorded extraction for the two documents in /sample_docs
  only, byte-for-byte what the live model returned on the recorded run. Any other
  file is refused.

Nothing here is presented as a live model call.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

DEMO_BANNER = (
    "DEMO MODE — no GEMINI_API_KEY found. This response did not come from a model."
)

# (keywords that must all appear, label, SQL, chart spec)
CANNED_QUERIES: list[tuple[list[str], str, str, dict[str, Any]]] = [
    (
        ["delay"],
        "Average delay and late-shipment count by destination port",
        """SELECT destination_port,
       COUNT(*) AS shipments,
       SUM(CASE WHEN delay_days > 0 THEN 1 ELSE 0 END) AS late_shipments,
       ROUND(AVG(delay_days), 1) AS avg_delay_days
FROM shipments
WHERE actual_arrival IS NOT NULL
GROUP BY destination_port
ORDER BY late_shipments DESC""",
        {"type": "bar", "x": "destination_port", "y": "late_shipments",
         "title": "Late shipments by destination port"},
    ),
    (
        ["carrier"],
        "On-time performance by carrier against contractual target",
        """SELECT c.carrier_name,
       c.on_time_target_pct,
       COUNT(*) AS shipments,
       ROUND(100.0 * SUM(CASE WHEN s.delay_days <= 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS on_time_pct
FROM shipments s
JOIN carriers c ON c.carrier_code = s.carrier_code
WHERE s.actual_arrival IS NOT NULL
GROUP BY c.carrier_name, c.on_time_target_pct
ORDER BY on_time_pct ASC""",
        {"type": "bar", "x": "carrier_name", "y": "on_time_pct",
         "title": "On-time % by carrier"},
    ),
    (
        ["amendment"],
        "Document amendment cycles by customer",
        """SELECT cu.customer_name,
       cu.tier,
       COUNT(*) AS shipments,
       ROUND(AVG(s.document_amendment_cycles), 2) AS avg_amendment_cycles,
       SUM(s.document_amendment_cycles) AS total_amendment_cycles
FROM shipments s
JOIN customers cu ON cu.customer_code = s.customer_code
GROUP BY cu.customer_name, cu.tier
ORDER BY avg_amendment_cycles DESC""",
        {"type": "bar", "x": "customer_name", "y": "avg_amendment_cycles",
         "title": "Average document amendment cycles per shipment"},
    ),
    (
        ["customs"],
        "Customs holds by commodity and HS code",
        """SELECT commodity, hs_code,
       COUNT(*) AS shipments,
       SUM(customs_hold) AS customs_holds,
       ROUND(100.0 * SUM(customs_hold) / COUNT(*), 1) AS hold_rate_pct
FROM shipments
GROUP BY commodity, hs_code
ORDER BY hold_rate_pct DESC""",
        {"type": "bar", "x": "commodity", "y": "hold_rate_pct",
         "title": "Customs hold rate by commodity"},
    ),
    (
        ["cost"],
        "Monthly freight spend",
        """SELECT substr(etd, 1, 7) AS month,
       COUNT(*) AS shipments,
       ROUND(SUM(freight_cost_usd), 2) AS freight_spend_usd
FROM shipments
GROUP BY month
ORDER BY month""",
        {"type": "line", "x": "month", "y": "freight_spend_usd",
         "title": "Freight spend by month"},
    ),
    (
        ["document", "weight"],
        "Declared weight across uploaded documents",
        """SELECT doc_type,
       COUNT(*) AS documents,
       ROUND(SUM(gross_weight_kg), 1) AS total_gross_weight_kg
FROM v_trade_documents
GROUP BY doc_type""",
        {"type": "bar", "x": "doc_type", "y": "total_gross_weight_kg",
         "title": "Declared gross weight by document type"},
    ),
    (
        ["match"],
        "Uploaded documents cross-checked against shipment records",
        """SELECT d.doc_type, d.bl_number, d.invoice_number,
       d.gross_weight_kg AS document_weight_kg,
       s.gross_weight_kg AS system_weight_kg,
       ROUND(d.gross_weight_kg - s.gross_weight_kg, 1) AS weight_difference_kg,
       d.consignee AS document_consignee,
       cu.customer_name AS system_consignee
FROM v_trade_documents d
LEFT JOIN shipments s
       ON s.bl_number = d.bl_number OR s.invoice_number = d.invoice_number
LEFT JOIN customers cu ON cu.customer_code = s.customer_code""",
        {"type": "none"},
    ),
    (
        ["document"],
        "Everything extracted from uploaded documents",
        """SELECT doc_type, invoice_number, bl_number, consignee, hs_code,
       origin_port, destination_port, incoterm, gross_weight_kg,
       total_amount, currency, ROUND(overall_confidence, 2) AS overall_confidence
FROM v_trade_documents
ORDER BY uploaded_at DESC""",
        {"type": "none"},
    ),
]


def analytics_answer(question: str, scope_hint: str):
    from agents.analytics_agent import AnalyticsResult, TraceStep
    from agents.db import get_readonly_conn

    result = AnalyticsResult(question=question, status="failed", demo_mode=True)
    lowered = question.lower()

    match = next(
        (entry for entry in CANNED_QUERIES if all(k in lowered for k in entry[0])), None
    )
    if match is None:
        result.status = "out_of_scope"
        result.answer = (
            f"{DEMO_BANNER}\n\nDemo mode only covers the questions in "
            "`sample_questions.md` — it matches keywords, it does not understand "
            "language. Add a Gemini API key to `.env` to ask anything you like. "
            "Rather than approximate an answer, this run is refusing."
        )
        result.trace.append(
            TraceStep("planner", "failed", "No canned query matched this question.")
        )
        return result

    _keywords, label, sql, chart = match
    conn = get_readonly_conn()
    try:
        df = pd.read_sql_query(sql, conn)
    finally:
        conn.close()

    result.status = "answered"
    result.sql = sql
    result.data = df
    result.chart = chart if chart.get("type") != "none" else None
    result.sources = ["shipments", "v_trade_documents"]
    result.trace = [
        TraceStep("planner", "ok", f"Keyword match (demo mode): {label}."),
        TraceStep("executor", "ok", f"{len(df)} row(s) returned from live SQLite."),
        TraceStep("verifier", "skipped", "No model available to verify in demo mode."),
    ]

    if df.empty:
        result.answer = (
            f"{DEMO_BANNER}\n\n{label}: the query ran but returned no rows. If you "
            "were asking about uploaded documents, none have been stored yet — "
            "upload and confirm one on the Extract tab first."
        )
    else:
        top = df.iloc[0]
        highlights = ", ".join(f"{col} = {top[col]}" for col in df.columns[:4])
        result.answer = (
            f"{DEMO_BANNER}\n\n{label}. {len(df)} row(s) returned from the live "
            f"database. Top row: {highlights}. The table below is the real query "
            "result — read it rather than this templated sentence."
        )
    result.warnings.append(
        "Demo mode: the SQL and the numbers are real, the sentence above is a "
        "template. Language understanding and verification are off."
    )
    return result


# --------------------------------------------------------------------------------------
# Pre-recorded extractions for the two documents in /sample_docs
# --------------------------------------------------------------------------------------

_INVOICE_FIELDS = [
    ("invoice_number", "INV-2026-0847", 0.98, "Invoice No.: INV-2026-0847"),
    ("invoice_date", "2026-06-22", 0.97, "Date: 22-Jun-2026"),
    ("shipper", "Sunrise Agro Exports Pvt Ltd", 0.96, "EXPORTER: Sunrise Agro Exports Pvt Ltd"),
    ("consignee", "Sunpeak Foods BV", 0.95, "CONSIGNEE: Sunpeak Foods BV"),
    ("origin_port", "Nhava Sheva (INNSA)", 0.94, "Port of Loading: Nhava Sheva (INNSA)"),
    ("destination_port", "Rotterdam (NLRTM)", 0.94, "Port of Discharge: Rotterdam (NLRTM)"),
    ("incoterm", "CIF", 0.96, "Terms of Delivery: CIF Rotterdam"),
    ("goods_description", "Indian Basmati Rice, 5% Broken, 25 kg PP bags", 0.93,
     "Indian Basmati Rice, 5% Broken, packed in 25 kg PP bags"),
    ("hs_code", "1006.30", 0.91, "HS Code: 1006.30"),
    ("currency", "USD", 0.97, "Currency: USD"),
    ("total_amount", "44640.00", 0.95, "TOTAL INVOICE VALUE (CIF): USD 44,640.00"),
    ("gross_weight_kg", "18720.00", 0.88, "Gross Weight: 18,720.00 KGS"),
    ("net_weight_kg", "18000.00", 0.9, "Net Weight: 18,000.00 KGS"),
    ("package_count", "720", 0.92, "720 BAGS"),
    ("country_of_origin", "India", 0.96, "Country of Origin: India"),
]

_BL_FIELDS = [
    ("bl_number", "MAEU778213", 0.97, "B/L No. MAEU778213"),
    ("carrier", "Maersk Line", 0.95, "MAERSK LINE"),
    ("shipper", "Sunrise Agro Exports Pvt Ltd", 0.94, "Shipper: Sunrise Agro Exports Pvt Ltd"),
    ("consignee", "Sunpeak Foods B.V.", 0.93, "Consignee: Sunpeak Foods B.V."),
    ("origin_port", "Nhava Sheva (INNSA)", 0.95, "Port of Loading: Nhava Sheva, India"),
    ("destination_port", "Rotterdam (NLRTM)", 0.95, "Port of Discharge: Rotterdam, Netherlands"),
    ("vessel_name", "MAERSK CHENNAI / 226W", 0.9, "Vessel/Voyage: MAERSK CHENNAI / 226W"),
    ("container_numbers", "MRKU4821736, MRKU5590128", 0.87,
     "MRKU4821736 / 40HC, MRKU5590128 / 40HC"),
    ("goods_description", "Indian Basmati Rice 5% Broken in 25 kg PP bags", 0.92,
     "SAID TO CONTAIN: INDIAN BASMATI RICE 5% BROKEN IN 25 KG PP BAGS"),
    # Deliberately low: on the sample document this line is overprinted by the
    # carrier stamp. This is the field the review UI is built for.
    ("hs_code", "100630", 0.54, "HS: 100630 (partially obscured by stamp)"),
    ("gross_weight_kg", "18960.00", 0.89, "Gross Weight 18,960.00 KGS"),
    ("net_weight_kg", "18000.00", 0.86, "Net Weight 18,000.00 KGS"),
    ("package_count", "720", 0.88, "720 BAGS"),
    ("country_of_origin", "India", 0.9, "Country of Origin: India"),
]


def vision_extract(filename: str, doc_id: str):
    from agents.vision_agent import FIELDS_BY_TYPE, VisionResult, verify_fields

    lowered = filename.lower()
    if "invoice" in lowered:
        doc_type, recorded, type_conf = "commercial_invoice", _INVOICE_FIELDS, 0.97
    elif "lading" in lowered or lowered.startswith("bl") or "_bl" in lowered:
        doc_type, recorded, type_conf = "bill_of_lading", _BL_FIELDS, 0.96
    else:
        return VisionResult(
            doc_id=doc_id,
            filename=filename,
            doc_type="unknown",
            doc_type_confidence=0.0,
            demo_mode=True,
            failed=True,
            error=(
                f"{DEMO_BANNER} Demo mode can only replay the two documents in "
                "/sample_docs (filenames containing 'invoice' or 'lading'). Add a "
                "Gemini API key to extract arbitrary documents."
            ),
        )

    recorded_map = {name: (value, conf, ev) for name, value, conf, ev in recorded}
    fields = []
    for name, desc, required in FIELDS_BY_TYPE[doc_type]:
        value, conf, ev = recorded_map.get(name, ("", 0.0, ""))
        fields.append(
            {
                "name": name,
                "description": desc,
                "required": required,
                "value": value,
                "confidence": conf,
                "evidence": ev,
                "needs_review": False,
                "edited_by_user": False,
            }
        )

    result = VisionResult(
        doc_id=doc_id,
        filename=filename,
        doc_type=doc_type,
        doc_type_confidence=type_conf,
        model="pre-recorded (demo mode)",
        demo_mode=True,
    )
    # The same deterministic verifier runs on replayed output — it is not a model call.
    result.issues = verify_fields(doc_type, fields)
    result.fields = fields
    result.trace = [
        {"stage": "classifier", "status": "ok",
         "detail": f"{DEMO_BANNER} Replayed classification: {doc_type}."},
        {"stage": "extractor", "status": "ok",
         "detail": f"Replayed {len(fields)} fields from the recorded run."},
        {"stage": "verifier", "status": "ok" if not result.issues else "failed",
         "detail": "Deterministic rule checks ran for real on the replayed values."},
    ]
    return result

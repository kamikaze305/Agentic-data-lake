"""The "data lake": one SQLite file holding both operational shipment data and
the fields the vision agent extracts from uploaded documents.

Keeping both in one store is the whole point of Flow C. The analytics agent does
not know or care whether a table came from an ERP export or from a PDF someone
dropped in ten seconds ago — it queries them the same way, and can join them.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "gocomet.db"

# --------------------------------------------------------------------------------------
# Connections
# --------------------------------------------------------------------------------------


def get_conn() -> sqlite3.Connection:
    """Read/write connection. Used only by trusted app code (seeding, storing docs)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_readonly_conn() -> sqlite3.Connection:
    """Read-only connection used for every LLM-generated query.

    Belt and braces with `guard_sql`: even if a prompt injection in an uploaded
    document talked the planner into emitting a DELETE, SQLite itself refuses it.
    """
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# --------------------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------------------

DOC_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id            TEXT PRIMARY KEY,
    filename          TEXT NOT NULL,
    doc_type          TEXT NOT NULL,
    doc_type_confidence REAL,
    uploaded_at       TEXT NOT NULL,
    reviewed_at       TEXT,
    status            TEXT NOT NULL CHECK (status IN ('pending_review','confirmed','rejected')),
    overall_confidence REAL,
    fields_edited_by_user INTEGER NOT NULL DEFAULT 0,
    model             TEXT,
    extraction_trace  TEXT
);

CREATE TABLE IF NOT EXISTS document_fields (
    doc_id        TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    field_name    TEXT NOT NULL,
    field_value   TEXT,
    confidence    REAL,
    evidence      TEXT,
    needs_review  INTEGER NOT NULL DEFAULT 0,
    edited_by_user INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (doc_id, field_name)
);
"""

# Pivoted view over confirmed documents only. Two reasons this exists:
#   1. text-to-SQL over a long/EAV table is where these demos usually fall over.
#   2. "confirmed only" encodes the trust rule in the schema: an extraction a
#      human has not reviewed can never leak into an analytics answer.
DOC_VIEW_SQL = """
DROP VIEW IF EXISTS v_trade_documents;
CREATE VIEW v_trade_documents AS
SELECT
    d.doc_id,
    d.filename,
    d.doc_type,
    d.uploaded_at,
    d.overall_confidence,
    MAX(CASE WHEN f.field_name = 'invoice_number'    THEN f.field_value END) AS invoice_number,
    MAX(CASE WHEN f.field_name = 'bl_number'         THEN f.field_value END) AS bl_number,
    MAX(CASE WHEN f.field_name = 'shipper'           THEN f.field_value END) AS shipper,
    MAX(CASE WHEN f.field_name = 'consignee'         THEN f.field_value END) AS consignee,
    MAX(CASE WHEN f.field_name = 'origin_port'       THEN f.field_value END) AS origin_port,
    MAX(CASE WHEN f.field_name = 'destination_port'  THEN f.field_value END) AS destination_port,
    MAX(CASE WHEN f.field_name = 'incoterm'          THEN f.field_value END) AS incoterm,
    MAX(CASE WHEN f.field_name = 'hs_code'           THEN f.field_value END) AS hs_code,
    MAX(CASE WHEN f.field_name = 'goods_description' THEN f.field_value END) AS goods_description,
    MAX(CASE WHEN f.field_name = 'country_of_origin' THEN f.field_value END) AS country_of_origin,
    MAX(CASE WHEN f.field_name = 'vessel_name'       THEN f.field_value END) AS vessel_name,
    MAX(CASE WHEN f.field_name = 'carrier'           THEN f.field_value END) AS carrier,
    MAX(CASE WHEN f.field_name = 'currency'          THEN f.field_value END) AS currency,
    CAST(MAX(CASE WHEN f.field_name = 'gross_weight_kg' THEN f.field_value END) AS REAL) AS gross_weight_kg,
    CAST(MAX(CASE WHEN f.field_name = 'net_weight_kg'   THEN f.field_value END) AS REAL) AS net_weight_kg,
    CAST(MAX(CASE WHEN f.field_name = 'package_count'   THEN f.field_value END) AS REAL) AS package_count,
    CAST(MAX(CASE WHEN f.field_name = 'total_amount'    THEN f.field_value END) AS REAL) AS total_amount,
    MAX(CASE WHEN f.field_name = 'invoice_date'      THEN f.field_value END) AS invoice_date,
    MIN(COALESCE(f.confidence, 1.0)) AS lowest_field_confidence
FROM documents d
JOIN document_fields f ON f.doc_id = d.doc_id
WHERE d.status = 'confirmed'
GROUP BY d.doc_id;
"""


def ensure_db() -> None:
    """Create the database on first run. Idempotent; safe to call on every page load."""
    fresh = not DB_PATH.exists()
    conn = get_conn()
    try:
        conn.executescript(DOC_SCHEMA_SQL)
        conn.executescript(DOC_VIEW_SQL)
        conn.commit()
        has_shipments = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='shipments'"
        ).fetchone()[0]
    finally:
        conn.close()

    if fresh or not has_shipments:
        from data.seed import seed_operational_data

        seed_operational_data()


def reset_documents() -> None:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM document_fields")
        conn.execute("DELETE FROM documents")
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------------------
# Schema description handed to the planner
# --------------------------------------------------------------------------------------

TABLE_NOTES = {
    "shipments": "One row per shipment leg. `delay_days` > 0 means late arrival; 0 or negative is on time. `status` is one of Delivered / In Transit / Customs Hold / Booked.",
    "carriers": "Carrier reference data. Join on shipments.carrier_code.",
    "customers": "Customer reference data. Join on shipments.customer_code.",
    "v_trade_documents": "ONE ROW PER UPLOADED DOCUMENT, extracted by the vision agent and confirmed by a human. This is the only place extracted document data lives. Join to shipments on bl_number or invoice_number to compare a document against the shipment record.",
    "documents": "Upload metadata for extracted documents, including rows still pending review.",
    "document_fields": "Field-level extraction detail with per-field confidence and the verbatim evidence snippet.",
}

QUERYABLE_OBJECTS = [
    "shipments",
    "carriers",
    "customers",
    "v_trade_documents",
    "documents",
    "document_fields",
]


def schema_description(include_samples: bool = True) -> str:
    """Human/LLM readable schema. Sample values matter more than types here —
    they stop the planner inventing status strings or port-code formats."""
    conn = get_readonly_conn()
    lines: list[str] = []
    try:
        for name in QUERYABLE_OBJECTS:
            cols = conn.execute(f"PRAGMA table_info({name})").fetchall()
            if not cols:
                continue
            lines.append(f"\nTABLE {name}")
            note = TABLE_NOTES.get(name)
            if note:
                lines.append(f"  -- {note}")
            row_count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            lines.append(f"  -- rows: {row_count}")
            for col in cols:
                line = f"  {col['name']} {col['type'] or 'TEXT'}"
                if include_samples and row_count:
                    samples = conn.execute(
                        f"SELECT DISTINCT [{col['name']}] FROM {name} "
                        f"WHERE [{col['name']}] IS NOT NULL LIMIT 4"
                    ).fetchall()
                    values = [str(s[0]) for s in samples]
                    distinct = conn.execute(
                        f"SELECT COUNT(DISTINCT [{col['name']}]) FROM {name}"
                    ).fetchone()[0]
                    if values and distinct <= 12:
                        line += f"   -- values: {', '.join(values[:4])}"
                    elif values:
                        line += f"   -- e.g. {values[0]}"
                lines.append(line)
    finally:
        conn.close()
    return "\n".join(lines).strip()


def table_row_counts() -> dict[str, int]:
    conn = get_readonly_conn()
    counts: dict[str, int] = {}
    try:
        for name in QUERYABLE_OBJECTS:
            try:
                counts[name] = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            except sqlite3.Error:
                counts[name] = 0
    finally:
        conn.close()
    return counts


# --------------------------------------------------------------------------------------
# Document storage (Flow B -> C handoff)
# --------------------------------------------------------------------------------------


def store_document(
    *,
    doc_id: str,
    filename: str,
    doc_type: str,
    doc_type_confidence: float | None,
    fields: list[dict[str, Any]],
    overall_confidence: float | None,
    model: str,
    trace: list[dict[str, Any]] | None = None,
    status: str = "confirmed",
) -> None:
    """Persist a reviewed extraction. Called only after a human clicks Confirm."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    edited = int(any(f.get("edited_by_user") for f in fields))
    conn = get_conn()
    try:
        conn.execute("DELETE FROM document_fields WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
        conn.execute(
            """INSERT INTO documents (doc_id, filename, doc_type, doc_type_confidence,
                                      uploaded_at, reviewed_at, status, overall_confidence,
                                      fields_edited_by_user, model, extraction_trace)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                doc_id,
                filename,
                doc_type,
                doc_type_confidence,
                now,
                now,
                status,
                overall_confidence,
                edited,
                model,
                json.dumps(trace or []),
            ),
        )
        conn.executemany(
            """INSERT INTO document_fields
               (doc_id, field_name, field_value, confidence, evidence, needs_review, edited_by_user)
               VALUES (?,?,?,?,?,?,?)""",
            [
                (
                    doc_id,
                    f["name"],
                    None if f.get("value") in ("", None) else str(f.get("value")),
                    f.get("confidence"),
                    f.get("evidence"),
                    int(bool(f.get("needs_review"))),
                    int(bool(f.get("edited_by_user"))),
                )
                for f in fields
            ],
        )
        conn.commit()
    finally:
        conn.close()


def list_documents() -> pd.DataFrame:
    conn = get_readonly_conn()
    try:
        return pd.read_sql_query(
            """SELECT doc_id, filename, doc_type, status, overall_confidence,
                      fields_edited_by_user, uploaded_at
               FROM documents ORDER BY uploaded_at DESC""",
            conn,
        )
    finally:
        conn.close()


def document_fields(doc_id: str) -> pd.DataFrame:
    conn = get_readonly_conn()
    try:
        return pd.read_sql_query(
            """SELECT field_name, field_value, confidence, needs_review, edited_by_user, evidence
               FROM document_fields WHERE doc_id = ? ORDER BY field_name""",
            conn,
            params=(doc_id,),
        )
    finally:
        conn.close()


def delete_document(doc_id: str) -> None:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM document_fields WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
        conn.commit()
    finally:
        conn.close()

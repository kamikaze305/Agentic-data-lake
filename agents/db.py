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

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "datalake.db"

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

# Part 2 — the SU -> CG verification loop. An email arrives, the agent verifies the
# attached document against the customer rule set, CG reviews and sends the reply.
# Everything is recorded the moment it happens: the queue-visibility and audit-trail
# pains from the brief become queryable tables on day one.
VERIFY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS su_emails (
    email_id     TEXT PRIMARY KEY,
    received_at  TEXT NOT NULL,
    from_addr    TEXT,
    subject      TEXT,
    body         TEXT,
    attachment   TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('processing','verified','failed')),
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS verifications (
    verification_id   TEXT PRIMARY KEY,
    email_id          TEXT REFERENCES su_emails(email_id),
    doc_id            TEXT,
    filename          TEXT,
    doc_type          TEXT,
    customer          TEXT,
    rules_version     TEXT,
    verdict           TEXT NOT NULL CHECK (verdict IN ('clean','amend','failed')),
    checks_total      INTEGER,
    checks_matched    INTEGER,
    checks_mismatched INTEGER,
    checks_uncertain  INTEGER,
    checks_missing    INTEGER,
    draft_subject     TEXT,
    draft_body        TEXT,
    final_subject     TEXT,
    final_body        TEXT,
    extraction_json   TEXT,
    error             TEXT,
    created_at        TEXT NOT NULL,
    cg_action         TEXT CHECK (cg_action IN ('approval_sent','amendment_sent')),
    cg_edited         INTEGER NOT NULL DEFAULT 0,
    cg_actioned_at    TEXT
);

CREATE TABLE IF NOT EXISTS verification_checks (
    verification_id TEXT NOT NULL REFERENCES verifications(verification_id) ON DELETE CASCADE,
    field_name      TEXT NOT NULL,
    rule_label      TEXT,
    expected        TEXT,
    found           TEXT,
    verdict         TEXT NOT NULL CHECK (verdict IN ('match','mismatch','uncertain','missing')),
    confidence      REAL,
    evidence        TEXT,
    detail          TEXT,
    PRIMARY KEY (verification_id, field_name)
);
"""

# One row per verification with its email context and the north-star metric
# (turnaround) computed from the audit trail itself — a CG team lead can check
# Day-14 progress with one query, no instrumentation project needed.
VERIFY_VIEW_SQL = """
DROP VIEW IF EXISTS v_verifications;
CREATE VIEW v_verifications AS
SELECT
    v.verification_id,
    v.email_id,
    e.received_at,
    e.from_addr,
    e.subject,
    v.filename,
    v.doc_type,
    v.customer,
    v.verdict,
    v.checks_total,
    v.checks_matched,
    v.checks_mismatched,
    v.checks_uncertain,
    v.checks_missing,
    CASE WHEN v.cg_action IS NULL THEN 'awaiting_cg' ELSE v.cg_action END AS status,
    v.cg_edited,
    v.created_at,
    v.cg_actioned_at,
    ROUND((julianday(v.cg_actioned_at) - julianday(e.received_at)) * 24 * 60, 1)
        AS turnaround_minutes
FROM verifications v
LEFT JOIN su_emails e ON e.email_id = v.email_id;
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
        conn.executescript(VERIFY_SCHEMA_SQL)
        conn.executescript(DOC_VIEW_SQL)
        conn.executescript(VERIFY_VIEW_SQL)
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
    "v_verifications": "ONE ROW PER VERIFICATION of a supplier document against the customer rule set (Part 2). `status` is 'awaiting_cg' until the CG validator sends the reply, then 'approval_sent' or 'amendment_sent'. `verdict` is the agent's finding: 'clean' or 'amend'. `turnaround_minutes` is email arrival to CG reply — the north-star metric.",
    "verification_checks": "Field-level verification detail: rule label, expected vs found, verdict (match/mismatch/uncertain/missing), confidence and evidence.",
    "su_emails": "Simulated supplier (SU) emails that triggered the verification agent.",
}

QUERYABLE_OBJECTS = [
    "shipments",
    "carriers",
    "customers",
    "v_trade_documents",
    "documents",
    "document_fields",
    "v_verifications",
    "verification_checks",
    "su_emails",
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


# --------------------------------------------------------------------------------------
# Part 2 — SU emails and verifications
# --------------------------------------------------------------------------------------


def email_seen(email_id: str) -> bool:
    conn = get_readonly_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM su_emails WHERE email_id = ?", (email_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def record_email(
    *, email_id: str, received_at: str, from_addr: str, subject: str, body: str,
    attachment: str, status: str = "processing",
) -> None:
    conn = get_conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO su_emails
               (email_id, received_at, from_addr, subject, body, attachment, status)
               VALUES (?,?,?,?,?,?,?)""",
            (email_id, received_at, from_addr, subject, body, attachment, status),
        )
        conn.commit()
    finally:
        conn.close()


def mark_email_status(email_id: str, status: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE su_emails SET status = ?, processed_at = ? WHERE email_id = ?",
            (status, now, email_id),
        )
        conn.commit()
    finally:
        conn.close()


def store_verification(
    *, verification_id: str, email_id: str | None, doc_id: str, filename: str,
    doc_type: str, customer: str, rules_version: str, verdict: str,
    checks: list[dict[str, Any]], draft_subject: str, draft_body: str,
    extraction_fields: list[dict[str, Any]], error: str | None = None,
) -> None:
    """Record a verification the moment the agent finishes. No human gate here —
    the record IS the queue; CG action is a later update, never a precondition."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tally = {"match": 0, "mismatch": 0, "uncertain": 0, "missing": 0}
    for c in checks:
        tally[c["verdict"]] = tally.get(c["verdict"], 0) + 1
    conn = get_conn()
    try:
        conn.execute("DELETE FROM verification_checks WHERE verification_id = ?", (verification_id,))
        conn.execute("DELETE FROM verifications WHERE verification_id = ?", (verification_id,))
        conn.execute(
            """INSERT INTO verifications
               (verification_id, email_id, doc_id, filename, doc_type, customer,
                rules_version, verdict, checks_total, checks_matched, checks_mismatched,
                checks_uncertain, checks_missing, draft_subject, draft_body,
                extraction_json, error, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                verification_id, email_id, doc_id, filename, doc_type, customer,
                rules_version, verdict, len(checks), tally["match"], tally["mismatch"],
                tally["uncertain"], tally["missing"], draft_subject, draft_body,
                json.dumps(extraction_fields), error, now,
            ),
        )
        conn.executemany(
            """INSERT INTO verification_checks
               (verification_id, field_name, rule_label, expected, found, verdict,
                confidence, evidence, detail)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [
                (
                    verification_id, c["field"], c.get("label"), c.get("expected"),
                    c.get("found"), c["verdict"], c.get("confidence"),
                    c.get("evidence"), c.get("detail"),
                )
                for c in checks
            ],
        )
        conn.commit()
    finally:
        conn.close()


def mark_cg_action(
    *, verification_id: str, action: str, final_subject: str, final_body: str,
    edited: bool,
) -> None:
    """The one write that only a human click can trigger."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = get_conn()
    try:
        conn.execute(
            """UPDATE verifications
               SET cg_action = ?, final_subject = ?, final_body = ?, cg_edited = ?,
                   cg_actioned_at = ?
               WHERE verification_id = ?""",
            (action, final_subject, final_body, int(edited), now, verification_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_verifications() -> pd.DataFrame:
    conn = get_readonly_conn()
    try:
        return pd.read_sql_query(
            "SELECT * FROM v_verifications ORDER BY received_at DESC, created_at DESC",
            conn,
        )
    finally:
        conn.close()


def get_verification(verification_id: str) -> dict[str, Any] | None:
    conn = get_readonly_conn()
    try:
        row = conn.execute(
            "SELECT * FROM verifications WHERE verification_id = ?", (verification_id,)
        ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["checks"] = [
            dict(c)
            for c in conn.execute(
                """SELECT field_name, rule_label, expected, found, verdict,
                          confidence, evidence, detail
                   FROM verification_checks WHERE verification_id = ?
                   ORDER BY CASE verdict
                       WHEN 'mismatch' THEN 0 WHEN 'missing' THEN 1
                       WHEN 'uncertain' THEN 2 ELSE 3 END, field_name""",
                (verification_id,),
            ).fetchall()
        ]
        email = conn.execute(
            "SELECT * FROM su_emails WHERE email_id = ?", (record["email_id"],)
        ).fetchone()
        record["email"] = dict(email) if email else None
        return record
    finally:
        conn.close()


def reset_verifications() -> None:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM verification_checks")
        conn.execute("DELETE FROM verifications")
        conn.execute("DELETE FROM su_emails")
        conn.commit()
    finally:
        conn.close()

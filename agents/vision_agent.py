"""Agent B — Vision Document Agent.

A trade document (PDF or image) goes in; a structured, reviewable record comes out.

    CLASSIFIER  what kind of document is this? Drives which field set applies.
    EXTRACTOR   pulls the canonical fields, each with a confidence AND a verbatim
                evidence snippet copied from the page. The snippet is what makes
                review fast — a human checks the quote, not the whole document.
    VERIFIER    deterministic rules, no model involved: formats, ranges, cross-field
                consistency, required-field presence. Anything that fails is marked
                needs_review and its confidence is capped, no matter how confident
                the model claimed to be.
    REPAIR      one focused second pass for required fields that came back empty.

Nothing is written to the data lake here. Storage happens only after a human
confirms in the UI — see `db.store_document`.
"""

from __future__ import annotations

import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agents import mock
from agents.llm import LLMUnavailable, call_json, demo_mode

# Confidence bands used consistently in the agent and the UI.
HIGH, MEDIUM = 0.85, 0.60

# Above this long-edge, an image is downscaled before it is sent to the model.
# A trade document is text; 2000px keeps every character legible while turning a
# multi-megabyte phone scan into a few hundred KB, so the one extraction call is
# fast. PDFs are never touched — they go to the model as-is.
MAX_IMAGE_EDGE = 2000

SUPPORTED_MIME = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}

INCOTERMS = {"EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP"}
CURRENCIES = {"USD", "EUR", "GBP", "INR", "SGD", "AED", "CNY", "JPY"}

# Canonical field set. Keeping one vocabulary across document types is what lets
# `v_trade_documents` be a flat, queryable table in Flow C — and it is the same
# vocabulary a Part 2 rule set would be written against.
FieldSpec = tuple[str, str, bool]  # (name, what to look for, required)

COMMON: list[FieldSpec] = [
    ("shipper", "Shipper / exporter / seller name as printed", True),
    ("consignee", "Consignee / buyer / notify party name as printed", True),
    ("origin_port", "Port of loading / port of departure / airport of departure", True),
    ("destination_port", "Port of discharge / final destination port", True),
    ("goods_description", "Description of goods, one line", True),
    ("hs_code", "HS / HTS / tariff code, digits and dots only", False),
    ("gross_weight_kg", "Gross weight in kilograms, numeric only", False),
    ("country_of_origin", "Country of origin of the goods", False),
]

FIELDS_BY_TYPE: dict[str, list[FieldSpec]] = {
    "commercial_invoice": COMMON
    + [
        ("invoice_number", "Invoice number", True),
        ("invoice_date", "Invoice date in YYYY-MM-DD", True),
        ("incoterm", "Incoterm, three letters (FOB, CIF, ...)", True),
        ("currency", "Currency code, three letters", True),
        ("total_amount", "Invoice total, numeric only, no currency symbol", True),
        ("net_weight_kg", "Net weight in kilograms, numeric only", False),
        ("package_count", "Number of packages / cartons, numeric only", False),
    ],
    "bill_of_lading": COMMON
    + [
        ("bl_number", "Bill of Lading number", True),
        ("carrier", "Carrier / shipping line name", True),
        ("vessel_name", "Vessel name and voyage number", False),
        ("container_numbers", "Container numbers, comma separated", False),
        ("package_count", "Number of packages / containers, numeric only", False),
        ("net_weight_kg", "Net weight in kilograms, numeric only", False),
    ],
    "packing_list": COMMON
    + [
        ("invoice_number", "Related invoice number", False),
        ("package_count", "Total number of packages, numeric only", True),
        ("net_weight_kg", "Net weight in kilograms, numeric only", True),
    ],
    "certificate_of_origin": COMMON
    + [
        ("invoice_number", "Related invoice number", False),
        ("country_of_origin", "Declared country of origin", True),
    ],
}
FIELDS_BY_TYPE["unknown"] = COMMON


def _superset_fields() -> list[FieldSpec]:
    """Union of every field across all document types, deduped by name.

    A single call against this superset both classifies the document and extracts
    every field any type could need — so a clean, confidently-classified document
    is done in one model call. required is forced False here because whether a
    field is mandatory depends on the type, which we only know after classifying;
    the type-specific required flags are applied afterwards.
    """
    seen: dict[str, FieldSpec] = {}
    for specs in FIELDS_BY_TYPE.values():
        for name, desc, _required in specs:
            seen.setdefault(name, (name, desc, False))
    return list(seen.values())


ALL_FIELDS: list[FieldSpec] = _superset_fields()

NUMERIC_FIELDS = {"gross_weight_kg", "net_weight_kg", "package_count", "total_amount"}


@dataclass
class VisionResult:
    doc_id: str
    filename: str
    doc_type: str
    doc_type_confidence: float
    fields: list[dict[str, Any]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    demo_mode: bool = False
    failed: bool = False
    error: str | None = None

    @property
    def overall_confidence(self) -> float:
        vals = [f.get("confidence") or 0.0 for f in self.fields if f.get("value")]
        return round(sum(vals) / len(vals), 3) if vals else 0.0

    @property
    def review_count(self) -> int:
        return sum(1 for f in self.fields if f.get("needs_review"))


EXTRACT_SYSTEM = """You extract structured data from international trade documents
(commercial invoices, bills of lading, packing lists, certificates of origin).

Absolute rules:
- Only report what is visibly printed on the document. Never infer, complete or
  normalise a value from world knowledge. A partially legible value is reported as
  what you can see with a low confidence, not as your best guess at the full value.
- If a field is not present on the document, return value "" with confidence 0.
  A missing field is a correct answer. An invented field is a serious error.
- `evidence` must be text copied verbatim from the document, including its label,
  e.g. "Gross Weight: 18,720.00 KGS". If you cannot quote it, you cannot claim it.
- `confidence` is your honest read of legibility and ambiguity: 0.95+ crisp and
  unambiguous, 0.7-0.9 legible but the label is unusual or the value could belong
  to a neighbouring field, below 0.6 you are struggling. Do not inflate.
- Numbers: digits and a decimal point only. Strip thousands separators, units and
  currency symbols. Dates: YYYY-MM-DD.

Return JSON only:
{
  "doc_type": "commercial_invoice"|"bill_of_lading"|"packing_list"|"certificate_of_origin"|"unknown",
  "doc_type_confidence": 0.0-1.0,
  "fields": [{"name": string, "value": string, "confidence": 0.0-1.0, "evidence": string}],
  "notes": [string]
}"""


def _field_prompt(specs: list[FieldSpec]) -> str:
    return "\n".join(f"- {name}: {desc}" for name, desc, _ in specs)


def _extract(file_bytes: bytes, mime: str, specs: list[FieldSpec], focus: list[str] | None = None):
    if focus:
        instruction = (
            "A previous pass missed these required fields. Look again, carefully, "
            "at the whole page including headers, footers, stamps and margins. If a "
            "field genuinely is not on the document, return it with an empty value "
            "and confidence 0 — that is an acceptable answer.\n\nFIELDS TO FIND:\n"
            + "\n".join(f"- {n}" for n in focus)
        )
    else:
        instruction = (
            "Classify the document, then extract every field below that appears on "
            "it.\n\nFIELDS TO EXTRACT:\n" + _field_prompt(specs)
        )
    return call_json(instruction, system=EXTRACT_SYSTEM, file_bytes=file_bytes, mime_type=mime)


# --------------------------------------------------------------------------------------
# Deterministic verification — no model, no negotiation
# --------------------------------------------------------------------------------------


def _clean_number(value: str) -> float | None:
    try:
        return float(re.sub(r"[^0-9.\-]", "", str(value)))
    except (ValueError, TypeError):
        return None


def verify_fields(doc_type: str, fields: list[dict[str, Any]]) -> list[str]:
    """Apply format, range and cross-field rules.

    Mutates `fields` in place: sets `needs_review` and caps `confidence` where a
    rule fails. Returns the list of human-readable issues found.
    """
    issues: list[str] = []
    by_name = {f["name"]: f for f in fields}
    specs = FIELDS_BY_TYPE.get(doc_type, FIELDS_BY_TYPE["unknown"])

    def flag(name: str, message: str, cap: float = 0.5) -> None:
        issues.append(message)
        f = by_name.get(name)
        if f:
            f["needs_review"] = True
            f["confidence"] = min(f.get("confidence") or 0.0, cap)
            f.setdefault("issues", []).append(message)

    for name, _desc, required in specs:
        f = by_name.get(name)
        value = (f or {}).get("value")
        if required and not value:
            issues.append(f"Required field `{name}` was not found on the document.")
            if f:
                f["needs_review"] = True
        if f and value and (f.get("confidence") or 0) < MEDIUM:
            f["needs_review"] = True
        if f and value and not (f.get("evidence") or "").strip():
            flag(name, f"`{name}` has no evidence snippet — the value is unverifiable.", 0.4)

    for name in NUMERIC_FIELDS:
        f = by_name.get(name)
        if f and f.get("value"):
            num = _clean_number(f["value"])
            if num is None:
                flag(name, f"`{name}` is not numeric: {f['value']!r}.")
            elif num <= 0:
                flag(name, f"`{name}` is {num}, which is not a plausible value.")
            else:
                f["value"] = str(num)

    hs = by_name.get("hs_code")
    if hs and hs.get("value"):
        digits = re.sub(r"\D", "", str(hs["value"]))
        if not 6 <= len(digits) <= 10:
            flag(
                "hs_code",
                f"HS code {hs['value']!r} has {len(digits)} digits; expected 6 to 10.",
            )

    inco = by_name.get("incoterm")
    if inco and inco.get("value"):
        code = str(inco["value"]).strip().upper()[:3]
        if code not in INCOTERMS:
            flag("incoterm", f"Incoterm {inco['value']!r} is not a recognised Incoterm 2020 code.")
        else:
            inco["value"] = code

    cur = by_name.get("currency")
    if cur and cur.get("value"):
        code = str(cur["value"]).strip().upper()[:3]
        if code not in CURRENCIES:
            flag("currency", f"Currency {cur['value']!r} is not a recognised ISO code.")
        else:
            cur["value"] = code

    for name in ("invoice_date",):
        f = by_name.get(name)
        if f and f.get("value"):
            try:
                datetime.strptime(str(f["value"])[:10], "%Y-%m-%d")
            except ValueError:
                flag(name, f"`{name}` {f['value']!r} is not a valid YYYY-MM-DD date.")

    gross = _clean_number((by_name.get("gross_weight_kg") or {}).get("value") or "")
    net = _clean_number((by_name.get("net_weight_kg") or {}).get("value") or "")
    if gross and net and net > gross:
        flag(
            "net_weight_kg",
            f"Net weight ({net} kg) exceeds gross weight ({gross} kg) — one of them is misread.",
        )

    return issues


# --------------------------------------------------------------------------------------
# Image preprocessing
# --------------------------------------------------------------------------------------


def _maybe_downscale(file_bytes: bytes, mime: str) -> tuple[bytes, str, str | None]:
    """Shrink an oversized image before the one send to the model.

    Returns (bytes, mime, note). PDFs and already-small images are returned
    untouched. Any failure (Pillow missing, unreadable image) also returns the
    original bytes — downscaling is an optimisation, never a gate on extraction.
    """
    if mime == "application/pdf":
        return file_bytes, mime, None
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(file_bytes))
        long_edge = max(img.size)
        if long_edge <= MAX_IMAGE_EDGE and len(file_bytes) < 1_500_000:
            return file_bytes, mime, None

        scale = MAX_IMAGE_EDGE / long_edge if long_edge > MAX_IMAGE_EDGE else 1.0
        if scale < 1.0:
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        out = buf.getvalue()
        if len(out) >= len(file_bytes):  # recompression didn't help; keep the original
            return file_bytes, mime, None
        note = (
            f"Image downscaled {len(file_bytes)//1024} KB -> {len(out)//1024} KB "
            f"({long_edge}px -> {max(img.size)}px long edge) before the model call."
        )
        return out, "image/jpeg", note
    except Exception:
        return file_bytes, mime, None


# --------------------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------------------


def extract(file_bytes: bytes, filename: str) -> VisionResult:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime = SUPPORTED_MIME.get(ext)
    doc_id = f"DOC-{uuid.uuid4().hex[:8].upper()}"

    if mime is None:
        return VisionResult(
            doc_id=doc_id,
            filename=filename,
            doc_type="unknown",
            doc_type_confidence=0.0,
            failed=True,
            error=f"Unsupported file type '.{ext}'. Supported: {', '.join(SUPPORTED_MIME)}.",
        )

    if demo_mode():
        return mock.vision_extract(filename, doc_id)

    result = VisionResult(
        doc_id=doc_id, filename=filename, doc_type="unknown", doc_type_confidence=0.0
    )

    file_bytes, mime, downscale_note = _maybe_downscale(file_bytes, mime)
    if downscale_note:
        result.trace.append({"stage": "preprocess", "status": "ok", "detail": downscale_note})

    # --- pass 1: classify + extract the full field superset in a single call ----------
    # This is the quota win: a clean, confidently-classified document is finished in
    # one model call. A second type-specific pass fires only when classification was
    # uncertain (see below), and the repair pass only when a required field is missing.
    try:
        first = _extract(file_bytes, mime, ALL_FIELDS)
    except LLMUnavailable as exc:
        result.failed = True
        result.error = str(exc)
        result.trace.append({"stage": "extractor", "status": "failed", "detail": str(exc)})
        return result

    result.model = first.model
    data = first.data
    doc_type = str(data.get("doc_type") or "unknown").lower()
    if doc_type not in FIELDS_BY_TYPE:
        doc_type = "unknown"
    result.doc_type = doc_type
    result.doc_type_confidence = float(data.get("doc_type_confidence") or 0.0)
    result.trace.append(
        {
            "stage": "classifier",
            "status": "ok",
            "detail": f"Classified as {doc_type} (confidence {result.doc_type_confidence:.2f})",
            "ms": first.latency_ms,
        }
    )
    result.trace.append(
        {
            "stage": "extractor",
            "status": "ok",
            "detail": (
                f"Single-pass extraction of {len(data.get('fields') or [])} fields "
                f"(superset of {len(ALL_FIELDS)})."
            ),
        }
    )

    specs = FIELDS_BY_TYPE[doc_type]
    spec_names = [n for n, _, _ in specs]

    # --- pass 2 (conditional): re-extract type-specific ONLY if classification was shaky
    needs_second_pass = doc_type != "unknown" and result.doc_type_confidence < HIGH
    if needs_second_pass:
        try:
            second = _extract(file_bytes, mime, specs)
            data = second.data
            result.trace.append(
                {
                    "stage": "extractor",
                    "status": "ok",
                    "detail": (
                        f"Low classification confidence ({result.doc_type_confidence:.2f}) — "
                        f"ran a focused {doc_type} pass to confirm."
                    ),
                    "ms": second.latency_ms,
                }
            )
        except LLMUnavailable as exc:
            result.trace.append(
                {"stage": "extractor", "status": "retry", "detail": f"Type-specific pass failed: {exc}"}
            )
    else:
        result.trace.append(
            {
                "stage": "optimizer",
                "status": "skipped",
                "detail": (
                    "Type-specific second pass skipped — classification was confident, "
                    "so one call was enough. (Saves an API request.)"
                ),
            }
        )

    raw = {
        str(f.get("name")): f
        for f in (data.get("fields") or [])
        if isinstance(f, dict) and f.get("name")
    }
    fields: list[dict[str, Any]] = []
    for name, desc, required in specs:
        src = raw.get(name, {})
        fields.append(
            {
                "name": name,
                "description": desc,
                "required": required,
                "value": str(src.get("value") or "").strip(),
                "confidence": float(src.get("confidence") or 0.0),
                "evidence": str(src.get("evidence") or "").strip(),
                "needs_review": False,
                "edited_by_user": False,
            }
        )

    # --- repair pass for missing required fields --------------------------------------
    missing = [f["name"] for f in fields if f["required"] and not f["value"]]
    if missing:
        try:
            repair = _extract(file_bytes, mime, specs, focus=missing)
            found = {
                str(f.get("name")): f
                for f in (repair.data.get("fields") or [])
                if isinstance(f, dict)
            }
            recovered = []
            for f in fields:
                src = found.get(f["name"])
                if f["name"] in missing and src and str(src.get("value") or "").strip():
                    f["value"] = str(src["value"]).strip()
                    # A value only found on a second look never counts as high confidence.
                    f["confidence"] = min(float(src.get("confidence") or 0.0), 0.75)
                    f["evidence"] = str(src.get("evidence") or "").strip()
                    f["needs_review"] = True
                    recovered.append(f["name"])
            result.trace.append(
                {
                    "stage": "repair",
                    "status": "ok" if recovered else "failed",
                    "detail": (
                        f"Second pass recovered: {', '.join(recovered)} (flagged for review)."
                        if recovered
                        else f"Second pass still could not find: {', '.join(missing)}."
                    ),
                    "ms": repair.latency_ms,
                }
            )
        except LLMUnavailable as exc:
            result.trace.append({"stage": "repair", "status": "failed", "detail": str(exc)})

    # --- deterministic verification ----------------------------------------------------
    result.issues = verify_fields(doc_type, fields)
    result.fields = fields
    result.trace.append(
        {
            "stage": "verifier",
            "status": "ok" if not result.issues else "failed",
            "detail": (
                "All rule checks passed."
                if not result.issues
                else f"{len(result.issues)} rule check(s) failed; affected fields flagged for review."
            ),
        }
    )
    if result.doc_type_confidence < HIGH and doc_type != "unknown":
        result.issues.append(
            f"Document type was identified as {doc_type} with only "
            f"{result.doc_type_confidence:.0%} confidence — confirm the type before storing."
        )
    if doc_type == "unknown":
        result.issues.append(
            "Document type could not be determined. Only the common field set was attempted."
        )

    # Field-set names are handy for the UI's ordering.
    result.trace.append(
        {"stage": "schema", "status": "ok", "detail": f"Field set: {', '.join(spec_names)}"}
    )
    return result

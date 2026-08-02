"""Agent V — Part 2, the SU → CG document verification loop.

An SU email lands in a watched folder; a reviewed, ready-to-send reply comes out.

    TRIGGER     poll the simulated mailbox. A new envelope (or bare document)
                activates the agent. Idempotent — an email is processed once.
    EXTRACTOR   Part 1's vision agent, called unmodified. Perception only.
    COMPARATOR  deterministic checks against the customer's written rule set.
                No model in this stage: a verdict must be auditable, so it is a
                rule table, not a judgment call. Per field: match / mismatch /
                uncertain / missing. Uncertain is NEVER a pass.
    DRAFTER     the reply email, rendered from the check table. Because the text
                is generated from recorded verdicts, the email cannot claim
                anything the comparator did not find.

The agent stops there. It has no send capability at all — the only send action
in the system is the CG validator's button in the UI, which calls `cg_send`.

Everything is stored the moment it happens (`verifications`,
`verification_checks`, `su_emails`), which is what makes the queue visible and
the audit trail queryable via the Part 1 analytics layer.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents import config, db, vision_agent
from agents.vision_agent import MEDIUM, VisionResult

RULES_PATH = config.PROJECT_ROOT / "rules" / "sunpeak_foods.json"
SAMPLE_EMAILS_DIR = config.PROJECT_ROOT / "sample_emails"

ENVELOPE_SUFFIX = ".json"


# --------------------------------------------------------------------------------------
# Data shapes
# --------------------------------------------------------------------------------------


@dataclass
class SUEmail:
    email_id: str
    received_at: str
    from_addr: str
    subject: str
    body: str
    attachments: list[Path]


@dataclass
class VerificationResult:
    verification_id: str
    email: SUEmail
    extraction: VisionResult | None
    checks: list[dict[str, Any]] = field(default_factory=list)
    verdict: str = "failed"  # clean | amend | failed
    draft_subject: str = ""
    draft_body: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def count(self, verdict: str) -> int:
        return sum(1 for c in self.checks if c["verdict"] == verdict)


def load_rules() -> dict[str, Any]:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------
# TRIGGER — the simulated mailbox
# --------------------------------------------------------------------------------------


def _email_id_for(path: Path) -> str:
    return "EMAIL-" + re.sub(r"[^A-Za-z0-9]+", "-", path.stem).strip("-").upper()


def _resolve_attachment(raw: str, inbox: Path) -> Path | None:
    """Envelope attachment paths may be project-relative, inbox-relative or absolute."""
    for candidate in (Path(raw), config.PROJECT_ROOT / raw, inbox / raw):
        if candidate.is_file():
            return candidate
    return None


def _load_envelope(path: Path) -> SUEmail:
    data = json.loads(path.read_text(encoding="utf-8"))
    inbox = path.parent
    attachments = [
        resolved
        for raw in (data.get("attachments") or [])
        if (resolved := _resolve_attachment(str(raw), inbox)) is not None
    ]
    return SUEmail(
        email_id=_email_id_for(path),
        # The audit clock starts when the mailbox first sees the email, not at the
        # envelope's claimed sent time — turnaround must never go negative because
        # a supplier's clock (or a demo fixture) disagrees with ours.
        received_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        from_addr=str(data.get("from") or "unknown@supplier"),
        subject=str(data.get("subject") or path.stem),
        body=str(data.get("body") or ""),
        attachments=attachments,
    )


def _bare_document_email(path: Path) -> SUEmail:
    """A document dropped into the mailbox with no covering note is still an email."""
    return SUEmail(
        email_id=_email_id_for(path),
        received_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        from_addr="unknown@supplier",
        subject=f"(no covering email) {path.name}",
        body="",
        attachments=[path],
    )


def poll_inbox() -> list[SUEmail]:
    """Return SU emails that have not been seen before, oldest first.

    This is the mocked plumbing the brief allows: in production the same function
    would be fed by an IMAP/Graph webhook. The logic that matters — new-message
    detection, idempotency, activation — is real.
    """
    inbox = config.su_inbox()
    new: list[SUEmail] = []
    for path in sorted(inbox.iterdir(), key=lambda p: p.stat().st_mtime):
        if not path.is_file():
            continue
        if path.suffix.lower() == ENVELOPE_SUFFIX:
            email = _load_envelope(path)
        elif path.suffix.lower() in config.SUPPORTED_SUFFIXES:
            email = _bare_document_email(path)
        else:
            continue
        if not db.email_seen(email.email_id):
            new.append(email)
    return new


def list_sample_emails() -> list[Path]:
    if not SAMPLE_EMAILS_DIR.exists():
        return []
    return sorted(SAMPLE_EMAILS_DIR.glob("*.json"))


def simulate_email_arrival(sample: Path) -> Path:
    """Copy a bundled sample envelope into the watched folder — 'an SU email arrives'."""
    target = config.su_inbox() / sample.name
    target.write_bytes(sample.read_bytes())
    return target


# --------------------------------------------------------------------------------------
# COMPARATOR — deterministic, auditable, no model
# --------------------------------------------------------------------------------------


def _norm_name(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", value.lower()).strip()


def _norm_digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _rule_passes(check: str, expected: str, found: str) -> bool:
    if check == "name":
        return _norm_name(found) == _norm_name(expected)
    if check == "digits":
        return _norm_digits(found) == _norm_digits(expected)
    if check == "equals":
        return found.strip().upper() == expected.strip().upper()
    if check == "contains":
        return expected.strip().lower() in found.lower()
    return False  # unknown rule type: fail loudly rather than pass silently


def compare(extraction: VisionResult, ruleset: dict[str, Any]) -> list[dict[str, Any]]:
    """Field-by-field verdicts: the customer's rules first, then required-presence.

    The trust core is the verdict ladder. A value that fails the confidence bar is
    `uncertain` even if it happens to satisfy the rule — the agent does not know
    what is printed on the page well enough to say 'match', so it does not.
    """
    by_name = {f["name"]: f for f in extraction.fields}
    checks: list[dict[str, Any]] = []

    def field_state(name: str) -> tuple[str, float, str, bool]:
        f = by_name.get(name) or {}
        return (
            str(f.get("value") or "").strip(),
            float(f.get("confidence") or 0.0),
            str(f.get("evidence") or "").strip(),
            bool(f.get("needs_review")),
        )

    for rule in ruleset["rules"]:
        applies = rule.get("applies_to") or ["*"]
        if "*" not in applies and extraction.doc_type not in applies:
            continue
        found, conf, evidence, needs_review = field_state(rule["field"])
        if not found:
            verdict = "missing"
            detail = "Not found on the document. The customer requires it."
        elif conf < MEDIUM or needs_review or not evidence:
            verdict = "uncertain"
            detail = (
                f"Extracted at {conf:.0%} confidence"
                + ("" if evidence else " with no quotable evidence")
                + " — below the bar to judge. A human must confirm this against the document."
            )
        elif _rule_passes(rule["check"], rule["expected"], found):
            verdict = "match"
            detail = ""
        else:
            verdict = "mismatch"
            detail = f"Found '{found}' — the customer requires '{rule['expected']}'."
        checks.append(
            {
                "field": rule["field"],
                "label": rule["label"],
                "expected": rule["expected"],
                "found": found,
                "verdict": verdict,
                "confidence": conf,
                "evidence": evidence,
                "detail": detail,
            }
        )

    covered = {c["field"] for c in checks}
    specs = vision_agent.FIELDS_BY_TYPE.get(
        extraction.doc_type, vision_agent.FIELDS_BY_TYPE["unknown"]
    )
    for name, _desc, required in specs:
        if not required or name in covered:
            continue
        found, conf, evidence, needs_review = field_state(name)
        if not found:
            verdict = "missing"
            detail = f"Required on a {extraction.doc_type.replace('_', ' ')} but not found."
        elif conf < MEDIUM or needs_review or not evidence:
            verdict = "uncertain"
            detail = f"Present but extracted at {conf:.0%} confidence — needs human confirmation."
        else:
            verdict = "match"
            detail = ""
        checks.append(
            {
                "field": name,
                "label": f"Required field on every {extraction.doc_type.replace('_', ' ')}",
                "expected": "present on document",
                "found": found,
                "verdict": verdict,
                "confidence": conf,
                "evidence": evidence,
                "detail": detail,
            }
        )
    return checks


# --------------------------------------------------------------------------------------
# DRAFTER — rendered from the check table, so it cannot invent a claim
# --------------------------------------------------------------------------------------


def _su_display_name(from_addr: str) -> str:
    local = from_addr.split("@")[0].replace(".", " ").replace("_", " ").title()
    return local or "Documentation Team"


def draft_reply(
    email: SUEmail, extraction: VisionResult, checks: list[dict[str, Any]],
    verdict: str, ruleset: dict[str, Any],
) -> tuple[str, str]:
    customer = ruleset["customer"]
    doc_label = extraction.doc_type.replace("_", " ")
    filename = extraction.filename

    if verdict == "clean":
        subject = f"APPROVED — {filename} verified against {customer} requirements"
        lines = [
            f"Dear {_su_display_name(email.from_addr)},",
            "",
            f"The {doc_label} you sent ({filename}) has been verified against "
            f"{customer}'s documentation requirements. All {len(checks)} checks passed:",
            "",
        ]
        lines += [f"  • {c['field']}: {c['found']} — matched" for c in checks]
        lines += [
            "",
            f"The document will be forwarded to {customer}. No further action is "
            "needed on this document.",
        ]
    else:
        problems = [c for c in checks if c["verdict"] != "match"]
        subject = f"AMENDMENT REQUIRED — {filename}: {len(problems)} issue(s) to fix"
        lines = [
            f"Dear {_su_display_name(email.from_addr)},",
            "",
            f"We checked the {doc_label} you sent ({filename}) against {customer}'s "
            f"documentation requirements. {len(problems)} of {len(checks)} checks "
            "need your attention before we can forward the documents:",
            "",
        ]
        for c in problems:
            if c["verdict"] == "mismatch":
                lines.append(
                    f"  • {c['field']} — found: '{c['found']}' | expected: "
                    f"'{c['expected']}'. {c['label']}"
                )
            elif c["verdict"] == "missing":
                lines.append(
                    f"  • {c['field']} — missing from the document. Please add it "
                    "and resend."
                )
            else:  # uncertain
                lines.append(
                    f"  • {c['field']} — we could not read this reliably "
                    f"(best reading: '{c['found'] or '—'}'). Please confirm the value "
                    "or resend a clearer copy."
                )
        lines += [
            "",
            "Please correct the above and reply with the revised document. "
            "Everything else checked out, so one clean resend should close this out.",
        ]

    lines += [
        "",
        "Best regards,",
        "Control Group — Document Validation",
        "",
        "(Draft prepared by the verification agent from the recorded check results. "
        "Reviewed and sent by the CG validator.)",
    ]
    return subject, "\n".join(lines)


# --------------------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------------------


def process_attachment(email: SUEmail, attachment: Path) -> VerificationResult:
    verification_id = f"VER-{uuid.uuid4().hex[:8].upper()}"
    ruleset = load_rules()
    result = VerificationResult(verification_id=verification_id, email=email, extraction=None)
    result.trace.append(
        {
            "stage": "trigger",
            "status": "ok",
            "detail": (
                f"Email {email.email_id} from {email.from_addr} — attachment "
                f"{attachment.name} activated the agent."
            ),
        }
    )

    extraction = vision_agent.extract(attachment.read_bytes(), attachment.name)
    result.extraction = extraction
    if extraction.failed:
        result.verdict = "failed"
        result.error = extraction.error
        result.trace.append(
            {"stage": "extractor", "status": "failed", "detail": extraction.error or ""}
        )
        db.store_verification(
            verification_id=verification_id, email_id=email.email_id,
            doc_id=extraction.doc_id, filename=attachment.name,
            doc_type=extraction.doc_type, customer=ruleset["customer"],
            rules_version=ruleset["version"], verdict="failed", checks=[],
            draft_subject="", draft_body="", extraction_fields=[],
            error=extraction.error,
        )
        return result

    result.trace.append(
        {
            "stage": "extractor",
            "status": "ok",
            "detail": (
                f"Part 1 vision agent: {extraction.doc_type} "
                f"({extraction.doc_type_confidence:.0%}), {len(extraction.fields)} fields"
                + (" — replayed, demo mode" if extraction.demo_mode else "")
            ),
        }
    )

    checks = compare(extraction, ruleset)
    result.checks = checks
    mismatched = sum(1 for c in checks if c["verdict"] == "mismatch")
    uncertain = sum(1 for c in checks if c["verdict"] == "uncertain")
    missing = sum(1 for c in checks if c["verdict"] == "missing")
    # The aggregation rule the trust story hangs on: clean means every check is a
    # confident match. Uncertain counts against approval exactly like a mismatch.
    result.verdict = "clean" if (mismatched + uncertain + missing) == 0 else "amend"
    result.trace.append(
        {
            "stage": "comparator",
            "status": "ok" if result.verdict == "clean" else "failed",
            "detail": (
                f"{len(checks)} deterministic checks against rule set "
                f"{ruleset['version']}: {mismatched} mismatch, {missing} missing, "
                f"{uncertain} uncertain. No model was involved in these verdicts."
            ),
        }
    )

    result.draft_subject, result.draft_body = draft_reply(
        email, extraction, checks, result.verdict, ruleset
    )
    result.trace.append(
        {
            "stage": "drafter",
            "status": "ok",
            "detail": (
                ("Approval draft" if result.verdict == "clean" else "Amendment draft")
                + " rendered from the check table. The agent cannot send it — "
                "only the CG validator can."
            ),
        }
    )

    db.store_verification(
        verification_id=verification_id, email_id=email.email_id,
        doc_id=extraction.doc_id, filename=extraction.filename,
        doc_type=extraction.doc_type, customer=ruleset["customer"],
        rules_version=ruleset["version"], verdict=result.verdict, checks=checks,
        draft_subject=result.draft_subject, draft_body=result.draft_body,
        extraction_fields=extraction.fields,
    )
    return result


def check_inbox_and_process() -> list[VerificationResult]:
    """The full loop: poll → record → extract → compare → draft → store."""
    results: list[VerificationResult] = []
    for email in poll_inbox():
        for attachment in email.attachments or []:
            db.record_email(
                email_id=email.email_id, received_at=email.received_at,
                from_addr=email.from_addr, subject=email.subject, body=email.body,
                attachment=attachment.name,
            )
            outcome = process_attachment(email, attachment)
            results.append(outcome)
            db.mark_email_status(
                email.email_id, "failed" if outcome.verdict == "failed" else "verified"
            )
        if not email.attachments:
            db.record_email(
                email_id=email.email_id, received_at=email.received_at,
                from_addr=email.from_addr, subject=email.subject, body=email.body,
                attachment="(none found)", status="failed",
            )
    return results


# --------------------------------------------------------------------------------------
# CG send — the human's action, not the agent's
# --------------------------------------------------------------------------------------


def cg_send(verification_id: str, final_subject: str, final_body: str) -> dict[str, Any]:
    """Executed only from the CG validator's Send button.

    Marks the action, writes the sent reply to the outbox folder, and — on an
    approval — stores the extracted document as `confirmed`, which is the moment
    it becomes queryable in `v_trade_documents`. An amended document is stored as
    `rejected`: kept for the audit trail, excluded from analytics answers.
    """
    record = db.get_verification(verification_id)
    if record is None:
        raise ValueError(f"Unknown verification {verification_id}")
    if record["verdict"] == "failed":
        raise ValueError("A failed verification has no reply to send.")

    action = "approval_sent" if record["verdict"] == "clean" else "amendment_sent"
    edited = (
        final_subject.strip() != (record["draft_subject"] or "").strip()
        or final_body.strip() != (record["draft_body"] or "").strip()
    )
    db.mark_cg_action(
        verification_id=verification_id, action=action,
        final_subject=final_subject, final_body=final_body, edited=edited,
    )

    fields = json.loads(record["extraction_json"] or "[]")
    values = [f.get("confidence") or 0.0 for f in fields if f.get("value")]
    overall = round(sum(values) / len(values), 3) if values else 0.0
    db.store_document(
        doc_id=record["doc_id"], filename=record["filename"],
        doc_type=record["doc_type"], doc_type_confidence=None, fields=fields,
        overall_confidence=overall, model="via verification agent",
        trace=[], status="confirmed" if action == "approval_sent" else "rejected",
    )

    to_addr = (record.get("email") or {}).get("from_addr") or "supplier"
    outbox_file = config.cg_outbox() / f"{verification_id}_reply.txt"
    outbox_file.write_text(
        f"To: {to_addr}\nFrom: cg-desk@gocomet.example\n"
        f"Subject: {final_subject}\nSent-by: CG validator (human)\n"
        f"Edited-before-send: {'yes' if edited else 'no'}\n\n{final_body}\n",
        encoding="utf-8",
    )
    return {"action": action, "edited": edited, "outbox_file": str(outbox_file)}

"""Flow V — the SU → CG verification loop.

An SU email arrives → the agent extracts the attached trade document, compares it
against the customer rule set, flags every discrepancy and drafts the reply. The CG
validator reviews and sends. The agent never sends.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from agents import config, db, verification_agent
from ui.common import STATUS_BADGE, VERDICT_BADGE, confidence_badge, format_age, render_trace


def render() -> None:
    ruleset = verification_agent.load_rules()
    st.markdown("#### CG verification inbox")
    st.caption(
        f"Customer rule set: **{ruleset['customer']}** · v{ruleset['version']} · "
        "When an SU email arrives, the agent reads the attached document, compares "
        "every field against the rules, and drafts the reply. **You review. You "
        "send. The agent has no send button.**"
    )

    ctrl_check, ctrl_pick, ctrl_sim = st.columns([1.2, 2.4, 1.4])
    with ctrl_check:
        check_now = st.button("📥 Check inbox now", type="primary", width="stretch")
    samples = verification_agent.list_sample_emails()
    with ctrl_pick:
        chosen_sample = st.selectbox(
            "Sample SU email",
            [p.name for p in samples],
            label_visibility="collapsed",
            help="Bundled SU emails covering a clean pass, an HS-code mismatch and "
                 "an incomplete document.",
        ) if samples else None
    with ctrl_sim:
        simulate = st.button("✉️ Simulate this SU email arriving", width="stretch")

    if simulate and chosen_sample:
        picked = next(p for p in samples if p.name == chosen_sample)
        verification_agent.simulate_email_arrival(picked)
        check_now = True

    if check_now:
        with st.spinner("Trigger → extract → compare → flag → draft…"):
            new_results = verification_agent.check_inbox_and_process()
        for r in new_results:
            st.session_state.verify_traces[r.verification_id] = r.trace
        if new_results:
            st.session_state.selected_verification = new_results[0].verification_id
            st.toast(f"{len(new_results)} new document(s) verified")
        else:
            st.toast("No new SU emails in the inbox.")

    st.caption(
        f"Watched folder: `{config.su_inbox()}` — drop an email envelope (.json) or a "
        "bare PDF/image there and hit *Check inbox now*. The email plumbing is "
        "simulated; the trigger logic is real."
    )

    queue = db.list_verifications()

    # ---- Queue visibility (docs/PRD.md §7, Iteration 2b) ------------------------------
    # "Nobody can see how many documents are pending." The data has always been in
    # v_verifications; this is the UI on top of it.
    if not queue.empty:
        sla = config.queue_sla_minutes()
        now = pd.Timestamp.now(tz="UTC")
        received = pd.to_datetime(queue["received_at"], utc=True)
        queue = queue.assign(age_minutes=(now - received).dt.total_seconds() / 60)
        pending = queue[queue["status"] == "awaiting_cg"]
        breaches = pending[pending["age_minutes"] > sla]

        st.markdown("##### 📋 Pending queue")
        st.caption(
            "The pain named in the PRD — *\"nobody can see how many documents are "
            "pending.\"* This reads straight off the verification audit trail; no "
            "separate tracking needed."
        )
        m1, m2, m3 = st.columns(3)
        m1.metric("Awaiting CG review", len(pending))
        m2.metric(
            "Oldest waiting",
            format_age(pending["age_minutes"].max()) if not pending.empty else "—",
        )
        m3.metric(f"SLA breaches (> {sla}m)", len(breaches))
        if not breaches.empty:
            oldest_first = breaches.sort_values("age_minutes", ascending=False)
            st.warning(
                f"⚠️ {len(breaches)} document(s) past the {sla}-minute "
                "review SLA: "
                + ", ".join(
                    f"{row.filename} ({format_age(row.age_minutes)})"
                    for row in oldest_first.itertuples()
                )
            )
        elif not pending.empty:
            st.success(f"All {len(pending)} pending document(s) are within SLA.")

    # ---- State 1 · Incoming ----------------------------------------------------------
    st.markdown("##### 📨 Incoming")
    if queue.empty:
        st.info(
            "No SU emails yet. Pick a sample above and click **Simulate this SU "
            "email arriving** — the agent will wake up, read the attachment and "
            "verify it while you watch."
        )
    else:
        oldest_pending_first = st.checkbox(
            "Sort by queue priority (oldest pending first)",
            value=not queue[queue["status"] == "awaiting_cg"].empty,
            help="Off: most recently received first. On: whatever's waited longest "
                 "for CG review floats to the top, same as a real work queue.",
        )
        if oldest_pending_first:
            sorted_queue = queue.assign(
                _pending_first=(queue["status"] != "awaiting_cg").astype(int)
            ).sort_values(
                by=["_pending_first", "age_minutes"], ascending=[True, False], kind="stable"
            )
        else:
            sorted_queue = queue
        # Pending rows show live wait time; actioned rows show final turnaround —
        # age_minutes keeps ticking upward after send, which isn't what "took" means.
        display_minutes = sorted_queue["age_minutes"].where(
            sorted_queue["status"] == "awaiting_cg", sorted_queue["turnaround_minutes"]
        )
        incoming = sorted_queue.assign(
            status_badge=sorted_queue["status"].map(STATUS_BADGE).fillna(sorted_queue["status"]),
            verdict_badge=sorted_queue["verdict"].map(
                {"clean": "✅ clean", "amend": "❌ needs amendment", "failed": "🚨 failed"}
            ),
            age_badge=display_minutes.map(format_age),
        )[
            ["verification_id", "received_at", "from_addr", "subject", "filename",
             "verdict_badge", "status_badge", "age_badge"]
        ].rename(
            columns={
                "verification_id": "ID", "received_at": "Received", "from_addr": "From",
                "subject": "Subject", "filename": "Attachment",
                "verdict_badge": "Agent verdict", "status_badge": "Status",
                "age_badge": "Waiting / took",
            }
        )
        st.dataframe(incoming, width="stretch", hide_index=True,
                     height=45 + 35 * min(len(incoming), 5))

        options = sorted_queue["verification_id"].tolist()
        default_index = (
            options.index(st.session_state.selected_verification)
            if st.session_state.selected_verification in options
            else 0
        )
        chosen_ver = st.selectbox(
            "Open a verification",
            options,
            index=default_index,
            format_func=lambda vid: (
                f"{vid} · "
                f"{queue.set_index('verification_id').loc[vid, 'filename']} · "
                f"{queue.set_index('verification_id').loc[vid, 'verdict']}"
            ),
        )
        st.session_state.selected_verification = chosen_ver
        record = db.get_verification(chosen_ver)

        if record is not None:
            _render_verification(chosen_ver, record)

    if st.session_state.last_sent:
        sent = st.session_state.last_sent
        st.success(
            f"Reply sent ({sent['action'].replace('_', ' ')}"
            f"{', edited' if sent['edited'] else ''}) and written to "
            f"`{sent['outbox_file']}`. The verification is now part of the audit "
            "trail — try asking the data lake *“What is the average verification "
            "turnaround time by verdict?”*"
        )
        st.session_state.last_sent = None


def _render_verification(chosen_ver: str, record: dict) -> None:
    """States 2–4 for one verification: result, discrepancy detail, draft reply."""
    checks = record["checks"]
    email_meta = record.get("email") or {}
    st.divider()

    # ---- State 2 · Verification result -----------------------------------------------
    st.markdown("##### 🔎 Verification result")
    if record["verdict"] == "failed":
        st.error(
            f"**The agent could not process this document.** {record['error'] or ''} "
            "Nothing was approved and no reply was drafted — this one needs "
            "the manual path."
        )
    else:
        n_bad = (
            record["checks_mismatched"]
            + record["checks_uncertain"]
            + record["checks_missing"]
        )
        if record["verdict"] == "clean":
            st.success(
                f"**All {record['checks_total']} checks matched** against "
                f"{record['customer']}'s requirements. An approval draft is "
                "ready below — nothing goes out until you send it."
            )
        else:
            st.error(
                f"**{n_bad} of {record['checks_total']} checks need attention** "
                f"({record['checks_mismatched']} mismatch · "
                f"{record['checks_missing']} missing · "
                f"{record['checks_uncertain']} uncertain). An amendment draft "
                "listing each issue is ready below."
            )

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Matched", record["checks_matched"])
        m2.metric("Mismatched", record["checks_mismatched"])
        m3.metric("Missing", record["checks_missing"])
        m4.metric("Uncertain", record["checks_uncertain"])

        check_table = pd.DataFrame(
            [
                {
                    "Field": c["field_name"],
                    "Verdict": VERDICT_BADGE.get(c["verdict"], c["verdict"]),
                    "Found on document": c["found"] or "—",
                    "Customer requires": c["expected"] or "—",
                    "Confidence": confidence_badge(c["confidence"]),
                    "Evidence (quoted)": c["evidence"] or "—",
                }
                for c in checks
            ]
        )
        st.dataframe(check_table, width="stretch", hide_index=True,
                     height=45 + 35 * min(len(check_table), 12))
        st.caption(
            "Verdicts are deterministic rule checks — no model judgment. An "
            "**uncertain** field counts against approval exactly like a "
            "mismatch: the agent never silently approves what it could not read."
        )

        # ---- State 3 · Discrepancy detail --------------------------------------------
        flagged = [c for c in checks if c["verdict"] != "match"]
        if flagged:
            st.markdown("##### 🚩 Discrepancy detail")
            picked_field = st.selectbox(
                "Flagged field",
                [c["field_name"] for c in flagged],
                format_func=lambda name: (
                    f"{name} — "
                    f"{next(c['verdict'] for c in flagged if c['field_name'] == name)}"
                ),
            )
            detail = next(c for c in flagged if c["field_name"] == picked_field)
            d1, d2 = st.columns(2)
            with d1:
                st.markdown("**Found on the document**")
                st.markdown(f"### {detail['found'] or '— nothing —'}")
                st.caption(
                    f"Extraction confidence {confidence_badge(detail['confidence'])} · "
                    f"evidence: “{detail['evidence'] or 'no quotable evidence'}”"
                )
            with d2:
                st.markdown("**What the customer requires**")
                st.markdown(f"### {detail['expected']}")
                st.caption(detail["rule_label"] or "")
            if detail["detail"]:
                st.warning(detail["detail"])

        # ---- State 4 · Draft reply ---------------------------------------------------
        st.markdown("##### ✉️ Draft reply to SU")
        already_sent = record["cg_action"] is not None
        if already_sent:
            st.success(
                f"Sent by the CG validator at {record['cg_actioned_at']} "
                f"({STATUS_BADGE.get(record['cg_action'], record['cg_action'])}"
                f"{', edited before sending' if record['cg_edited'] else ', sent as drafted'})."
            )
            st.text_input("Subject", value=record["final_subject"] or "",
                          disabled=True, key=f"subj_sent_{chosen_ver}")
            st.text_area("Body", value=record["final_body"] or "", height=280,
                         disabled=True, key=f"body_sent_{chosen_ver}")
        else:
            st.caption(
                f"To: **{email_meta.get('from_addr', 'supplier')}** · Drafted from "
                "the check table above — the email cannot claim anything the "
                "comparator did not record. Edit anything, then send."
            )
            subj = st.text_input("Subject", value=record["draft_subject"] or "",
                                 key=f"subj_{chosen_ver}")
            body = st.text_area("Body", value=record["draft_body"] or "",
                                height=280, key=f"body_{chosen_ver}")
            send_col, note_col = st.columns([1, 2.5])
            if send_col.button("📤 Send reply (as CG)", type="primary",
                               key=f"send_{chosen_ver}"):
                outcome = verification_agent.cg_send(chosen_ver, subj, body)
                st.session_state.last_sent = outcome
                st.rerun()
            note_col.caption(
                "This button is the only send in the system, and it is yours. "
                "On approval the document also becomes queryable in the data "
                "lake; on amendment it is kept for audit only."
            )

    trace = st.session_state.verify_traces.get(chosen_ver)
    if trace:
        with st.expander("Agent trace (trigger → extract → compare → draft)"):
            render_trace(trace)
    fields_json = record.get("extraction_json")
    if fields_json:
        stored_fields = json.loads(fields_json)
        if stored_fields:
            with st.expander("Full extraction — vision agent output"):
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Field": f["name"],
                                "Value": f.get("value") or "—",
                                "Confidence": confidence_badge(f.get("confidence")),
                                "Evidence": f.get("evidence") or "—",
                            }
                            for f in stored_fields
                        ]
                    ),
                    width="stretch",
                    hide_index=True,
                )

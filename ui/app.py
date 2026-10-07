"""
Streamlit UI for the Agentic Healthcare Assistant (Part 2, steps 7-8).

Run with:
    streamlit run ui/app.py
"""

import os
import sys
import json

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd

from config import require_keys
from data import patient_db as db
from agents.crew import run_assistant
from monitoring.logging_utils import read_events
from evaluation.eval_harness import grade_response, summarize_metrics, log_tool_outcome
from evaluation.symptom_evaluator import run_symptom_analysis_suite, summarize_suite_results
from monitoring.monitor import summarize_latency, throughput_over_time
from workflows.prescription_refill import process_refill_request
from evaluation.feedback_evaluator import run_feedback_eval_suite, summarize_feedback_eval

st.set_page_config(page_title="Agentic Healthcare Assistant", layout="wide")


@st.cache_resource
def _init_db_once():
    """Schema creation + migration introspection (PRAGMA table_info, ALTER
    TABLE checks) is one-time setup work, not something that needs to
    re-run on every Streamlit interaction -- Streamlit reruns this whole
    script top-to-bottom on every widget interaction, so without caching
    this was re-running full schema/migration checks on every keystroke
    and button click. st.cache_resource runs this once per server
    process and shares the result across all sessions, which is exactly
    right for global one-time setup like this."""
    db.init_db(seed=True)


_init_db_once()

st.title("🩺 Agentic Healthcare Assistant")
st.caption("Agentic AI + RAG for appointment booking, medical records, and disease information")
st.caption("💡 Runs on a free local LLM (no API key) — the assistant tabs below load the model on first use.")

missing = require_keys("CALCOM_API_KEY")
if missing:
    st.warning(
        f"Missing environment variables: {', '.join(missing)}. "
        "Set them in a `.env` file (see `.env.example`) before using live features. "
        "The Patient/Doctor record views below still work without them."
    )

try:
    from agents.llm import is_model_downloaded
    from config import LOCAL_LLM_MODEL
    if not is_model_downloaded():
        st.info(
            f"The local model ({LOCAL_LLM_MODEL}) hasn't been downloaded yet — the first "
            "message in the Assistant Chat tab will trigger a one-time download "
            "(size varies by model) before it can respond. Later runs are instant."
        )
except ImportError:
    pass  # local LLM deps not installed yet — chat tab will surface a clearer error when used

tab_chat, tab_patient, tab_doctor, tab_appts, tab_rx, tab_feedback, tab_eval, tab_monitor, tab_logs = st.tabs(
    ["💬 Assistant Chat", "🧑‍🦳 Patient View", "🩺 Doctor View", "📅 Appointments", "💊 Prescriptions",
     "📋 Feedback & Support", "📊 Evaluation", "📈 Monitoring", "🧠 Memory & Logs"]
)

# ---------------- Assistant Chat ----------------
with tab_chat:
    st.subheader("Talk to the Agentic Assistant")
    st.markdown(
        "Try: *\"My 70-year-old father has chronic kidney disease. I want to "
        "book a nephrologist for him. Also, can you summarize latest treatment methods?\"*"
    )

    patients = db.list_patients()
    patient_options = {"(none / new patient)": None}
    patient_options.update({f"{p['full_name']} (id={p['patient_id']})": p["patient_id"] for p in patients})
    selected_label = st.selectbox("Patient context (optional hint for memory retrieval)", list(patient_options.keys()))
    patient_id_hint = patient_options[selected_label]

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    for turn in st.session_state.chat_history:
        with st.chat_message(turn["role"]):
            st.write(turn["content"])

    user_query = st.chat_input("Describe what you need (booking, records, disease info)...")
    if user_query:
        st.session_state.chat_history.append({"role": "user", "content": user_query})
        with st.chat_message("user"):
            st.write(user_query)

        with st.chat_message("assistant"):
            with st.spinner("Planning and executing..."):
                try:
                    result = run_assistant(user_query, patient_id_hint=patient_id_hint)
                    response_text = result.get("final_response") or "(No response generated.)"
                    st.write(response_text)

                    with st.expander("🔍 Plan & tool trace"):
                        st.json(result.get("plan", {}))
                        for t in result.get("tool_trace", []):
                            st.write(f"**{t['tool']}** — {t.get('status')}")
                            st.code(json.dumps(t.get("args", {}), indent=2))
                            if "result" in t:
                                st.caption(t["result"][:300])
                            log_tool_outcome(module=t["tool"], success=(t.get("status") == "success"))

                    st.session_state.chat_history.append({"role": "assistant", "content": response_text})

                    with st.expander("📊 Grade this response"):
                        if st.button("Run LLM-as-judge evaluation", key=f"eval_{len(st.session_state.chat_history)}"):
                            grade = grade_response(user_query, response_text, module="assistant_response")
                            st.json(grade)

                except Exception as e:
                    st.error(f"Error: {e}")

# ---------------- Patient View ----------------
with tab_patient:
    st.subheader("Register / View Patient Records")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Register a patient**")
        with st.form("add_patient_form"):
            name = st.text_input("Full name")
            age = st.number_input("Age", min_value=0, max_value=120, value=40)
            sex = st.selectbox("Sex", ["M", "F", "Other"])
            relation = st.text_input("Relation to requester", value="self")
            submitted = st.form_submit_button("Register patient")
            if submitted and name:
                pid = db.add_patient(name, int(age), sex, relation)
                st.success(f"Registered {name} with patient_id={pid}")
                st.rerun()

    patients = db.list_patients()

    with col2:
        st.markdown("**All patients**")
        if patients:
            st.dataframe(pd.DataFrame(patients), use_container_width=True)
        else:
            st.info("No patients yet.")

    st.divider()
    st.markdown("**Add a history entry**")
    if patients:
        pid_map = {f"{p['full_name']} (id={p['patient_id']})": p["patient_id"] for p in patients}
        chosen = st.selectbox("Patient", list(pid_map.keys()), key="hist_patient")
        entry_type = st.selectbox("Entry type", ["diagnosis", "treatment", "alert", "note"])
        is_structured = st.checkbox("Structured (JSON)", value=(entry_type != "note"))
        content = st.text_area("Content", placeholder='e.g. {"condition": "Hypertension"} or free text note')
        if st.button("Add entry"):
            payload = content
            if is_structured:
                try:
                    payload = json.loads(content)
                except json.JSONDecodeError:
                    st.error("Structured content must be valid JSON.")
                    payload = None
            if payload is not None:
                db.add_history_entry(pid_map[chosen], entry_type, payload, is_structured)
                st.success("Entry added.")
                st.rerun()

# ---------------- Doctor View ----------------
with tab_doctor:
    st.subheader("Doctor / Clinician View")
    patients = db.list_patients()
    if not patients:
        st.info("No patients registered yet.")
    else:
        pid_map = {f"{p['full_name']} (id={p['patient_id']})": p["patient_id"] for p in patients}
        chosen = st.selectbox("Select patient", list(pid_map.keys()), key="doc_patient")
        pid = pid_map[chosen]
        history = db.get_history(pid)
        if not history:
            st.info("No history recorded.")
        else:
            # Preferred display order; any other entry_type found in the data
            # (e.g. imported types like 'lab_result', 'visit', 'clinical_note')
            # is appended automatically so nothing imported gets hidden.
            preferred_order = ["diagnosis", "treatment", "alert", "visit", "lab_result",
                                "clinical_note", "emergency_contact", "note"]
            present_types = list(dict.fromkeys(
                [t for t in preferred_order if any(e["entry_type"] == t for e in history)]
                + sorted({e["entry_type"] for e in history} - set(preferred_order))
            ))
            for entry_type in present_types:
                entries = [e for e in history if e["entry_type"] == entry_type]
                if not entries:
                    continue
                st.markdown(f"**{entry_type.replace('_', ' ').capitalize()}s**")
                for e in entries:
                    content = e["content"]
                    if isinstance(content, str) and len(content) > 400:
                        with st.expander(f"{content[:120]}...  ({e['created_at'][:10]})"):
                            st.text(content)
                    else:
                        st.write(f"- {content}  \n  _{e['created_at'][:10]}_")

# ---------------- Appointments ----------------
with tab_appts:
    st.subheader("Real-time Appointment Tracking")
    appts = db.list_appointments()
    if appts:
        st.dataframe(pd.DataFrame(appts), use_container_width=True)
    else:
        st.info("No appointments booked yet. Use the Assistant Chat tab to book one.")

# ---------------- Prescriptions (refill automation pipeline) ----------------
with tab_rx:
    st.subheader("💊 Prescription Refill Automation")
    st.caption(
        "Identity verification → clinical rule engine → golden-path eRx queue injection, "
        "or context pre-assembly + routing to provider review."
    )

    rx_sim, rx_portal, rx_erx, rx_review = st.tabs(
        ["▶ Simulate a refill request", "🌐 Portal Integration (JWT + async)",
         "✅ eRx Queue (golden path)", "🚩 Provider Review Queue (flagged)"]
    )

    with rx_sim:
        patients = db.list_patients()
        if not patients:
            st.info("No patients registered yet.")
        else:
            pid_map = {f"{p['full_name']} (id={p['patient_id']})": p for p in patients}
            chosen_label = st.selectbox("Patient", list(pid_map.keys()), key="rx_patient")
            chosen_patient = pid_map[chosen_label]

            prescriptions = db.list_prescriptions(chosen_patient["patient_id"])
            if not prescriptions:
                st.info("This patient has no prescriptions on file.")
            else:
                rx_map = {
                    f"{rx['medication_name']} — {rx['remaining_refills']} refills left"
                    + (" ⚠ controlled" if rx["is_controlled_substance"] else "")
                    + (" 🧪 lab check required" if rx["requires_lab_check"] else ""): rx
                    for rx in prescriptions
                }
                chosen_rx_label = st.selectbox("Prescription", list(rx_map.keys()), key="rx_med")
                chosen_rx = rx_map[chosen_rx_label]

                dob_input = st.text_input(
                    "Date of birth (identity verification, YYYY-MM-DD)",
                    value=chosen_patient.get("dob", ""),
                    help="Pre-filled with the DOB on file for this demo — try changing it to see the identity-check failure path.",
                )

                with st.expander("➕ Additional verification factors (optional)"):
                    st.caption(
                        "If provided, these must ALSO match on file — use them for stronger "
                        "identity assurance. Leave blank to verify with DOB only (original behavior)."
                    )
                    address_input = st.text_input(
                        "Home address",
                        value="",
                        help=f"On file: {chosen_patient.get('address', '(none on file)')}",
                    )
                    ssn_input = st.text_input(
                        "Last 4 digits of SSN", value="", max_chars=4,
                        help=f"On file: {chosen_patient.get('ssn_last4', '(none on file)')}",
                    )

                pharmacy_id = st.text_input("Pharmacy ID", value="PHARM-001")

                if st.button("▶ Process refill request"):
                    with st.spinner("Running identity verification and clinical rule engine..."):
                        result = process_refill_request(
                            chosen_patient["patient_id"], dob_input,
                            chosen_rx["prescription_id"], pharmacy_id,
                            address=address_input or None,
                            ssn_last4=ssn_input or None,
                        )
                    if result["status"] == "processed":
                        st.success(result["message"])
                    elif result["status"] == "routed_to_review":
                        st.warning(result["message"])
                        st.caption(f"Failed rule: {result['reason']} — {result['detail']}")
                    else:
                        st.error(result["message"])
                    with st.expander("Raw pipeline result"):
                        st.json(result)

    with rx_portal:
        st.markdown(
            "Simulates the **web portal integration path**: a signed JWT stands in for the "
            "patient's session (no DOB re-entry), the request gets an instant tracking ID, "
            "and processing happens asynchronously — mirrors what `POST /api/v1/refills` does "
            "when a real portal calls it."
        )
        try:
            from api.portal_auth import create_test_token, decode_portal_token, resolve_patient, PortalAuthError
            portal_auth_ready = True
        except ImportError as e:
            portal_auth_ready = False
            st.warning(f"Portal auth module unavailable: {e}")

        if portal_auth_ready:
            patients = db.list_patients()
            pid_map2 = {f"{p['full_name']} ({p.get('external_id', 'no id')})": p for p in patients}
            chosen_label2 = st.selectbox("Patient (portal identity)", list(pid_map2.keys()), key="portal_patient")
            chosen_patient2 = pid_map2[chosen_label2]

            if st.button("🔑 Mint a test portal JWT for this patient"):
                try:
                    token = create_test_token(
                        chosen_patient2.get("external_id", ""),
                        scopes=["prescriptions:read", "refills:write"],
                    )
                    st.session_state["portal_token"] = token
                    st.session_state["portal_token_patient"] = chosen_patient2
                    st.success("Token minted (this is what the real portal would sign and send).")
                except PortalAuthError as e:
                    st.error(str(e))

            if st.session_state.get("portal_token"):
                st.code(st.session_state["portal_token"], language=None)

                if st.button("✅ Verify token (simulates the auth dependency)"):
                    try:
                        payload = decode_portal_token(st.session_state["portal_token"])
                        patient = resolve_patient(payload)
                        st.success(f"Token verified. Identity resolved to: {patient['full_name']} "
                                   f"(patient_id={patient['patient_id']}). Scopes: {payload['scopes']}")
                    except PortalAuthError as e:
                        st.error(f"Auth failed: {e.message}")

                st.divider()
                token_patient = st.session_state["portal_token_patient"]
                prescriptions2 = db.list_prescriptions(token_patient["patient_id"])
                if prescriptions2:
                    rx_map2 = {f"{rx['medication_name']}": rx for rx in prescriptions2}
                    chosen_rx_label2 = st.selectbox("Prescription to refill", list(rx_map2.keys()), key="portal_rx")
                    chosen_rx2 = rx_map2[chosen_rx_label2]
                    pharmacy_id2 = st.text_input("Pharmacy ID", value="PHARM-001", key="portal_pharmacy")
                    idem_key = st.text_input("Idempotency-Key (optional — try submitting twice with the same key)",
                                              value="demo-key-001")

                    if st.button("📤 Submit refill via portal path (JWT auth, async, idempotent)"):
                        import uuid as uuid_mod
                        try:
                            payload = decode_portal_token(st.session_state["portal_token"])
                            patient = resolve_patient(payload)
                        except PortalAuthError as e:
                            st.error(f"Auth failed: {e.message}")
                            patient = None

                        if patient:
                            existing_tid = db.get_tracking_id_for_idempotency_key(idem_key) if idem_key else None
                            if existing_tid:
                                st.info(f"Duplicate request detected (Idempotency-Key already seen) — "
                                        f"returning original tracking_id: `{existing_tid}` instead of "
                                        f"creating a new refill.")
                                tracking_id = existing_tid
                            else:
                                tracking_id = str(uuid_mod.uuid4())
                                db.create_refill_task(tracking_id, patient["patient_id"],
                                                       chosen_rx2["prescription_id"], pharmacy_id2,
                                                       identity_method="jwt")
                                if idem_key:
                                    db.record_idempotency_key(idem_key, tracking_id)
                                st.success(f"202 Accepted — tracking_id: `{tracking_id}` "
                                           f"(returned instantly; processing happens in the background)")

                            with st.spinner("Background task processing (rule engine + queue routing)..."):
                                from tasks.refill_tasks import run_synchronously_as_fallback
                                result = run_synchronously_as_fallback(
                                    tracking_id, patient["patient_id"], chosen_rx2["prescription_id"], pharmacy_id2
                                )
                            task = db.get_refill_task(tracking_id)
                            st.write(f"**Final status:** {task['status']}")
                            st.json(result)
                else:
                    st.info("This patient has no prescriptions on file.")

    with rx_erx:
        st.markdown("Refills auto-approved and sent directly to the pharmacy.")
        erx = db.list_erx_queue()
        if erx:
            st.dataframe(pd.DataFrame(erx), use_container_width=True)
        else:
            st.info("Nothing in the eRx queue yet — simulate a golden-path refill above.")

    with rx_review:
        st.markdown("Refills that failed a safety check, routed to the provider with full context.")
        reviews = db.list_provider_review()
        if reviews:
            for r in reviews:
                with st.expander(f"{r['full_name']} — {r['medication_name']} — {r['reason']} ({r['status']})"):
                    st.json(r["context_payload"])
                    col1, col2 = st.columns(2)
                    if r["status"] == "pending":
                        if col1.button("✅ Approve", key=f"approve_{r['review_id']}"):
                            db.update_review_status(r["review_id"], "approved")
                            prescription = db.get_prescription(r["prescription_id"])
                            from workflows.prescription_refill import golden_path_queue_injection
                            golden_path_queue_injection(prescription, pharmacy_id="PROVIDER-APPROVED")
                            st.rerun()
                        if col2.button("❌ Deny", key=f"deny_{r['review_id']}"):
                            db.update_review_status(r["review_id"], "denied")
                            st.rerun()
        else:
            st.info("Nothing pending provider review — simulate a flagged refill above "
                     "(try a controlled substance, zero refills, or missing lab data).")

# ---------------- Feedback & Support ----------------
with tab_feedback:
    st.subheader("📋 Feedback & Support Ticket Triage")
    st.caption(
        "Classifier routes each message to the Feedback Handler (positive/negative), "
        "Query Handler (ticket status), or Prescription Refill Agent (multi-turn "
        "confirmation with controlled-substance schedule rules)."
    )

    fb_sim, fb_tickets, fb_logs = st.tabs(
        ["▶ Simulate a conversation", "🎫 Support Tickets", "🔍 Logs & Debugging"]
    )

    with fb_sim:
        if "feedback_chat_history" not in st.session_state:
            st.session_state.feedback_chat_history = []
        if "feedback_active_ticket" not in st.session_state:
            st.session_state.feedback_active_ticket = None
        if "feedback_active_stage" not in st.session_state:
            st.session_state.feedback_active_stage = None

        st.markdown("**Quick test scenarios** (from the capstone spec's worked examples):")
        col1, col2, col3, col4 = st.columns(4)
        example_msg = None
        if col1.button("😊 Positive feedback"):
            example_msg = "Thanks for sorting out my medication refill issue."
        if col2.button("😠 Negative feedback"):
            example_msg = "My medication refill still hasn't been filled."
        if col3.button("❓ Status query"):
            recent = db.list_support_tickets()
            example_msg = (f"Could you check the status of ticket {recent[0]['ticket_number']}?"
                            if recent else "Could you check the status of ticket 650932?")
        if col4.button("💊 New refill request"):
            example_msg = "I need to refill my prescription."

        if st.session_state.feedback_active_ticket:
            stage_label = {"awaiting_confirmation": "Awaiting your confirmation (yes/no)",
                           "awaiting_correction": "Awaiting corrected patient name + medication"}
            st.info(f"🎫 Active ticket #{st.session_state.feedback_active_ticket} — "
                     f"{stage_label.get(st.session_state.feedback_active_stage, st.session_state.feedback_active_stage)}")
            if st.button("↺ End this conversation / start fresh"):
                st.session_state.feedback_active_ticket = None
                st.session_state.feedback_active_stage = None
                st.rerun()

        for turn in st.session_state.feedback_chat_history:
            with st.chat_message(turn["role"]):
                st.write(turn["content"])

        patient_name_input = st.text_input("Patient/customer name", value="", key="fb_patient_name")

        corrected_name, corrected_med = None, None
        if st.session_state.feedback_active_stage == "awaiting_correction":
            st.caption("The previous answer indicated the details were wrong — provide the correction:")
            corrected_name = st.text_input("Corrected patient name", key="fb_corrected_name")
            corrected_med = st.text_input("Corrected medication name", key="fb_corrected_med")
        elif st.session_state.feedback_active_stage is None:
            corrected_med = st.text_input("Medication name (for new refill requests)", key="fb_med_name")

        message_input = st.chat_input("Type a message...") or example_msg

        if message_input:
            st.session_state.feedback_chat_history.append({"role": "user", "content": message_input})
            with st.spinner("Processing..."):
                from agents.feedback_crew import run_feedback_workflow
                try:
                    result = run_feedback_workflow(
                        message_input,
                        patient_name=patient_name_input or None,
                        active_ticket_number=st.session_state.feedback_active_ticket,
                        corrected_patient_name=corrected_name or None,
                        corrected_medication_name=corrected_med or None,
                    )
                    reply = result["message"]
                    meta = f"_{result['category']} · {result['classification_method']}_"
                    st.session_state.feedback_chat_history.append(
                        {"role": "assistant", "content": f"{reply}\n\n{meta}"})

                    stage = result.get("stage")
                    if stage in ("awaiting_confirmation", "awaiting_correction"):
                        st.session_state.feedback_active_ticket = result.get("ticket_number")
                        st.session_state.feedback_active_stage = stage
                    else:
                        st.session_state.feedback_active_ticket = None
                        st.session_state.feedback_active_stage = None

                    chained = result.get("chained_refill_agent")
                    if chained:
                        st.session_state.feedback_chat_history.append(
                            {"role": "assistant", "content": chained["message"]})
                        st.session_state.feedback_active_ticket = chained["ticket_number"]
                        st.session_state.feedback_active_stage = chained["stage"]

                except Exception as e:
                    st.session_state.feedback_chat_history.append(
                        {"role": "assistant", "content": f"Error: {e}"})
            st.rerun()

    with fb_tickets:
        tickets = db.list_support_tickets()
        if tickets:
            st.dataframe(pd.DataFrame(tickets), use_container_width=True)
            st.divider()
            st.markdown("**Update a ticket's status**")
            ticket_map = {f"#{t['ticket_number']} — {t['status']}": t for t in tickets}
            chosen_label = st.selectbox("Ticket", list(ticket_map.keys()))
            new_status = st.selectbox("New status", ["Unresolved", "In Progress", "Resolved"])
            if st.button("Update status"):
                db.update_ticket_status(ticket_map[chosen_label]["ticket_number"], new_status)
                st.rerun()
        else:
            st.info("No support tickets yet — a negative-feedback message in the Simulate tab creates one.")

        confirmations = db.list_refill_confirmations()
        if confirmations:
            st.divider()
            st.markdown("**Prescription refill confirmation sessions**")
            st.dataframe(pd.DataFrame(confirmations), use_container_width=True)

    with fb_logs:
        st.markdown("Prompt traces, classification outputs, and ticket actions.")
        events = [e for e in read_events(limit=200)
                  if e.get("event_type") in ("feedback_classification", "feedback_response",
                                              "refill_confirmation_started", "refill_escalated_to_doctor",
                                              "refill_agent_response")]
        if events:
            for ev in reversed(events):
                with st.expander(f"[{ev.get('timestamp', '')}] {ev.get('event_type')}"):
                    st.json(ev)
        else:
            st.info("No feedback/support activity logged yet.")

        st.divider()
        st.markdown("**Run the classification accuracy & routing success-rate test suite**")
        if st.button("▶ Run feedback evaluation suite"):
            with st.spinner("Running test cases..."):
                fb_results = run_feedback_eval_suite()
            summary = summarize_feedback_eval(fb_results)
            m1, m2, m3 = st.columns(3)
            m1.metric("Classification accuracy", f"{summary['classification_accuracy']*100:.0f}%")
            m2.metric("Routing success rate", f"{summary['routing_success_rate']*100:.0f}%")
            m3.metric("Avg latency", f"{summary['avg_latency_ms']:.0f} ms")
            for r in fb_results:
                mark = "✅" if r["classification_correct"] and r["routing_succeeded"] else "❌"
                with st.expander(f"{mark} {r['id']} — expected {r['expected_category']}, "
                                  f"got {r['predicted_category']}"):
                    st.write(r)

# ---------------- Evaluation ----------------
with tab_eval:
    st.subheader("Model Evaluation Metrics")
    metrics = summarize_metrics()
    st.markdown("**LLM-graded response quality (per module)**")
    if metrics["llm_graded"]:
        st.dataframe(pd.DataFrame(metrics["llm_graded"]).T, use_container_width=True)
    else:
        st.info("No graded responses yet — use 'Run LLM-as-judge evaluation' in the chat tab.")

    st.markdown("**Tool success rate (per module)**")
    if metrics["tool_success"]:
        st.dataframe(pd.DataFrame(metrics["tool_success"]).T, use_container_width=True)
    else:
        st.info("No tool-call outcomes logged yet.")

    st.divider()
    st.markdown("### 🩻 Symptom Analysis Accuracy")
    st.caption(
        "Runs a curated set of symptom descriptions (ground-truthed against the patient "
        "dataset's actual conditions) through the medical-search RAG pipeline, and checks "
        "whether the synthesized answer names the correct condition — both by keyword match "
        "and by an LLM-as-judge grade. Also measures how long each case takes (speed)."
    )
    col1, col2 = st.columns([1, 3])
    with col1:
        use_judge = st.checkbox("Also use LLM-as-judge", value=True)
        n_cases = st.number_input("Number of test cases", min_value=1, max_value=12, value=6)
        run_suite = st.button("▶ Run symptom-analysis test suite")

    if run_suite:
        with st.spinner(f"Running {n_cases} symptom-analysis test cases..."):
            try:
                results = run_symptom_analysis_suite(use_llm_judge=use_judge, limit=int(n_cases))
                st.session_state["symptom_eval_results"] = results
            except Exception as e:
                st.error(f"Error running symptom analysis suite: {e}")
                results = None

    if st.session_state.get("symptom_eval_results"):
        results = st.session_state["symptom_eval_results"]
        summary = summarize_suite_results(results)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Cases run", summary["count"])
        m2.metric("Keyword accuracy", f"{summary['keyword_accuracy']*100:.0f}%")
        m3.metric("LLM-judge accuracy",
                   f"{summary['llm_judge_accuracy']*100:.0f}%" if summary["llm_judge_accuracy"] is not None else "—")
        m4.metric("Avg latency", f"{summary['avg_latency_ms']:.0f} ms" if summary["avg_latency_ms"] else "—")

        for r in results:
            mark = "✅" if r.get("keyword_match") else "❌"
            with st.expander(f"{mark} {r['id']} — expected: {r['expected_condition']} — {r.get('latency_ms', '?')} ms"):
                st.write(f"**Symptom description:** {r.get('symptom_description', '')}")
                st.write(f"**Synthesized answer:** {r.get('answer', '')}")
                st.write(f"**Keyword match:** {r.get('keyword_match')} | "
                         f"**LLM verdict:** {r.get('llm_verdict')} | "
                         f"**LLM accuracy score:** {r.get('llm_accuracy')}")

# ---------------- Monitoring ----------------
with tab_monitor:
    st.subheader("System Monitoring")
    st.caption(
        "Operational metrics for every layer of the agent — latency, throughput, and "
        "success/error rate — separate from response-quality evaluation above."
    )
    latency_summary = summarize_latency()
    if latency_summary:
        df = pd.DataFrame(latency_summary).T
        df = df.sort_values("avg_ms", ascending=False)
        st.markdown("**Latency & reliability per module**")
        st.dataframe(df, use_container_width=True)

        st.markdown("**Average latency by module (ms)**")
        st.bar_chart(df["avg_ms"])

        throughput = throughput_over_time()
        if throughput:
            st.markdown("**Request throughput over time**")
            tdf = pd.DataFrame(throughput).set_index("time")
            st.line_chart(tdf["requests"])
    else:
        st.info("No monitoring data yet — use the Assistant Chat tab or run the symptom-analysis "
                 "test suite in the Evaluation tab to generate activity.")

# ---------------- Memory & Logs ----------------
with tab_logs:
    st.subheader("Agent Memory Traces & Planning Breakdowns")
    events = read_events(limit=100)
    if not events:
        st.info("No agent activity logged yet.")
    else:
        for ev in reversed(events):
            label = ev.get("event_type", "event")
            with st.expander(f"[{ev.get('timestamp','')}] {label}"):
                st.json(ev)

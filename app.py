import logging
import os
import uuid

import streamlit as st
from dotenv import load_dotenv
from langchain_core.messages import AIMessageChunk, HumanMessage

from graph import checkpointer, court_graph, list_thread_ids
from ingestion.nodes import (
    cache_path,
    claim_extractor,
    deduplicate_claims,
    load_and_chunk_pdf,
    load_cache,
    ranker,
    select_top_claims,
)
from nodes import MAX_REVIEW_ROUNDS, RateLimitError, is_daily_limit, is_rate_limit, wait_hint
from retrieval.vectorstore import set_active_document
from state import VerdictClass

load_dotenv()
logger = logging.getLogger(__name__)

# ============================================================
# PAGE SETUP
# ============================================================

st.set_page_config(page_title="Claim Court", layout="wide")

st.markdown(
    """
    <style>
    .block-container { max-width: 1100px; padding-top: 2rem; padding-bottom: 3rem; }
    .stApp { background-color: #0d1117; }
    h1, h2, h3 { font-family: Georgia, serif; color: #e6edf3; }
    p, li, span, div { color: #c9d1d9; }
    .subtitle { color: #8b949e; margin-bottom: 2rem; }

    .case-card {
        border-radius: 10px; padding: 20px 24px; margin-bottom: 24px;
        border-left: 4px solid; background-color: #161b22; line-height: 1.6;
    }
    .prosecutor-card { border-color: #f85149; }
    .defender-card { border-color: #58a6ff; }

    .verdict-card {
        border-radius: 12px; padding: 32px; text-align: center;
        background: linear-gradient(135deg, #1c1a10, #161b22);
        border: 1px solid #d4a72c; margin: 28px 0;
    }
    .verdict-label {
        font-family: Georgia, serif; font-size: 32px; font-weight: bold;
        color: #d4a72c; text-transform: uppercase; letter-spacing: 3px;
    }
    .verdict-reasoning { color: #c9d1d9; margin-top: 14px; font-size: 15px; line-height: 1.6; }
    .verdict-confidence { color: #8b949e; margin-top: 12px; font-size: 13px; }

    .citation-line { padding: 6px 0; border-bottom: 1px solid #21262d; }
    .citation-ok { color: #3fb950; font-weight: bold; }
    .citation-fail { color: #f85149; font-weight: bold; }

    .stButton > button {
        background-color: #21262d; color: #e6edf3; border: 1px solid #30363d; border-radius: 6px;
    }
    .stButton > button:hover { border-color: #d4a72c; color: #d4a72c; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("⚖️ Claim Court")
st.markdown('<p class="subtitle">An adversarial multi-agent fact-checking trial</p>', unsafe_allow_html=True)

# ============================================================
# BACKEND ERRORS: turn failures into plain messages, keep the trial resumable
# ============================================================


def describe_error(error):
    """Turns a backend failure into (kind, message). Rate limits get a plain wait message."""
    chain, current = [], error
    while current is not None and all(current is not seen for seen in chain):
        chain.append(current)
        current = current.__cause__ or current.__context__

    limited = next((e for e in chain if isinstance(e, RateLimitError) or is_rate_limit(e)), None)
    if limited is None:
        logger.error("Backend failure", exc_info=error)  # full details stay in the terminal
        return "error", "Something went wrong while running the court. Your progress is saved, so try again. If it keeps failing, check the terminal for details."

    hint = getattr(limited, "wait_hint", None) or wait_hint(limited)
    if getattr(limited, "daily", False) or is_daily_limit(limited):
        when = f" It should reset in about {hint}." if hint else ""
        return "rate", f"The AI service has used up its free daily limit.{when} Your progress is saved, so press Try again once it resets."
    return "rate", f"The AI service is busy (free-tier rate limit). Please wait {'about ' + hint if hint else 'a minute'} and press Try again. Your progress is saved."


def report_error(error):
    kind, text = describe_error(error)
    (st.warning if kind == "rate" else st.error)(text)


def record_error(error):
    st.session_state["backend_error"] = describe_error(error)


# ============================================================
# STEP 1: DOCUMENT SOURCE: upload a PDF, or use the bundled default
# ============================================================

uploaded_file = st.file_uploader("Upload a PDF to fact-check (optional)", type="pdf")

if uploaded_file is not None and st.session_state.get("last_uploaded_name") != uploaded_file.name:
    st.session_state["last_uploaded_name"] = uploaded_file.name
    st.session_state.pop("uploaded_ranked_claims", None)  # clear any previous upload's claims

    os.makedirs("data", exist_ok=True)
    save_path = os.path.join("data", "uploaded_doc.pdf")
    with open(save_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    with st.status("Processing your document...", expanded=True) as status:
        try:
            status.write("📄 Reading and chunking the PDF...")
            new_chunks = load_and_chunk_pdf(save_path)

            status.write(f"🔎 Rebuilding the search index ({len(new_chunks)} chunks)...")
            set_active_document(new_chunks)

            # cap chunks sent to the LLM extractor to keep this free-tier friendly on large PDFs
            capped_chunks = new_chunks[:30]
            status.write(f"🧠 Extracting factual claims from the document ({len(capped_chunks)} chunks)...")
            extraction_result = claim_extractor(capped_chunks)

            status.write("🧹 Removing duplicate claims...")
            unique_claims = deduplicate_claims(extraction_result["claims"])

            status.write(f"📊 Ranking {len(unique_claims)} claims by risk...")
            ranked = ranker(unique_claims)
            st.session_state["uploaded_ranked_claims"] = ranked["ranked_claims"]

            status.update(label="Document processed!", state="complete")
        except Exception as e:
            status.update(label="Processing failed", state="error")
            report_error(e)

# ============================================================
# STEP 2: CLAIM INPUT: pick an extracted claim, or type your own
# ============================================================

if st.session_state.get("uploaded_ranked_claims"):
    top_claims_from_upload = select_top_claims(st.session_state["uploaded_ranked_claims"], n=5)
    options = [rc["claim"].text for rc in top_claims_from_upload]
    claim_text = st.selectbox("Highest-risk claims found in your document:", options)
else:
    cached = load_cache(cache_path)
    default_top_claims = select_top_claims(ranked_claims=cached, n=1)
    default_claim = default_top_claims[0]["claim"].text
    claim_text = st.text_input("Or type a claim to fact-check:", value=default_claim)

run_clicked = st.button("Run Trial")

# ============================================================
# STEP 3: RUN TRIAL UP TO THE JUDGE (with live status updates)
# ============================================================

STAGE_LABELS = {
    "retrieve_docs": "📄 Fetching evidence from the document...",
    "grade_doc": "🔍 Grading document evidence for relevance...",
    "web_search": "🌐 Searching the web for independent sources...",
    "grade_web": "🔍 Grading web evidence for relevance...",
    "rewrite": "✏️ Evidence was weak, refining the search query...",
    "prosecutor": "⚔️ Prosecutor is building their case...",
    "defender": "🛡️ Defender is building their case...",
}

# ============================================================
# TIME TRAVEL (sidebar): rewind a trial to any checkpoint and run it forward again
# ============================================================

NODE_NAMES = {
    "__start__": "trial start",
    "retrieve_docs": "document retrieval",
    "grade_doc": "document grading",
    "web_search": "web search",
    "grade_web": "web grading",
    "rewrite": "query rewrite",
    "prosecutor": "prosecutor",
    "defender": "defender",
    "judge_node": "judge",
    "verify_citations": "citation check",
    "chat_node": "chat reply",
}


def describe_checkpoint(snapshot):
    step = snapshot.metadata.get("step", "?")
    if snapshot.next:
        upcoming = ", ".join(NODE_NAMES.get(n, n) for n in snapshot.next)
        return f"Step {step}: before {upcoming}"
    chat_count = len(snapshot.values.get("messages", []))
    if chat_count:
        return f"Step {step}: chat, {chat_count} messages"
    return f"Step {step}: trial finished"


def sync_from_checkpoint(thread_id):
    """Loads a thread's latest checkpoint into session state so the main view matches it."""
    snapshot = court_graph.get_state({"configurable": {"thread_id": thread_id}})
    values = snapshot.values
    if not values:
        return False

    verdict = values.get("verdict")
    if isinstance(verdict, dict):  # depending on the checkpointer version, models can come back as dicts
        verdict = VerdictClass(**verdict)

    st.session_state.pop("backend_error", None)
    st.session_state.update(
        thread_id=thread_id,
        trial_started=True,
        claim_text=values.get("claim", ""),
        prosecutor_case=values.get("prosecutor_case", ""),
        defender_case=values.get("defender_case", ""),
        verdict=verdict,
        citation_notes=values.get("citation_notes", []),
        judge_done=verdict is not None,
        review_status=values.get("review_status", "pending"),
        review_rounds=values.get("review_rounds", 0),
    )
    st.query_params["thread"] = thread_id  # a page refresh reopens this trial
    return True


TRIAL_KEYS = ["thread_id", "trial_started", "judge_done", "claim_text", "prosecutor_case", "defender_case", "verdict", "citation_notes", "review_status", "review_rounds", "backend_error"]


def new_conversation():
    for key in TRIAL_KEYS:
        st.session_state.pop(key, None)
    st.query_params.clear()


def set_review_status(thread_id, status):
    # as_node="defender" records the change without moving the graph off its pause before the judge
    court_graph.update_state({"configurable": {"thread_id": thread_id}}, {"review_status": status}, as_node="defender")


def send_back_to_lawyers(thread_id, note):
    """Re-runs both lawyers with the reviewer's note, starting from the checkpoint just before they last ran."""
    config = {"configurable": {"thread_id": thread_id}}
    before_lawyers = next(
        snap for snap in court_graph.get_state_history(config) if set(snap.next) == {"prosecutor", "defender"}
    )
    rounds = court_graph.get_state(config).values.get("review_rounds", 0) + 1
    fork_config = court_graph.update_state(
        before_lawyers.config,
        {"reviewer_note": note, "review_rounds": rounds, "review_status": "pending"},
        as_node="grade_web",
    )
    with st.status("Sending the cases back to the lawyers...", expanded=True) as status:
        for update in court_graph.stream(None, config=fork_config, stream_mode="updates"):
            for node_name in update:
                if node_name in STAGE_LABELS:
                    status.write(STAGE_LABELS[node_name])
        status.update(label="Both sides have revised their case.", state="complete")


def resume_trial():
    """Continues the trial from its last checkpoint after a failure, without repeating finished steps."""
    thread_id = st.session_state["thread_id"]
    config = {"configurable": {"thread_id": thread_id}}
    with st.status("Trying again...", expanded=True) as status:
        try:
            for update in court_graph.stream(None, config=config, stream_mode="updates"):
                for node_name in update:
                    if node_name in STAGE_LABELS:
                        status.write(STAGE_LABELS[node_name])
                    elif node_name == "judge_node":
                        status.write("⚖️ Judge is deliberating...")
                    elif node_name == "verify_citations":
                        status.write("📋 Verifying citations...")
                    elif node_name == "chat_node":
                        status.write("💬 Writing an answer...")
            status.update(label="Done.", state="complete")
        except Exception as e:
            status.update(label="Still failing", state="error")
            record_error(e)
            return
    sync_from_checkpoint(thread_id)
    st.rerun()


def show_backend_error():
    """Shows the last backend failure with a button that resumes the trial."""
    if not st.session_state.get("backend_error"):
        return
    kind, text = st.session_state["backend_error"]
    (st.warning if kind == "rate" else st.error)(text)
    if st.button("Try again", key="retry_backend"):
        resume_trial()


def stream_answer(question, config):
    """Yields the chat reply token by token from the graph's chat node."""
    streamed = False
    for chunk, meta in court_graph.stream(
        {"messages": [HumanMessage(content=question)]}, config=config, stream_mode="messages"
    ):
        if meta.get("langgraph_node") == "chat_node" and isinstance(chunk, AIMessageChunk) and chunk.content:
            streamed = True
            yield chunk.content
    if not streamed:  # the model returned the answer in one piece instead of streaming it
        messages = court_graph.get_state(config).values.get("messages", [])
        if messages:
            yield messages[-1].content


# reopen the trial named in the URL after a page refresh
if st.query_params.get("thread") and not st.session_state.get("thread_id"):
    if not sync_from_checkpoint(st.query_params["thread"]):
        st.query_params.clear()


with st.sidebar:
    if st.button("➕ New conversation", use_container_width=True):
        new_conversation()
        st.rerun()

    with st.expander("🗂️ Past conversations", expanded=True):
        shown = 0
        for tid in list_thread_ids():
            values = court_graph.get_state({"configurable": {"thread_id": tid}}).values
            if not values.get("claim"):
                continue
            shown += 1

            row_open, row_delete = st.columns([5, 1])
            is_current = tid == st.session_state.get("thread_id")
            claim_label = values["claim"] if len(values["claim"]) <= 42 else values["claim"][:40] + "..."
            if values.get("review_status") == "declined":
                claim_label = "🚫 " + claim_label
            if row_open.button(claim_label, key=f"open_{tid}", type="primary" if is_current else "secondary", use_container_width=True):
                if sync_from_checkpoint(tid):
                    st.rerun()
            if row_delete.button("🗑️", key=f"delete_{tid}"):
                st.session_state["confirm_delete"] = tid

            if st.session_state.get("confirm_delete") == tid:
                st.warning("Delete this conversation and its chat?")
                yes_col, no_col = st.columns(2)
                if yes_col.button("Delete", key=f"yes_{tid}"):
                    checkpointer.delete_thread(tid)
                    st.session_state.pop("confirm_delete", None)
                    if is_current:
                        new_conversation()
                    st.rerun()
                if no_col.button("Cancel", key=f"no_{tid}"):
                    st.session_state.pop("confirm_delete", None)
                    st.rerun()

        if not shown:
            st.caption("No saved conversations yet.")

    st.header("⏪ Time travel")
    st.caption("Rewind a trial to any checkpoint and run it forward again.")

    if st.session_state.get("thread_id"):
        st.caption("Current thread ID (save it to reload this trial later)")
        st.code(st.session_state["thread_id"], language=None)

        thread_config = {"configurable": {"thread_id": st.session_state["thread_id"]}}
        history = list(reversed(list(court_graph.get_state_history(thread_config))))  # oldest first

        if history:
            choice = st.selectbox(
                "Checkpoint",
                range(len(history)),
                format_func=lambda i: describe_checkpoint(history[i]),
                key="checkpoint_choice",
            )

            if st.button("Resume from this checkpoint"):
                replayed = False
                with st.status("Re-running from checkpoint...", expanded=True) as status:
                    try:
                        for update in court_graph.stream(None, config=history[choice].config, stream_mode="updates"):
                            for node_name in update:
                                if node_name in STAGE_LABELS:
                                    status.write(STAGE_LABELS[node_name])
                                elif node_name == "judge_node":
                                    status.write("⚖️ Judge is deliberating...")
                                elif node_name == "verify_citations":
                                    status.write("📋 Verifying citations...")
                        status.update(label="Replay complete.", state="complete")
                        replayed = True
                    except Exception as e:
                        status.update(label="Replay failed", state="error")
                        report_error(e)

                if replayed:
                    sync_from_checkpoint(st.session_state["thread_id"])
                    st.rerun()

if run_clicked:
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    st.session_state["thread_id"] = thread_id
    st.session_state["trial_started"] = True
    st.session_state["judge_done"] = False
    st.session_state["claim_text"] = claim_text
    st.session_state["prosecutor_case"] = ""
    st.session_state["defender_case"] = ""
    st.session_state["verdict"] = None
    st.session_state["citation_notes"] = []
    st.session_state["review_status"] = "pending"
    st.session_state["review_rounds"] = 0
    st.session_state.pop("backend_error", None)
    st.query_params["thread"] = thread_id

    trial_failed = False
    with st.status("Running the trial...", expanded=True) as status:
        try:
            for update in court_graph.stream({"claim": claim_text}, config=config, stream_mode="updates"):
                for node_name, node_output in update.items():
                    if node_name in STAGE_LABELS:
                        status.write(STAGE_LABELS[node_name])
                    if node_name == "prosecutor":
                        st.session_state["prosecutor_case"] = node_output["prosecutor_case"]
                    elif node_name == "defender":
                        st.session_state["defender_case"] = node_output["defender_case"]
            status.update(label="Both sides have presented their case.", state="complete")
        except Exception as e:
            status.update(label="The trial stopped early", state="error")
            record_error(e)
            trial_failed = True
    if not trial_failed:
        st.rerun()  # refresh the sidebar so the new trial shows up in the list

# ============================================================
# DISPLAY: CASES
# ============================================================

if st.session_state.get("trial_started"):
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("⚔️ Prosecutor's Case")
        st.markdown('<div class="case-card prosecutor-card">', unsafe_allow_html=True)
        st.markdown(st.session_state["prosecutor_case"] or "Waiting...")
        st.markdown('</div>', unsafe_allow_html=True)

    with col2:
        st.subheader("🛡️ Defender's Case")
        st.markdown('<div class="case-card defender-card">', unsafe_allow_html=True)
        st.markdown(st.session_state["defender_case"] or "Waiting...")
        st.markdown('</div>', unsafe_allow_html=True)

    # ------------------------------------------------------------
    # human checkpoint
    # ------------------------------------------------------------
    if not st.session_state["judge_done"]:
        thread_id = st.session_state["thread_id"]

        if st.session_state.get("review_status") == "declined":
            st.warning("You declined to send this case to the judge. No verdict was issued.")
            if st.button("Reopen"):
                set_review_status(thread_id, "pending")
                st.session_state["review_status"] = "pending"
                st.rerun()
        else:
            send_clicked = st.button("Send to Judge for Verdict")

            rounds_used = st.session_state.get("review_rounds", 0)
            with st.expander("Send back to the lawyers with feedback"):
                if rounds_used >= MAX_REVIEW_ROUNDS:
                    st.caption(f"You have used all {MAX_REVIEW_ROUNDS} feedback rounds for this trial.")
                else:
                    feedback = st.text_area(
                        "What should they fix or address?",
                        key="reviewer_feedback",
                        placeholder="For example: the Defender ignored W2, and the Prosecutor should not rely on D1.",
                    )
                    st.caption(f"Feedback rounds used: {rounds_used} of {MAX_REVIEW_ROUNDS}")
                    if st.button("Send back"):
                        if feedback.strip():
                            try:
                                send_back_to_lawyers(thread_id, feedback.strip())
                            except Exception as e:
                                record_error(e)
                            else:
                                sync_from_checkpoint(thread_id)
                                st.rerun()
                        else:
                            st.error("Write some feedback first.")

            if st.button("Decline to send to the judge"):
                st.session_state["confirm_decline"] = True
            if st.session_state.get("confirm_decline"):
                st.warning("End this trial without a verdict? You can reopen it later.")
                yes_col, no_col = st.columns(2)
                if yes_col.button("Yes, decline", key="decline_yes"):
                    set_review_status(thread_id, "declined")
                    st.session_state["review_status"] = "declined"
                    st.session_state.pop("confirm_decline", None)
                    st.rerun()
                if no_col.button("Cancel", key="decline_no"):
                    st.session_state.pop("confirm_decline", None)
                    st.rerun()

            if send_clicked:
                config = {"configurable": {"thread_id": thread_id}}

                judge_failed = False
                with st.status("Finalizing verdict...", expanded=True) as status:
                    try:
                        for update in court_graph.stream(None, config=config, stream_mode="updates"):
                            for node_name, node_output in update.items():
                                if node_name == "judge_node":
                                    status.write("⚖️ Judge is deliberating...")
                                    st.session_state["verdict"] = node_output["verdict"]
                                elif node_name == "verify_citations":
                                    status.write("📋 Verifying citations...")
                                    st.session_state["citation_notes"] = node_output.get("citation_notes", [])
                        status.update(label="Verdict reached!", state="complete")
                    except Exception as e:
                        status.update(label="The judge could not finish", state="error")
                        record_error(e)
                        judge_failed = True

                if not judge_failed:
                    st.session_state["judge_done"] = True
                    st.rerun()

        show_backend_error()
        st.caption("Chat opens after the judge rules.")

    # ------------------------------------------------------------
    # verdict + citation check
    # ------------------------------------------------------------
    if st.session_state["judge_done"] and st.session_state["verdict"]:
        verdict = st.session_state["verdict"]

        st.markdown(
            f"""
            <div class="verdict-card">
                <div class="verdict-label">{verdict.label}</div>
                <div class="verdict-reasoning">{verdict.reasoning}</div>
                <div class="verdict-confidence">Confidence: {verdict.confidence:.0%}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.subheader("📋 Citation Check")
        for note in st.session_state["citation_notes"]:
            css_class = "citation-ok" if note["verified"] else "citation-fail"
            label = "OK" if note["verified"] else "FAIL"
            st.markdown(
                f'<div class="citation-line"><span class="{css_class}">{label}</span> '
                f'<b>{note["side"]}</b> ({note["label"]}): {note["reason"]}</div>',
                unsafe_allow_html=True,
            )

        # ============================================================
        # FOLLOW-UP CHAT
        # ============================================================
        st.divider()
        st.subheader("💬 Ask about this verdict")
        show_backend_error()

        thread_config = {"configurable": {"thread_id": st.session_state["thread_id"]}}
        for msg in court_graph.get_state(thread_config).values.get("messages", []):
            with st.chat_message("user" if msg.type == "human" else "assistant"):
                st.write(msg.content)

        user_question = st.chat_input("Ask a question about this trial...")

        if user_question:
            with st.chat_message("user"):
                st.write(user_question)

            chat_failed = False
            with st.chat_message("assistant"):
                try:
                    st.write_stream(stream_answer(user_question, thread_config))
                except Exception as e:
                    record_error(e)
                    chat_failed = True
            if chat_failed:
                st.rerun()  # shows the message and the Try again button above the chat

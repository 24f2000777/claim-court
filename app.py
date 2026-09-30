import os
import uuid

import streamlit as st
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from graph import court_graph
from ingestion.nodes import (
    cache_path,
    claim_extractor,
    deduplicate_claims,
    load_and_chunk_pdf,
    load_cache,
    ranker,
    select_top_claims,
)
from retrieval.vectorstore import set_active_document

load_dotenv()

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
# STEP 1: DOCUMENT SOURCE — upload a PDF, or use the bundled default
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
            st.error(f"Could not process this PDF: {e}")

# ============================================================
# STEP 2: CLAIM INPUT — pick an extracted claim, or type your own
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
    "rewrite": "✏️ Evidence was weak — refining the search query...",
    "prosecutor": "⚔️ Prosecutor is building their case...",
    "defender": "🛡️ Defender is building their case...",
}

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
    st.session_state["chat_history"] = []

    with st.status("Running the trial...", expanded=True) as status:
        for update in court_graph.stream({"claim": claim_text}, config=config, stream_mode="updates"):
            for node_name, node_output in update.items():
                if node_name in STAGE_LABELS:
                    status.write(STAGE_LABELS[node_name])
                if node_name == "prosecutor":
                    st.session_state["prosecutor_case"] = node_output["prosecutor_case"]
                elif node_name == "defender":
                    st.session_state["defender_case"] = node_output["defender_case"]
        status.update(label="Both sides have presented their case.", state="complete")

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
        send_clicked = st.button("Send to Judge for Verdict")

        if send_clicked:
            config = {"configurable": {"thread_id": st.session_state["thread_id"]}}

            with st.status("Finalizing verdict...", expanded=True) as status:
                for update in court_graph.stream(None, config=config, stream_mode="updates"):
                    for node_name, node_output in update.items():
                        if node_name == "judge_node":
                            status.write("⚖️ Judge is deliberating...")
                            st.session_state["verdict"] = node_output["verdict"]
                        elif node_name == "verify_citations":
                            status.write("📋 Verifying citations...")
                            st.session_state["citation_notes"] = node_output.get("citation_notes", [])
                status.update(label="Verdict reached!", state="complete")

            st.session_state["judge_done"] = True
            st.rerun()

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
                f'— <b>{note["side"]}</b> ({note["label"]}): {note["reason"]}</div>',
                unsafe_allow_html=True,
            )

        # ============================================================
        # FOLLOW-UP CHAT
        # ============================================================
        st.divider()
        st.subheader("💬 Ask about this verdict")

        if "chat_history" not in st.session_state:
            st.session_state["chat_history"] = []

        chat_model = ChatGroq(model=os.getenv("MODEL_NAME"), temperature=0.3)

        chat_system_prompt = f"""You are answering questions about one fact-checking trial that already ran. Use only the information below. If the person asks something this trial did not cover, say so plainly rather than guessing.

Claim: {st.session_state['claim_text']}

Prosecutor's case:
{st.session_state['prosecutor_case']}

Defender's case:
{st.session_state['defender_case']}

Judge's verdict: {verdict.label} (confidence {verdict.confidence:.0%})
Judge's reasoning: {verdict.reasoning}

Answer in plain, natural sentences. Do not use bold text, headers, or bullet points unless the person specifically asks for a list."""

        for msg in st.session_state["chat_history"]:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])

        user_question = st.chat_input("Ask a question about this trial...")

        if user_question:
            st.session_state["chat_history"].append({"role": "user", "content": user_question})
            with st.chat_message("user"):
                st.write(user_question)

            messages = [SystemMessage(content=chat_system_prompt)]
            for msg in st.session_state["chat_history"]:
                if msg["role"] == "user":
                    messages.append(HumanMessage(content=msg["content"]))
                else:
                    messages.append(AIMessage(content=msg["content"]))

            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    response = chat_model.invoke(messages)
                    answer = response.content
                    st.write(answer)

            st.session_state["chat_history"].append({"role": "assistant", "content": answer})
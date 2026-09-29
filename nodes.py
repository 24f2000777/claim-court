import os
import re
import time

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from prompt import (
    defender_prompt,
    doc_grader_prompt,
    judge_prompt,
    prosecutor_prompt,
    query_writer_prompt,
    web_grader_prompt,
    citation_check_prompt
)
from retrieval.vectorstore import retrieve_top_chunks
from retrieval.websearch import web_search_tool
from state import CourtState, EvidenceGrades, VerdictClass,CitationChecks

load_dotenv()


# ============================================================
# 1. config
# ============================================================

MODEL_NAME = os.getenv("MODEL_NAME")
SECONDARY_MODEL = "openai/gpt-oss-20b"  # Groq fallback, different daily quota than MODEL_NAME
MAX_RETRIES = 2
DEBUG = True

# ============================================================
# 2. models (Groq primary, Groq secondary fallback for every role)
# ============================================================

prosecutor_model = ChatGroq(model=MODEL_NAME, temperature=0.3)
prosecutor_fallback = ChatGroq(model=SECONDARY_MODEL, temperature=0.3)

defender_model = ChatGroq(model=MODEL_NAME, temperature=0.3)
defender_fallback = ChatGroq(model=SECONDARY_MODEL, temperature=0.3)

judge_model = ChatGroq(model=MODEL_NAME, temperature=0).with_structured_output(VerdictClass)
judge_fallback = None  # gpt-oss-20b's tool-calling is unreliable for structured output

query_model = ChatGroq(model=MODEL_NAME, temperature=0)
query_fallback = ChatGroq(model=SECONDARY_MODEL, temperature=0)

grader_model = ChatGroq(model=MODEL_NAME, temperature=0).with_structured_output(EvidenceGrades)
grader_fallback = None

checker_model = ChatGroq(model=MODEL_NAME, temperature=0).with_structured_output(CitationChecks)
checker_fallback = None


# ============================================================
# 3. helpers
# ============================================================


# calls the model, retries on rate limit, and falls back to a secondary model if given
def call_with_retry(model, messages, fallback_model=None):
    waits = [2, 5, 10, 20]
    for attempt in range(len(waits)):
        try:
            return model.invoke(messages)
        except Exception as e:
            if "429" in str(e) or "rate_limit" in str(e).lower():
                print(f"Rate limit hit, waiting {waits[attempt]}s")
                time.sleep(waits[attempt])
                continue
            raise

    if fallback_model is not None:
        print("Primary model exhausted, falling back to secondary model")
        try:
            return fallback_model.invoke(messages)
        except Exception as e:
            raise RuntimeError(f"Both primary and fallback model failed: {e}") from e

    raise RuntimeError("Rate limit: still failing after all retries")


# neutral search query without the claim's figures, optionally leaning "for" or "against"
def write_search_query(claim, previous_query=None, stance=None):
    text = f"Claim: {claim}"
    if stance:
        text += f"\n\nStance: {stance}"
    if previous_query:
        text += f"\n\nPrevious query (found little useful evidence): {previous_query}"

    try:
        response = call_with_retry(
            query_model,
            [SystemMessage(content=query_writer_prompt), HumanMessage(content=text)],
            fallback_model=query_fallback,
        )
        query = (response.content or "").strip().strip('"')
    except Exception as e:
        print(f"Query writer failed: {e}")
        query = ""

    if not query:
        # fallback: claim without numbers
        query = " ".join(re.sub(r"[\d.,%$]+", "", claim).split()) + " forecast"

    return query


# numbered text for the graders
def number_items(items):
    lines = []
    for i, item in enumerate(items):
        lines.append(f"{i+1}. [{item['source']}] {item['text']}")
    return "\n".join(lines)


# labelled evidence block for the lawyers (D = document, W = web)
def format_evidence(state: CourtState):
    doc_lines = []
    for i, item in enumerate(state.get("doc_evidence", [])):
        doc_lines.append(f"D{i+1}. [{item['source']}] {item['text']}")

    web_lines = []
    for i, item in enumerate(state.get("web_evidence", [])):
        web_lines.append(f"W{i+1}. [{item['source']}] {item['text']}")

    doc_text = "\n".join(doc_lines) or "No document evidence."
    web_text = "\n".join(web_lines) or "No independent web evidence was found."

    return (
        "DOCUMENT CONTEXT (the claim's own source, not independent proof):\n"
        f"{doc_text}\n\n"
        "INDEPENDENT WEB EVIDENCE:\n"
        f"{web_text}"
    )

# finds every (W1), (D2) style label in a case's text, with the sentence it appeared in
def extract_citations(case_text):
    # split into sentences on '. ' so each citation stays with its own claim
    sentences = re.split(r"(?<=[.!?])\s+", case_text)

    citations = []
    for sentence in sentences:
        labels = re.findall(r"\((D|W)(\d+)\)", sentence)
        for prefix, number in labels:
            citations.append({
                "label": f"{prefix}{number}",
                "claimed": sentence.strip(),
            })
    return citations

# looks up a label's actual evidence text from doc_evidence / web_evidence
def resolve_label(label, state):
    prefix = label[0]
    index = int(label[1:]) - 1  # labels are 1-indexed (W1 = index 0)

    if prefix == "D":
        items = state.get("doc_evidence", [])
    elif prefix == "W":
        items = state.get("web_evidence", [])
    else:
        return None

    if 0 <= index < len(items):
        return items[index]["text"]
    return None


# ============================================================
# 4. clerk: evidence pipeline
# ============================================================


def retrieve_docs(state: CourtState):
    try:
        chunks = retrieve_top_chunks.invoke(state["claim"])
    except Exception as e:
        print(f"Document retrieval failed: {e}")
        return {"doc_evidence": []}

    items = []
    for chunk in chunks:
        items.append({"source": f"document page {chunk['page']}", "text": chunk["text"]})

    return {"doc_evidence": items}


# drops irrelevant chunks, keeps everything if grading fails
def grade_doc(state: CourtState):
    evidence = state.get("doc_evidence", [])
    if not evidence:
        return {"doc_evidence": []}

    try:
        response = call_with_retry(
            grader_model,
            [
                SystemMessage(content=doc_grader_prompt),
                HumanMessage(
                    content=f"Claim: {state['claim']}\n\nChunks:\n{number_items(evidence)}"
                ),
            ],
            fallback_model=grader_fallback,
        )
    except Exception as e:
        print(f"Doc grading failed, keeping all chunks: {e}")
        return {"doc_evidence": evidence}

    if response is None:
        return {"doc_evidence": evidence}

    irrelevant = set()
    for g in response.grades:
        if DEBUG:
            print("doc", g.item_number, g.grade, "-", g.reason)
        if g.grade == "irrelevant":
            irrelevant.add(g.item_number)

    kept = []
    for i, item in enumerate(evidence):
        if (i + 1) not in irrelevant:
            kept.append(item)

    return {"doc_evidence": kept}


# runs one search per stance, merges results and drops duplicate urls
def web_search(state: CourtState):
    saved = state.get("search_query")

    if saved and " | " in saved:
        queries = saved.split(" | ")
    else:
        queries = [
            write_search_query(state["claim"], stance="for"),
            write_search_query(state["claim"], stance="against"),
        ]

    items = []
    seen_urls = set()
    for query in queries:
        try:
            result = web_search_tool.invoke(query)
        except Exception as e:
            print(f"Web search failed for '{query}': {e}")
            continue

        for item in result.get("results", []):
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            items.append({"source": item["url"], "text": item["content"][:600]})

    return {"search_query": " | ".join(queries), "web_evidence": items}


# drops irrelevant results (including the claim's own source); evidence_ok needs 1 relevant
def grade_web(state: CourtState):
    evidence = state.get("web_evidence", [])
    if not evidence:
        return {"web_evidence": [], "evidence_ok": False}

    try:
        response = call_with_retry(
            grader_model,
            [
                SystemMessage(content=web_grader_prompt),
                HumanMessage(
                    content=f"Claim: {state['claim']}\n\nResults:\n{number_items(evidence)}"
                ),
            ],
            fallback_model=grader_fallback,
        )
    except Exception as e:
        print(f"Web grading failed: {e}")
        return {"web_evidence": evidence, "evidence_ok": False}

    if response is None:
        return {"web_evidence": evidence, "evidence_ok": False}

    grade_by_number = {}
    for g in response.grades:
        if DEBUG:
            print("web", g.item_number, g.grade, "-", g.reason)
        grade_by_number[g.item_number] = g.grade

    kept = []
    relevant_count = 0
    for i, item in enumerate(evidence):
        grade = grade_by_number.get(i + 1, "ambiguous")  # ungraded: keep, don't count
        if grade == "irrelevant":
            continue
        if grade == "relevant":
            relevant_count += 1
        kept.append(item)

    return {"web_evidence": kept, "evidence_ok": relevant_count >= 1}


def rewrite(state: CourtState):
    previous = state.get("search_query", "")
    old_queries = previous.split(" | ")

    new_for = write_search_query(state["claim"], previous_query=old_queries[0], stance="for")
    new_against = write_search_query(
        state["claim"], previous_query=old_queries[-1], stance="against"
    )

    return {
        "search_query": f"{new_for} | {new_against}",
        "retry_count": state.get("retry_count", 0) + 1,
    }


# enough evidence or out of retries: both lawyers, otherwise search again
def route_after_web_grade(state: CourtState):
    if state.get("evidence_ok") or state.get("retry_count", 0) >= MAX_RETRIES:
        return ["prosecutor", "defender"]
    return "rewrite"


# ============================================================
# 5. prosecutor
# ============================================================


def prosecutor(state: CourtState):
    evidence = format_evidence(state)

    try:
        response = call_with_retry(
            prosecutor_model,
            [
                SystemMessage(content=prosecutor_prompt),
                HumanMessage(content=f"Claim: {state['claim']}\n\nEvidence:\n{evidence}"),
            ],
            fallback_model=prosecutor_fallback,
        )
    except Exception as e:
        raise RuntimeError(f"Prosecutor LLM call failed: {e}") from e

    case = (response.content or "").strip()
    if not case:
        raise ValueError("Prosecutor returned an empty case")

    return {"prosecutor_case": case}


# ============================================================
# 6. defender
# ============================================================


def defender(state: CourtState):
    evidence = format_evidence(state)

    try:
        response = call_with_retry(
            defender_model,
            [
                SystemMessage(content=defender_prompt),
                HumanMessage(content=f"Claim: {state['claim']}\n\nEvidence:\n{evidence}"),
            ],
            fallback_model=defender_fallback,
        )
    except Exception as e:
        raise RuntimeError(f"Defender LLM call failed: {e}") from e

    case = (response.content or "").strip()
    if not case:
        raise ValueError("Defender returned an empty case")

    return {"defender_case": case}


# ============================================================
# 7. judge
# ============================================================


# retries once if the structured output fails
def judge(state: CourtState):
    prosecutor_case = state.get("prosecutor_case")
    defender_case = state.get("defender_case")

    if not prosecutor_case or not defender_case:
        raise ValueError("Judge needs both prosecutor_case and defender_case")

    messages = [
        SystemMessage(content=judge_prompt),
        HumanMessage(
            content=(
                f"Claim: {state['claim']}\n\n"
                f"Prosecutor's case:\n{prosecutor_case}\n\n"
                f"Defender's case:\n{defender_case}"
            )
        ),
    ]

    last_error = None
    for _ in range(2):
        try:
            response = call_with_retry(judge_model, messages, fallback_model=judge_fallback)
        except Exception as e:
            last_error = e
            continue

        if response is not None:
            return {"verdict": response}
        last_error = ValueError("Judge returned no verdict")

    raise RuntimeError(f"Judge failed after retry: {last_error}") from last_error


# ============================================================
# 8. citation verification (checks that each (W1)/(D2) label actually supports its claim)
# ============================================================


def verify_citations(state: CourtState):
    prosecutor_citations = extract_citations(state.get("prosecutor_case", ""))
    for c in prosecutor_citations:
        c["side"] = "prosecutor"

    defender_citations = extract_citations(state.get("defender_case", ""))
    for c in defender_citations:
        c["side"] = "defender"

    all_citations = prosecutor_citations + defender_citations
    if not all_citations:
        return {"citation_notes": []}

    citation_notes = []
    to_check = []

    for c in all_citations:
        evidence_text = resolve_label(c["label"], state)
        if evidence_text is None:
            # label doesn't exist in doc_evidence/web_evidence, no LLM call needed
            citation_notes.append({
                "label": c["label"],
                "side": c["side"],
                "claimed": c["claimed"],
                "verified": False,
                "reason": "Label does not exist in the evidence given to this trial.",
            })
        else:
            c["evidence_text"] = evidence_text
            to_check.append(c)

    if not to_check:
        return {"citation_notes": citation_notes}

    lines = []
    for i, c in enumerate(to_check):
        lines.append(
            f"{i+1}. Label: {c['label']} | Claimed: {c['claimed']} | Evidence: {c['evidence_text']}"
        )
    text = "\n".join(lines)

    try:
        response = call_with_retry(
            checker_model,
            [
                SystemMessage(content=citation_check_prompt),
                HumanMessage(content=text),
            ],
            fallback_model=checker_fallback,
        )
    except Exception as e:
        print(f"Citation check failed: {e}")
        # can't verify, so mark these as unverified rather than silently dropping them
        for c in to_check:
            citation_notes.append({
                "label": c["label"],
                "side": c["side"],
                "claimed": c["claimed"],
                "verified": False,
                "reason": "Citation check failed to run.",
            })
        return {"citation_notes": citation_notes}

    # match the LLM's checks back to the citations by label (checks are unordered)
    checks_by_label = {check.label: check for check in response.checks}

    for c in to_check:
        check = checks_by_label.get(c["label"])
        if check is None:
            citation_notes.append({
                "label": c["label"],
                "side": c["side"],
                "claimed": c["claimed"],
                "verified": False,
                "reason": "No verification result returned for this label.",
            })
        else:
            citation_notes.append({
                "label": c["label"],
                "side": c["side"],
                "claimed": c["claimed"],
                "verified": check.verified,
                "reason": check.reason,
            })

    return {"citation_notes": citation_notes}

if __name__ == "__main__":
    fake_state = {
        "prosecutor_case": "The market grew slowly (W1).",
        "defender_case": "The market is strong (W1) and growing (D1).",
        "doc_evidence": [{"source": "doc", "text": "The document says nothing about growth speed."}],
        "web_evidence": [{"source": "web", "text": "Market analysts report 9% annual growth."}],
    }
    print(verify_citations(fake_state))
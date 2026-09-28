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
)
from retrieval.vectorstore import retrieve_top_chunks
from retrieval.websearch import web_search_tool
from state import CourtState, EvidenceGrades, VerdictClass

load_dotenv()


# ============================================================
# 1. config
# ============================================================

MODEL_NAME = os.getenv("MODEL_NAME")
MAX_RETRIES = 2
DEBUG = True  # prints grader decisions


# ============================================================
# 2. models
# ============================================================

prosecutor_model = ChatGroq(model=MODEL_NAME, temperature=0.3)
defender_model = ChatGroq(model=MODEL_NAME, temperature=0.3)
judge_model = ChatGroq(model=MODEL_NAME, temperature=0).with_structured_output(
    VerdictClass
)

query_model = ChatGroq(model=MODEL_NAME, temperature=0)
grader_model = ChatGroq(model=MODEL_NAME, temperature=0).with_structured_output(
    EvidenceGrades
)


# ============================================================
# 3. helpers
# ============================================================

# calls the model, and on a rate limit (429) waits a little and tries again
def call_with_retry(model, messages):
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
            ]
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

# drops irrelevant results (including the claim's own source), evidence_ok needs 2 relevant
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
            ]
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

    return {"web_evidence": kept, "evidence_ok": relevant_count >= 2}


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
        response = call_with_retry(prosecutor_model,
            [
                SystemMessage(content=prosecutor_prompt),
                HumanMessage(content=f"Claim: {state['claim']}\n\nEvidence:\n{evidence}"),
            ]
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
        response = call_with_retry(defender_model,
            [
                SystemMessage(content=defender_prompt),
                HumanMessage(content=f"Claim: {state['claim']}\n\nEvidence:\n{evidence}"),
            ]
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
            response = call_with_retry(judge_model,messages)
        except Exception as e:
            last_error = e
            continue

        if response is not None:
            return {"verdict": response}
        last_error = ValueError("Judge returned no verdict")

    raise RuntimeError(f"Judge failed after retry: {last_error}") from last_error


# ============================================================
# 8. manual test (python3 nodes.py)
# ============================================================

if __name__ == "__main__":
    fake_state = {
        "claim": "The online grocery market in India is projected to increase by 45 percent from 2025 to 2030."
    }
    for step in (retrieve_docs, grade_doc, web_search, grade_web):
        fake_state.update(step(fake_state))
    print("doc:", len(fake_state["doc_evidence"]), "web:", len(fake_state["web_evidence"]))
    print("evidence_ok:", fake_state["evidence_ok"])
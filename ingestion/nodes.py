import json
import os
import re

from dotenv import load_dotenv
from langchain_community.document_loaders import PyPDFLoader
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langchain_text_splitters import RecursiveCharacterTextSplitter

from ingestion.prompt import extractor_prompt, ranker_prompt
from ingestion.state import Claim, ExtractedClaims, RankedClaim, RankedClaims

load_dotenv()


# ============================================================
# LOAD + CHUNK THE SOURCE PDF
# ============================================================

pdf_path = "data/usda_qcommerce.pdf"

loader = PyPDFLoader(pdf_path)
documents = loader.load()

all_pages_text = []

for document in documents:
    all_pages_text.append(document.page_content)

total_text = "".join(all_pages_text)
cleaned_text = total_text.strip()
if len(cleaned_text) < 50:
    raise ValueError("Not enough text in PDF")

pages = []
for document in documents:
    pages.append({"page": document.metadata["page"] + 1, "text": document.page_content})


splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)


chunks = []
for page in pages:
    page_chunk = splitter.split_text(page["text"])

    for chunk in page_chunk:

        chunks.append({"page": page["page"], "text": chunk})


# ============================================================
# CLAIM EXTRACTION (chunk -> list[Claim])
# ============================================================

extractor_model = ChatGroq(model=os.getenv("MODEL_NAME")).with_structured_output(
    ExtractedClaims
)


# runs every chunk through the extractor and tags each claim with its source page
def claim_extractor(chunks):
    claims = []
    for chunk in chunks:
        text = chunk["text"]
        page = chunk["page"]

        response = extractor_model.invoke(
            [
                SystemMessage(content=extractor_prompt),
                HumanMessage(content=f"Page: {page}\n\nText:\n{text}"),
            ]
        )
        for claim in response.claims:
            claim.page = page
        claims.extend(response.claims)
    return {"claims": claims}


# ============================================================
# DEDUPLICATION (drop near-identical claims by page + type + numbers)
# ============================================================

def extract_numbers(text):

    return set(re.findall(r"\d+(?:\.\d+)?", text))


def deduplicate_claims(claims):
    unique_claims = []
    seen = set()

    for claim in claims:
        numbers = frozenset(extract_numbers(claim.text))
        key = (claim.page, claim.claim_type, numbers)

        if key in seen:
            continue
        seen.add(key)
        unique_claims.append(claim)

    return unique_claims


# ============================================================
# RISK RANKING (score each unique claim 1-10)
# ============================================================

ranker_model = ChatGroq(
    model=os.getenv("MODEL_NAME"), temperature=0
).with_structured_output(RankedClaims)


# dedupes, sends the claim list to the ranker model, and maps scores back to their original Claim objects
def ranker(claims: list):
    unique_claims = deduplicate_claims(claims=claims)

    claim_lines = []
    for i, claim in enumerate(unique_claims):
        claim_lines.append(
            f"{i+1}. [{claim.claim_type}, page {claim.page}] {claim.text}"
        )

    claim_text = "\n".join(claim_lines)

    try:
        response = ranker_model.invoke(
            [
                SystemMessage(content=ranker_prompt),
                HumanMessage(
                    content=f"Score the risk of each of these claims:\n\n{claim_text}"
                ),
            ]
        )
    except Exception as e:
        raise RuntimeError(f"Ranker LLM call Failed: {e}") from e

    if response is None:
        raise ValueError("Ranker returned no result")

    result = []
    for rc in response.claims:
        if rc.claim_number < 1 or rc.claim_number > len(unique_claims):
            continue  # LLM gave an out-of-range claim_number, skip it
        original_claim = unique_claims[rc.claim_number - 1]
        result.append(
            {
                "claim": original_claim,
                "risk_score": rc.risk_score,
                "risk_reason": rc.risk_reason,
            }
        )

    return {"ranked_claims": result}


# picks the n highest-risk claims
def select_top_claims(ranked_claims, n=3):
    sorted_claims = sorted(ranked_claims, key=lambda rc: rc["risk_score"], reverse=True)
    return sorted_claims[:n]


# ============================================================
# CACHE (avoid re-running the LLM pipeline on every run)
# ============================================================

def save_cache(ranked_claims, path):
    data_to_save = []
    for item in ranked_claims:
        data_to_save.append(
            {
                "claim": item["claim"].model_dump(),
                "risk_score": item["risk_score"],
                "risk_reason": item["risk_reason"],
            }
        )

    with open(path, "w") as f:
        json.dump(data_to_save, f, indent=2)


def load_cache(path):
    if not os.path.exists(path):
        return None

    with open(path, "r") as f:
        data = json.load(f)

    ranked_claims = []
    for item in data:
        ranked_claims.append(
            {
                "claim": Claim(**item["claim"]),
                "risk_score": item["risk_score"],
                "risk_reason": item["risk_reason"],
            }
        )

    return ranked_claims


cache_path = "data/usda_qcommerce_claims.json"


# ============================================================
# SCRIPT ENTRY POINT (build/refresh the cache, print top claims)
# ============================================================

if __name__ == "__main__":
    cache_path = "data/usda_qcommerce_claims.json"

    cached = load_cache(cache_path)
    if cached is not None:
        print("Cache mila, LLM call nahi ho rahi")
        ranked_claims = cached
    else:
        print("Cache nahi mila, poora pipeline chal raha hai")
        result = claim_extractor(chunks)  # poore document par, chunks[:5] nahi
        unique = deduplicate_claims(result["claims"])
        ranked = ranker(unique)
        ranked_claims = ranked["ranked_claims"]
        save_cache(ranked_claims, cache_path)

    top_claims = select_top_claims(ranked_claims, n=3)
    for rc in top_claims:
        print(rc["risk_score"], "-", rc["claim"].text)

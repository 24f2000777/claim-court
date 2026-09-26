from pydantic import BaseModel, Field
from typing import Literal


# ============================================================
# EXTRACTION SCHEMAS (claim_extractor output)
# ============================================================

class Claim(BaseModel):
    text: str = Field(
        description="A single checkable factual claim, rewritten to stand on its own without pronouns or missing context (for example 'India's online grocery market was valued at $8.8 billion in 2024', not 'it was worth $8.8 billion')."
    )
    source_quote: str = Field(
        description="The exact line or sentence from the document this claim is based on, copied word for word."
    )
    page: int = Field(
        description="The page number of the document where this claim appears."
    )
    claim_type: Literal["numeric", "comparative", "causal", "other"] = Field(
        description="numeric for a specific number or statistic, comparative for a comparison between two things, causal for a cause-and-effect statement, other for anything else checkable."
    )


class ExtractedClaims(BaseModel):
    claims: list[Claim] = Field(
        description="All checkable factual claims found in this chunk. Return an empty list if none are found."
    )


# ============================================================
# RANKING SCHEMAS (ranker output)
# ============================================================

class RankedClaim(BaseModel):
    claim_number: int = Field(description="The number of this claim as given in the input list (1, 2, 3, ...)")
    risk_score: int = Field(
        ge=1, le=10,
        description="Risk score from 1 to 10. Higher means the claim is a specific number or comparison, has no cited source or baseline, and is central to the document's main argument. Lower means the claim is vague, peripheral, or already well-sourced."
    )
    risk_reason: str = Field(description="One short sentence explaining why this claim got this risk score")


class RankedClaims(BaseModel):
    claims: list[RankedClaim] = Field(description="A risk score and reason for every claim number given, in any order")

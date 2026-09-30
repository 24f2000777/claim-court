from typing import Annotated, TypedDict, Literal
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field



# ============================================================
# VERDICT SCHEMA (judge's structured output)
# ============================================================

class VerdictClass(BaseModel):
    # final call on the claim, plus the reasoning and confidence behind it
    label: Literal["supported", "disputed", "unsupported"] = Field(
        description=(
            "Final decision on the claim. "
            "'supported' if the defender's case is clearly stronger, "
            "'unsupported' if the prosecutor's case is clearly stronger or no real evidence backs the claim, "
            "'disputed' if both sides make strong points or the evidence is mixed."
        ))
    reasoning: str = Field(
        description=(
            "2 to 3 sentences explaining the decision. "
            "Mention the strongest point from each side and say which one decided the outcome."
        )
    )
    confidence: float = Field(
        ge=0,
        le=1,
        description=(
            "How sure the judge is, as a number between 0 and 1. "
            "Use a low value (below 0.5) when the evidence is thin or the cases are close, "
            "and a high value (above 0.8) only when one side is clearly stronger."
        ),
    )


# ============================================================
# COURT GRAPH STATE (shared across prosecutor/defender/judge nodes)
# ============================================================

class CourtState(TypedDict):
    claim: str
    prosecutor_case: str
    defender_case: str
    verdict: VerdictClass

    search_query: str
    retry_count: int
    doc_evidence: list[dict]
    web_evidence: list[dict]
    evidence_ok: bool
    citation_notes: list[dict]
    reviewer_note: str  # human feedback sent back to the lawyers
    review_rounds: int  # how many times the cases were sent back
    review_status: str  # "declined" when the human refuses to send the case to the judge
    messages: Annotated[list, add_messages]  # follow-up chat, saved with the trial by the checkpointer



class ItemGrade(BaseModel):
    item_number: int = Field(description="The number of the evidence item being graded, matching its position in the numbered list given in the prompt")
    grade: str = Field(description="One of: relevant, ambiguous, irrelevant")
    reasoning: str = Field(description="One short sentence explaining why this grade was given")

class EvidenceGrades(BaseModel):
    grades: list[ItemGrade] = Field(description="Exactly one ItemGrade per evidence item, in any order, matched back by item_number")


class CitationCheck(BaseModel):
    item_number: int = Field(description="The number of the citation being checked, matching its position in the numbered list given in the prompt")
    label: str = Field(description="The evidence label this citation used, for example W3 or D1")
    verified: bool = Field(description="True if the evidence supports what the lawyer claimed, False otherwise")
    reason: str = Field(description="One short sentence comparing what was claimed to what the evidence actually says")

class CitationChecks(BaseModel):
    checks: list[CitationCheck] = Field(description="Exactly one CitationCheck per citation, in any order, matched back by item_number")
from typing import TypedDict, Literal, Annotated
from pydantic import BaseModel, Field
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


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
    prosecutor_messages:Annotated[list[BaseMessage],add_messages]
    defender_messages:Annotated[list[BaseMessage],add_messages]

    search_query:str
    retry_count: int
    doc_evidence: list[dict]
    web_evidence: list[dict]
    evidence_ok: bool

class ItemGrade(BaseModel):
    item_number: int = Field(
        description="The number of the evidence item as given in the input list (1, 2, 3, ...)"
    )
    grade: Literal["relevant", "ambiguous", "irrelevant"] = Field(
        description=(
            "'relevant' if the item is on the claim's topic and gives a figure, date or "
            "statement that can be compared with the claim. "
            "'ambiguous' if it is on the topic but too vague or incomplete to use. "
            "'irrelevant' if it is off-topic, or is only the claim's own original source "
            "restating the claim."
        )
    )
    reason: str = Field(description="One short sentence explaining the grade")


class EvidenceGrades(BaseModel):
    grades: list[ItemGrade] = Field(
        description="One grade for every evidence item number given in the input"
    )

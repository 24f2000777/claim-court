from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from prompt import prosecutor_prompt, defender_prompt, judge_prompt
from state import CourtState, VerdictClass
import os

load_dotenv()


# ============================================================
# MODELS (one per trial role, judge forced into structured output)
# ============================================================

prosecutor_model = ChatGroq(model=os.getenv("MODEL_NAME"), temperature=0.3)
defender_model = ChatGroq(model=os.getenv("MODEL_NAME"), temperature=0.3)
judge_model = ChatGroq(model=os.getenv("MODEL_NAME"), temperature=0).with_structured_output(VerdictClass)


# ============================================================
# PROSECUTOR NODE
# ============================================================

# builds the case against the claim
def prosecutor(state: CourtState):
    claim = state["claim"]
    messages = [
        SystemMessage(content=prosecutor_prompt),
        HumanMessage(content=f"Claim: {claim}"),
    ]

    try:
        response = prosecutor_model.invoke(messages)
    except Exception as e:
        raise RuntimeError(f"Prosecutor LLM call failed: {e}") from e

    case = (response.content or "").strip()

    if not case:
        raise ValueError("Prosecutor returned an empty case")
    return {"prosecutor_case": case}


# ============================================================
# DEFENDER NODE
# ============================================================

# builds the case for the claim
def defender(state: CourtState):
    claim = state["claim"]

    prompt = defender_prompt
    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=f"Claim: {claim}")
    ]

    try:
        response = defender_model.invoke(messages)
    except Exception as e:
        raise RuntimeError(f"Defender LLM call failed :{e}") from e

    case = (response.content or "").strip()

    if not case:
        raise ValueError("Defender returned an empty case")

    return {
        "defender_case": case
    }


# ============================================================
# JUDGE NODE
# ============================================================

# weighs both cases and produces the final structured verdict (retries once on failure)
def judge(state: CourtState):

    claim = state["claim"]
    prosecutor_case = state.get("prosecutor_case")
    defender_case = state.get("defender_case")
    prompt = judge_prompt

    if not prosecutor_case or not defender_case:
        raise ValueError("Judge needs both prosecutor_case and defender_case")

    messages = [SystemMessage(content=prompt),
                HumanMessage(content=(
                    f"Claim: {claim}\n\n"
                    f"Prosecutor's case:\n{prosecutor_case}\n\n"
                    f"Defender's case:\n{defender_case}"
                ))]

    last_error = None

    for _ in range(2):

        try:
            response = judge_model.invoke(messages)
        except Exception as e:
            last_error = e
            continue

        if response is not None:
            return {"verdict": response}

        last_error = ValueError("Judge returned no verdict")

    raise RuntimeError(f"Judge failed after retry: {last_error}") from last_error

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage,AIMessage,ToolMessage
from langchain_groq import ChatGroq
from langgraph.prebuilt import ToolNode

from prompt import prosecutor_prompt, defender_prompt, judge_prompt
from state import CourtState, VerdictClass
import os

from retrieval.vectorstore import retrieve_top_chunks
from retrieval.websearch import web_search_tool

load_dotenv()

tools=[retrieve_top_chunks,web_search_tool]
prosecutor_tool_node=ToolNode(tools=tools,messages_key="prosecutor_messages")
defender_tool_node=ToolNode(tools=tools,messages_key="defender_messages")

# ============================================================
# MODELS (one per trial role, judge forced into structured output)
# ============================================================

prosecutor_model = ChatGroq(model=os.getenv("MODEL_NAME"), temperature=0.3)
defender_model = ChatGroq(model=os.getenv("MODEL_NAME"), temperature=0.3)
judge_model = ChatGroq(model=os.getenv("MODEL_NAME"), temperature=0).with_structured_output(VerdictClass)


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

prosecutor_llm_with_tools=prosecutor_model.bind_tools(tools=tools)
defender_llm_with_tools=defender_model.bind_tools(tools=tools)

def prosecutor_agent(state:CourtState):
    messages=state.get("prosecutor_messages",[])
    if not messages:
        claim = state["claim"]
        messages = [
            SystemMessage(content=prosecutor_prompt),
            HumanMessage(content=f"Claim: {claim}"),
        ]

    tool_call_count=0

    for m in messages:
        if isinstance(m,ToolMessage):
            tool_call_count += 1

    if tool_call_count>=2:
        response=prosecutor_model.invoke(messages)
    else:
        response=prosecutor_llm_with_tools.invoke(messages)
    result = {"prosecutor_messages": [response]}

    if not response.tool_calls:
        case=(response.content or "").strip()

        if not case:
            raise ValueError("Prosecutor returned an empty case")
        result["prosecutor_case"] = case
    return result


def defender_agent(state:CourtState):
    messages=state.get("defender_messages" ,[])

    if not messages:
        claim=state["claim"]
        messages=[
            SystemMessage(content=defender_prompt),
            HumanMessage(content=f"Claim: {claim}")
        ]
    tool_call_count=0

    for m in messages:
        if isinstance(m,ToolMessage):
            tool_call_count += 1

    if tool_call_count>=2:
        response=defender_model.invoke(messages)
    else:
        response=defender_llm_with_tools.invoke(messages)

    result={"defender_messages":[response]}

    if not response.tool_calls:
        case=(response.content or "").strip()

        if not case:
            raise ValueError("Defender returned an empty case")
        result["defender_case"] = case
    return result

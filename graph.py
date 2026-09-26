from functools import partial

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import tools_condition

from nodes import (judge, prosecutor_agent, prosecutor_tool_node,
                   defender_agent, defender_tool_node)
from state import CourtState

# tools_condition defaults to reading "messages"; our state keeps each side's
# tool-calling loop under its own key, so each needs to be told where to look
prosecutor_tools_condition = partial(tools_condition, messages_key="prosecutor_messages")
defender_tools_condition = partial(tools_condition, messages_key="defender_messages")

# ============================================================
# GRAPH DEFINITION
# ============================================================

graph = StateGraph(CourtState)

# ------------------------------------------------------------
# nodes (register everything before wiring edges)
# ------------------------------------------------------------
graph.add_node("prosecutor_agent", prosecutor_agent)
graph.add_node("prosecutor_tools", prosecutor_tool_node)
graph.add_node("defender_agent", defender_agent)
graph.add_node("defender_tools", defender_tool_node)
graph.add_node("judge_node", judge)

# ------------------------------------------------------------
# 1. START -> prosecutor loop -> defender_agent
# ------------------------------------------------------------
graph.add_edge(START, "prosecutor_agent")

graph.add_conditional_edges(
    "prosecutor_agent",
    prosecutor_tools_condition,
    {"tools": "prosecutor_tools", "__end__": "defender_agent"}
)
graph.add_edge("prosecutor_tools", "prosecutor_agent")   # loop back until no more tool calls

# ------------------------------------------------------------
# 2. defender loop -> judge_node
# ------------------------------------------------------------
graph.add_conditional_edges(
    "defender_agent",
    defender_tools_condition,
    {"tools": "defender_tools", "__end__": "judge_node"}
)
graph.add_edge("defender_tools", "defender_agent")   # loop back until no more tool calls

# ------------------------------------------------------------
# 3. judge_node -> END
# ------------------------------------------------------------
graph.add_edge("judge_node", END)

# compiled, importable graph
court_graph = graph.compile()
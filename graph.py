from langgraph.graph import StateGraph, START, END
from nodes import defender, prosecutor, judge
from state import CourtState


# ============================================================
# GRAPH DEFINITION (prosecutor -> defender -> judge)
# ============================================================

graph = StateGraph(CourtState)

# nodes
graph.add_node("prosecutor_node", prosecutor)
graph.add_node("defender_node", defender)
graph.add_node("judge_node", judge)

# edges
graph.add_edge(START, "prosecutor_node")
graph.add_edge("prosecutor_node", "defender_node")
graph.add_edge("defender_node", "judge_node")
graph.add_edge("judge_node", END)

# compiled, importable graph
court_graph = graph.compile()

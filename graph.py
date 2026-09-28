from langgraph.graph import END, START, StateGraph

from nodes import (
    defender,
    grade_doc,
    grade_web,
    judge,
    prosecutor,
    retrieve_docs,
    rewrite,
    route_after_web_grade,
    web_search,
)
from state import CourtState

# ============================================================
# GRAPH: clerk (evidence pipeline) -> lawyers in parallel -> judge
# ============================================================

graph = StateGraph(CourtState)

# ------------------------------------------------------------
# nodes
# ------------------------------------------------------------
graph.add_node("retrieve_docs", retrieve_docs)
graph.add_node("grade_doc", grade_doc)
graph.add_node("web_search", web_search)
graph.add_node("grade_web", grade_web)
graph.add_node("rewrite", rewrite)
graph.add_node("prosecutor", prosecutor)
graph.add_node("defender", defender)
graph.add_node("judge_node", judge, defer=True)  # waits for both lawyers

# ------------------------------------------------------------
# 1. clerk: straight line up to grade_web
# ------------------------------------------------------------
graph.add_edge(START, "retrieve_docs")
graph.add_edge("retrieve_docs", "grade_doc")
graph.add_edge("grade_doc", "web_search")
graph.add_edge("web_search", "grade_web")

# ------------------------------------------------------------
# 2. after grading: enough evidence -> both lawyers, else rewrite and search again
# ------------------------------------------------------------
graph.add_conditional_edges(
    "grade_web",
    route_after_web_grade,
    ["prosecutor", "defender", "rewrite"],
)
graph.add_edge("rewrite", "web_search")  # retry loop

# ------------------------------------------------------------
# 3. lawyers -> judge -> END
# ------------------------------------------------------------
graph.add_edge("prosecutor", "judge_node")
graph.add_edge("defender", "judge_node")
graph.add_edge("judge_node", END)

court_graph = graph.compile()
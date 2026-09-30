import sqlite3

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver
from nodes import (
    defender,
    grade_doc,
    grade_web,
    judge,
    chat_node,
    prosecutor,
    retrieve_docs,
    rewrite,
    route_after_web_grade,
    route_start,
    web_search,
    verify_citations,
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
graph.add_node("verify_citations", verify_citations)
graph.add_node("chat_node", chat_node)

# ------------------------------------------------------------
# 1. clerk: straight line up to grade_web (a chat message on a finished trial skips to chat_node)
# ------------------------------------------------------------
graph.add_conditional_edges(START, route_start, ["retrieve_docs", "chat_node"])
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
graph.add_edge("judge_node", "verify_citations")
graph.add_edge("verify_citations", END)
graph.add_edge("chat_node", END)


conn=sqlite3.connect("checkpoint.db",check_same_thread=False)
checkpointer=SqliteSaver(conn=conn)
court_graph = graph.compile(checkpointer=checkpointer,interrupt_before=["judge_node"])


def list_thread_ids(limit=15):
    """Saved trials, most recently active first."""
    with checkpointer.lock:
        rows = conn.execute(
            "SELECT thread_id FROM checkpoints GROUP BY thread_id ORDER BY MAX(checkpoint_id) DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [row[0] for row in rows]

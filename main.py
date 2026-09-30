import uuid

from graph import court_graph
from ingestion.nodes import load_cache, cache_path, select_top_claims

# ============================================================
# PICK THE CLAIM TO TRY (highest-risk claim from the cached ranking)
# ============================================================

cached = load_cache(cache_path)
top_claims = select_top_claims(ranked_claims=cached, n=1)
top_claim_text = top_claims[0]["claim"].text

thread_id = str(uuid.uuid4())
config = {"configurable": {"thread_id": thread_id}}

print("CLAIM:", top_claim_text)
print("=" * 100)

# ============================================================
# STREAM UP TO THE JUDGE (prints each node's result as soon as it finishes)
# ============================================================

for update in court_graph.stream({"claim": top_claim_text}, config=config, stream_mode="updates"):
    for node_name, node_output in update.items():
        if node_name == "__interrupt__":
            continue  # internal marker, nothing useful to print here

        print(f"\n>>> [{node_name}] done")
        if node_name == "prosecutor":
            print(node_output["prosecutor_case"])
        elif node_name == "defender":
            print(node_output["defender_case"])
        elif node_name == "web_search":
            print(f"search query used: {node_output.get('search_query')}")

print("\n" + "=" * 100)

# ============================================================
# HUMAN CHECKPOINT: let a person approve before the judge decides
# ============================================================

answer = input("Send this to the judge for a verdict? (y/n): ").strip().lower()

if answer != "y":
    print("Trial paused. Resume later by streaming again with the same thread_id.")
    print(f"THREAD ID: {thread_id}")
else:
    for update in court_graph.stream(None, config=config, stream_mode="updates"):
        for node_name, node_output in update.items():
            print(f"\n>>> [{node_name}] done")
            if node_name == "judge_node":
                print("VERDICT:", node_output["verdict"])
            elif node_name == "verify_citations":
                print("CITATION CHECK:")
                for note in node_output.get("citation_notes", []):
                    status = "OK" if note["verified"] else "FAIL"
                    print(f"[{status}] {note['side']} ({note['label']}): {note['reason']}")

    print("=" * 100)
    print(f"THREAD ID: {thread_id}")
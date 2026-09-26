from graph import court_graph
from ingestion.nodes import load_cache, cache_path, select_top_claims

# ============================================================
# PICK THE CLAIM TO TRY (highest-risk claim from the cached ranking)
# ============================================================

cached = load_cache(cache_path)
top_claims = select_top_claims(ranked_claims=cached, n=1)
top_claim_text = top_claims[0]["claim"].text

# ============================================================
# RUN THE TRIAL (prosecutor -> defender -> judge)
# ============================================================

response = court_graph.invoke({"claim": top_claim_text})

print("CLAIM:", top_claim_text)
print("=" * 100)
print("PROSECUTOR CASE:")
print(response["prosecutor_case"])
print("=" * 100)

# raw message trail, useful for checking which tool calls the prosecutor actually made
print("PROSECUTOR MESSAGES (to see tool calls):")
for msg in response["prosecutor_messages"]:
    print(type(msg).__name__, "-", str(msg.content)[:200])
    print("-" * 40)
print("=" * 100)
print("DEFENDER CASE:")
print(response["defender_case"])
print("=" * 100)
print("VERDICT:")
print(response["verdict"])
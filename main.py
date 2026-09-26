from graph import court_graph
from ingestion.nodes import load_cache, save_cache, cache_path, select_top_claims


# ============================================================
# LOAD RANKED CLAIMS (expects ingestion/nodes.py to have run at least once)
# ============================================================

cached = load_cache(cache_path)
if cached is None:
    raise RuntimeError(
        f"No cache found at {cache_path}. Run ingestion/nodes.py first to build it."
    )

print("Cache is there fetching data from cache")
ranked_claims = cached


# ============================================================
# RUN THE HIGHEST-RISK CLAIM THROUGH THE COURT GRAPH
# ============================================================

top_claims = select_top_claims(ranked_claims=ranked_claims, n=1)

response = court_graph.invoke({"claim": top_claims[0]['claim'].text})

print(type(response))

for key in response.keys():
    print(f"{key}: {response[key]}")
    print("==" * 50)

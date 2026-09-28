import json
import os
import sys
import time

from graph import court_graph

CLAIMS_PATH = "eval/claims.json"
RESULTS_PATH = "eval/results.json"
GAP_SECONDS = 30  # pause between claims so the per-minute token limit resets


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def run_one(claim):
    response = court_graph.invoke({"claim": claim})
    verdict = response["verdict"]
    return {
        "label": verdict.label,
        "confidence": verdict.confidence,
        "reasoning": verdict.reasoning,
        "retries": response.get("retry_count", 0),
        "web_items": len(response.get("web_evidence", [])),
    }


def print_summary(claims, results):
    stats = {}  # source -> [correct, total]
    errors = 0

    for item in claims:
        result = results.get(item["claim"])
        if result is None:
            continue
        if "error" in result:
            errors += 1
            continue

        source = item["source"]
        stats.setdefault(source, [0, 0])
        stats[source][1] += 1
        if result["label"] == item["expected_label"]:
            stats[source][0] += 1

    total_correct = sum(v[0] for v in stats.values())
    total = sum(v[1] for v in stats.values())

    print("\n" + "=" * 60)
    for source, (correct, count) in stats.items():
        print(f"{source}: {correct}/{count}")
    if total:
        print(f"OVERALL: {total_correct}/{total} = {total_correct / total:.0%}")
    if errors:
        print(f"errors (not counted): {errors}")


def main():
    claims = load_json(CLAIMS_PATH, [])
    results = load_json(RESULTS_PATH, {})

    # optional: python3 -m eval.run_eval 2  -> run only the first 2 claims
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(claims)
    claims = claims[:limit]

    for i, item in enumerate(claims):
        claim = item["claim"]

        # already done in an earlier run, skip (delete results.json to redo)
        if claim in results and "error" not in results[claim]:
            print(f"[{i+1}/{len(claims)}] skipped (already done): {claim[:60]}")
            continue

        print(f"[{i+1}/{len(claims)}] running: {claim[:60]}")
        try:
            results[claim] = run_one(claim)
        except Exception as e:
            print(f"  failed: {e}")
            results[claim] = {"error": str(e)}

        r = results[claim]
        if "error" not in r:
            ok = "OK " if r["label"] == item["expected_label"] else "MISS"
            print(f"  {ok} expected={item['expected_label']} got={r['label']} "
                  f"conf={r['confidence']} retries={r['retries']}")

        # save after every claim so a crash or quota stop loses nothing
        with open(RESULTS_PATH, "w") as f:
            json.dump(results, f, indent=2)

        if i < len(claims) - 1:
            time.sleep(GAP_SECONDS)

    print_summary(claims, results)


if __name__ == "__main__":
    main()
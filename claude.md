# Claim Court

Adversarial multi-agent fact-checking system built with LangGraph, RAG, and Groq. A Prosecutor and Defender argue opposite sides of a factual claim using corrected retrieval (CRAG) and self-auditing citation verification, before a Judge delivers a verdict.

## Tech stack
- Orchestration: LangGraph (StateGraph, conditional edges, `defer=True` fan-in)
- LLM: Groq — `openai/gpt-oss-120b` primary, `openai/gpt-oss-20b` fallback (plain-text roles only)
- Structured output: Pydantic models via `.with_structured_output()`
- Document RAG: Chroma vector store + SentenceTransformer embeddings (`all-MiniLM-L6-v2`)
- Web search: Tavily Search API
- UI: Streamlit (`app.py`)
- Persistence: SQLite checkpointer (`langgraph-checkpoint-sqlite`)

## Project structure
claim-court/
├── main.py # CLI entry point, runs a single claim through the trial graph
├── app.py # Streamlit web UI: PDF upload, claim extraction, trial, chat, sidebar conversations (new/resume/delete), sidebar time travel (get_state_history)
├── state.py # CourtState, VerdictClass, EvidenceGrades, CitationChecks
├── prompt.py # all prompts: prosecutor, defender, judge, graders, query writer, citation checker
├── nodes.py # graph nodes: retrieval, grading, lawyers, judge, citation verification
├── graph.py # StateGraph wiring, SQLite checkpointer, interrupt_before=["judge_node"]
├── ingestion/
│ ├── nodes.py # PDF loading, chunking, claim extraction, dedup, ranking
│ ├── state.py
│ └── prompt.py
├── retrieval/
│ ├── vectorstore.py # Chroma + embeddings, retrieve_top_chunks, set_active_document (dynamic doc swap)
│ └── websearch.py # Tavily search tool (two-sided: "for" + "against" queries)
├── eval/
│ ├── claims.json # 12-claim eval set with expected labels
│ ├── run_eval.py # resumable evaluation harness
│ └── results.json
└── .streamlit/config.toml # fileWatcherType = "none" (silences transformers/torchvision watcher noise)


## Graph flow
retrieve_docs → grade_doc → web_search → grade_web → (rewrite loop if evidence weak) → prosecutor + defender (parallel) → judge_node (interrupt here for human review) → verify_citations → END

A conditional edge at START (`route_start`) sends a chat message on a finished trial to `chat_node → END`. Chat history lives in `CourtState.messages` (`add_messages`) and is saved by the SQLite checkpointer, so there is no separate chat database.

## Key design decisions
- Retrieval is deterministic and pre-graded (not agent tool-calling) — grading uses batched LLM calls (one call per stage, not per-chunk) to conserve Groq free-tier quota.
- Web search runs two queries (for/against the claim) merged and deduplicated by URL, to avoid one-sided evidence biasing the Judge away from `disputed`.
- Citation verification matches by `item_number` (list position), not by label string — labels like `W3` can be cited multiple times and label-based matching caused silent overwrites.
- Prosecutor/Defender prompts: 3-5 bullets, under 200 words, no bold text, no headers, no em-dashes, natural sentence-per-bullet, each ending with the evidence label in brackets.
- Structured-output roles (Judge, graders, citation checker) do NOT fall back to the smaller model — unreliable at tool-call-style output under load. They retry on the primary model only and fail gracefully (skip the claim) if quota is exhausted.

## Constraints (non-negotiable)
- Must stay 100% free — no Groq paid tier, no separate Groq accounts to bypass rate limits.
- No placeholder code, proper error handling, brief comments for non-obvious logic only.
- Modify existing files rather than rewriting from scratch.
- Prefer simpler solutions over over-engineering.
- No em-dashes anywhere in code, prompts, or docs.

## Running
```bash
source venv/bin/activate
python3 -m streamlit run app.py     # web UI (use python3 -m, not bare `streamlit`, to force venv interpreter)
python3 main.py                     # CLI single-claim run
python3 -m eval.run_eval            # resumable 12-claim eval harness
```

## Known gotchas
- `streamlit` CLI command can resolve to the wrong (non-venv) Python — always use `python3 -m streamlit run app.py`.
- Streamlit's file watcher throws harmless `ModuleNotFoundError: No module named 'torchvision'` while introspecting `transformers` submodules (pulled in via `sentence-transformers`) — silenced via `.streamlit/config.toml` (`fileWatcherType = "none"`). Non-fatal, does not affect app function.
- `Deserializing unregistered type state.VerdictClass from checkpoint` warning when loading a past trial in `app.py` is a harmless LangGraph checkpointer forward-compat warning — safe to ignore.

## Status
Eval: 12/12 (100%) on the eval harness (general knowledge + USDA report claims), reproduced across multiple runs — note this predates the batch-grading/citation-matching/prompt fixes made most recently, so a re-run to reconfirm is a good idea. Phase 8 (persistence, human-in-the-loop, time-travel) and the Streamlit UI (PDF upload with auto claim extraction, manual claim fallback, live progress via `st.status`, post-verdict chat) are both built and working end-to-end.
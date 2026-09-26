import os
from langchain_tavily import TavilySearch
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# WEB SEARCH TOOL (exposed to the prosecutor agent)
# ============================================================

web_search_tool=TavilySearch(max_results=3)

# quick manual check: run this file directly to sanity-test the search tool
if __name__ == "__main__":
    result = web_search_tool.invoke("India online grocery market growth 2025 2030")
    print(result)
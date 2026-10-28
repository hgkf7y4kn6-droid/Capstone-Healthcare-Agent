"""
Trusted medical information search: queries MedlinePlus (NLM) directly via
its free public XML API, with an optional Tavily web-search fallback/boost
for broader coverage (e.g. WHO fact sheets) if TAVILY_API_KEY is configured.

This is the retrieval half of the RAG pipeline for disease-info queries —
retrieved snippets are passed to the LLM (see agents/rag_chain.py) for
grounded summarization rather than being shown raw.
"""

import requests
from crewai.tools import tool

import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from integrations.medlineplus import search_medlineplus as _search_medlineplus, search_tavily as _search_tavily
from monitoring.monitor import track


@tool("search_disease_information")
def search_disease_information(query: str) -> str:
    """Search trusted medical sources (MedlinePlus, and WHO via web search if
    configured) for up-to-date information on a disease, condition, or
    treatment. Returns raw source snippets with titles and URLs — the agent
    should synthesize/summarize these for the user rather than quoting
    directly, and should cite MedlinePlus/WHO as the source."""
    with track("medical_search.search_disease_information", meta={"query": query}) as status:
        results = []
        try:
            results.extend(_search_medlineplus(query))
        except requests.exceptions.RequestException as e:
            results.append({"source": "MedlinePlus", "title": "", "snippet": f"MedlinePlus lookup failed: {e}", "url": ""})

        results.extend(_search_tavily(query))

        if not results:
            status["success"] = False
            return f"No trusted medical sources found for '{query}'. Advise the user to consult a licensed clinician."
        return str(results)


MEDICAL_SEARCH_TOOLS = [search_disease_information]

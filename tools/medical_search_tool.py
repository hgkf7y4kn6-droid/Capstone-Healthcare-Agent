"""
Trusted medical information search: queries MedlinePlus (NLM) directly via
its free public XML API, with an optional Tavily web-search fallback/boost
for broader coverage (e.g. WHO fact sheets) if TAVILY_API_KEY is configured.

This is the retrieval half of the RAG pipeline for disease-info queries —
retrieved snippets are passed to the LLM (see agents/rag_chain.py) for
grounded summarization rather than being shown raw.
"""

import requests
import xml.etree.ElementTree as ET
from crewai.tools import tool

import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import TAVILY_API_KEY
from monitoring.monitor import track

MEDLINEPLUS_ENDPOINT = "https://wsearch.nlm.nih.gov/ws/query"


def _search_medlineplus(query: str, max_results: int = 3) -> list[dict]:
    params = {"db": "healthTopics", "term": query, "retmax": max_results}
    resp = requests.get(MEDLINEPLUS_ENDPOINT, params=params, timeout=15)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    results = []
    for doc in root.findall(".//document"):
        title, snippet, url = "", "", doc.get("url", "")
        for content in doc.findall("content"):
            name = content.get("name")
            text = "".join(content.itertext()).strip()
            if name == "title":
                title = text
            elif name in ("snippet", "FullSummary"):
                snippet = text[:600]
        if title:
            results.append({"source": "MedlinePlus", "title": title, "snippet": snippet, "url": url})
    return results


def _search_tavily(query: str, max_results: int = 3) -> list[dict]:
    if not TAVILY_API_KEY:
        return []
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": f"{query} site:who.int OR site:medlineplus.gov",
                "max_results": max_results,
                "include_answer": False,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {"source": "WebSearch(WHO/Medline)", "title": r.get("title", ""),
             "snippet": (r.get("content", "") or "")[:600], "url": r.get("url", "")}
            for r in data.get("results", [])
        ]
    except requests.exceptions.RequestException:
        return []


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

"""
Trusted medical information lookup — MedlinePlus (NLM) via its free public
XML API, plus an optional Tavily boost restricted to WHO/MedlinePlus when
TAVILY_API_KEY is set.

Plain functions with no CrewAI dependency, so both the CrewAI tool
(tools/medical_search_tool.py) and the deterministic concierge
(agents/concierge.py) share one implementation.
"""

import os
import re
import sys
import html
import xml.etree.ElementTree as ET

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import TAVILY_API_KEY

MEDLINEPLUS_ENDPOINT = "https://wsearch.nlm.nih.gov/ws/query"
_TAG = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub("", text or ""))).strip()


def search_medlineplus(query: str, max_results: int = 3, timeout: float = 15) -> list[dict]:
    params = {"db": "healthTopics", "term": query, "retmax": max_results}
    resp = requests.get(MEDLINEPLUS_ENDPOINT, params=params, timeout=timeout)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    results = []
    for doc in root.findall(".//document"):
        title, snippet, url = "", "", doc.get("url", "")
        for content in doc.findall("content"):
            name = content.get("name")
            text = "".join(content.itertext()).strip()
            if name == "title":
                title = _clean(text)
            elif name in ("snippet", "FullSummary") and not snippet:
                snippet = _clean(text)[:600]
        if title:
            results.append({"source": "MedlinePlus", "title": title, "snippet": snippet, "url": url})
    return results


def search_tavily(query: str, max_results: int = 3, timeout: float = 15) -> list[dict]:
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
            timeout=timeout,
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

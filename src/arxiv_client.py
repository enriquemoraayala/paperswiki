"""
arXiv API client.
Fetches papers matching given keywords/categories published within a date window.
"""

import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

import requests

if TYPE_CHECKING:
    from src.logger import AgentLogger

ARXIV_API_URL = "http://export.arxiv.org/api/query"
NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _build_query(keywords: list, categories: list) -> str:
    """Build an arXiv search query string from keywords and categories."""
    keyword_parts = [f'all:"{kw}"' for kw in keywords]
    cat_parts = [f"cat:{c}" for c in categories] if categories else []

    kw_query = " OR ".join(keyword_parts)
    cat_query = " OR ".join(cat_parts)

    if kw_query and cat_query:
        return f"({kw_query}) AND ({cat_query})"
    return kw_query or cat_query


def _parse_entry(entry: ET.Element) -> dict:
    """Parse a single arXiv Atom entry into a paper dict."""
    arxiv_id = entry.findtext("atom:id", namespaces=NS) or ""
    # Normalise ID: extract short ID from URL
    short_id = arxiv_id.rstrip("/").split("/abs/")[-1]

    published_raw = entry.findtext("atom:published", namespaces=NS) or ""
    published = published_raw[:10]  # YYYY-MM-DD

    authors = [
        a.findtext("atom:name", namespaces=NS) or ""
        for a in entry.findall("atom:author", namespaces=NS)
    ]

    return {
        "id": short_id,
        "url": f"https://arxiv.org/abs/{short_id}",
        "title": (entry.findtext("atom:title", namespaces=NS) or "").strip().replace("\n", " "),
        "abstract": (entry.findtext("atom:summary", namespaces=NS) or "").strip().replace("\n", " "),
        "authors": authors,
        "published": published,
    }


def fetch_papers(
    keywords: list,
    categories: list,
    days: int = 7,
    max_results: int = 20,
    topic_name: str = "",
    logger: Optional["AgentLogger"] = None,
) -> list:
    """
    Query arXiv and return papers published within the last `days` days.
    Papers are sorted by submission date (newest first).
    """
    query = _build_query(keywords, categories)
    if not query:
        return []

    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": max_results,
    }

    t0 = time.monotonic()
    try:
        resp = requests.get(ARXIV_API_URL, params=params, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"arXiv API request failed: {exc}") from exc

    root = ET.fromstring(resp.content)
    entries = root.findall("atom:entry", namespaces=NS)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    papers = []
    for entry in entries:
        paper = _parse_entry(entry)
        if paper["published"]:
            pub_date = datetime.strptime(paper["published"], "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            if pub_date >= cutoff:
                papers.append(paper)

    duration_ms = int((time.monotonic() - t0) * 1000)

    if logger:
        logger.log_arxiv_fetch(
            topic=topic_name,
            query=query,
            max_results=max_results,
            returned=len(entries),
            in_window=len(papers),
            duration_ms=duration_ms,
        )

    # Be polite to arXiv API
    time.sleep(0.5)
    return papers

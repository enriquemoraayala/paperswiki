"""
MCP-backed arXiv client.

Drop-in replacement for src/arxiv_client.py — exposes the same
`fetch_papers` signature but routes all searches through the local
`mcp_server.py` FastMCP server via stdio transport.
"""

import asyncio
import json
import pathlib
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.logger import AgentLogger

# Absolute path to mcp_server.py (lives at the project root, one level up from src/)
_MCP_SERVER = pathlib.Path(__file__).parent.parent / "mcp_server.py"


def _build_query(keywords: list, categories: list) -> str:
    """Build a natural-language topic query from keywords and categories."""
    parts = list(keywords)
    if categories:
        parts += [f"cat:{c}" for c in categories]
    return " ".join(parts)


async def _search_via_mcp(topic: str, max_results: int) -> list[dict]:
    """Call search_papers on the MCP server and return the raw result list."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command="uv",
        args=["run", "python", str(_MCP_SERVER)],
        cwd=str(_MCP_SERVER.parent),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "search_papers",
                {"topic": topic, "max_results": max_results},
            )
    # FastMCP serialises each list item as a separate TextContent object.
    papers_raw = []
    for item in result.content:
        text = item.text if hasattr(item, "text") else str(item)
        if text:
            try:
                papers_raw.append(json.loads(text))
            except json.JSONDecodeError:
                pass
    return papers_raw


def fetch_papers(
    keywords: list,
    categories: list,
    days: int = 7,
    max_results: int = 20,
    topic_name: str = "",
    logger: Optional["AgentLogger"] = None,
) -> list:
    """
    Search arXiv via the local MCP server and return papers published
    within the last `days` days.

    Signature is identical to src.arxiv_client.fetch_papers so agent.py
    only needs to update the import.
    """
    query = _build_query(keywords, categories)
    if not query:
        return []

    t0 = time.monotonic()
    raw_papers: list[dict] = asyncio.run(_search_via_mcp(query, max_results))

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    papers = []
    for p in raw_papers:
        published = p.get("published", "")
        if published:
            try:
                pub_date = datetime.strptime(published, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if pub_date < cutoff:
                    continue
            except ValueError:
                pass  # keep paper if date can't be parsed

        arxiv_id = p.get("id", "")
        papers.append({
            "id": arxiv_id,
            "url": p.get("url") or f"https://arxiv.org/abs/{arxiv_id}",
            "title": p.get("title", ""),
            "abstract": p.get("summary", p.get("abstract", "")),
            "authors": p.get("authors", []),
            "published": published,
        })

    duration_ms = int((time.monotonic() - t0) * 1000)

    if logger:
        logger.log_arxiv_fetch(
            topic=topic_name,
            query=query,
            max_results=max_results,
            returned=len(raw_papers),
            in_window=len(papers),
            duration_ms=duration_ms,
        )

    return papers

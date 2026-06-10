## Why

The `paperswiki` repository needs a paper research platform — search, summarise, browse, and deep-analyse arXiv papers. The `rndresearch` project already implements this platform but queries arXiv directly via raw HTTP; the `arxivsearcher` repository already wraps arXiv search as an MCP server with OpenTelemetry instrumentation. Migrating rndresearch into paperswiki and routing all arXiv search through the MCP gives us a standardised, observable, and swappable search backend.

## What Changes

- **Copy** the full `rndresearch` codebase (agent, summariser, deep-research, viewer, topics config) into the `paperswiki` repo root.
- **Add** the `arxivsearcher` MCP server (`mcp_server.py`) to `paperswiki` so search is served locally.
- **Replace** the direct arXiv REST API client (`src/arxiv_client.py`) with an MCP client that calls the `search_papers` tool on the local MCP server.
- **Add** `pyproject.toml` / `uv.lock` (from arxivsearcher) and update `requirements.txt` to include MCP client dependencies.
- **Add** `docker-compose.yml` (or equivalent) for running the MCP server alongside the agent.
- Keep all existing `rndresearch` features (multi-provider LLM, advanced filter, deep research, viewer, JSONL logging, topic config) fully intact.

## Capabilities

### New Capabilities

- `arxiv-research-platform`: The complete research pipeline — scan topics, filter papers, generate LLM summaries, browse via local web UI, run deep analysis.
- `mcp-arxiv-search`: ArXiv search delegated to the FastMCP server (`search_papers` tool) instead of direct REST calls. Provides OpenTelemetry traces, caching semantics, and a clean tool contract.

### Modified Capabilities

_(none — this is a greenfield addition to paperswiki; no existing specs are affected)_

## Impact

- **New files**: `agent.py`, `deep_research.py`, `viewer.py`, `mcp_server.py`, `src/`, `research_topics.yaml`, `deep_research_prompt.yaml`, `requirements.txt`, `pyproject.toml`, `docker-compose.yml`, `.env.example`
- **Dependencies added**: `mcp[client]`, `fastmcp`, `arxiv`, `anthropic`, `openai`, `certifi`, `httpx`, OpenTelemetry SDK packages
- **No breaking changes** to existing paperswiki repo structure (currently empty except `.github/` and `openspec/`)

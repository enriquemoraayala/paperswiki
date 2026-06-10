## Context

`paperswiki` is currently an empty repo (only `.github/` and `openspec/`). Two companion repos exist:

- **rndresearch** — CLI + local web viewer for scanning arXiv, summarising papers with LLMs, and running deep analysis. Search is done via direct HTTP to the arXiv Atom API (`src/arxiv_client.py`).
- **arxivsearcher** — FastMCP server (`mcp_server.py`) that wraps arXiv search (`search_papers`) and PDF download (`download_paper`) as MCP tools, with full OpenTelemetry instrumentation.

The migration goal: copy `rndresearch` into `paperswiki`, replace the direct arXiv HTTP client with an MCP client that calls `arxivsearcher`'s `search_papers` tool, and bundle the MCP server alongside the agent.

## Goals / Non-Goals

**Goals:**
- All `rndresearch` features work identically in `paperswiki` (scan, summarise, viewer, deep research, advanced filter, JSONL logging, topic config).
- ArXiv search goes exclusively through the MCP `search_papers` tool.
- The MCP server (`mcp_server.py`) lives in the `paperswiki` repo so the project is self-contained.
- `requirements.txt` and `pyproject.toml` cover all runtime dependencies.
- No configuration changes needed at runtime — the MCP server is launched automatically by the MCP client via stdio transport.

**Non-Goals:**
- The arxivsearcher React frontend is **not** copied — only the MCP server.
- No new LLM providers or summarisation models beyond what `rndresearch` already supports.
- No containerisation of the whole stack (Docker only for observability infra, same as today).
- No REST API or remote MCP server; stdio transport keeps it local and simple.

## Decisions

### 1. Transport: stdio (subprocess) over HTTP/SSE

**Decision**: The `src/mcp_arxiv_client.py` adapter starts `mcp_server.py` as a short-lived subprocess via `stdio_client` for each search request, using `asyncio.run()` to bridge the sync agent to the async MCP client.

**Alternatives considered**:
- *Persistent HTTP/SSE server*: Requires explicit server lifecycle management (`start`/`stop` around the agent run), complicates the CLI UX, and introduces failure modes if the server isn't running. stdio is zero-config.
- *Import the arxiv library directly*: Defeats the purpose — we want search observable through MCP with OpenTelemetry.

**Rationale**: stdio transport is zero-dependency (no port, no server process to manage), keeps the agent a single `python agent.py` command, and matches how the existing `mcp_servers.json` already configures the arxiv MCP server.

### 2. MCP client is a drop-in replacement for `src/arxiv_client.py`

**Decision**: Create `src/mcp_arxiv_client.py` with the same public signature as `src/arxiv_client.py` (`fetch_papers(keywords, categories, days, max_results, topic_name, logger)`). `agent.py` only changes its import, nothing else.

**Rationale**: Minimises diff in `agent.py`. The adapter converts the `(keywords, categories, days)` call convention into a natural language `topic` query string (e.g., `"causal machine learning" OR "causal inference" cat:cs.LG`) and passes it to `search_papers`. Client-side date filtering (already in the original `fetch_papers`) is preserved.

### 3. `mcp_server.py` copied verbatim, OpenTelemetry optional

**Decision**: Copy `mcp_server.py` from arxivsearcher as-is, including all OpenTelemetry instrumentation. Add OTLP/Prometheus/Grafana configs similarly. The server gracefully degrades when no OTLP collector is running (calls still succeed, spans are just dropped).

**Rationale**: OpenTelemetry is already wired in the source; removing it would lose observability for free. The fallback behaviour (`insecure=True`, collector optional) means it doesn't block development.

### 4. `pyproject.toml` + `uv` as primary package manager, `requirements.txt` kept for compatibility

**Decision**: Add `pyproject.toml` (based on arxivsearcher's) with all deps. Keep `requirements.txt` for users who prefer pip. Both are maintained.

**Rationale**: `uv` is already used in the MCP server invocation (`uv run python mcp_server.py`), so using `uv` throughout is consistent. `requirements.txt` lowers the barrier for users not using `uv`.

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| `asyncio.run()` inside a synchronous loop may conflict with running event loops (e.g., Jupyter) | Document the constraint; `viewer.py` is a standalone server, not Jupyter. `agent.py` is a CLI — no existing loop. |
| Each `fetch_papers` call spawns a new subprocess, adding ~200–500 ms startup overhead per topic | Acceptable for a batch CLI; can be optimised to a persistent subprocess pool if needed. |
| MCP server subprocess inherits the working directory — `downloads/` created relative to CWD | Document that the agent must be run from the repo root; same assumption rndresearch already makes. |
| OpenTelemetry OTLP exporter errors at startup if no collector is running | The `insecure=True` flag and exception swallowing in `_setup_telemetry` prevent hard failures. |
| `arxiv` library used by `mcp_server.py` (newer API) vs. `requests`-based XML client in `arxiv_client.py` (older) | The new MCP client replaces the old one entirely; no version conflict. |

## Migration Plan

1. Copy files from `rndresearch`: `agent.py`, `deep_research.py`, `viewer.py`, `src/`, `research_topics.yaml`, `deep_research_prompt.yaml`, `requirements.txt`, `.env.example`, `analysis/`.
2. Copy `mcp_server.py` from `arxivsearcher`.
3. Create `src/mcp_arxiv_client.py` with the MCP-backed `fetch_papers` function.
4. Replace `from src.arxiv_client import fetch_papers` with `from src.mcp_arxiv_client import fetch_papers` in `agent.py`.
5. Update `requirements.txt` and `pyproject.toml` with MCP + arxiv library deps.
6. Add `mcp_servers.json` pointing at the local `mcp_server.py`.
7. Update `README.md` to document the MCP-based search and setup steps.

**Rollback**: Revert import in `agent.py` to `src.arxiv_client`. Both files coexist in the repo.

## Open Questions

- Should `download_paper` MCP tool be wired into `deep_research.py` as well, replacing its current PDF download logic? _(Out of scope for this change — tracked separately.)_
- Should the viewer expose a UI toggle for MCP vs. direct search? _(No — keep it transparent to the user.)_

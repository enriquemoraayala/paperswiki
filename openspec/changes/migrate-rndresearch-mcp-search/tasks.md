## 1. Repository Setup

- [x] 1.1 Create `requirements.txt` in `paperswiki` combining rndresearch deps + MCP + arxiv library deps (`mcp[cli]`, `arxiv`, `certifi`, `httpx`, OpenTelemetry packages)
- [x] 1.2 Create `pyproject.toml` based on arxivsearcher's, updated for the `paperswiki` project name and combined deps
- [x] 1.3 Create `.env.example` with all required env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `OTEL_EXPORTER_OTLP_ENDPOINT`)
- [x] 1.4 Update `.gitignore` to include `summaries/`, `logs/`, `downloads/`, `viewer_state.json`, `.seen_papers.json`, `.venv/`, `__pycache__/`

## 2. Copy rndresearch Core Files

- [x] 2.1 Copy `agent.py` from rndresearch to `paperswiki/` root
- [x] 2.2 Copy `deep_research.py` from rndresearch to `paperswiki/` root
- [x] 2.3 Copy `viewer.py` from rndresearch to `paperswiki/` root
- [x] 2.4 Copy `research_topics.yaml` from rndresearch to `paperswiki/` root
- [x] 2.5 Copy `deep_research_prompt.yaml` from rndresearch to `paperswiki/` root
- [x] 2.6 Copy `src/` directory (all modules: `arxiv_client.py`, `summarizer.py`, `deep_analyser.py`, `abstract_filter.py`, `paper_store.py`, `logger.py`, `__init__.py`) from rndresearch to `paperswiki/src/`
- [x] 2.7 Copy `analysis/log_analysis.ipynb` from rndresearch to `paperswiki/analysis/`

## 3. Bundle MCP Server

- [x] 3.1 Copy `mcp_server.py` from arxivsearcher to `paperswiki/` root
- [x] 3.2 Create `mcp_servers.json` in `paperswiki/` root with the local MCP server entry (`type: "local"`, `command: "uv"`, `args: ["run", "python", "mcp_server.py"]`, `active: true`)

## 4. Implement MCP ArXiv Client

- [x] 4.1 Create `src/mcp_arxiv_client.py` with `fetch_papers(keywords, categories, days, max_results, topic_name, logger)` that builds a topic query string from keywords and categories
- [x] 4.2 Implement the async MCP call in `mcp_arxiv_client.py`: use `mcp.client.stdio.stdio_client` with `StdioServerParameters(command="uv", args=["run", "python", "mcp_server.py"])` to call `search_papers`
- [x] 4.3 Implement client-side date filtering in `mcp_arxiv_client.py`: filter out papers where `published` is older than `days` days
- [x] 4.4 Ensure the returned paper dicts from the MCP client have the same fields as the original client: `id`, `url`, `title`, `abstract`, `authors`, `published`
- [x] 4.5 Handle logging in `mcp_arxiv_client.py` using the same `logger.log_arxiv_fetch(...)` call pattern as the original client

## 5. Wire MCP Client into Agent

- [x] 5.1 In `agent.py`, replace `from src.arxiv_client import fetch_papers` with `from src.mcp_arxiv_client import fetch_papers`
- [x] 5.2 Verify `agent.py` runs end-to-end with `--dry-run` and produces expected console output

## 6. Observability (Optional Infrastructure)

- [x] 6.1 Copy `otel-collector-config.yaml` from arxivsearcher to `paperswiki/`
- [x] 6.2 Copy `docker-compose.yml` from arxivsearcher to `paperswiki/` and update service names/labels for paperswiki
- [x] 6.3 Copy `prometheus/` and `grafana/` configs from arxivsearcher to `paperswiki/`

## 7. Documentation

- [x] 7.1 Write `README.md` documenting: project overview, prerequisites, install steps (`pip install -r requirements.txt` or `uv sync`), `.env` setup, running `agent.py` (all flags), running `viewer.py`, running `deep_research.py`, and the MCP server role
- [x] 7.2 Document that `mcp_server.py` is started automatically by the agent — no manual server startup needed
- [x] 7.3 Document the optional observability stack (Docker Compose + OTLP + Grafana)

## 8. Validation

- [x] 8.1 Run `python agent.py --dry-run` and confirm it prints topics, paper counts, and `[dry-run]` lines without errors
- [x] 8.2 Run `python agent.py --provider anthropic` (or equivalent) against at least one topic and confirm a `.md` summary is written to `summaries/`
- [x] 8.3 Run `python3 viewer.py` and confirm the index page loads with the generated summaries
- [x] 8.4 Confirm `logs/` contains a JSONL file with `run_start`, `arxiv_fetch`, `llm_summarize`, and `run_end` events after a real run

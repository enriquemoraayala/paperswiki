# paperswiki

An arXiv research platform — scan topics, generate LLM summaries, browse results in a local web UI, and run deep analysis on selected papers. ArXiv search is powered by a local [MCP](https://modelcontextprotocol.io/) server (`mcp_server.py`) with full OpenTelemetry instrumentation.

## Features

- 🖥️ **Local viewer** — browse summaries at `http://localhost:8080` with `python3 viewer.py`
- 📋 **Topic-driven** — research scope defined in `research_topics.yaml`, no code changes needed
- 🔌 **MCP-backed search** — arXiv queries go through `mcp_server.py` (FastMCP), started automatically as a subprocess — no manual server management needed
- 🤖 **Multi-provider LLM summaries** — Anthropic Claude, OpenAI GPT, or Azure OpenAI; auto-detected from env vars
- 📁 **One file per paper** — summaries saved to `summaries/` with date-stamped filenames
- 🔁 **Incremental** — local cache skips already-summarised papers on subsequent runs
- 🗂️ **Topic mode** — `--topic-mode any` (default): papers matching at least one topic; `--topic-mode all`: papers in every topic's result set
- 🔬 **Advanced filter** — LLM checks each abstract against a per-topic criterion before summarising
- 📊 **Structured logging** — every run writes JSONL traces to `logs/` (arXiv fetches, filter decisions, LLM calls)
- 🔭 **Observability** — OpenTelemetry traces/metrics via `docker-compose.yml` → Grafana + Jaeger + Prometheus

## Setup

### 1. Prerequisites

- Python 3.11–3.13 (protobuf C extensions require < 3.14)
- [`uv`](https://docs.astral.sh/uv/) (recommended) **or** pip
- An API key for at least one LLM provider:
  - [Anthropic](https://console.anthropic.com/) → `ANTHROPIC_API_KEY`
  - [OpenAI](https://platform.openai.com/api-keys) → `OPENAI_API_KEY`
  - [Azure OpenAI](https://portal.azure.com/) → `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT`

### 2. Install dependencies

```bash
# With uv (recommended)
uv sync

# With pip
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env and set at least one LLM API key
```

## Usage

```bash
# Scan papers from the last 7 days (provider auto-detected from .env)
uv run python agent.py

# Use Anthropic Claude explicitly
uv run python agent.py --provider anthropic

# Use OpenAI GPT-4o
uv run python agent.py --provider openai

# Use Azure OpenAI (--model must be your deployment name)
uv run python agent.py --provider azure --model my-gpt4o-deployment

# Scan papers from the last 30 days
uv run python agent.py --days 30

# Preview without writing files or calling the LLM
uv run python agent.py --dry-run

# Enable abstract-level LLM filter (reduces noise)
uv run python agent.py --advanced-filter

# Only papers appearing in EVERY topic's result set
uv run python agent.py --topic-mode all

# Combine for maximum precision
uv run python agent.py --topic-mode all --advanced-filter

# Custom topics file or output directory
uv run python agent.py --topics my_topics.yaml --output-dir my_summaries/
```

## Summaries Viewer

```bash
python3 viewer.py
# Opens http://localhost:8080 in your browser
```

The viewer provides:
- **Index page** — papers listed newest-first with title, date, authors, abstract preview, arXiv link. Live text search and status filter tabs.
- **Detail page** — full rendered markdown summary with arXiv link and status selector.
- **Search page** (`/search`) — edit `research_topics.yaml` in the browser, configure agent parameters, run the agent and watch output stream live.
- **Deep Research page** (`/deep-research`) — launch deep analysis on papers marked *Pendiente de analizar*.

**Status values** (stored in `viewer_state.json`, git-ignored):

| Status | Icon | Meaning |
|---|---|---|
| No leído | 📬 | Default — every new paper |
| Interesante | 💡 | Worth following up |
| Favorito | ⭐ | Must-read |
| Pendiente de analizar | 🔎 | Queued for deep analysis |
| Analizado | 🧪 | Deep analysis completed |
| Descartado | 🗑️ | Not relevant |

## Deep Research Agent

Mark papers as *Pendiente de analizar* in the viewer, then run:

```bash
python3 deep_research.py

# With explicit provider/model
python3 deep_research.py --provider anthropic --model claude-opus-4-5

# Preview without LLM calls
python3 deep_research.py --dry-run
```

The agent downloads each paper from arXiv, runs an in-depth LLM analysis using the prompt in `deep_research_prompt.yaml`, and appends a `## 🧪 Deep Analysis` section to the existing summary file.

## MCP Server

`mcp_server.py` is a [FastMCP](https://github.com/jlowin/fastmcp) server that exposes two tools:

| Tool | Description |
|---|---|
| `search_papers(topic, max_results)` | Search arXiv by topic, returns list of papers sorted by submission date |
| `download_paper(arxiv_id)` | Download a paper PDF to `downloads/` |

**The server is started automatically** by the agent via stdio transport — you don't need to start it manually. If you want to use it independently (e.g. from an MCP-compatible client), run:

```bash
uv run python mcp_server.py
```

## Topic Configuration

Edit `research_topics.yaml` to add, remove, or modify topics:

```yaml
topics:
  - name: "My Topic"
    keywords:
      - "some keyword"
      - "another keyword"
    arxiv_categories:
      - "cs.LG"
    max_results: 20
    abstract_filter: >
      The paper must be primarily about X. Papers that merely mention X are NOT relevant.
```

See [arXiv category taxonomy](https://arxiv.org/category_taxonomy) for valid category codes.
`abstract_filter` is only used when `--advanced-filter` is passed.

## Topic Modes

| Mode | Behaviour |
|---|---|
| `--topic-mode any` *(default)* | Papers matching **at least one** topic |
| `--topic-mode all` | Only papers in **every** topic's result set |

## Observability (Optional)

Start the full observability stack (requires Docker):

```bash
docker compose up -d
```

This starts:
- **OTLP Collector** — `localhost:4317`
- **Jaeger** — traces at `http://localhost:16686`
- **Prometheus** — metrics at `http://localhost:9090`
- **Loki** — log aggregation at `localhost:3100`
- **Grafana** — dashboards at `http://localhost:3000` (admin/admin)

Set `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317` in `.env`. The MCP server emits traces and metrics for every search and download call.

> **Note:** If you already have an OTLP stack running on `localhost:4317`, skip `docker compose up` — the MCP server will export to your existing collector automatically.

## Log Analysis

```bash
jupyter notebook analysis/log_analysis.ipynb
```

Visualise run history, papers per topic, filter rates, LLM token usage, and timing.

## Project Structure

```
paperswiki/
├── research_topics.yaml      # Topic configuration (edit this!)
├── deep_research_prompt.yaml # Deep analysis prompt (edit this!)
├── agent.py                  # arXiv scan & summarise CLI
├── deep_research.py          # Deep analysis CLI
├── viewer.py                 # Local summaries web viewer
├── mcp_server.py             # FastMCP server for arXiv search (auto-started)
├── mcp_servers.json          # MCP server registry
├── src/
│   ├── mcp_arxiv_client.py   # MCP-backed arXiv search (replaces direct API calls)
│   ├── arxiv_client.py       # Original direct arXiv client (kept for reference)
│   ├── summarizer.py         # Anthropic, OpenAI & Azure summarisation
│   ├── deep_analyser.py      # LLM wrapper for deep analysis
│   ├── abstract_filter.py    # LLM-based abstract relevance filter
│   ├── paper_store.py        # Seen-paper cache
│   └── logger.py             # Structured JSONL logger
├── summaries/                # Generated markdown summaries (git-ignored)
├── logs/                     # JSONL log files, one per day (git-ignored)
├── downloads/                # Downloaded PDFs (git-ignored)
├── analysis/
│   └── log_analysis.ipynb    # Jupyter notebook for log analysis
├── docker-compose.yml        # Observability stack (Grafana, Jaeger, Prometheus, Loki)
├── otel-collector-config.yaml
├── prometheus/
├── grafana/
├── .env.example
├── requirements.txt
├── pyproject.toml
└── README.md
```

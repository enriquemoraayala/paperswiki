## ADDED Requirements

### Requirement: ArXiv search via MCP tool
The system SHALL route all arXiv paper searches through the `search_papers` MCP tool exposed by the local `mcp_server.py` FastMCP server. Direct HTTP calls to the arXiv REST API SHALL NOT be made by the agent.

#### Scenario: Agent searches via MCP
- **WHEN** the agent calls `fetch_papers(keywords, categories, days, max_results)`
- **THEN** the MCP client constructs a topic query string and calls `search_papers` on the MCP server via stdio transport, returning structured paper dicts

#### Scenario: MCP server is started automatically
- **WHEN** the agent calls `fetch_papers` for the first time
- **THEN** the MCP client spawns `mcp_server.py` as a subprocess via stdio transport with no manual server startup required

### Requirement: MCP client is a drop-in replacement for the direct arXiv client
The `src/mcp_arxiv_client.py` module SHALL expose a `fetch_papers` function with the same signature as the replaced `src/arxiv_client.py` function. The rest of the codebase SHALL require no changes beyond updating the import.

#### Scenario: Existing agent logic unchanged
- **WHEN** `agent.py` imports `fetch_papers` from `src.mcp_arxiv_client`
- **THEN** the function accepts `(keywords, categories, days, max_results, topic_name, logger)` and returns a list of paper dicts with the same fields (`id`, `url`, `title`, `abstract`, `authors`, `published`)

### Requirement: Client-side date filtering preserved
The MCP client SHALL apply the same date-window filter as the original direct client: only papers published within the last `days` days SHALL be returned to the caller.

#### Scenario: Old papers are excluded
- **WHEN** `fetch_papers` is called with `days=7` and the MCP server returns papers older than 7 days
- **THEN** those papers are filtered out before the list is returned to the caller

### Requirement: MCP server bundled in the repository
The `mcp_server.py` FastMCP server SHALL be included in the `paperswiki` repository root so the project is fully self-contained without requiring the `arxivsearcher` repo to be present.

#### Scenario: Repository is self-contained
- **WHEN** a user clones `paperswiki` and installs dependencies
- **THEN** they can run `python agent.py` without needing to clone or install any other repository

### Requirement: OpenTelemetry instrumentation in MCP server
The `mcp_server.py` SHALL emit traces, metrics, and logs via OTLP. When no OTLP collector is reachable, the server SHALL continue to function normally without hard failures.

#### Scenario: Search succeeds without OTLP collector
- **WHEN** the agent calls `search_papers` and no OTLP collector is running at `localhost:4317`
- **THEN** the search returns results successfully; telemetry export errors are silently dropped

#### Scenario: Traces emitted when collector is available
- **WHEN** an OTLP collector is running and the agent performs a search
- **THEN** a span named `execute_tool` with `gen_ai.tool.name=search_papers` is exported to the collector

### Requirement: MCP server configuration in `mcp_servers.json`
The repository SHALL include an `mcp_servers.json` file that registers the local `mcp_server.py` as the active MCP server, following the same format used by `arxivsearcher`.

#### Scenario: Server config present
- **WHEN** a user opens `mcp_servers.json`
- **THEN** the file contains an entry with `type: "local"`, `command: "uv"`, `args: ["run", "python", "mcp_server.py"]`, and `active: true`

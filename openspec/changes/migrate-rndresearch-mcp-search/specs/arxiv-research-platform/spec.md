## ADDED Requirements

### Requirement: Agent scans arXiv topics and generates summaries
The system SHALL provide a CLI agent (`agent.py`) that reads topics from `research_topics.yaml`, searches arXiv for papers published within a configurable time window, and generates LLM-powered markdown summaries saved to `summaries/`.

#### Scenario: Successful scan with default settings
- **WHEN** the user runs `python agent.py`
- **THEN** the agent loads topics from `research_topics.yaml`, searches arXiv for papers from the last 7 days, and saves one `.md` summary file per new paper to `summaries/`

#### Scenario: Incremental runs skip already-seen papers
- **WHEN** the agent is run a second time and some papers were already summarised
- **THEN** the agent reads `.seen_papers.json` from the output directory and skips papers already in the cache

#### Scenario: Dry run produces no file writes
- **WHEN** the user runs `python agent.py --dry-run`
- **THEN** the agent fetches papers and logs what it would do, but makes no LLM calls and writes no files

### Requirement: Multi-provider LLM summarisation
The system SHALL support Anthropic Claude, OpenAI GPT, and Azure OpenAI for generating paper summaries. The provider SHALL be auto-detected from environment variables if not explicitly specified.

#### Scenario: Auto-detect Anthropic
- **WHEN** `ANTHROPIC_API_KEY` is set and `--provider` is not passed
- **THEN** the agent uses Anthropic Claude as the summarisation provider

#### Scenario: Explicit provider override
- **WHEN** the user passes `--provider openai --model gpt-4-turbo`
- **THEN** the agent uses OpenAI GPT-4-turbo for all summarisation calls

### Requirement: Advanced abstract filter
The system SHALL support an optional LLM-based relevance filter. When `--advanced-filter` is enabled, each paper's abstract SHALL be evaluated against the topic's `abstract_filter` criterion before summarisation.

#### Scenario: Irrelevant paper is skipped
- **WHEN** `--advanced-filter` is enabled and a paper's abstract does not match the topic's `abstract_filter` criterion
- **THEN** the paper is logged as `rejected` and no summary is written

#### Scenario: Relevant paper proceeds to summarisation
- **WHEN** `--advanced-filter` is enabled and a paper's abstract matches the topic's criterion
- **THEN** the paper proceeds to the summarisation step

### Requirement: Topic mode selection
The system SHALL support two topic combination modes: `any` (paper matches at least one topic) and `all` (paper appears in every topic's result set).

#### Scenario: ANY mode includes single-topic papers
- **WHEN** `--topic-mode any` is used and a paper matches only one of three configured topics
- **THEN** the paper is included for processing

#### Scenario: ALL mode requires presence in every topic
- **WHEN** `--topic-mode all` is used and a paper appears in only two of three topic result sets
- **THEN** the paper is excluded

### Requirement: Local summaries viewer
The system SHALL provide a local web application (`viewer.py`) to browse, filter, and manage paper summaries without any external deployment.

#### Scenario: User browses index
- **WHEN** the user runs `python3 viewer.py` and opens `http://localhost:8080`
- **THEN** all papers are listed newest-first with title, date, authors, abstract preview, and an arXiv link

#### Scenario: User changes paper status
- **WHEN** the user clicks a status button on a paper card (e.g., "Interesante")
- **THEN** the status is saved to `viewer_state.json` immediately without page reload

#### Scenario: User runs agent from viewer
- **WHEN** the user navigates to `/search`, configures parameters, and clicks Run
- **THEN** agent output streams live in the browser and the paper index reloads on completion

### Requirement: Deep research analysis
The system SHALL provide a deep research agent (`deep_research.py`) that downloads full paper content from arXiv and runs an in-depth LLM analysis, appending a `## 🧪 Deep Analysis` section to the existing summary file.

#### Scenario: Papers queued for deep analysis are processed
- **WHEN** papers have status `pending_analysis` in `viewer_state.json` and the user runs `python3 deep_research.py`
- **THEN** each queued paper is downloaded, analysed with the LLM, the analysis is appended to the `.md` file, and the status is updated to `analyzed`

### Requirement: Structured JSONL logging
The system SHALL write structured JSONL event traces to `logs/YYYY-MM-DD.jsonl` for every non-dry-run agent execution.

#### Scenario: Run produces a log file
- **WHEN** the agent completes a non-dry-run scan
- **THEN** a JSONL file exists in `logs/` with events: `run_start`, `arxiv_fetch` (one per topic), optionally `abstract_filter` entries, `llm_summarize` entries, and `run_end`

### Requirement: Topic configuration via YAML
The system SHALL load research topics exclusively from `research_topics.yaml`. Topics SHALL support `name`, `keywords`, `arxiv_categories`, `max_results`, and `abstract_filter` fields.

#### Scenario: User adds a new topic without code changes
- **WHEN** the user adds a new entry to `research_topics.yaml` and runs `python agent.py`
- **THEN** the agent searches arXiv for papers matching the new topic's keywords and categories

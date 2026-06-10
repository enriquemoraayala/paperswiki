"""
rndresearch agent — CLI entry point.

Usage:
    python agent.py [--days N] [--provider {anthropic,openai,azure}] [--model MODEL]
                    [--topics TOPICS_FILE] [--output-dir DIR]
                    [--topic-mode {any,all}] [--advanced-filter] [--dry-run]
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

import yaml

# Ensure src/ is importable when running from repo root
sys.path.insert(0, str(Path(__file__).parent))

from src.abstract_filter import is_relevant
from src.mcp_arxiv_client import fetch_papers
from src.logger import AgentLogger, NullLogger
from src.paper_store import PaperStore
from src.summarizer import (
    PROVIDER_ANTHROPIC,
    PROVIDER_AZURE,
    PROVIDER_OPENAI,
    detect_provider,
    safe_filename,
    summarise_paper,
)

DEFAULT_TOPICS_FILE = Path(__file__).parent / "research_topics.yaml"
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "summaries"
DEFAULT_LOG_DIR = Path(__file__).parent / "logs"

TOPIC_MODE_ANY = "any"
TOPIC_MODE_ALL = "all"


def load_topics(topics_file: Path) -> list:
    with open(topics_file, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("topics", [])


def _process_paper(paper: dict, topic_name: str, abstract_filter_criterion: str,
                   advanced_filter: bool, effective_provider: Optional[str],
                   effective_model: Optional[str], provider: Optional[str],
                   model: Optional[str], output_dir: Path, logger: "AgentLogger",
                   dry_run: bool) -> str:
    """
    Apply advanced filter (if enabled) and summarise a single paper.
    Returns: 'filtered' | 'dry_run' | 'saved' | 'error'
    """
    if advanced_filter and abstract_filter_criterion and not dry_run:
        print(f"  🔬 Filtering: {paper['title'][:60]}...")
        relevant = is_relevant(
            paper=paper,
            abstract_filter=abstract_filter_criterion,
            provider=effective_provider,
            model=effective_model,
            topic_name=topic_name,
            logger=logger,
        )
        if not relevant:
            print(f"  ⏭️  Filtered out (not relevant): {paper['title'][:60]}")
            return "filtered"

    print(f"  ✍️  Summarising: {paper['title'][:70]}...")

    if dry_run:
        print(f"       [dry-run] would write: {safe_filename(paper)}")
        return "dry_run"

    try:
        markdown = summarise_paper(paper, provider=provider, model=model, logger=logger)
    except EnvironmentError as exc:
        print(f"  ❌ {exc}")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ Failed to summarise {paper['id']}: {exc}")
        return "error"

    out_file = output_dir / safe_filename(paper)
    out_file.write_text(markdown, encoding="utf-8")
    print(f"  ✅ Saved: {out_file.name}")
    return "saved"


def run(days: int, topics_file: Path, output_dir: Path, dry_run: bool,
        provider: Optional[str], model: Optional[str],
        advanced_filter: bool, log_dir: Path, topic_mode: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    topics = load_topics(topics_file)

    logger = NullLogger() if dry_run else AgentLogger(log_dir=log_dir)

    # Resolve provider/model early so we can log them correctly
    effective_provider = provider
    effective_model = model
    if not dry_run:
        try:
            effective_provider = provider or detect_provider()
            if not effective_model and effective_provider == PROVIDER_ANTHROPIC:
                from src.summarizer import DEFAULT_MODEL_ANTHROPIC
                effective_model = effective_model or DEFAULT_MODEL_ANTHROPIC
            elif not effective_model and effective_provider in (PROVIDER_OPENAI, PROVIDER_AZURE):
                from src.summarizer import DEFAULT_MODEL_OPENAI
                effective_model = effective_model or DEFAULT_MODEL_OPENAI
        except EnvironmentError:
            pass  # will surface as error during summarisation

    t_run_start = time.monotonic()
    logger.log_run_start(
        days=days,
        provider=effective_provider,
        model=effective_model,
        advanced_filter=advanced_filter,
        topics_count=len(topics),
        topic_mode=topic_mode,
    )

    print(f"🔍 Scanning arXiv for papers from the last {days} day(s)...")
    print(f"📂 Output directory: {output_dir}")
    print(f"📋 Topics loaded: {len(topics)}")
    print(f"🤖 Provider: {effective_provider or 'auto-detect'}" +
          (f" | Model: {effective_model}" if effective_model else ""))
    print(f"🗂️  Topic mode: {'ALL (papers must match every topic)' if topic_mode == TOPIC_MODE_ALL else 'ANY (papers matching at least one topic)'}")
    if advanced_filter:
        print("🔬 Advanced filter: ON (abstract relevance check per paper)")
    if dry_run:
        print("⚠️  Dry-run mode: no files will be written, no LLM calls made\n")

    total_new = 0
    total_skipped = 0
    total_filtered = 0

    store = PaperStore(output_dir / ".seen_papers.json")

    if topic_mode == TOPIC_MODE_ALL:
        # ── ALL mode: only papers present in every topic's result set ────────
        print("\n⏳ Fetching papers for all topics before intersecting...\n")
        per_topic_ids: list = []     # list of sets of paper IDs per topic
        paper_registry: dict = {}    # paper_id → paper dict (first seen wins)
        # topic_by_paper_id: paper_id → topic dict (for abstract_filter lookup)
        topic_by_paper_id: dict = {}

        for topic in topics:
            name = topic.get("name", "Unknown")
            keywords = topic.get("keywords", [])
            categories = topic.get("arxiv_categories", [])
            max_results = topic.get("max_results", 20)

            print(f"  📌 Fetching: {name}")
            try:
                papers = fetch_papers(
                    keywords, categories, days=days, max_results=max_results,
                    topic_name=name, logger=logger,
                )
            except RuntimeError as exc:
                print(f"  ❌ Failed to fetch papers for '{name}': {exc}")
                # A failed fetch means no papers for this topic → intersection = empty
                per_topic_ids.append(set())
                continue

            ids = set()
            for p in papers:
                ids.add(p["id"])
                if p["id"] not in paper_registry:
                    paper_registry[p["id"]] = p
                    topic_by_paper_id[p["id"]] = topic
            per_topic_ids.append(ids)
            print(f"     → {len(papers)} paper(s) found")

        # Intersection across all topics
        if per_topic_ids:
            common_ids = per_topic_ids[0]
            for s in per_topic_ids[1:]:
                common_ids = common_ids & s
        else:
            common_ids = set()

        print(f"\n🔗 Papers matching ALL {len(topics)} topic(s): {len(common_ids)}\n")

        for paper_id in sorted(common_ids):
            paper = paper_registry[paper_id]
            topic = topic_by_paper_id[paper_id]
            abstract_filter_criterion = topic.get("abstract_filter", "")

            if store.is_seen(paper_id):
                total_skipped += 1
                continue

            result = _process_paper(
                paper=paper, topic_name=topic.get("name", ""),
                abstract_filter_criterion=abstract_filter_criterion,
                advanced_filter=advanced_filter,
                effective_provider=effective_provider, effective_model=effective_model,
                provider=provider, model=model,
                output_dir=output_dir, logger=logger, dry_run=dry_run,
            )
            if result == "filtered":
                total_filtered += 1
            elif result in ("saved", "dry_run"):
                if result == "saved":
                    store.mark_seen(paper_id)
                total_new += 1

    else:
        # ── ANY mode (default): papers matching at least one topic ────────────
        for topic in topics:
            name = topic.get("name", "Unknown")
            keywords = topic.get("keywords", [])
            categories = topic.get("arxiv_categories", [])
            max_results = topic.get("max_results", 20)
            abstract_filter_criterion = topic.get("abstract_filter", "")

            print(f"\n📌 Topic: {name}")

            try:
                papers = fetch_papers(
                    keywords, categories, days=days, max_results=max_results,
                    topic_name=name, logger=logger,
                )
            except RuntimeError as exc:
                print(f"  ❌ Failed to fetch papers: {exc}")
                continue

            print(f"  Found {len(papers)} paper(s) in the last {days} day(s)")

            for paper in papers:
                if store.is_seen(paper["id"]):
                    total_skipped += 1
                    continue

                result = _process_paper(
                    paper=paper, topic_name=name,
                    abstract_filter_criterion=abstract_filter_criterion,
                    advanced_filter=advanced_filter,
                    effective_provider=effective_provider, effective_model=effective_model,
                    provider=provider, model=model,
                    output_dir=output_dir, logger=logger, dry_run=dry_run,
                )
                if result == "filtered":
                    total_filtered += 1
                elif result in ("saved", "dry_run"):
                    if result == "saved":
                        store.mark_seen(paper["id"])
                    total_new += 1

    duration_ms = int((time.monotonic() - t_run_start) * 1000)
    logger.log_run_end(
        new_summaries=total_new,
        skipped=total_skipped,
        filtered_out=total_filtered,
        duration_ms=duration_ms,
    )

    summary_line = f"\n🎉 Done. New summaries: {total_new} | Already seen (skipped): {total_skipped}"
    if advanced_filter:
        summary_line += f" | Filtered out: {total_filtered}"
    if not dry_run:
        summary_line += f"\n📝 Log: {logger._get_log_file()}"
    print(summary_line)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan arXiv for scientific papers and generate markdown summaries using an LLM."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to look back for new papers (default: 7)",
    )
    parser.add_argument(
        "--provider",
        choices=[PROVIDER_ANTHROPIC, PROVIDER_OPENAI, PROVIDER_AZURE],
        default=None,
        help="LLM provider to use (default: auto-detect from env vars)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help=(
            "Model name override (default: claude-opus-4-5 for Anthropic, gpt-4o for OpenAI). "
            "Required for Azure: provide your deployment name."
        ),
    )
    parser.add_argument(
        "--topics",
        type=Path,
        default=DEFAULT_TOPICS_FILE,
        help=f"Path to topics YAML file (default: {DEFAULT_TOPICS_FILE})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to save markdown summaries (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=DEFAULT_LOG_DIR,
        help=f"Directory for JSONL log files (default: {DEFAULT_LOG_DIR})",
    )
    parser.add_argument(
        "--topic-mode",
        choices=[TOPIC_MODE_ANY, TOPIC_MODE_ALL],
        default=TOPIC_MODE_ANY,
        help=(
            "How topics are combined when selecting papers. "
            "'any' (default): include papers that match at least one topic. "
            "'all': include only papers that appear in every topic's result set."
        ),
    )
    parser.add_argument(
        "--advanced-filter",
        action="store_true",
        help=(
            "Enable advanced abstract-level filtering. Uses the LLM to check each paper "
            "abstract against the topic's 'abstract_filter' criterion before summarising."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch papers but do not call the LLM or write files",
    )
    args = parser.parse_args()
    run(
        days=args.days,
        topics_file=args.topics,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
        provider=args.provider,
        model=args.model,
        advanced_filter=args.advanced_filter,
        log_dir=args.log_dir,
        topic_mode=args.topic_mode,
    )


if __name__ == "__main__":
    main()



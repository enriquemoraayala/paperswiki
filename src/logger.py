"""
Structured JSONL logger for the rndresearch agent.
Writes one JSON object per line to logs/YYYY-MM-DD.jsonl.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DEFAULT_LOG_DIR = Path(__file__).parent.parent / "logs"


class AgentLogger:
    """
    Lightweight structured logger that appends JSONL entries to a daily log file.
    Each entry is a JSON object with at least: timestamp, event, run_id.
    """

    def __init__(self, log_dir: Path = DEFAULT_LOG_DIR, run_id: Optional[str] = None):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._log_file: Optional[Path] = None

    def _get_log_file(self) -> Path:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.log_dir / f"{date_str}.jsonl"

    def log(self, event: str, **kwargs: Any) -> None:
        """Append a structured log entry."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "event": event,
            **kwargs,
        }
        log_file = self._get_log_file()
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def log_run_start(self, days: int, provider: Optional[str], model: Optional[str],
                      advanced_filter: bool, topics_count: int,
                      topic_mode: str = "any") -> None:
        self.log(
            "run_start",
            days=days,
            provider=provider or "auto-detect",
            model=model,
            advanced_filter=advanced_filter,
            topic_mode=topic_mode,
            topics_count=topics_count,
        )

    def log_run_end(self, new_summaries: int, skipped: int, filtered_out: int,
                    duration_ms: int) -> None:
        self.log(
            "run_end",
            new_summaries=new_summaries,
            skipped=skipped,
            filtered_out=filtered_out,
            duration_ms=duration_ms,
        )

    def log_arxiv_fetch(self, topic: str, query: str, max_results: int,
                        returned: int, in_window: int, duration_ms: int) -> None:
        self.log(
            "arxiv_fetch",
            topic=topic,
            query=query,
            max_results=max_results,
            returned=returned,
            in_window=in_window,
            duration_ms=duration_ms,
        )

    def log_abstract_filter(self, topic: str, paper_id: str, title: str,
                             result: str, duration_ms: int) -> None:
        """result: 'accepted' | 'rejected' | 'error'"""
        self.log(
            "abstract_filter",
            topic=topic,
            paper_id=paper_id,
            title=title[:100],
            result=result,
            duration_ms=duration_ms,
        )

    def log_llm_summarize(self, paper_id: str, title: str, provider: str, model: str,
                           duration_ms: int, tokens_in: Optional[int] = None,
                           tokens_out: Optional[int] = None) -> None:
        self.log(
            "llm_summarize",
            paper_id=paper_id,
            title=title[:100],
            provider=provider,
            model=model,
            duration_ms=duration_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )


# Null logger for dry-run / when logging is disabled
class NullLogger(AgentLogger):
    def log(self, event: str, **kwargs: Any) -> None:
        pass

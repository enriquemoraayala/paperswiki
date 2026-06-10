"""
Paper store — tracks which arXiv papers have already been summarised.
Backed by a JSON file so state persists across runs.
"""

import json
from pathlib import Path

DEFAULT_STORE_PATH = Path(__file__).parent.parent / "summaries" / ".seen_papers.json"


class PaperStore:
    def __init__(self, store_path: Path = DEFAULT_STORE_PATH):
        self.store_path = store_path
        self._seen: set[str] = self._load()

    def _load(self) -> set[str]:
        if self.store_path.exists():
            try:
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
                return set(data.get("seen_ids", []))
            except (json.JSONDecodeError, KeyError):
                return set()
        return set()

    def _save(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(
            json.dumps({"seen_ids": sorted(self._seen)}, indent=2),
            encoding="utf-8",
        )

    def is_seen(self, paper_id: str) -> bool:
        return paper_id in self._seen

    def mark_seen(self, paper_id: str) -> None:
        self._seen.add(paper_id)
        self._save()

    def count(self) -> int:
        return len(self._seen)

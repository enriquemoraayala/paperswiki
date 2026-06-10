#!/usr/bin/env python3
"""
deep_research.py — Deep analysis agent for rndresearch.

For each paper in summaries/ whose status in viewer_state.json is
'pending_analysis', this agent:
  1. Downloads the paper PDF from arXiv.
  2. Extracts the text (pypdf) and truncates to fit the LLM context.
  3. Calls the configured LLM with the prompt defined in
     deep_research_prompt.yaml.
  4. Appends the analysis as a new section to the existing summary .md file.
  5. Updates the paper's status to 'analyzed' in viewer_state.json.

Usage:
    python3 deep_research.py
    python3 deep_research.py --provider anthropic --model claude-opus-4-5
    python3 deep_research.py --prompt deep_research_prompt.yaml
    python3 deep_research.py --dry-run
"""

import argparse
import io
import json
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Optional

import requests
import yaml

sys.path.insert(0, str(Path(__file__).parent))

from src.deep_analyser import call_llm
from src.summarizer import detect_provider

DEFAULT_PROMPT_FILE  = Path(__file__).parent / "deep_research_prompt.yaml"
DEFAULT_SUMMARIES    = Path(__file__).parent / "summaries"
DEFAULT_STATE_FILE   = Path(__file__).parent / "viewer_state.json"
PENDING_STATE        = "pending_analysis"
ANALYZED_STATE       = "analyzed"

# Max characters of paper text sent to the LLM (~30 k chars ≈ 7–8 k tokens)
MAX_PAPER_CHARS = 30_000

# ── helpers ────────────────────────────────────────────────────────────────

def _load_prompt(prompt_file: Path) -> tuple[str, str]:
    """Return (system_prompt, user_prompt_template) from the YAML file."""
    data = yaml.safe_load(prompt_file.read_text(encoding="utf-8"))
    system   = data.get("system_prompt", "You are a helpful research assistant.")
    template = data.get("user_prompt_template", "Analyze this paper:\n\n{paper_content}")
    return system.strip(), template.strip()


def _load_states(state_file: Path) -> dict:
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_states(state_file: Path, states: dict) -> None:
    state_file.write_text(json.dumps(states, indent=2, ensure_ascii=False), encoding="utf-8")


def _extract_arxiv_id(md_path: Path) -> Optional[str]:
    """Extract arXiv ID from the metadata table inside the .md file."""
    text = md_path.read_text(encoding="utf-8")
    # Look for the arXiv ID link in the metadata table
    m = re.search(r"\|\s*\*\*arXiv ID\*\*\s*\|\s*\[([^\]]+)\]", text)
    if m:
        return m.group(1).strip()
    # Fallback: parse from filename  YYYY-MM-DD_ARXIVID_title.md
    m2 = re.match(r"\d{4}-\d{2}-\d{2}_([0-9.v]+)_", md_path.stem)
    if m2:
        return m2.group(1)
    return None


def _already_has_deep_analysis(md_path: Path) -> bool:
    return "## 🧪 Deep Analysis" in md_path.read_text(encoding="utf-8")


def _download_paper_text(arxiv_id: str) -> str:
    """
    Download the paper from arXiv and return extracted text.
    Tries the HTML version first (cleaner); falls back to PDF via pypdf.
    """
    # ── 1. Try HTML (available for most papers submitted after ~2020) ──────
    clean_id = arxiv_id.rstrip("v0123456789").rstrip("v")  # strip version for HTML
    html_url = f"https://arxiv.org/html/{arxiv_id}"
    try:
        r = requests.get(html_url, timeout=20, headers={"User-Agent": "rndresearch/1.0"})
        if r.status_code == 200 and len(r.text) > 5000:
            # Basic HTML → text: strip tags, keep useful content
            text = re.sub(r"<style[^>]*>.*?</style>", " ", r.text, flags=re.DOTALL)
            text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s{3,}", "\n\n", text)
            text = text.strip()
            if len(text) > 2000:
                print(f"    [html] Downloaded HTML version ({len(text):,} chars)")
                return text[:MAX_PAPER_CHARS]
    except Exception as exc:
        print(f"    [html] Could not fetch HTML: {exc}")

    # ── 2. Fall back to PDF ────────────────────────────────────────────────
    try:
        from pypdf import PdfReader
    except ImportError:
        raise RuntimeError(
            "pypdf is not installed. Run: pip3 install pypdf\n"
            "Or install all dependencies: pip3 install -r requirements.txt"
        )

    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
    print(f"    [pdf] Downloading PDF from {pdf_url} …")
    r = requests.get(pdf_url, timeout=60, headers={"User-Agent": "rndresearch/1.0"}, stream=True)
    r.raise_for_status()

    pdf_bytes = b"".join(r.iter_content(chunk_size=65536))
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages  = []
    for page in reader.pages:
        t = page.extract_text()
        if t:
            pages.append(t)
    text = "\n\n".join(pages).strip()
    print(f"    [pdf] Extracted {len(text):,} chars from {len(reader.pages)} pages")
    return text[:MAX_PAPER_CHARS]


def _append_analysis(md_path: Path, analysis: str, provider: str, model: str) -> None:
    """Append the deep analysis section to the existing .md file."""
    today = date.today().isoformat()
    section = (
        f"\n\n---\n\n"
        f"## 🧪 Deep Analysis\n\n"
        f"*Analyzed on {today} · {provider} / {model}*\n\n"
        f"{analysis}\n"
    )
    with md_path.open("a", encoding="utf-8") as f:
        f.write(section)


# ── main run ───────────────────────────────────────────────────────────────

def run(
    summaries_dir: Path,
    state_file: Path,
    prompt_file: Path,
    provider: Optional[str],
    model: Optional[str],
    dry_run: bool,
) -> None:
    # Resolve provider
    if not dry_run:
        try:
            effective_provider = provider or detect_provider()
        except EnvironmentError as exc:
            print(f"❌ {exc}")
            sys.exit(1)
    else:
        effective_provider = provider or "dry-run"
    effective_model = model or "(default)"

    print(f"🔬 Deep Research Agent")
    print(f"📂 Summaries: {summaries_dir}")
    print(f"🤖 Provider: {effective_provider} | Model: {effective_model}")
    print(f"📋 Prompt file: {prompt_file}")
    if dry_run:
        print("⚠️  Dry-run mode: no LLM calls, no file writes\n")
    else:
        print()

    # Load prompt
    if not prompt_file.exists():
        print(f"❌ Prompt file not found: {prompt_file}")
        sys.exit(1)
    system_prompt, user_template = _load_prompt(prompt_file)

    # Find papers pending analysis
    states = _load_states(state_file)
    pending = [
        slug for slug, st in states.items() if st == PENDING_STATE
    ]

    if not pending:
        print("ℹ️  No papers marked as 'Pendiente de analizar'. Nothing to do.")
        return

    print(f"📌 Found {len(pending)} paper(s) pending deep analysis:")
    for slug in pending:
        print(f"   · {slug}")
    print()

    new_analyses = 0
    errors       = 0

    for slug in pending:
        md_files = list(summaries_dir.glob(f"{slug}.md"))
        if not md_files:
            print(f"  ⚠️  Summary file not found for slug: {slug} — skipping")
            errors += 1
            continue
        md_path = md_files[0]

        if _already_has_deep_analysis(md_path):
            print(f"  ⏭️  Already has deep analysis: {md_path.name} — updating state only")
            if not dry_run:
                states[slug] = ANALYZED_STATE
                _save_states(state_file, states)
            continue

        arxiv_id = _extract_arxiv_id(md_path)
        if not arxiv_id:
            print(f"  ⚠️  Could not extract arXiv ID from {md_path.name} — skipping")
            errors += 1
            continue

        print(f"  🔎 Analyzing: {md_path.name}")
        print(f"     arXiv ID: {arxiv_id}")

        if dry_run:
            print(f"     [dry-run] Would download {arxiv_id} and call {effective_provider}")
            new_analyses += 1
            continue

        # Download paper
        try:
            paper_text = _download_paper_text(arxiv_id)
        except Exception as exc:
            print(f"  ❌ Failed to download {arxiv_id}: {exc}")
            errors += 1
            continue

        # Build user prompt
        user_prompt = user_template.replace("{paper_content}", paper_text)

        # Call LLM
        print(f"     Calling {effective_provider} …")
        try:
            analysis, tin, tout, ms = call_llm(
                system_prompt, user_prompt, provider, model
            )
        except Exception as exc:
            print(f"  ❌ LLM error for {arxiv_id}: {exc}")
            errors += 1
            continue

        print(f"     ✅ Analysis generated ({tout} tokens out, {ms} ms)")

        # Append to .md
        _append_analysis(md_path, analysis, effective_provider, effective_model)
        print(f"     💾 Appended to {md_path.name}")

        # Update state
        states[slug] = ANALYZED_STATE
        _save_states(state_file, states)
        print(f"     📬 State updated to 'analizado'")
        new_analyses += 1

    print(f"\n🎉 Done. Deep analyses: {new_analyses} | Errors: {errors}")


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deep analysis agent — analyzes papers marked as 'pending_analysis'."
    )
    parser.add_argument(
        "--provider", choices=["anthropic", "openai", "azure"], default=None,
        help="LLM provider (default: auto-detect from env vars)",
    )
    parser.add_argument(
        "--model", default=None,
        help="Model name / Azure deployment name override",
    )
    parser.add_argument(
        "--prompt", type=Path, default=DEFAULT_PROMPT_FILE,
        help=f"Path to prompt YAML file (default: {DEFAULT_PROMPT_FILE})",
    )
    parser.add_argument(
        "--summaries", type=Path, default=DEFAULT_SUMMARIES,
        help=f"Path to summaries directory (default: {DEFAULT_SUMMARIES})",
    )
    parser.add_argument(
        "--state-file", type=Path, default=DEFAULT_STATE_FILE,
        help=f"Path to viewer_state.json (default: {DEFAULT_STATE_FILE})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show which papers would be analyzed without making LLM calls",
    )
    args = parser.parse_args()

    run(
        summaries_dir=args.summaries,
        state_file=args.state_file,
        prompt_file=args.prompt,
        provider=args.provider,
        model=args.model,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()

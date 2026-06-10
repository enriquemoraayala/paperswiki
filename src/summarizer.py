"""
Summariser — generates structured markdown summaries for arXiv papers.
Supports Anthropic Claude, OpenAI GPT, and Azure OpenAI as LLM backends.
"""

import os
import time
from typing import TYPE_CHECKING, Optional

from dotenv import load_dotenv

if TYPE_CHECKING:
    from src.logger import AgentLogger

load_dotenv()

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI = "openai"
PROVIDER_AZURE = "azure"
DEFAULT_MODEL_ANTHROPIC = "claude-opus-4-5"
DEFAULT_MODEL_OPENAI = "gpt-4o"
DEFAULT_AZURE_API_VERSION = "2024-02-01"

_SYSTEM_PROMPT = """\
You are a research assistant specialised in causal AI, causal inference, and reinforcement learning.
Your task is to produce a concise, structured Markdown analysis of a scientific paper based on its title and abstract.
Always respond in English, regardless of the paper's original language."""

_USER_TEMPLATE = """\
Please analyse the following arXiv paper and produce a structured Markdown report with these sections:

## 🔍 Key Contributions
(2–4 bullet points on the paper's main contributions)

## 🧠 Core Methodology
(1–3 sentences describing the main method or approach)

## 📊 Results & Findings
(1–3 sentences on the key results or takeaways)

## 🔗 Relevance to Causal AI / Reinforcement Learning
(1–2 sentences explaining why this paper matters to causal AI or RL research)

---
Title: {title}
Abstract: {abstract}
"""


def detect_provider() -> str:
    """Auto-detect provider from available environment variables."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return PROVIDER_ANTHROPIC
    if os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT"):
        return PROVIDER_AZURE
    if os.getenv("OPENAI_API_KEY"):
        return PROVIDER_OPENAI
    raise EnvironmentError(
        "No API key found. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or "
        "AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT in your .env file."
    )


def _render_markdown(paper: dict, analysis: str) -> str:
    """Render the final markdown document for a paper."""
    authors_str = ", ".join(paper["authors"][:5])
    if len(paper["authors"]) > 5:
        authors_str += f" et al. (+{len(paper['authors']) - 5} more)"

    return f"""# {paper['title']}

| Field | Value |
|-------|-------|
| **arXiv ID** | [{paper['id']}]({paper['url']}) |
| **Published** | {paper['published']} |
| **Authors** | {authors_str} |

## 📄 Abstract

> {paper['abstract']}

{analysis}
"""


def _summarise_with_anthropic(paper: dict, model: str) -> tuple:
    """Returns (analysis_text, tokens_in, tokens_out)."""
    import anthropic  # noqa: PLC0415

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in.")

    client = anthropic.Anthropic(api_key=api_key)
    user_message = _USER_TEMPLATE.format(title=paper["title"], abstract=paper["abstract"])

    message = client.messages.create(
        model=model,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    tokens_in = message.usage.input_tokens if message.usage else None
    tokens_out = message.usage.output_tokens if message.usage else None
    return message.content[0].text.strip(), tokens_in, tokens_out


def _summarise_with_openai(paper: dict, model: str) -> tuple:
    """Returns (analysis_text, tokens_in, tokens_out)."""
    import openai  # noqa: PLC0415

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY is not set. Copy .env.example to .env and fill it in.")

    client = openai.OpenAI(api_key=api_key)
    user_message = _USER_TEMPLATE.format(title=paper["title"], abstract=paper["abstract"])

    response = client.chat.completions.create(
        model=model,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )
    usage = response.usage
    tokens_in = usage.prompt_tokens if usage else None
    tokens_out = usage.completion_tokens if usage else None
    return response.choices[0].message.content.strip(), tokens_in, tokens_out


def _summarise_with_azure(paper: dict, model: str) -> tuple:
    """
    Use Azure OpenAI. `model` is treated as the deployment name.
    Returns (analysis_text, tokens_in, tokens_out).
    Requires env vars: AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT,
    and optionally AZURE_OPENAI_API_VERSION (default: 2024-02-01).
    """
    import openai  # noqa: PLC0415

    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", DEFAULT_AZURE_API_VERSION)

    if not api_key:
        raise EnvironmentError("AZURE_OPENAI_API_KEY is not set. Copy .env.example to .env and fill it in.")
    if not endpoint:
        raise EnvironmentError("AZURE_OPENAI_ENDPOINT is not set. Copy .env.example to .env and fill it in.")

    client = openai.AzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version=api_version,
    )
    user_message = _USER_TEMPLATE.format(title=paper["title"], abstract=paper["abstract"])

    response = client.chat.completions.create(
        model=model,  # deployment name in Azure
        max_completion_tokens=1024,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )
    usage = response.usage
    tokens_in = usage.prompt_tokens if usage else None
    tokens_out = usage.completion_tokens if usage else None
    return response.choices[0].message.content.strip(), tokens_in, tokens_out


def summarise_paper(paper: dict, provider: Optional[str] = None, model: Optional[str] = None,
                    logger: Optional["AgentLogger"] = None) -> str:
    """
    Generate a full markdown summary document for a paper dict.

    Args:
        paper:    Paper metadata dict from arxiv_client.
        provider: 'anthropic', 'openai', or 'azure'. Auto-detected from env vars if None.
        model:    Model name (or Azure deployment name) override. Uses provider default if None.
        logger:   Optional AgentLogger instance for structured logging.

    Returns:
        Markdown string ready to be written to a file.
    """
    if provider is None:
        provider = detect_provider()

    t0 = time.monotonic()

    if provider == PROVIDER_ANTHROPIC:
        model = model or DEFAULT_MODEL_ANTHROPIC
        analysis, tokens_in, tokens_out = _summarise_with_anthropic(paper, model)
    elif provider == PROVIDER_OPENAI:
        model = model or DEFAULT_MODEL_OPENAI
        analysis, tokens_in, tokens_out = _summarise_with_openai(paper, model)
    elif provider == PROVIDER_AZURE:
        if not model:
            raise ValueError(
                "Azure OpenAI requires --model to specify your deployment name. "
                "Example: --model my-gpt4o-deployment"
            )
        analysis, tokens_in, tokens_out = _summarise_with_azure(paper, model)
    else:
        raise ValueError(f"Unknown provider '{provider}'. Choose 'anthropic', 'openai', or 'azure'.")

    duration_ms = int((time.monotonic() - t0) * 1000)

    if logger:
        logger.log_llm_summarize(
            paper_id=paper["id"],
            title=paper["title"],
            provider=provider,
            model=model,
            duration_ms=duration_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

    return _render_markdown(paper, analysis)


def safe_filename(paper: dict) -> str:
    """Return a filesystem-safe filename for a paper's summary."""
    safe_title = "".join(
        c if c.isalnum() or c in " -_" else "_" for c in paper["title"]
    )[:60].strip().replace(" ", "_")
    return f"{paper['published']}_{paper['id'].replace('/', '_')}_{safe_title}.md"

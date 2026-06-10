"""
Abstract-level relevance filter.

When --advanced-filter is active, uses the configured LLM to classify each paper's
abstract against the topic's `abstract_filter` criterion (a natural language description).
Only papers classified as relevant are forwarded for summarisation.
"""

import os
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.logger import AgentLogger

_FILTER_SYSTEM_PROMPT = """\
You are a strict relevance classifier for scientific papers.
Given a paper abstract and a relevance criterion, answer ONLY with a single word: YES or NO.
YES means the abstract clearly matches the criterion. NO means it does not or is only tangentially related.
Do not explain your answer."""

_FILTER_USER_TEMPLATE = """\
Criterion: {criterion}

Abstract: {abstract}

Does this abstract match the criterion? Answer YES or NO."""


def _call_anthropic(criterion: str, abstract: str, model: str) -> bool:
    import anthropic  # noqa: PLC0415

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set.")

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=5,
        system=_FILTER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _FILTER_USER_TEMPLATE.format(
            criterion=criterion, abstract=abstract
        )}],
    )
    answer = message.content[0].text.strip().upper()
    return answer.startswith("YES")


def _call_openai_or_azure(criterion: str, abstract: str, model: str,
                           use_azure: bool = False) -> bool:
    import openai  # noqa: PLC0415

    if use_azure:
        api_key = os.getenv("AZURE_OPENAI_API_KEY")
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
        client = openai.AzureOpenAI(api_key=api_key, azure_endpoint=endpoint,
                                     api_version=api_version)
    else:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY is not set.")
        client = openai.OpenAI(api_key=api_key)

    response = client.chat.completions.create(
        model=model,
        **({"max_completion_tokens": 5} if use_azure else {"max_tokens": 5}),
        messages=[
            {"role": "system", "content": _FILTER_SYSTEM_PROMPT},
            {"role": "user", "content": _FILTER_USER_TEMPLATE.format(
                criterion=criterion, abstract=abstract
            )},
        ],
    )
    answer = response.choices[0].message.content.strip().upper()
    return answer.startswith("YES")


def is_relevant(
    paper: dict,
    abstract_filter: str,
    provider: str,
    model: str,
    topic_name: str = "",
    logger: Optional["AgentLogger"] = None,
) -> bool:
    """
    Ask the LLM whether the paper abstract matches the abstract_filter criterion.

    Returns True if relevant (should be kept), False if not.
    On error, defaults to True (keep the paper) to avoid silent data loss.
    """
    t0 = time.monotonic()
    result = "error"
    try:
        from src.summarizer import PROVIDER_ANTHROPIC, PROVIDER_AZURE, PROVIDER_OPENAI  # noqa: PLC0415

        if provider == PROVIDER_ANTHROPIC:
            accepted = _call_anthropic(abstract_filter, paper["abstract"], model)
        elif provider == PROVIDER_OPENAI:
            accepted = _call_openai_or_azure(abstract_filter, paper["abstract"], model,
                                              use_azure=False)
        elif provider == PROVIDER_AZURE:
            accepted = _call_openai_or_azure(abstract_filter, paper["abstract"], model,
                                              use_azure=True)
        else:
            accepted = True  # unknown provider — pass through

        result = "accepted" if accepted else "rejected"
    except Exception:  # noqa: BLE001
        accepted = True  # fail open: keep the paper
        result = "error"

    duration_ms = int((time.monotonic() - t0) * 1000)

    if logger:
        logger.log_abstract_filter(
            topic=topic_name,
            paper_id=paper["id"],
            title=paper["title"],
            result=result,
            duration_ms=duration_ms,
        )

    return accepted

"""
deep_analyser.py — LLM wrapper for deep paper analysis.

Provides call_llm(system_prompt, user_prompt, provider, model) that works
with the same Anthropic / OpenAI / Azure backends as summarizer.py.
"""

import os
import time
from typing import Optional

from dotenv import load_dotenv

from src.summarizer import (
    PROVIDER_ANTHROPIC,
    PROVIDER_AZURE,
    PROVIDER_OPENAI,
    DEFAULT_MODEL_ANTHROPIC,
    DEFAULT_MODEL_OPENAI,
    detect_provider,
)

load_dotenv()


def call_llm(
    system_prompt: str,
    user_prompt: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> tuple[str, int, int, int]:
    """
    Call the configured LLM with a custom system + user prompt.

    Returns:
        (response_text, tokens_in, tokens_out, duration_ms)
    """
    if provider is None:
        provider = detect_provider()

    t0 = time.monotonic()

    if provider == PROVIDER_ANTHROPIC:
        text, tin, tout = _call_anthropic(system_prompt, user_prompt, model or DEFAULT_MODEL_ANTHROPIC)
    elif provider == PROVIDER_OPENAI:
        text, tin, tout = _call_openai(system_prompt, user_prompt, model or DEFAULT_MODEL_OPENAI)
    elif provider == PROVIDER_AZURE:
        if not model:
            raise ValueError("Azure OpenAI requires --model (deployment name).")
        text, tin, tout = _call_azure(system_prompt, user_prompt, model)
    else:
        raise ValueError(f"Unknown provider '{provider}'.")

    duration_ms = int((time.monotonic() - t0) * 1000)
    return text, tin or 0, tout or 0, duration_ms


def _call_anthropic(system_prompt: str, user_prompt: str, model: str) -> tuple:
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set.")

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    tin  = msg.usage.input_tokens  if msg.usage else 0
    tout = msg.usage.output_tokens if msg.usage else 0
    return msg.content[0].text.strip(), tin, tout


def _call_openai(system_prompt: str, user_prompt: str, model: str) -> tuple:
    import openai

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY is not set.")

    client = openai.OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
    )
    usage = resp.usage
    tin  = usage.prompt_tokens     if usage else 0
    tout = usage.completion_tokens if usage else 0
    return resp.choices[0].message.content.strip(), tin, tout


def _call_azure(system_prompt: str, user_prompt: str, model: str) -> tuple:
    import openai

    api_key    = os.getenv("AZURE_OPENAI_API_KEY")
    endpoint   = os.getenv("AZURE_OPENAI_ENDPOINT")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")

    if not api_key:
        raise EnvironmentError("AZURE_OPENAI_API_KEY is not set.")
    if not endpoint:
        raise EnvironmentError("AZURE_OPENAI_ENDPOINT is not set.")

    client = openai.AzureOpenAI(
        api_key=api_key, azure_endpoint=endpoint, api_version=api_version
    )
    resp = client.chat.completions.create(
        model=model,
        max_completion_tokens=4096,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
    )
    usage = resp.usage
    tin  = usage.prompt_tokens     if usage else 0
    tout = usage.completion_tokens if usage else 0
    return resp.choices[0].message.content.strip(), tin, tout

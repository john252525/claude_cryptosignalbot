from __future__ import annotations

import structlog

from config import settings
from parser.base import BaseLLMParser

log = structlog.get_logger(__name__)


def get_parser() -> BaseLLMParser:
    """Select an LLM provider.

    Resolution order:
      1. Explicit LLM_PROVIDER=anthropic|deepseek
      2. Auto-detect by which API key is set (Anthropic wins if both)
      3. Raise if neither key is present
    """
    provider = settings.llm_provider.lower()

    if provider == "auto":
        if settings.anthropic_api_key:
            provider = "anthropic"
        elif settings.deepseek_api_key:
            provider = "deepseek"
        else:
            raise RuntimeError(
                "No LLM API key set. Provide ANTHROPIC_API_KEY or DEEPSEEK_API_KEY."
            )

    if provider == "anthropic":
        from parser.anthropic_parser import AnthropicParser
        log.info("parser.provider", provider="anthropic", model=settings.anthropic_model)
        return AnthropicParser()

    if provider == "deepseek":
        from parser.deepseek_parser import DeepSeekParser
        log.info("parser.provider", provider="deepseek", model=settings.deepseek_model)
        return DeepSeekParser()

    raise ValueError(
        f"Unknown LLM_PROVIDER={settings.llm_provider!r}. "
        "Use 'anthropic', 'deepseek', or 'auto'."
    )

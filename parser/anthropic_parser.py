from __future__ import annotations

from anthropic import AsyncAnthropic

from config import settings
from parser.base import FEWSHOT, SYSTEM_PROMPT, BaseLLMParser


class AnthropicParser(BaseLLMParser):
    provider_name = "anthropic"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        key = api_key or settings.anthropic_api_key
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self._client = AsyncAnthropic(api_key=key)
        self._model = model or settings.anthropic_model

    async def _call_llm(self, text: str) -> str:
        messages = list(FEWSHOT) + [{"role": "user", "content": text}]
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

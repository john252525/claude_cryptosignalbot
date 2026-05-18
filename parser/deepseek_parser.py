from __future__ import annotations

from openai import AsyncOpenAI

from config import settings
from parser.base import FEWSHOT, SYSTEM_PROMPT, BaseLLMParser


class DeepSeekParser(BaseLLMParser):
    """DeepSeek uses an OpenAI-compatible API. We talk to it via the openai SDK
    with a custom base_url. Drop-in alternative to AnthropicParser, ~3-4x cheaper
    per request."""

    provider_name = "deepseek"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        key = api_key or settings.deepseek_api_key
        if not key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set")
        self._client = AsyncOpenAI(
            api_key=key,
            base_url=base_url or settings.deepseek_base_url,
        )
        self._model = model or settings.deepseek_model

    async def _call_llm(self, text: str) -> str:
        # OpenAI-style: system message goes in the messages list, not separately.
        messages = (
            [{"role": "system", "content": SYSTEM_PROMPT}]
            + list(FEWSHOT)
            + [{"role": "user", "content": text}]
        )
        resp = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=512,
            messages=messages,
        )
        return resp.choices[0].message.content or ""

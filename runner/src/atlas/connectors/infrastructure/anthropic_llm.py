"""AnthropicLlmProvider: the first adapter behind the LlmProvider port.

Usage comes from the API's own usage fields — never estimated — because it
feeds the budget ledger (v1.4 provider resolution).
"""

from __future__ import annotations

from anthropic import AsyncAnthropic

from atlas.connectors.application.ports import LlmRequest, LlmResponse
from atlas.connectors.domain.tools import TokenUsage


class AnthropicLlmProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        # Only pass timeout/max_retries when the caller set them, so jobs
        # (which construct this with neither) keep the SDK's own defaults.
        client = AsyncAnthropic(api_key=api_key)
        if timeout is not None:
            client = client.with_options(timeout=timeout)
        if max_retries is not None:
            client = client.with_options(max_retries=max_retries)
        self._client = client
        self._model = model

    async def complete(self, request: LlmRequest) -> LlmResponse:
        message = await self._client.messages.create(
            model=self._model,
            max_tokens=request.max_tokens,
            system=request.system,
            messages=[{"role": "user", "content": request.prompt}],
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        return LlmResponse(
            text=text,
            usage=TokenUsage(
                input_tokens=message.usage.input_tokens,
                output_tokens=message.usage.output_tokens,
            ),
        )

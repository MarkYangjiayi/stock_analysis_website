from __future__ import annotations

from collections.abc import AsyncIterator
import json
from typing import Any

from openai import AsyncOpenAI

from core.config import settings


def _create_client() -> AsyncOpenAI:
    """Create an isolated DeepSeek client for one business operation."""
    return AsyncOpenAI(
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_BASE_URL,
    )


def _thinking_options() -> dict:
    mode = "enabled" if settings.DEEPSEEK_THINKING_ENABLED else "disabled"
    return {"thinking": {"type": mode}}


async def generate_deepseek_text(prompt: str) -> str:
    """Generate one complete response through DeepSeek's chat API."""
    async with _create_client() as client:
        response = await client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            extra_body=_thinking_options(),
        )

    if not response.choices:
        return ""
    return response.choices[0].message.content or ""


async def generate_deepseek_json(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
) -> dict[str, Any]:
    """Generate one strict JSON object through DeepSeek JSON Output mode."""
    async with _create_client() as client:
        response = await client.chat.completions.create(
            model=model or settings.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            extra_body=_thinking_options(),
        )

    if not response.choices:
        raise ValueError("DeepSeek returned no choices")
    content = response.choices[0].message.content or ""
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("DeepSeek JSON Output must be an object")
    return parsed


async def stream_deepseek_text(prompt: str) -> AsyncIterator[str]:
    """Yield final-answer text chunks from DeepSeek's streaming chat API."""
    async with _create_client() as client:
        response_stream = await client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
            extra_body=_thinking_options(),
        )

        async for chunk in response_stream:
            if not chunk.choices:
                continue
            content = chunk.choices[0].delta.content
            if content:
                yield content

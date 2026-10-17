from types import SimpleNamespace

import pytest

from core.config import settings
from services import deepseek_client


class _FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _FakeClient:
    def __init__(self, response, capture, **kwargs):
        self.chat = SimpleNamespace(completions=_FakeCompletions(response))
        self.capture = capture
        capture["client_kwargs"] = kwargs
        capture["client"] = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.capture["closed"] = True


def _completion(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def _chunk(content=None, *, include_choice=True):
    choices = (
        [SimpleNamespace(delta=SimpleNamespace(content=content))]
        if include_choice
        else []
    )
    return SimpleNamespace(choices=choices)


@pytest.mark.asyncio
async def test_generate_deepseek_text_uses_configured_chat_api(monkeypatch):
    capture = {}

    def fake_client(**kwargs):
        return _FakeClient(_completion("完整报告"), capture, **kwargs)

    monkeypatch.setattr(deepseek_client, "AsyncOpenAI", fake_client)
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(settings, "DEEPSEEK_BASE_URL", "https://example.test")
    monkeypatch.setattr(settings, "DEEPSEEK_MODEL", "test-model")
    monkeypatch.setattr(settings, "DEEPSEEK_THINKING_ENABLED", False)

    result = await deepseek_client.generate_deepseek_text("生成报告")

    assert result == "完整报告"
    assert capture["client_kwargs"] == {
        "api_key": "test-key",
        "base_url": "https://example.test",
    }
    assert capture["closed"] is True
    assert capture["client"].chat.completions.calls == [
        {
            "model": "test-model",
            "messages": [{"role": "user", "content": "生成报告"}],
            "extra_body": {"thinking": {"type": "disabled"}},
        }
    ]


@pytest.mark.asyncio
async def test_stream_deepseek_text_yields_only_content(monkeypatch):
    capture = {}

    async def chunks():
        yield _chunk(include_choice=False)
        yield _chunk()
        yield _chunk("流式")
        yield _chunk("内容")

    def fake_client(**kwargs):
        return _FakeClient(chunks(), capture, **kwargs)

    monkeypatch.setattr(deepseek_client, "AsyncOpenAI", fake_client)
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setattr(settings, "DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr(settings, "DEEPSEEK_THINKING_ENABLED", True)

    result = [
        chunk
        async for chunk in deepseek_client.stream_deepseek_text("流式报告")
    ]

    assert result == ["流式", "内容"]
    assert capture["closed"] is True
    assert capture["client"].chat.completions.calls == [
        {
            "model": "deepseek-v4-flash",
            "messages": [{"role": "user", "content": "流式报告"}],
            "stream": True,
            "extra_body": {"thinking": {"type": "enabled"}},
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("global_thinking", "thinking", "max_tokens", "expected_mode"),
    [
        (True, False, 4000, "disabled"),
        (False, True, None, "enabled"),
        (True, None, None, "enabled"),
        (False, None, 800, "disabled"),
    ],
)
async def test_generate_deepseek_json_applies_thinking_override_and_token_cap(
    monkeypatch, global_thinking, thinking, max_tokens, expected_mode
):
    capture = {}

    def fake_client(**kwargs):
        return _FakeClient(_completion('{"results": []}'), capture, **kwargs)

    monkeypatch.setattr(deepseek_client, "AsyncOpenAI", fake_client)
    monkeypatch.setattr(settings, "DEEPSEEK_MODEL", "test-model")
    monkeypatch.setattr(settings, "DEEPSEEK_THINKING_ENABLED", global_thinking)

    result = await deepseek_client.generate_deepseek_json(
        system_prompt="sys",
        user_prompt="usr",
        thinking=thinking,
        max_tokens=max_tokens,
    )

    assert result == {"results": []}
    assert capture["closed"] is True
    expected_call = {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "usr"},
        ],
        "response_format": {"type": "json_object"},
        "extra_body": {"thinking": {"type": expected_mode}},
    }
    if max_tokens is not None:
        expected_call["max_tokens"] = max_tokens
    assert capture["client"].chat.completions.calls == [expected_call]

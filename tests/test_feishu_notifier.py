import httpx
import pytest

from services.notifications.feishu_bot import FeishuNotifier

URL = "https://example.com/test-webhook"


def _notifier(sleeps):
    """A notifier that records its backoff waits instead of sleeping."""
    notifier = FeishuNotifier()
    notifier.webhook_url = URL

    async def sleep(delay):
        sleeps.append(delay)

    notifier._sleep = sleep
    return notifier


def _patch_post(monkeypatch, outcomes, posts):
    """Replay ``outcomes`` in order: an exception to raise or a (status, body) response."""
    async def post(client, url, **kwargs):
        posts.append(kwargs["timeout"])
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        status, body = outcome
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", post)


@pytest.mark.asyncio
async def test_connect_failures_are_retried_with_backoff(monkeypatch):
    posts, sleeps = [], []
    request = httpx.Request("POST", URL)
    _patch_post(monkeypatch, [httpx.ConnectTimeout("slow", request=request),
                              httpx.ConnectError("refused", request=request), (200, {"code": 0})], posts)

    assert await _notifier(sleeps).send("Quantify", "report") is True
    assert len(posts) == 3 and sleeps == [3.0, 15.0]
    assert posts[0].connect == 10.0 and posts[0].read == 15.0


@pytest.mark.asyncio
async def test_rate_limit_and_server_errors_are_retried(monkeypatch):
    posts, sleeps = [], []
    _patch_post(monkeypatch, [(429, {}), (503, {}), (200, {"code": 0})], posts)
    assert await _notifier(sleeps).send("Quantify", "report") is True
    assert len(posts) == 3


@pytest.mark.asyncio
async def test_a_read_timeout_is_not_retried_because_the_card_may_have_arrived(monkeypatch):
    posts, sleeps = [], []
    _patch_post(monkeypatch, [httpx.ReadTimeout("no reply", request=httpx.Request("POST", URL))], posts)
    assert await _notifier(sleeps).send("Quantify", "report") is False
    assert len(posts) == 1 and sleeps == []


@pytest.mark.asyncio
async def test_a_rejected_card_is_not_retried(monkeypatch):
    posts, sleeps = [], []
    _patch_post(monkeypatch, [(200, {"code": 19024, "msg": "Key Words Not Found"})], posts)
    assert await _notifier(sleeps).send("Quantify", "report") is False
    assert len(posts) == 1


@pytest.mark.asyncio
async def test_delivery_gives_up_after_four_attempts(monkeypatch):
    posts, sleeps = [], []
    request = httpx.Request("POST", URL)
    _patch_post(monkeypatch, [httpx.ConnectTimeout("slow", request=request) for _ in range(4)], posts)
    assert await _notifier(sleeps).send("Quantify", "report") is False
    assert len(posts) == 4 and sleeps == [3.0, 15.0, 60.0]

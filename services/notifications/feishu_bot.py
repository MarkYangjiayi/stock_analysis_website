import asyncio
import httpx
import logging
from core.config import settings
from .base import BaseNotifier

logger = logging.getLogger(__name__)

# Raised before the request reaches Feishu, so a retry cannot post the card twice.
# A read timeout or dropped connection may follow a delivered card and is not retried.
RETRYABLE_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=10.0)


class FeishuNotifier(BaseNotifier):
    # Waits before the second, third and fourth attempt. On 2026-10-19 one
    # 5-second connect timeout dropped the morning report.
    retry_delays: tuple[float, ...] = (3.0, 15.0, 60.0)
    _sleep = staticmethod(asyncio.sleep)

    def __init__(self):
        self.webhook_url = settings.FEISHU_WEBHOOK_URL

    async def send(self, title: str, markdown_content: str, **kwargs) -> bool:
        if not self.webhook_url:
            logger.warning("FEISHU_WEBHOOK_URL is not configured. Skipping Feishu notification.")
            return False

        # Scheduled reports pass a prebuilt Card 2.0 layout; generic alerts
        # keep the original single-Markdown card.
        card = kwargs.get("card") or {
            "config": {
                "wide_screen_mode": True
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": title
                },
                "template": "blue"
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": markdown_content
                }
            ]
        }
        payload = {"msg_type": "interactive", "card": card}

        attempts = 1 + len(self.retry_delays)
        async with httpx.AsyncClient() as client:
            for attempt, delay in enumerate((0.0, *self.retry_delays), start=1):
                if delay:
                    await self._sleep(delay)
                try:
                    response = await client.post(
                        self.webhook_url,
                        json=payload,
                        headers={"Content-Type": "application/json"},
                        timeout=REQUEST_TIMEOUT,
                    )
                except RETRYABLE_ERRORS as e:
                    # Transport exceptions can embed the credential-bearing webhook.
                    logger.warning("Feishu notification not sent (%s), attempt %d/%d", type(e).__name__, attempt, attempts)
                    continue
                except Exception as e:
                    logger.error("Failed to send Feishu notification (%s); not retried in case it was delivered", type(e).__name__)
                    return False
                if response.status_code in RETRYABLE_STATUS:
                    logger.warning("Feishu returned HTTP %s, attempt %d/%d", response.status_code, attempt, attempts)
                    continue
                try:
                    response.raise_for_status()
                    result = response.json()
                except Exception as e:
                    logger.error("Failed to send Feishu notification (%s)", type(e).__name__)
                    return False
                if result.get("code") != 0:
                    logger.error("Feishu notification rejected (code=%s)", result.get("code"))
                    return False
                return True
        logger.error("Failed to send Feishu notification after %d attempts", attempts)
        return False

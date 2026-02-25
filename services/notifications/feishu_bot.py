import httpx
import logging
from core.config import settings
from .base import BaseNotifier

logger = logging.getLogger(__name__)

class FeishuNotifier(BaseNotifier):
    def __init__(self):
        self.webhook_url = settings.FEISHU_WEBHOOK_URL
        
    async def send(self, title: str, markdown_content: str, **kwargs) -> bool:
        if not self.webhook_url:
            logger.warning("FEISHU_WEBHOOK_URL is not configured. Skipping Feishu notification.")
            return False
            
        payload = {
            "msg_type": "interactive",
            "card": {
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
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.webhook_url, 
                    json=payload, 
                    headers={"Content-Type": "application/json"},
                    timeout=5.0
                )
                response.raise_for_status()
                result = response.json()
                if result.get("code") != 0:
                    logger.error(f"Feishu API error: {result}")
                    return False
                return True
        except Exception as e:
            logger.error(f"Failed to send Feishu notification: {e}")
            return False

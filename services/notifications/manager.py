import logging
from .feishu_bot import FeishuNotifier

logger = logging.getLogger(__name__)

class NotificationManager:
    _channels = {
        "feishu": FeishuNotifier()
    }
    
    @classmethod
    async def broadcast(cls, title: str, content: str, channels: list[str] = ["feishu"]) -> None:
        """
        Broadcasts a message to multiple specified channels concurrently.
        """
        for channel in channels:
            notifier = cls._channels.get(channel)
            if notifier:
                logger.info(f"Broadcasting to {channel}: {title}")
                success = await notifier.send(title, content)
                if not success:
                    logger.warning(f"Failed to broadcast to channel: {channel}")
            else:
                logger.warning(f"Unknown notification channel: {channel}")

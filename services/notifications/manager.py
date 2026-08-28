import logging
import asyncio
from .feishu_bot import FeishuNotifier

logger = logging.getLogger(__name__)

class NotificationManager:
    _channels = {
        "feishu": FeishuNotifier()
    }
    
    @classmethod
    async def broadcast(
        cls,
        title: str,
        content: str,
        channels: list[str] | None = None,
    ) -> bool:
        """
        Broadcast a message and report whether every requested channel accepted it.
        """
        requested_channels = ["feishu"] if channels is None else channels

        async def send(channel: str) -> bool:
            notifier = cls._channels.get(channel)
            if notifier:
                logger.info(f"Broadcasting to {channel}: {title}")
                success = await notifier.send(title, content)
                if not success:
                    logger.warning(f"Failed to broadcast to channel: {channel}")
                return success
            else:
                logger.warning(f"Unknown notification channel: {channel}")
                return False

        results = await asyncio.gather(*(send(channel) for channel in requested_channels))
        return bool(results) and all(results)

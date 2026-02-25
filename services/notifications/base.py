from abc import ABC, abstractmethod

class BaseNotifier(ABC):
    @abstractmethod
    async def send(self, title: str, markdown_content: str, **kwargs) -> bool:
        """
        Sends a notification message to the specified channel.
        """
        pass

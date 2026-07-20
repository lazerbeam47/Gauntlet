from abc import ABC, abstractmethod


class ConversationRuntime(ABC):
    @abstractmethod
    async def run(
        self,
        persona: dict,
        agent_prompt: str,
    ) -> dict:
        """
        Runs one conversation.

        Returns:
            {
                "transcript": ...,
                "metadata": ...
            }
        """
        pass

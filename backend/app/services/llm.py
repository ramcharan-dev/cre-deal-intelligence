"""Model-independent text-generation hook for Copilot answers.

No generator is configured in the POC, so Copilot answers are structured (computed from stored data) or
extractive (search results with sources). To add Gemini/Groq/Claude later, implement
`TextGenerator` and return it from `get_text_generator()`; the Copilot service does not change.
"""

from typing import Protocol


class TextGenerator(Protocol):
    model: str

    async def generate(self, system: str, prompt: str) -> str: ...


def get_text_generator() -> TextGenerator | None:
    return None

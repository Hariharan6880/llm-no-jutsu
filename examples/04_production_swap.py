"""Why the LLM interface exists: dev and production differ in one line.

Your agent depends on `LLM`, never on a concrete backend. Choose the backend at
the edge of the application, from config, and nothing downstream changes.
"""

import os

from devllm import LLM, ClaudeCLI, LLMResponse


class AnthropicAPI(LLM):
    """Sketch of the production backend. Needs `pip install anthropic`."""

    name = "anthropic-api"

    def __init__(self, model: str = "claude-sonnet-5"):
        from anthropic import Anthropic  # imported lazily, dev has no dep

        self.client = Anthropic()  # reads ANTHROPIC_API_KEY
        self.model = model

    def generate(self, prompt, *, schema=None, system=None) -> LLMResponse:
        message = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system or "",
            messages=[{"role": "user", "content": prompt}],
        )
        return LLMResponse(
            text=message.content[0].text,
            backend=self.name,
            model=self.model,
        )


def build_llm() -> LLM:
    """The only place in the codebase that knows which backend is in play."""
    if os.getenv("APP_ENV") == "production":
        return AnthropicAPI()
    return ClaudeCLI(model="sonnet")


def recommend(llm: LLM, budget: int) -> str:
    """Application logic. Identical in both environments."""
    return llm.generate(f"Recommend a phone under {budget} INR.").text


if __name__ == "__main__":
    print(recommend(build_llm(), 50_000))

"""An agent loop where devllm is only the reasoning step.

The pattern: keep the tool calling in your own Python. Ask the model for a
decision as JSON, run the tool yourself, feed the result back, repeat. The CLI
never gets to run its own agent loop, so you stay in control of the flow and
your tools stay yours.

Swap `fake_search` for Tavily, SerpAPI, or your own retriever.
"""

import json

from devllm import get_backend

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["search", "answer"]},
        "query": {"type": "string", "description": "search terms, if searching"},
        "answer": {"type": "string", "description": "final answer, if answering"},
    },
    "required": ["action"],
    "additionalProperties": False,
}

SYSTEM = (
    "You are a research agent. Decide whether you need a web search or can "
    "answer now. Search at most twice, then answer."
)


def fake_search(query: str) -> str:
    """Stand-in for a real search tool."""
    print(f"    [tool] search({query!r})")
    return json.dumps([
        {"title": "Best phones under 50k", "snippet": "OnePlus 13R at 42,999..."},
        {"title": "Buying guide 2026", "snippet": "iQOO Neo 10 at 31,999..."},
    ])


def run(question: str, max_steps: int = 4) -> str:
    llm = get_backend()  # DEVLLM_BACKEND env var, or claude by default
    transcript = [f"Question: {question}"]

    for step in range(max_steps):
        # Each call is a fresh process with no memory, so the running
        # transcript is re-sent every time. That is the main cost of using a
        # CLI as a backend -- and it keeps the loop trivially inspectable.
        decision = llm.generate(
            "\n\n".join(transcript) + "\n\nWhat is your next action?",
            schema=DECISION_SCHEMA,
            system=SYSTEM,
        ).structured

        print(f"  step {step + 1}: {decision['action']}")

        if decision["action"] == "answer":
            return decision.get("answer", "")

        results = fake_search(decision.get("query", question))
        transcript.append(f"Search results: {results}")

    return "gave up: hit the step limit"


if __name__ == "__main__":
    print(run("What are the best phones under 50000 INR in India right now?"))

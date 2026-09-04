"""A tool-calling agent where devllm is only the reasoning step.

Keep the tool calling in your own code: ask the model for a decision as JSON,
run the tool yourself, feed the result back, repeat. Swap `fake_search` for
Tavily, SerpAPI or your own retriever.

Start the server first:  python server.py
"""

import json
import urllib.request

URL = "http://localhost:8765/generate"

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["search", "answer"]},
        "query": {"type": "string"},
        "answer": {"type": "string"},
    },
    "required": ["action"],
    "additionalProperties": False,
}

SYSTEM = ("You are a research agent. Decide whether you need a web search or "
          "can answer now. Search at most twice, then answer.")


def generate(prompt: str, **options) -> dict:
    payload = json.dumps({"prompt": prompt, **options}).encode("utf-8")
    request = urllib.request.Request(
        URL, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def fake_search(query: str) -> str:
    print(f"    [tool] search({query!r})")
    return json.dumps([
        {"title": "Best phones under 50k", "snippet": "OnePlus 13R at 42,999"},
        {"title": "Buying guide 2026", "snippet": "iQOO Neo 10 at 31,999"},
    ])


def run(question: str, max_steps: int = 4) -> str:
    transcript = [f"Question: {question}"]
    for step in range(max_steps):
        # Each call is a fresh CLI process with no memory, so the running
        # transcript is resent every time. That is the cost of a stateless
        # backend, and it keeps the loop trivially inspectable.
        result = generate(
            "\n\n".join(transcript) + "\n\nWhat is your next action?",
            schema=DECISION_SCHEMA,
            system=SYSTEM,
        )
        decision = result["structured"]
        print(f"  step {step + 1}: {decision['action']}")

        if decision["action"] == "answer":
            return decision.get("answer", "")
        transcript.append(f"Search results: {fake_search(decision.get('query', question))}")

    return "gave up: hit the step limit"


if __name__ == "__main__":
    print(run("What are the best phones under 50000 INR in India right now?"))

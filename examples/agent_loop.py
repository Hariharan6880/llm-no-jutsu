"""A tool-calling agent where devllm is only the reasoning step.

Keep the tool calling in your own code: ask the model for a decision as JSON,
run the tool yourself, feed the result back, repeat. Swap `fake_search` for
Tavily, SerpAPI or your own retriever.

Start the server first:  python server.py
"""

import json
import urllib.error
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
        # `structured` is null unless the model honoured the schema, so this
        # is checked rather than indexed -- a KeyError here would look like a
        # bug in your loop when it is really a model that answered in prose.
        decision = result.get("structured")
        if not isinstance(decision, dict) or "action" not in decision:
            return ("gave up: no decision in the response "
                    f"({str(result.get('text'))[:200]!r})")
        print(f"  step {step + 1}: {decision['action']}")

        if decision["action"] == "answer":
            return decision.get("answer", "")
        transcript.append(f"Search results: {fake_search(decision.get('query', question))}")

    return "gave up: hit the step limit"


if __name__ == "__main__":
    try:
        print(run("What are the best phones under 50000 INR in India right now?"))
    except urllib.error.HTTPError as exc:
        # urlopen raises on 4xx/5xx. The body is the server's JSON error
        # envelope, which is worth far more to you than a traceback.
        body = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(body)["error"]
        except (json.JSONDecodeError, KeyError, TypeError):
            detail = body[:500]
        raise SystemExit(f"server returned {exc.code}: {detail}")

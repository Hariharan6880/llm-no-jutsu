"""Call the devllm server from Python. Standard library only.

Start the server first:  python server.py
"""

import json
import urllib.error
import urllib.request

URL = "http://localhost:8765/generate"


def explain(exc: urllib.error.HTTPError) -> str:
    """urlopen raises on 4xx/5xx. The body is the server's JSON error
    envelope, which is worth far more to you than a traceback."""
    body = exc.read().decode("utf-8", "replace")
    try:
        return f"server returned {exc.code}: {json.loads(body)['error']}"
    except (json.JSONDecodeError, KeyError, TypeError):
        return f"server returned {exc.code}: {body[:500]}"


def generate(prompt: str, **options) -> dict:
    payload = json.dumps({"prompt": prompt, **options}).encode("utf-8")
    request = urllib.request.Request(
        URL, data=payload, headers={"Content-Type": "application/json"}
    )
    # A call takes 10-40 seconds. Any client talking to this server needs a
    # generous timeout; the default in most HTTP libraries is far too short.
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


if __name__ == "__main__":
    try:
        result = generate("Recommend one phone under 50000 INR. One sentence.")
    except urllib.error.HTTPError as exc:
        raise SystemExit(explain(exc))
    print(result["text"])
    print(f"\n[{result['backend']} in {result['duration_s']}s]")

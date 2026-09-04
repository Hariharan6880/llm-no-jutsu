"""Call the devllm server from Python. Standard library only.

Start the server first:  python server.py
"""

import json
import urllib.request

URL = "http://localhost:8765/generate"


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
    result = generate("Recommend one phone under 50000 INR. One sentence.")
    print(result["text"])
    print(f"\n[{result['backend']} in {result['duration_s']}s]")

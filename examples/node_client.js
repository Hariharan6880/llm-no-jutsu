// Call the devllm server from Node. No packages needed (Node 18+).
// Start the server first:  python server.py

const URL = "http://localhost:8765/generate";

async function generate(prompt, options = {}) {
  // A call takes 10-40 seconds, so the default fetch timeout must be raised.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 300_000);
  try {
    const response = await fetch(URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, ...options }),
      signal: controller.signal,
    });
    const body = await response.json();
    if (!response.ok) throw new Error(`${response.status}: ${body.error}`);
    return body;
  } finally {
    clearTimeout(timer);
  }
}

generate("Recommend one phone under 50000 INR. One sentence.")
  .then((r) => console.log(r.text))
  .catch((e) => console.error(e.message));

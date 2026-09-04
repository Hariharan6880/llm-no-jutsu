# llm-no-jutsu

[![tests](https://github.com/Hariharan6880/llm-no-jutsu/actions/workflows/tests.yml/badge.svg)](https://github.com/Hariharan6880/llm-no-jutsu/actions/workflows/tests.yml)

**Use the Claude Code / Codex CLI you already have as a local LLM endpoint — so prototyping costs nothing beyond the subscription you already pay for.**

---

## The problem

You're building something on an LLM. You're not shipping yet — you're rewriting the prompt for the fortieth time. Every one of those iterations costs API credits, and free-tier limits run out mid-afternoon.

Meanwhile you already pay for Claude Pro or ChatGPT Plus, and both ship a CLI that runs non-interactively against your existing login.

`devllm` wraps those CLIs behind one small interface, so your dev loop runs on the subscription you already have and your production code swaps to a real API client in one line.

```
your project
      ↓
  POST localhost:8765/generate
      ↓
  claude -p / codex exec
      ↓
  your subscription
      ↓
  JSON response
```

> **This is a development tool.** It shells out to an interactive CLI, so it is slow, stateless and not built for concurrency or throughput. Ship on the official [Anthropic](https://docs.anthropic.com/) or [OpenAI](https://platform.openai.com/docs) APIs. Consumer subscription terms are written around interactive use of the CLI, not around powering other applications — use your judgement about what's appropriate, and don't put this in production.

---

## Quickstart

```bash
git clone https://github.com/Hariharan6880/llm-no-jutsu
cd llm-no-jutsu
python server.py --check     # is a CLI installed and logged in?
python server.py             # http://localhost:8765
```

Open [http://localhost:8765](http://localhost:8765) and send one prompt to prove it works — it's a plain HTML form, nothing to install.

Or call it from code:

```python
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
```

That's the whole interface. Everything below is reference material.

---

## Requirements

- Python 3.10+
- No dependencies — the server is standard library only
- At least one CLI installed and logged in:

```bash
npm install -g @anthropic-ai/claude-code    # then run: claude
npm install -g @openai/codex                # then run: codex login
```

---

## API reference

### `POST /generate`

| Field | Type | Required | Notes |
|---|---|---|---|
| `prompt` | string | yes | non-empty |
| `backend` | `"claude"` \| `"codex"` | no | defaults to the server's default backend |
| `model` | string | no | e.g. `"sonnet"`; backend-specific |
| `schema` | object | no | a JSON Schema; when given, the parsed object comes back on `structured` |
| `system` | string \| null | no | overrides the server's default system prompt; `null` sends no system prompt at all |
| `reasoning` | string | no | Codex only — `"low"` (default), `"none"`, `"high"`, `"xhigh"`, etc. |
| `timeout` | integer | no | per-request ceiling in seconds, capped at the server's `--timeout` |

Response:

```json
{
  "ok": true,
  "text": "...",
  "structured": null,
  "backend": "claude",
  "model": "sonnet",
  "duration_s": 9.8,
  "usage": {
    "input_tokens": 4,
    "output_tokens": 120,
    "cache_read_tokens": 4096,
    "cache_write_tokens": 0
  }
}
```

Status codes:

| Status | Meaning |
|---|---|
| 200 | success |
| 400 | bad request — missing/invalid `prompt`, `schema`, `system` or `timeout` |
| 401 | missing or invalid bearer token (only when `DEVLLM_TOKEN` is set) |
| 429 | queue full — too many requests already waiting for a CLI slot |
| 502 | the CLI ran but failed, or its output couldn't be parsed |
| 503 | no backend CLI is installed/available |
| 504 | the CLI didn't finish within the timeout |

### `GET /health`

Always 200 — an unconfigured server is still a running one. Reports which backends are installed, the server's default backend, and current queue occupancy. Performs no model call.

### `GET /`

A plain HTML page for driving the server by hand: prompt, system prompt, model, and an optional JSON schema. No build step, no JS framework.

---

## Timeouts

A call takes **10–40 seconds** — most of it CLI process startup, not model time. Most HTTP clients default to far less than that and will time out on the first real request. Raise the timeout explicitly:

```python
requests.post(url, json=payload, timeout=300)
```

```javascript
// fetch has no timeout option; use AbortSignal
fetch(url, { method: "POST", body, signal: AbortSignal.timeout(300_000) })
```

```javascript
axios.post(url, payload, { timeout: 300_000 })
```

If a client reports a timeout, this is almost always the fix — the server is not hung.

---

## Configuration

Flags to `server.py`:

| Flag | Default | Notes |
|---|---|---|
| `--port` | `8765` | |
| `--host` | `127.0.0.1` | see "binding off localhost" below |
| `--backend` | auto-detected | `claude` or `codex`; first installed backend, preferring claude |
| `--model` | backend default | e.g. `sonnet` |
| `--concurrency` | `2` | concurrent CLI processes; each is a heavy subprocess |
| `--timeout` | `300` | per-request ceiling in seconds; a request may lower it, never raise it |
| `--allow-remote` | off | permit binding off localhost |
| `--check` | — | report install/login state for each CLI, then exit |

`DEVLLM_TOKEN` (environment variable): when set, every request must send `Authorization: Bearer <token>`, and `/generate`/`/health` reply 401 without it.

**Binding off localhost** takes three deliberate actions, because this endpoint spawns subprocesses using your paid subscription login: pass a non-local `--host`, pass `--allow-remote`, and set `DEVLLM_TOKEN`. Missing any one of the three refuses to start.

---

## Speed: read this before you judge it

A CLI is not an API. Roughly **10 seconds of every call is process startup**, and you cannot tune that away. Measured on Windows 11, trivial prompt, median of repeated runs:

| Backend | Setting | Wall clock | Notes |
|---|---|---:|---|
| claude | `--model sonnet` | **~10s** | of which model time was 1.8s — the rest is Node boot |
| claude | `--model haiku` | ~12s | no faster; a smaller model does not help |
| codex | default (`xhigh` reasoning) | **~31s** | the CLI's own default |
| codex | `reasoning_effort="low"` | **~11s** | `devllm`'s default |
| codex | `reasoning_effort="none"` | ~12s | no better than `low` |

Two things follow:

1. **`CodexCLI` defaults to `reasoning_effort="low"`**, which is a ~3x speedup over the CLI's own default. Raise it when you want quality over iteration speed.
2. **Pick a Claude model for output quality, not speed.** Startup dominates, so `sonnet` costs about the same wall-clock as `haiku`.

Watch the token counts too. Each call is a fresh process, so the system prompt and tool schemas are re-sent every time:

| Claude invocation | Cached input tokens per call |
|---|---:|
| stock system prompt (`system=None`) | ~12,500 |
| `devllm`'s default `system=` | ~4,100 |

That per-call overhead — not the number of requests — is what exhausts a subscription's limits.

---

## Gotchas this library handles for you

These cost real debugging time. They're solved inside `devllm`; they're documented here because you'll hit them if you roll your own.

| Gotcha | What happens | Fix |
|---|---|---|
| **Windows npm shims** | `subprocess.run(["claude", ...])` raises `FileNotFoundError` — the executable is `claude.CMD` and Python doesn't apply `PATHEXT` | resolve with `shutil.which()` first |
| **Codex blocks on stdin** | If stdin is left open, `codex exec` waits forever for more input — a silent hang | pass `input=` (which closes stdin), or `-` explicitly |
| **Claude's schema output** | With `--json-schema`, the parsed object is on `structured_output`. `result` stays prose, so `json.loads(result)` fails | read `structured_output` |
| **cp1252 consoles** | Printing `₹` (or any non-Latin-1 char) raises `UnicodeEncodeError` on a default Windows console | `sys.stdout.reconfigure(encoding="utf-8")` — `devllm`'s CLI does this for you |
| **Noisy Codex stdout** | stdout is a human transcript: banner, token counts, log lines | read the answer from `-o <file>`, don't scrape |
| **Agents that wander** | Left alone, both CLIs will happily read and write your files | `devllm` disables their tools by default (`tools=True` to opt in) |
| **They think they're coding assistants** | With its stock prompt, Claude Code *refuses* ordinary questions: "phone buying advice isn't something I can help well with" | `devllm` replaces the system prompt by default (`DEFAULT_SYSTEM`). Pass `system=None` to get the CLI's own identity back |

---

## Verified on

| | Library and API (offline tests) | Live CLI calls |
|---|---|---|
| Windows 11 | CI | manually verified |
| Linux | CI | untested |
| macOS | CI | untested |

CI runs the offline test suite on Linux, macOS and Windows across Python 3.10–3.12 on every push. A live call needs a logged-in CLI holding a real subscription session, which a CI runner cannot have — those were verified by hand on Windows 11.

---

## Limitations

- **Slow.** ~10s floor per call. Fine for iterating, wrong for anything user-facing.
- **Stateless.** Every call is a new process with no memory. Multi-turn means re-sending the transcript yourself (see [`examples/agent_loop.py`](examples/agent_loop.py)).
- **No streaming.** The CLIs can stream; `devllm` waits for the final result.
- **Not concurrency-friendly.** Each call is a full CLI process. Use `--concurrency` to cap how many run at once, and don't fan out dozens.
- **No real system role on Codex.** `codex exec` has no system-prompt flag, so `system=` is prepended to the prompt.
- **Flags drift.** Verified against Claude Code 2.1.175 and codex-cli 0.152.1. Run `python server.py --check` after upgrading either CLI.

---

## Examples

| File | Shows |
|---|---|
| [`examples/python_client.py`](examples/python_client.py) | prompt in, text out, from the standard library only |
| [`examples/node_client.js`](examples/node_client.js) | the same, from Node's built-in `fetch` |
| [`examples/curl.sh`](examples/curl.sh) | plain text and structured output from the shell |
| [`examples/agent_loop.py`](examples/agent_loop.py) | a tool-calling agent where devllm is only the reasoning step |

---

## Development

```bash
python -m unittest discover -s tests
```

`tests/test_live.py` makes real calls against an installed, logged-in CLI and skips itself when none is available — it won't fail CI or a machine with no CLI installed.

## License

MIT

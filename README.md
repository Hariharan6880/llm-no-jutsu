# llm-no-jutsu

[![tests](https://github.com/Hariharan6880/llm-no-jutsu/actions/workflows/tests.yml/badge.svg)](https://github.com/Hariharan6880/llm-no-jutsu/actions/workflows/tests.yml)

**Use the Claude Code / Codex CLI you already have as a local LLM endpoint — so prototyping costs nothing beyond the subscription you already pay for.**

---

## The problem

You're building something on an LLM. You're not shipping yet — you're rewriting the prompt for the fortieth time. Every one of those iterations costs API credits, and free-tier limits run out mid-afternoon.

Meanwhile you already pay for Claude Pro or ChatGPT Plus, and both ship a CLI that runs non-interactively against your existing login.

`devllm` puts those CLIs behind one small HTTP endpoint, so your dev loop runs on the subscription you already have. Your code posts JSON to `localhost:8765`; when you ship, you point that same call at a real API instead.

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

`--check` does more than look for the executable: it **makes one real call per installed backend** to prove the whole path works, which takes 10–40s each and spends a small amount of your subscription quota. That is the point — an installed CLI that is logged out looks identical to a working one until something actually asks it a question.

Open [http://localhost:8765](http://localhost:8765) and send one prompt to prove it works — it's a small built-in page, nothing to install.

Or call it from code:

```python
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
```

`Content-Type: application/json` is required, not decorative — see the `415` row below.

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

Requests must carry `Content-Type: application/json`. Only `prompt` is required; every other field falls back to a server default set by command-line flags.

```json
{
  "prompt": "Recommend a phone under 50000 INR.",
  "backend": "claude",
  "model": "sonnet",
  "system": "You are a product recommendation engine.",
  "schema": { "type": "object", "properties": {} },
  "reasoning": "low",
  "timeout": 300
}
```

| Field | Type | Meaning |
|---|---|---|
| `prompt` | string, required | The user message. Non-empty after stripping. |
| `backend` | `"claude"` \| `"codex"` | Defaults to the server's `--backend`. |
| `model` | string | Backend-specific. Defaults to the server's `--model`. |
| `system` | string \| null | **Omitted** → the server's default system prompt. **`null`** → suppress the system prompt entirely, using the CLI's own. **String** → use it. |
| `schema` | object | A JSON Schema. When present, the parsed object is returned in `structured`. |
| `reasoning` | string | Codex only: `none`/`low`/`medium`/`high`/`xhigh`/`max`. Silently ignored for `claude` rather than rejected. |
| `timeout` | integer | Seconds. Clamped to the server's `--timeout` ceiling rather than rejected. |

Response, `200`:

```json
{
  "ok": true,
  "text": "...",
  "structured": null,
  "backend": "claude",
  "model": "sonnet",
  "duration_s": 12.3,
  "usage": { "input_tokens": 4, "output_tokens": 231,
             "cache_read_tokens": 4218, "cache_write_tokens": 720 }
}
```

`structured` is `null` unless `schema` was supplied. `usage` is `null` for `codex`, which reports no token counts — callers must not assume it is present.

Errors return the same envelope with a real HTTP status:

```json
{ "ok": false, "error": "codex did not respond within 300s",
  "error_type": "BackendTimeoutError" }
```

| Condition | Status |
|---|---|
| Malformed JSON, missing/empty `prompt`, unknown `backend`, invalid `schema`, `system` that isn't a string or null, `timeout` that isn't a positive integer | 400 |
| Missing or invalid token when auth is enabled | 401 |
| `Content-Type` is anything other than `application/json` | 415 |
| Queue full | 429 |
| CLI ran but failed (including not logged in), or returned unparseable output | 502 |
| CLI not installed | 503 |
| Request timed out | 504 |

Status codes are load-bearing: callers will use `raise_for_status()` and `response.ok`, and a 200 carrying an error body would break them.

The `415` is a security control. A cross-origin `fetch` sending `Content-Type: text/plain` is a CORS *simple* request — the browser delivers it with no preflight, so any page you have open could otherwise POST here, spawn a CLI and spend your subscription. Requiring `application/json` forces a preflight, which this server does not answer. Binding to localhost does not help: the request comes from your own browser.

### `GET /health`

```json
{
  "status": "ok",
  "backends": {
    "claude": { "installed": true,  "login": "subscription: pro" },
    "codex":  { "installed": false, "login": "no auth file (run `codex login`)" }
  },
  "default_backend": "claude",
  "queue": { "active": 0, "waiting": 0, "concurrency": 2 }
}
```

`default_backend` is `null` when no backend is installed. `status` is `"ok"` when at least one backend is installed, `"unconfigured"` otherwise — the server still starts and still answers `/health` so a first-time user can tell "server is down" from "server is up but no CLI is logged in". This performs no live model call.

### `GET /`

Serves a browser playground: prompt, system prompt, model, a backend selector, a reasoning (codex) dropdown, and an optional JSON schema. Its purpose is narrow — it exists so a new user can confirm the server actually answers *before* touching their own code, the difference between "my integration is broken" and "my CLI was never logged in". It's a smoke-test surface, not a product surface. It is not behind the token check, and it cannot send one either — see `DEVLLM_TOKEN` below.

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

Flags on `server.py`, chosen so bare `python server.py` is right for the common case:

| Flag | Default |
|---|---|
| `--port` | 8765 |
| `--host` | 127.0.0.1 |
| `--backend` | first installed backend, preferring claude |
| `--model` | backend default (`sonnet` for claude) |
| `--concurrency` | 2 |
| `--timeout` | 300 |
| `--allow-remote` | off |
| `--check` | runs the install/login report and exits, without starting the server. Makes one real call per installed backend to prove the whole path works — 10–40s and a little quota each. Exits non-zero if nothing is installed **or** a live call fails |

`DEVLLM_TOKEN` is the only environment variable. When set — at any bind address — requests must carry `Authorization: Bearer <token>` or receive `401`. Note that the browser page at `/` cannot authenticate: it sends no `Authorization` header, so while a token is set the page still loads but every **Send** fails with `401 missing or invalid token`. Use `curl` or a client of your own when testing with a token.

If no backend is installed the server still starts, logs a clear warning naming the install commands, and returns `503` from `/generate`.

**Binding off localhost.** The server binds `127.0.0.1` by default and refuses to bind anything else unless you pass **both** `--host <addr>` and `--allow-remote`, *and* set `DEVLLM_TOKEN` in the environment. When bound remotely it prints a warning naming the risk. This endpoint executes subprocesses using paid credentials, so the defaults are safe and the escape hatch — a home server, a container — cannot be triggered accidentally. The token is read from the environment rather than a flag so it does not land in shell history.

---

## Speed: read this before you judge it

A CLI is not an API. Roughly **10 seconds of every call is process startup**, and you cannot tune that away. Measured on Windows 11, trivial prompt, median of repeated runs:

| Backend | Setting | Wall clock | Notes |
|---|---|---:|---|
| claude | `--model sonnet` | **~10s** | of which model time was 1.8s — the rest is Node boot |
| claude | `--model haiku` | ~12s | no faster; a smaller model does not help |
| codex | default (`xhigh` reasoning) | **~31s** | the CLI's own default |
| codex | `"reasoning": "low"` | **~11s** | the server's default |
| codex | `"reasoning": "none"` | ~12s | no better than `low` |

Two things follow:

1. **The codex backend defaults to `"reasoning": "low"`**, which is a ~3x speedup over the CLI's own default. Raise it in the request body when you want quality over iteration speed.
2. **Pick a Claude model for output quality, not speed.** Startup dominates, so `sonnet` costs about the same wall-clock as `haiku`.

Watch the token counts too. Each call is a fresh process, so the system prompt and tool schemas are re-sent every time:

| Claude invocation | Cached input tokens per call |
|---|---:|
| stock system prompt (`"system": null`) | ~12,500 |
| the server's default system prompt | ~4,100 |

That per-call overhead — not the number of requests — is what exhausts a subscription's limits.

---

## Gotchas the server handles for you

These cost real debugging time. They're solved behind the endpoint; they're documented here because you'll hit them if you roll your own.

| Gotcha | What happens | Fix |
|---|---|---|
| **Windows npm shims** | `subprocess.run(["claude", ...])` raises `FileNotFoundError` — the executable is `claude.CMD` and Python doesn't apply `PATHEXT` | resolve with `shutil.which()` first |
| **Codex blocks on stdin** | If stdin is left open, `codex exec` waits forever for more input — a silent hang | pass `input=` (which closes stdin), or `-` explicitly |
| **Claude's schema output** | With `--json-schema`, the parsed object is on `structured_output`. `result` stays prose, so `json.loads(result)` fails | read `structured_output` |
| **cp1252 consoles** | Printing `₹` (or any non-Latin-1 char) raises `UnicodeEncodeError` on a default Windows console | `sys.stdout.reconfigure(encoding="utf-8")` — `devllm`'s CLI does this for you |
| **Noisy Codex stdout** | stdout is a human transcript: banner, token counts, log lines | read the answer from `-o <file>`, don't scrape |
| **Agents that wander** | Left alone, both CLIs will happily read and write your files | `devllm` disables their tools |
| **They think they're coding assistants** | With its stock prompt, Claude Code *refuses* ordinary questions: "phone buying advice isn't something I can help well with" | `devllm` replaces the system prompt by default. Send `"system": null` to get the CLI's own identity back |

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

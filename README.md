# devllm

**Use the Claude Code / Codex CLI you already have as an LLM backend in Python — for local development, so you stop burning API credits while you experiment.**

Zero dependencies. Python 3.10+. MIT.

```python
from devllm import ClaudeCLI

llm = ClaudeCLI(model="sonnet")
print(llm.generate("Name three phone brands sold in India.").text)
```

---

## The problem

You're building something on an LLM. You're not shipping yet — you're rewriting the prompt for the fortieth time. Every one of those iterations costs API credits, and free-tier limits run out mid-afternoon.

Meanwhile you already pay for Claude Pro or ChatGPT Plus, and both ship a CLI that runs non-interactively against your existing login.

`devllm` wraps those CLIs behind one small interface, so your dev loop runs on the subscription you already have and your production code swaps to a real API client in one line.

```
Your Python code
      ↓
   LLM.generate(prompt)          ← the only thing your app depends on
      ↓
  subprocess → claude -p / codex exec
      ↓
  your existing subscription login
      ↓
  response parsed back into Python
```

> **This is a development tool.** It shells out to an interactive CLI, so it is slow, stateless and not built for concurrency or throughput. Ship on the official [Anthropic](https://docs.anthropic.com/) or [OpenAI](https://platform.openai.com/docs) APIs. Consumer subscription terms are written around interactive use of the CLI, not around powering other applications — use your judgement about what's appropriate, and don't put this in production.

---

## Install

```bash
pip install git+https://github.com/<your-username>/devllm
```

Then install at least one CLI and log in once:

```bash
npm install -g @anthropic-ai/claude-code    # then run: claude
npm install -g @openai/codex                # then run: codex login
```

Check everything works:

```bash
devllm doctor
```

```
devllm doctor
----------------------------------------------------
  claude   C:\...\npm\claude.CMD
           subscription: pro
           live call OK in 18.7s -> 'ok'

  codex    C:\...\npm\codex.CMD
           subscription: ChatGPT OAuth
           live call OK in 13.0s -> 'ok'
----------------------------------------------------
At least one backend is working. You are good to go.
```

---

## Use it

### Plain text

```python
from devllm import ClaudeCLI, CodexCLI

llm = ClaudeCLI(model="sonnet")          # or CodexCLI()
r = llm.generate("Recommend a phone under 50000 INR.")

r.text          # the answer
r.duration_s    # how long it took
r.usage         # token counts, when the backend reports them
r.argv          # the exact command that ran — paste it into a terminal
```

### Structured output

Both CLIs can be constrained to a JSON Schema. This is what makes them usable inside an agent — you branch on data, not on scraped markdown.

```python
SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "price_inr": {"type": "integer"},
                },
                "required": ["name", "price_inr"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["picks"],
    "additionalProperties": False,
}

r = llm.generate("Pick 2 phones under 50000 INR.", schema=SCHEMA)

for pick in r.structured["picks"]:       # a real dict, already parsed
    print(pick["name"], pick["price_inr"])
```

### Choose the backend from config

```python
from devllm import get_backend

llm = get_backend()             # $DEVLLM_BACKEND, or "claude"
llm = get_backend("codex")
```

### The playground

```bash
devllm play
```

A local page for driving both backends by hand: prompt, system prompt, model, reasoning effort, and an optional JSON schema. Every result shows the answer, the parsed object, the token counts, and **the exact command line that produced it** — so it doubles as a way to learn the CLI flags.

### From the shell

```bash
devllm ask "Name three phone brands sold in India."
devllm ask -b codex --schema schema.json "List three phone brands."
cat long_prompt.txt | devllm ask --json
```

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

## Going to production

Your application depends on `LLM`, never on a concrete backend, so the swap is one line at the construction site:

```python
from devllm import LLM, ClaudeCLI, LLMResponse

class AnthropicAPI(LLM):
    name = "anthropic-api"
    def __init__(self, model="claude-sonnet-5"):
        from anthropic import Anthropic
        self.client, self.model = Anthropic(), model

    def generate(self, prompt, *, schema=None, system=None) -> LLMResponse:
        msg = self.client.messages.create(
            model=self.model, max_tokens=4096,
            system=system or "",
            messages=[{"role": "user", "content": prompt}],
        )
        return LLMResponse(text=msg.content[0].text, backend=self.name)


def build_llm() -> LLM:
    if os.getenv("APP_ENV") == "production":
        return AnthropicAPI()
    return ClaudeCLI(model="sonnet")     # ← the only line that differs
```

See [`examples/04_production_swap.py`](examples/04_production_swap.py).

---

## Examples

| File | Shows |
|---|---|
| [`01_basic.py`](examples/01_basic.py) | prompt in, text out |
| [`02_structured_output.py`](examples/02_structured_output.py) | JSON Schema → parsed dict |
| [`03_agent_loop.py`](examples/03_agent_loop.py) | a tool-calling agent where devllm is only the reasoning step |
| [`04_production_swap.py`](examples/04_production_swap.py) | dev/prod backends behind one interface |

---

## Limitations

- **Slow.** ~10s floor per call. Fine for iterating, wrong for anything user-facing.
- **Stateless.** Every call is a new process with no memory. Multi-turn means re-sending the transcript yourself (see `examples/03_agent_loop.py`).
- **No streaming.** The CLIs can stream; `devllm` waits for the final result.
- **Not concurrency-friendly.** Each call is a full CLI process. Don't fan out dozens.
- **No real system role on Codex.** `codex exec` has no system-prompt flag, so `system=` is prepended to the prompt.
- **Flags drift.** Verified against Claude Code 2.1.175 and codex-cli 0.152.1. Run `devllm doctor` after upgrading either CLI.

## Development

```bash
git clone https://github.com/<your-username>/devllm
cd devllm
pip install -e .
python -m unittest discover -s tests -v    # 18 tests, no network, no tokens
```

## License

MIT

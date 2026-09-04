"""Claude Code CLI backend."""

from __future__ import annotations

import json

from .base import (
    DEFAULT_SYSTEM,
    LLM,
    BackendInvocationError,
    BackendNotFoundError,
    LLMResponse,
    OutputParseError,
    Usage,
    resolve_executable,
    run_process,
)

# Disallowed, not removed: the schemas are still sent and still cost roughly 4k
# cached input tokens per call. This stops the agent wandering off and touching
# your filesystem when you only wanted a completion.
_AGENT_TOOLS = [
    "Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch",
    "Task", "TodoWrite", "NotebookEdit", "BashOutput", "KillShell",
]


class ClaudeCLI(LLM):
    """Uses `claude -p` with your Claude Pro/Max subscription login.

    Reads credentials from `~/.claude/.credentials.json`, so no API key is
    involved and no token handling is needed on your side.

        >>> llm = ClaudeCLI(model="sonnet")
        >>> llm.generate("Name three phone brands sold in India.").text

    Latency note: roughly 10s of every call is Node process startup, not model
    time. Choosing a smaller model barely moves it, so pick a model for output
    quality rather than speed.
    """

    name = "claude"
    executable = "claude"

    def __init__(
        self,
        model: str = "sonnet",
        *,
        system: str | None = DEFAULT_SYSTEM,
        timeout: int = 300,
        tools: bool = False,
        extra_args: list[str] | None = None,
    ):
        """
        Args:
            model: An alias (`sonnet`, `opus`, `haiku`) or a full model name.
            system: Default system prompt. Defaults to a neutral general
                assistant, because Claude Code's own prompt makes it refuse
                non-coding questions. Pass None to restore that prompt (and
                its ~12.5k tokens of per-call overhead).
            timeout: Seconds before giving up on the subprocess.
            tools: Leave False to use Claude purely as a text generator. Set
                True only if you want it to run its own agent loop, which is
                usually not what you want inside your own agent.
            extra_args: Raw flags appended to the command, for anything this
                wrapper does not expose.
        """
        self.model = model
        self.system = system
        self.timeout = timeout
        self.tools = tools
        self.extra_args = extra_args or []

    def generate(
        self,
        prompt: str,
        *,
        schema: dict | None = None,
        system: str | None = None,
    ) -> LLMResponse:
        exe = resolve_executable(self.executable)
        if exe is None:
            raise BackendNotFoundError(
                "`claude` is not on PATH. Install it with "
                "`npm install -g @anthropic-ai/claude-code`, then run `claude` "
                "once to log in."
            )

        argv = [
            exe, "-p",
            "--output-format", "json",
            "--model", self.model,
            "--disable-slash-commands",
        ]
        if not self.tools:
            argv += ["--disallowedTools", *_AGENT_TOOLS]
        active_system = system if system is not None else self.system
        if active_system:
            argv += ["--system-prompt", active_system]
        if schema is not None:
            argv += ["--json-schema", json.dumps(schema)]
        argv += self.extra_args

        result, elapsed = run_process(argv, prompt, self.timeout, self.name)

        if result.returncode != 0:
            raise BackendInvocationError(
                f"claude exited {result.returncode}: "
                f"{(result.stderr or result.stdout)[-800:].strip()}"
            )

        try:
            envelope = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise OutputParseError(
                f"claude did not return JSON. First 500 chars: "
                f"{result.stdout[:500]!r}"
            ) from exc

        if envelope.get("is_error"):
            raise BackendInvocationError(
                f"claude reported an error: {str(envelope.get('result'))[:800]}"
            )

        raw_usage = envelope.get("usage") or {}
        usage = Usage(
            input_tokens=raw_usage.get("input_tokens", 0),
            output_tokens=raw_usage.get("output_tokens", 0),
            cache_read_tokens=raw_usage.get("cache_read_input_tokens", 0),
            cache_write_tokens=raw_usage.get("cache_creation_input_tokens", 0),
        )

        # With --json-schema the parsed object lands in `structured_output`;
        # `result` stays prose. json.loads(result) will fail -- a very easy
        # mistake to make.
        return LLMResponse(
            text=envelope.get("result", ""),
            structured=envelope.get("structured_output") if schema else None,
            backend=self.name,
            model=self.model,
            duration_s=elapsed,
            usage=usage,
            argv=argv,
            raw=envelope,
        )

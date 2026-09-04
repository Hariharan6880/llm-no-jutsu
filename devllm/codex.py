"""Codex CLI backend."""

from __future__ import annotations

import json
import os
import shutil
import tempfile

from .base import (
    DEFAULT_SYSTEM,
    LLM,
    UNSET,
    BackendInvocationError,
    BackendNotFoundError,
    LLMResponse,
    OutputParseError,
    _Unset,
    resolve_executable,
    run_process,
)


class CodexCLI(LLM):
    """Uses `codex exec` with your ChatGPT Plus/Pro subscription login.

    Reads credentials from `~/.codex/auth.json`, so no API key is involved.

        >>> llm = CodexCLI()
        >>> llm.generate("Name three phone brands sold in India.").text

    Latency note: Codex defaults to `xhigh` reasoning effort, which measured
    ~31s on a trivial prompt versus ~11s at `low`. This class defaults to
    `low`, which is the single biggest speed win available while prototyping.
    """

    name = "codex"
    executable = "codex"

    def __init__(
        self,
        model: str | None = None,
        *,
        system: str | None | _Unset = UNSET,
        timeout: int = 300,
        reasoning_effort: str | None = "low",
        sandbox: str = "read-only",
        extra_args: list[str] | None = None,
    ):
        """
        Args:
            model: Model name, or None for the Codex default.
            system: Prepended to the prompt. Codex `exec` has no dedicated
                system-prompt flag, so this is concatenation, not a real
                system role. Defaults to a neutral general assistant; pass
                None to send the prompt alone.
            timeout: Seconds before giving up on the subprocess.
            reasoning_effort: One of none, low, medium, high, xhigh, max.
                Pass None to leave the CLI's own default in place.
            sandbox: Filesystem policy for any command the model tries to run.
                Leave as read-only unless you have a reason not to.
            extra_args: Raw flags appended to the command.
        """
        self.model = model
        self.system = DEFAULT_SYSTEM if system is UNSET else system
        self.timeout = timeout
        self.reasoning_effort = reasoning_effort
        self.sandbox = sandbox
        self.extra_args = extra_args or []

    def generate(
        self,
        prompt: str,
        *,
        schema: dict | None = None,
        system: str | None | _Unset = UNSET,
    ) -> LLMResponse:
        exe = resolve_executable(self.executable)
        if exe is None:
            raise BackendNotFoundError(
                "`codex` is not on PATH. Install it with "
                "`npm install -g @openai/codex`, then run `codex login`."
            )

        # Both the -o answer file and the schema file live in here. This runs
        # inside a long-lived server, so it is removed on every path -- one
        # leaked directory per request would otherwise accumulate forever.
        workdir = tempfile.mkdtemp(prefix="devllm_codex_")
        try:
            return self._run(prompt, schema, system, exe, workdir)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _run(
        self,
        prompt: str,
        schema: dict | None,
        system: str | None | _Unset,
        exe: str,
        workdir: str,
    ) -> LLMResponse:
        """The body of `generate`, split out so `workdir` has one owner."""
        last_message = os.path.join(workdir, "last.txt")

        # Codex writes a human transcript to stdout (banner, token counts, log
        # lines), so the answer is read from -o rather than scraped.
        argv = [
            exe, "exec",
            "--skip-git-repo-check",
            "--color", "never",
            "-s", self.sandbox,
            "-o", last_message,
        ]
        if self.model:
            argv += ["-m", self.model]
        if self.reasoning_effort:
            argv += ["-c", f"model_reasoning_effort={self.reasoning_effort}"]
        if schema is not None:
            schema_path = os.path.join(workdir, "schema.json")
            with open(schema_path, "w", encoding="utf-8") as handle:
                json.dump(schema, handle)
            argv += ["--output-schema", schema_path]
        argv += self.extra_args
        argv.append("-")  # read the prompt from stdin

        active_system = self.system if system is UNSET else system
        full_prompt = f"{active_system}\n\n{prompt}" if active_system else prompt

        result, elapsed = run_process(argv, full_prompt, self.timeout, self.name)

        if result.returncode != 0 or not os.path.exists(last_message):
            raise BackendInvocationError(
                f"codex exited {result.returncode}: "
                f"{(result.stderr or result.stdout)[-800:].strip()}"
            )

        # Read into memory before returning: the caller deletes the directory
        # this file lives in.
        with open(last_message, encoding="utf-8") as handle:
            text = handle.read().strip()

        structured = None
        if schema is not None:
            try:
                structured = json.loads(text)
            except json.JSONDecodeError as exc:
                raise OutputParseError(
                    f"a schema was requested but codex returned non-JSON: "
                    f"{text[:500]!r}"
                ) from exc

        return LLMResponse(
            text=text,
            structured=structured,
            backend=self.name,
            model=self.model or "default",
            duration_s=elapsed,
            argv=argv,
        )

"""Core types shared by every backend."""

from __future__ import annotations

import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


DEFAULT_SYSTEM = (
    "You are a helpful, knowledgeable general-purpose assistant. Answer the "
    "user's question directly and concisely."
)
"""Both CLIs ship as *coding* agents. Left with their stock system prompt,
Claude Code will refuse an ordinary question with "I'm a software engineering
assistant" -- which makes it useless as a general LLM backend. Replacing the
system prompt fixes that, and cuts per-call overhead from ~12.5k cached input
tokens to ~4k as a bonus. Pass `system=None` to restore the CLI's own prompt."""


class LLMError(RuntimeError):
    """Base class for every error devllm raises."""


class BackendNotFoundError(LLMError):
    """The CLI executable is not installed or not on PATH."""


class BackendTimeoutError(LLMError):
    """The CLI did not finish within the configured timeout."""


class BackendInvocationError(LLMError):
    """The CLI ran but exited non-zero, or reported an error."""


class OutputParseError(LLMError):
    """The CLI returned output that could not be parsed as expected."""


@dataclass(frozen=True)
class Usage:
    """Token accounting, when the backend reports it.

    Worth watching: every call is a fresh process, so the system prompt and
    tool schemas are re-sent each time. That overhead, not the request count,
    is what exhausts a subscription's limits.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_input(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


@dataclass
class LLMResponse:
    """A successful generation. Failures raise `LLMError` instead."""

    text: str
    """The model's prose answer."""

    structured: Any | None = None
    """Parsed object, when `schema=` was passed to `generate()`."""

    backend: str = ""
    model: str = ""
    duration_s: float = 0.0
    usage: Usage | None = None

    argv: list[str] = field(default_factory=list)
    """The exact command line that was executed. Useful for debugging and for
    reproducing a call in a terminal."""

    raw: dict = field(default_factory=dict)
    """The backend's full response envelope, if it emitted one."""

    def __str__(self) -> str:
        return self.text


class LLM(ABC):
    """The interface your application should depend on.

    Keep your code talking to this, and swapping a CLI backend for a real API
    client in production is a one-line change at the construction site.
    """

    name: str = "llm"

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        schema: dict | None = None,
        system: str | None = None,
    ) -> LLMResponse:
        """Send `prompt`, block until the model finishes, return the response.

        Args:
            prompt: The user message. Sent over stdin, so it can be any length.
            schema: A JSON Schema. When given, the parsed object is returned on
                `LLMResponse.structured`.
            system: System prompt for this call, overriding the instance default.

        Raises:
            LLMError: on any failure. See the subclasses for specific cases.
        """

    def available(self) -> bool:
        """True if this backend's CLI is installed and on PATH."""
        return resolve_executable(self.executable) is not None

    @property
    def executable(self) -> str:
        raise NotImplementedError


def resolve_executable(name: str) -> str | None:
    """Find a CLI on PATH, returning its full path.

    Required on Windows: `claude` and `codex` install as `claude.CMD` /
    `codex.CMD` npm shims, and passing the bare name to `subprocess.run` raises
    `FileNotFoundError` because Python does not apply PATHEXT. `shutil.which`
    does.
    """
    return shutil.which(name)


def run_process(
    argv: list[str],
    prompt: str,
    timeout: int,
    backend: str,
) -> tuple[subprocess.CompletedProcess, float]:
    """Run a CLI with the prompt on stdin. Returns (result, elapsed_seconds).

    The prompt goes over stdin rather than argv on purpose: Windows caps a
    command line near 32k characters, and quoting a multi-line prompt through
    cmd.exe is a reliable source of pain. Passing `input=` also closes stdin,
    which stops Codex from blocking forever waiting for more input.
    """
    started = time.monotonic()
    try:
        result = subprocess.run(
            argv,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise BackendTimeoutError(
            f"{backend} did not respond within {timeout}s. Cold CLI processes "
            f"take 10-40s; raise `timeout=` if your prompts are long."
        ) from exc
    return result, time.monotonic() - started

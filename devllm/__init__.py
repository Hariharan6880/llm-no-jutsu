"""devllm - use your installed Claude Code / Codex CLI as an LLM backend.

For local development and testing only. Point your code at the `LLM` interface,
and swap in a real API client for production without touching anything else.

    from devllm import ClaudeCLI

    llm = ClaudeCLI(model="sonnet")
    print(llm.generate("Name three phone brands sold in India.").text)
"""

from __future__ import annotations

import os

from .base import (
    BACKEND_NAMES,
    DEFAULT_SYSTEM,
    LLM,
    UNSET,
    BackendInvocationError,
    BackendNotFoundError,
    BackendTimeoutError,
    LLMError,
    LLMResponse,
    OutputParseError,
    Usage,
)
from .claude import ClaudeCLI
from .codex import CodexCLI

__version__ = "0.1.0"

__all__ = [
    "LLM",
    "DEFAULT_SYSTEM",
    "UNSET",
    "BACKEND_NAMES",
    "LLMResponse",
    "Usage",
    "ClaudeCLI",
    "CodexCLI",
    "get_backend",
    "available_backends",
    "LLMError",
    "BackendNotFoundError",
    "BackendTimeoutError",
    "BackendInvocationError",
    "OutputParseError",
]

_BACKENDS: dict[str, type[LLM]] = {
    "claude": ClaudeCLI,
    "codex": CodexCLI,
}


def get_backend(name: str | None = None, **kwargs) -> LLM:
    """Build a backend by name, so the choice can live in config.

    Falls back to the `DEVLLM_BACKEND` environment variable, then to `claude`
    (which is the faster of the two in practice).

        llm = get_backend(os.getenv("LLM_BACKEND"), model="sonnet")
    """
    name = (name or os.getenv("DEVLLM_BACKEND") or "claude").lower()
    try:
        return _BACKENDS[name](**kwargs)
    except KeyError:
        raise ValueError(
            f"unknown backend {name!r}; expected one of {sorted(_BACKENDS)}"
        ) from None


def available_backends() -> list[str]:
    """Names of the backends whose CLI is actually installed on this machine."""
    return [name for name, cls in _BACKENDS.items() if cls().available()]

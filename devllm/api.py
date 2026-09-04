"""HTTP layer. Thin: routing, validation and I/O only.

All request logic lives in `handle_*` functions that take plain dicts and
return `(status, body)`, so the whole surface is testable without sockets.
"""

from __future__ import annotations

import dataclasses
import threading
from contextlib import contextmanager
from dataclasses import dataclass

from .base import (
    BACKEND_NAMES,
    UNSET,
    BackendInvocationError,
    BackendNotFoundError,
    BackendTimeoutError,
    LLMError,
    OutputParseError,
)
from .claude import ClaudeCLI
from .codex import CodexCLI


@dataclass
class ServerConfig:
    """Server-wide defaults. Every request may override most of these."""

    host: str = "127.0.0.1"
    port: int = 8765
    backend: str | None = None      # None -> resolve at startup
    model: str | None = None
    concurrency: int = 2
    timeout: int = 300
    max_queue: int = 8
    token: str | None = None


class QueueFull(Exception):
    """Too many requests already waiting for a slot."""


class RequestGate:
    """Caps concurrent CLI processes.

    Every generation spawns a full Node process, so unbounded threading melts
    a laptop. Requests past `concurrency` wait; past `max_queue` waiting they
    are rejected, because a synchronous caller blocked for minutes behind a
    queue is worse served than one told to retry.
    """

    def __init__(self, concurrency: int = 2, max_queue: int = 8):
        self.concurrency = concurrency
        self.max_queue = max_queue
        self.active = 0
        self.waiting = 0
        self._semaphore = threading.BoundedSemaphore(concurrency)
        self._lock = threading.Lock()

    @contextmanager
    def slot(self):
        with self._lock:
            if self.waiting >= self.max_queue:
                raise QueueFull(
                    f"{self.waiting} requests already queued "
                    f"(max {self.max_queue}); retry shortly"
                )
            self.waiting += 1
        try:
            self._semaphore.acquire()
        finally:
            with self._lock:
                self.waiting -= 1
        with self._lock:
            self.active += 1
        try:
            yield
        finally:
            with self._lock:
                self.active -= 1
            self._semaphore.release()


_INSTALL_HINT = (
    "no backend installed. Install one: "
    "`npm install -g @anthropic-ai/claude-code` then run `claude`, or "
    "`npm install -g @openai/codex` then `codex login`"
)

# Status codes are load-bearing: callers use raise_for_status(), so a failure
# must never come back as 200.
_STATUS_FOR_ERROR = {
    BackendNotFoundError: 503,
    BackendTimeoutError: 504,
    BackendInvocationError: 502,
    OutputParseError: 502,
}


def resolve_default_backend() -> str | None:
    """First installed backend, preferring claude (it is the faster of the two)."""
    for name, cls in (("claude", ClaudeCLI), ("codex", CodexCLI)):
        if cls().available():
            return name
    return None


def _error(status: int, message: str, error_type: str) -> tuple[int, dict]:
    return status, {"ok": False, "error": message, "error_type": error_type}


def _build_backend(payload: dict, config: ServerConfig):
    """Construct the backend for one request. Raises ValueError on bad input."""
    name = payload.get("backend") or config.backend
    if name is None:
        raise LookupError(_INSTALL_HINT)
    if name not in BACKEND_NAMES:
        raise ValueError(
            f"unknown backend {name!r}; expected one of {list(BACKEND_NAMES)}"
        )

    # "system" omitted -> UNSET (server default). Explicit null -> None
    # (suppress the system prompt). A string is used as-is.
    system = payload["system"] if "system" in payload else UNSET
    if system is not UNSET and system is not None and not isinstance(system, str):
        raise ValueError("`system` must be a string or null")

    timeout = payload.get("timeout", config.timeout)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
        raise ValueError("`timeout` must be a positive integer")
    timeout = min(timeout, config.timeout)

    model = payload.get("model") or config.model

    if name == "codex":
        return CodexCLI(
            model=model,
            system=system,
            timeout=timeout,
            reasoning_effort=payload.get("reasoning") or "low",
        )
    # `reasoning` is accepted and ignored for claude rather than rejected:
    # a harmless extra field should not fail a request.
    return ClaudeCLI(
        model=model or "sonnet",
        system=system,
        timeout=timeout,
    )


def handle_generate(payload: dict, config: ServerConfig,
                    gate: RequestGate) -> tuple[int, dict]:
    """POST /generate. Returns (status, body)."""
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return _error(400, "`prompt` is required and must be a non-empty string",
                      "BadRequest")

    schema = payload.get("schema")
    if schema is not None and not isinstance(schema, dict):
        return _error(400, "`schema` must be a JSON object", "BadRequest")

    try:
        llm = _build_backend(payload, config)
    except LookupError as exc:
        return _error(503, str(exc), "BackendNotFoundError")
    except ValueError as exc:
        return _error(400, str(exc), "BadRequest")

    try:
        with gate.slot():
            response = llm.generate(prompt, schema=schema)
    except QueueFull as exc:
        return _error(429, str(exc), "QueueFull")
    except LLMError as exc:
        status = _STATUS_FOR_ERROR.get(type(exc), 502)
        return _error(status, str(exc), type(exc).__name__)

    return 200, {
        "ok": True,
        "text": response.text,
        "structured": response.structured,
        "backend": response.backend,
        "model": response.model,
        "duration_s": round(response.duration_s, 2),
        "usage": dataclasses.asdict(response.usage) if response.usage else None,
    }

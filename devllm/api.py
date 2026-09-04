"""HTTP layer. Thin: routing, validation and I/O only.

All request logic lives in `handle_*` functions that take plain dicts and
return `(status, body)`, so the whole surface is testable without sockets.
"""

from __future__ import annotations

import dataclasses
import hmac
import json
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
from .doctor import check_backends
from .playground import PAGE


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

# How much of a rejected request's body we are willing to read and throw away
# so the response reaches the client. Past this, a reset is the better trade.
_MAX_DRAIN = 1 << 20

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
    """Construct the backend for one request.

    Raises:
        ValueError: the caller sent something invalid -> 400.
        LookupError: no backend is installed or configured -> 503.

    The two are distinct because the split decides the status code: a bad
    field is the caller's fault, a missing CLI is the server's.
    """
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


def handle_health(config: ServerConfig, gate: RequestGate) -> tuple[int, dict]:
    """GET /health. Always 200 — an unconfigured server is still a running one.

    Lets a first-time user tell "server is down" from "server is up but no CLI
    is logged in", which is the more common problem. Performs no model call.
    """
    backends = check_backends()
    installed = any(state["installed"] for state in backends.values())
    return 200, {
        "status": "ok" if installed else "unconfigured",
        "backends": backends,
        "default_backend": config.backend,
        "queue": {
            "active": gate.active,
            "waiting": gate.waiting,
            "concurrency": gate.concurrency,
        },
    }


def check_auth(header_value: str | None, token: str | None) -> bool:
    """True when the request may proceed. No token configured means no auth."""
    if not token:
        return True
    if not header_value or not header_value.startswith("Bearer "):
        return False
    # compare_digest, not ==: a short-circuiting comparison leaks the token
    # prefix through response timing. Encoded because compare_digest rejects
    # str arguments that are not pure ASCII.
    return hmac.compare_digest(
        header_value[len("Bearer "):].encode("utf-8"),
        token.encode("utf-8"),
    )


def make_handler(config: ServerConfig, gate: RequestGate):
    """Build a handler class bound to this config and gate."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "devllm"

        def log_message(self, *args):  # replaced by our own one-line log
            pass

        def _send(self, status: int, ctype: str, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _json(self, status: int, body: dict) -> None:
            self._send(status, "application/json",
                       json.dumps(body).encode("utf-8"))

        def _drain(self) -> None:
            """Discard a request body we are not going to parse.

            Closing the connection with bytes still unread makes the OS send
            an RST (WinError 10054 on Windows), so the client sees a reset
            instead of the status we just wrote. Every early rejection --
            401, 404, 415 -- has to drain first or it is invisible.
            """
            try:
                remaining = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                return
            remaining = min(remaining, _MAX_DRAIN)
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    return
                remaining -= len(chunk)

        def _log(self, status: int, started: float, backend: str = "-") -> None:
            print(f"{time.strftime('%H:%M:%S')}  {self.command} {self.path}  "
                  f"{backend}  {time.monotonic() - started:.1f}s  {status}")

        def _authorised(self) -> bool:
            if check_auth(self.headers.get("Authorization"), config.token):
                return True
            self._drain()
            self._json(*_error(401, "missing or invalid token", "Unauthorized"))
            return False

        def do_GET(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
            started = time.monotonic()
            path = self.path.split("?")[0]
            if path == "/":
                self._send(200, "text/html; charset=utf-8",
                           PAGE.encode("utf-8"))
                return self._log(200, started)
            if path == "/health":
                if not self._authorised():
                    return self._log(401, started)
                status, body = handle_health(config, gate)
                self._json(status, body)
                return self._log(status, started)
            self._json(*_error(404, f"no route {path}", "NotFound"))
            self._log(404, started)

        def do_POST(self):  # noqa: N802
            started = time.monotonic()
            if self.path.split("?")[0] != "/generate":
                self._drain()
                self._json(*_error(404, "no route", "NotFound"))
                return self._log(404, started)
            if not self._authorised():
                return self._log(401, started)

            # Insisting on application/json is a security control, not
            # pedantry. A cross-origin fetch carrying Content-Type: text/plain
            # is a CORS *simple* request: the browser sends it with no
            # preflight, so any page open while this server runs could spawn
            # the CLI and spend the user's subscription. The attacker never
            # reads the reply, and does not need to. Requiring
            # application/json forces a preflight, which this server does not
            # answer. Binding to localhost is no defence -- the request comes
            # from the user's own browser.
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype != "application/json":
                self._drain()
                self._json(*_error(415,
                                   "Content-Type must be application/json",
                                   "UnsupportedMediaType"))
                return self._log(415, started)

            try:
                length = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                self._json(*_error(400, "invalid Content-Length header",
                                   "BadRequest"))
                return self._log(400, started)

            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError as exc:
                self._json(*_error(400, f"invalid JSON: {exc}", "BadRequest"))
                return self._log(400, started)
            if not isinstance(payload, dict):
                self._json(*_error(400, "body must be a JSON object",
                                   "BadRequest"))
                return self._log(400, started)

            status, body = handle_generate(payload, config, gate)
            self._json(status, body)
            self._log(status, started, body.get("backend", "-"))

    Handler.config = config
    Handler.gate = gate
    return Handler


def serve(config: ServerConfig) -> None:
    """Start the server and block until interrupted."""
    gate = RequestGate(config.concurrency, config.max_queue)
    try:
        server = ThreadingHTTPServer((config.host, config.port),
                                     make_handler(config, gate))
    except OSError as exc:
        # Running `python server.py` twice is the common way to hit this, and
        # a WinError traceback tells a first-time user nothing.
        raise SystemExit(
            f"cannot bind {config.host}:{config.port}: {exc.strerror or exc}. "
            f"If another server is already using that port, try "
            f"--port {config.port + 1}."
        ) from exc
    print(f"devllm listening on http://{config.host}:{config.port}")
    print("  POST /generate   GET /health   GET / (browser test page)")
    if config.backend is None:
        print(f"  WARNING: {_INSTALL_HINT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()

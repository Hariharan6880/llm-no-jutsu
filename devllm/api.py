"""HTTP layer. Thin: routing, validation and I/O only.

All request logic lives in `handle_*` functions that take plain dicts and
return `(status, body)`, so the whole surface is testable without sockets.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass


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

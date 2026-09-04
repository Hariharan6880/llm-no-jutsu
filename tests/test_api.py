"""Offline tests for the HTTP layer. No sockets, no CLI, no network."""

import threading
import unittest

from devllm.api import QueueFull, RequestGate, ServerConfig


class TestServerConfig(unittest.TestCase):
    def test_safe_defaults(self):
        config = ServerConfig()
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 8765)
        self.assertEqual(config.concurrency, 2)
        self.assertEqual(config.timeout, 300)
        self.assertEqual(config.max_queue, 8)
        self.assertIsNone(config.token)


class TestRequestGate(unittest.TestCase):
    def test_counts_active_slots(self):
        gate = RequestGate(concurrency=2, max_queue=8)
        self.assertEqual(gate.active, 0)
        with gate.slot():
            self.assertEqual(gate.active, 1)
        self.assertEqual(gate.active, 0)

    def test_rejects_when_queue_is_full(self):
        gate = RequestGate(concurrency=1, max_queue=1)
        started = threading.Event()
        release = threading.Event()

        def hold():
            with gate.slot():
                started.set()
                release.wait(5)

        holder = threading.Thread(target=hold, daemon=True)
        holder.start()
        started.wait(5)

        # One waiter fills the queue; the next must be rejected.
        waiter = threading.Thread(target=lambda: self._try_slot(gate), daemon=True)
        waiter.start()
        self._wait_until(lambda: gate.waiting == 1)

        with self.assertRaises(QueueFull):
            with gate.slot():
                pass

        release.set()
        holder.join(5)
        waiter.join(5)

    @staticmethod
    def _try_slot(gate):
        try:
            with gate.slot():
                pass
        except QueueFull:
            pass

    @staticmethod
    def _wait_until(predicate, timeout=5.0):
        deadline = __import__("time").monotonic() + timeout
        while __import__("time").monotonic() < deadline:
            if predicate():
                return
        raise AssertionError("condition not reached in time")


if __name__ == "__main__":
    unittest.main()

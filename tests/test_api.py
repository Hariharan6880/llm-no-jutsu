"""Offline tests for the HTTP layer. No sockets, no CLI, no network."""

import threading
import time
import unittest
from contextlib import contextmanager
from unittest import mock

from devllm.api import QueueFull, RequestGate, ServerConfig, handle_generate, handle_health
from devllm.base import (
    UNSET,
    BackendInvocationError,
    BackendNotFoundError,
    BackendTimeoutError,
    LLMResponse,
    OutputParseError,
    Usage,
)


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

    def test_released_permit_is_recycled(self):
        # The gate's central promise: a permit freed by one holder must
        # become available to the next waiter, not stay leaked.
        gate = RequestGate(concurrency=1, max_queue=8)
        started = threading.Event()
        release = threading.Event()
        admitted = threading.Event()

        def hold():
            with gate.slot():
                started.set()
                release.wait(5)

        def wait_for_slot():
            with gate.slot():
                admitted.set()

        holder = threading.Thread(target=hold, daemon=True)
        holder.start()
        self.assertTrue(started.wait(5), "holder never entered its slot")

        waiter = threading.Thread(target=wait_for_slot, daemon=True)
        waiter.start()

        release.set()
        holder.join(5)
        self.assertFalse(holder.is_alive(), "holder thread never finished")

        self.assertTrue(
            admitted.wait(5),
            "waiter was never admitted; released permit was not recycled",
        )
        waiter.join(5)
        self.assertFalse(waiter.is_alive(), "waiter thread never finished")

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
        self.assertTrue(started.wait(5), "holder never entered its slot")

        # One waiter fills the queue; the next must be rejected.
        waiter = threading.Thread(target=lambda: self._try_slot(gate), daemon=True)
        waiter.start()
        self._wait_until(lambda: gate.waiting == 1)

        with self.assertRaises(QueueFull):
            with gate.slot():
                pass

        release.set()
        holder.join(5)
        self.assertFalse(holder.is_alive(), "holder thread never finished")
        waiter.join(5)
        self.assertFalse(waiter.is_alive(), "waiter thread never finished")

    @staticmethod
    def _try_slot(gate):
        try:
            with gate.slot():
                pass
        except QueueFull:
            pass

    @staticmethod
    def _wait_until(predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError("condition not reached in time")


def _response(**kwargs):
    defaults = dict(text="an answer", backend="claude", model="sonnet",
                    duration_s=1.5)
    defaults.update(kwargs)
    return LLMResponse(**defaults)


class TestHandleGenerateSuccess(unittest.TestCase):
    def setUp(self):
        self.config = ServerConfig(backend="claude", model="sonnet")
        self.gate = RequestGate()

    @mock.patch("devllm.api.ClaudeCLI")
    def test_returns_200_and_the_text(self, cls):
        cls.return_value.generate.return_value = _response(
            usage=Usage(input_tokens=4, output_tokens=231))
        status, body = handle_generate({"prompt": "hi"}, self.config, self.gate)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["text"], "an answer")
        self.assertEqual(body["backend"], "claude")
        self.assertEqual(body["usage"]["output_tokens"], 231)
        self.assertIsNone(body["structured"])

    @mock.patch("devllm.api.ClaudeCLI")
    def test_usage_is_null_when_backend_reports_none(self, cls):
        cls.return_value.generate.return_value = _response(usage=None)
        _, body = handle_generate({"prompt": "hi"}, self.config, self.gate)
        self.assertIsNone(body["usage"])

    @mock.patch("devllm.api.ClaudeCLI")
    def test_schema_is_passed_through_and_returned(self, cls):
        schema = {"type": "object"}
        cls.return_value.generate.return_value = _response(
            structured={"picks": []})
        _, body = handle_generate({"prompt": "hi", "schema": schema},
                                  self.config, self.gate)
        self.assertEqual(cls.return_value.generate.call_args.kwargs["schema"],
                         schema)
        self.assertEqual(body["structured"], {"picks": []})

    @mock.patch("devllm.api.ClaudeCLI")
    def test_omitted_system_is_unset_not_none(self, cls):
        cls.return_value.generate.return_value = _response()
        handle_generate({"prompt": "hi"}, self.config, self.gate)
        self.assertIs(cls.call_args.kwargs["system"], UNSET)

    @mock.patch("devllm.api.ClaudeCLI")
    def test_explicit_null_system_suppresses_the_prompt(self, cls):
        cls.return_value.generate.return_value = _response()
        handle_generate({"prompt": "hi", "system": None},
                        self.config, self.gate)
        self.assertIsNone(cls.call_args.kwargs["system"])

    @mock.patch("devllm.api.CodexCLI")
    def test_reasoning_reaches_codex(self, cls):
        cls.return_value.generate.return_value = _response(backend="codex")
        handle_generate({"prompt": "hi", "backend": "codex",
                         "reasoning": "high"}, self.config, self.gate)
        self.assertEqual(cls.call_args.kwargs["reasoning_effort"], "high")

    @mock.patch("devllm.api.ClaudeCLI")
    def test_reasoning_is_ignored_for_claude(self, cls):
        cls.return_value.generate.return_value = _response()
        status, _ = handle_generate({"prompt": "hi", "reasoning": "high"},
                                    self.config, self.gate)
        self.assertEqual(status, 200)
        self.assertNotIn("reasoning_effort", cls.call_args.kwargs)

    @mock.patch("devllm.api.ClaudeCLI")
    def test_timeout_is_clamped_to_the_server_ceiling(self, cls):
        cls.return_value.generate.return_value = _response()
        handle_generate({"prompt": "hi", "timeout": 99999},
                        self.config, self.gate)
        self.assertEqual(cls.call_args.kwargs["timeout"], self.config.timeout)


class TestHandleGenerateValidation(unittest.TestCase):
    def setUp(self):
        self.config = ServerConfig(backend="claude")
        self.gate = RequestGate()

    def test_missing_prompt_is_400(self):
        status, body = handle_generate({}, self.config, self.gate)
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertIn("prompt", body["error"])

    def test_blank_prompt_is_400(self):
        status, _ = handle_generate({"prompt": "   "}, self.config, self.gate)
        self.assertEqual(status, 400)

    def test_non_string_prompt_is_400(self):
        status, _ = handle_generate({"prompt": 42}, self.config, self.gate)
        self.assertEqual(status, 400)

    def test_unknown_backend_is_400(self):
        status, body = handle_generate({"prompt": "hi", "backend": "gpt9"},
                                       self.config, self.gate)
        self.assertEqual(status, 400)
        self.assertIn("gpt9", body["error"])

    def test_non_object_schema_is_400(self):
        status, _ = handle_generate({"prompt": "hi", "schema": "nope"},
                                    self.config, self.gate)
        self.assertEqual(status, 400)

    def test_no_backend_configured_is_503(self):
        status, body = handle_generate({"prompt": "hi"},
                                       ServerConfig(backend=None), self.gate)
        self.assertEqual(status, 503)
        self.assertIn("npm install", body["error"])


class TestHandleGenerateErrors(unittest.TestCase):
    def setUp(self):
        self.config = ServerConfig(backend="claude")
        self.gate = RequestGate()

    def _raises(self, exc):
        patcher = mock.patch("devllm.api.ClaudeCLI")
        cls = patcher.start()
        self.addCleanup(patcher.stop)
        cls.return_value.generate.side_effect = exc
        return handle_generate({"prompt": "hi"}, self.config, self.gate)

    def test_missing_cli_is_503(self):
        status, body = self._raises(BackendNotFoundError("claude not on PATH"))
        self.assertEqual(status, 503)
        self.assertEqual(body["error_type"], "BackendNotFoundError")

    def test_timeout_is_504(self):
        status, body = self._raises(BackendTimeoutError("timed out"))
        self.assertEqual(status, 504)
        self.assertEqual(body["error_type"], "BackendTimeoutError")

    def test_cli_failure_is_502(self):
        status, body = self._raises(BackendInvocationError("exit 1"))
        self.assertEqual(status, 502)
        self.assertEqual(body["error_type"], "BackendInvocationError")

    def test_unparseable_output_is_502(self):
        status, body = self._raises(OutputParseError("not json"))
        self.assertEqual(status, 502)

    def test_error_bodies_are_never_ok(self):
        _, body = self._raises(BackendTimeoutError("timed out"))
        self.assertFalse(body["ok"])
        self.assertIn("error", body)


class TestQueueFullIs429(unittest.TestCase):
    @mock.patch("devllm.api.ClaudeCLI")
    def test_returns_429_when_the_gate_rejects(self, cls):
        cls.return_value.generate.return_value = _response()

        class FullGate(RequestGate):
            @contextmanager
            def slot(self):
                raise QueueFull("8 requests already queued")
                yield  # pragma: no cover

        status, body = handle_generate({"prompt": "hi"},
                                       ServerConfig(backend="claude"),
                                       FullGate())
        self.assertEqual(status, 429)
        self.assertEqual(body["error_type"], "QueueFull")


class TestHandleHealth(unittest.TestCase):
    _INSTALLED = {
        "claude": {"installed": True, "path": "/x/claude",
                   "login": "subscription: pro"},
        "codex": {"installed": False, "path": None, "login": "not installed"},
    }
    _NONE = {
        "claude": {"installed": False, "path": None, "login": "not installed"},
        "codex": {"installed": False, "path": None, "login": "not installed"},
    }

    @mock.patch("devllm.api.check_backends")
    def test_ok_when_a_backend_is_installed(self, check):
        check.return_value = self._INSTALLED
        status, body = handle_health(ServerConfig(backend="claude"),
                                     RequestGate())
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["default_backend"], "claude")
        self.assertTrue(body["backends"]["claude"]["installed"])

    @mock.patch("devllm.api.check_backends")
    def test_unconfigured_when_nothing_is_installed(self, check):
        check.return_value = self._NONE
        status, body = handle_health(ServerConfig(backend=None), RequestGate())
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "unconfigured")
        self.assertIsNone(body["default_backend"])

    @mock.patch("devllm.api.check_backends")
    def test_reports_queue_state(self, check):
        check.return_value = self._INSTALLED
        _, body = handle_health(ServerConfig(backend="claude"),
                                RequestGate(concurrency=3, max_queue=8))
        self.assertEqual(body["queue"],
                         {"active": 0, "waiting": 0, "concurrency": 3})

    @mock.patch("devllm.api.check_backends")
    def test_makes_no_live_call(self, check):
        check.return_value = self._INSTALLED
        with mock.patch("devllm.api.ClaudeCLI") as cls:
            handle_health(ServerConfig(backend="claude"), RequestGate())
            cls.return_value.generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()

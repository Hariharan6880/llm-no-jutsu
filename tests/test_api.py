"""Offline tests for the HTTP layer. No CLI and no network.

Most of these call the `handle_*` functions directly. `TestHandlerOverHTTP`
does use a real loopback socket, because routing, auth and Content-Type
enforcement live in the handler and a pure-function test never executes them.
"""

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from unittest import mock

from devllm.api import (
    QueueFull,
    RequestGate,
    ServerConfig,
    check_auth,
    handle_generate,
    handle_health,
    make_handler,
    resolve_default_backend,
)
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

    def test_string_timeout_is_400(self):
        # JSON has no integer type discipline, so "300" arrives routinely.
        status, body = handle_generate({"prompt": "hi", "timeout": "300"},
                                       self.config, self.gate)
        self.assertEqual(status, 400)
        self.assertIn("timeout", body["error"])

    def test_negative_timeout_is_400(self):
        status, body = handle_generate({"prompt": "hi", "timeout": -5},
                                       self.config, self.gate)
        self.assertEqual(status, 400)
        self.assertIn("timeout", body["error"])

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


class TestCheckAuth(unittest.TestCase):
    def test_no_token_configured_allows_everything(self):
        self.assertTrue(check_auth(None, None))
        self.assertTrue(check_auth("Bearer anything", None))

    def test_correct_bearer_token_is_accepted(self):
        self.assertTrue(check_auth("Bearer s3cret", "s3cret"))

    def test_wrong_or_missing_token_is_rejected(self):
        self.assertFalse(check_auth("Bearer wrong", "s3cret"))
        self.assertFalse(check_auth(None, "s3cret"))
        self.assertFalse(check_auth("s3cret", "s3cret"))  # missing "Bearer "


class TestResolveDefaultBackend(unittest.TestCase):
    """Decides what a bare `python server.py` uses, and the README promises
    an order. Note the patch target: `resolve_default_backend` reaches
    `resolve_executable` through `LLM.available()` in devllm.base, so patching
    `devllm.api.resolve_executable` would bind nothing and pass silently."""

    def test_prefers_claude_when_both_installed(self):
        with mock.patch("devllm.base.resolve_executable",
                        return_value="/x/cli"):
            self.assertEqual(resolve_default_backend(), "claude")

    def test_falls_back_to_codex(self):
        with mock.patch("devllm.base.resolve_executable",
                        side_effect=lambda n: "/x/codex" if n == "codex"
                        else None):
            self.assertEqual(resolve_default_backend(), "codex")

    def test_none_when_nothing_installed(self):
        with mock.patch("devllm.base.resolve_executable", return_value=None):
            self.assertIsNone(resolve_default_backend())


class TestHandlerWiring(unittest.TestCase):
    """The handler class is built by a factory so config and gate are bound
    without module-level globals."""

    def test_factory_returns_a_distinct_class_per_config(self):
        gate = RequestGate()
        a = make_handler(ServerConfig(port=1), gate)
        b = make_handler(ServerConfig(port=2), gate)
        self.assertIsNot(a, b)
        self.assertEqual(a.config.port, 1)
        self.assertEqual(b.config.port, 2)
        self.assertIs(a.gate, gate)


class _LiveServerCase(unittest.TestCase):
    """Runs the real handler on a loopback port with `handle_generate` mocked.

    Everything inside `make_handler` -- the auth gate, the route table, the
    Content-Type check, body parsing -- is reachable only through a socket.
    Deleting the auth call from `do_POST` used to leave the whole suite green;
    these tests are what makes that go red.
    """

    token: str | None = None

    def setUp(self):
        patcher = mock.patch(
            "devllm.api.handle_generate",
            return_value=(200, {"ok": True, "text": "an answer",
                                "structured": None, "backend": "claude"}),
        )
        self.generate = patcher.start()
        self.addCleanup(patcher.stop)

        # The handler prints one access-log line per request.
        quiet = mock.patch("builtins.print")
        quiet.start()
        self.addCleanup(quiet.stop)

        config = ServerConfig(backend="claude", token=self.token)
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(config, RequestGate()))
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive(),
                         "server thread outlived the test")

    def request(self, method="POST", path="/generate", body=b'{"prompt":"hi"}',
                headers=None):
        """Returns (status, raw_body, headers). 4xx/5xx do not raise."""
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=body, method=method)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers

    JSON = {"Content-Type": "application/json"}


class TestHandlerOverHTTP(_LiveServerCase):
    """No token configured."""

    def test_post_generate_returns_200(self):
        status, body, headers = self.request(headers=self.JSON)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertTrue(json.loads(body)["ok"])
        self.generate.assert_called_once()

    def test_get_root_serves_html(self):
        status, body, headers = self.request(method="GET", path="/", body=None)
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"<!doctype html>", body)

    def test_unknown_get_path_is_404_json(self):
        status, body, _ = self.request(method="GET", path="/nope", body=None)
        self.assertEqual(status, 404)
        payload = json.loads(body)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_type"], "NotFound")

    def test_unknown_post_path_is_404_json(self):
        status, body, _ = self.request(path="/nope", headers=self.JSON)
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error_type"], "NotFound")
        self.generate.assert_not_called()

    def test_malformed_json_is_400(self):
        status, body, _ = self.request(body=b"{not json", headers=self.JSON)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error_type"], "BadRequest")
        self.generate.assert_not_called()

    def test_non_object_body_is_400(self):
        status, body, _ = self.request(body=b'["hi"]', headers=self.JSON)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error_type"], "BadRequest")

    def test_text_plain_content_type_is_415(self):
        # The drive-by POST: text/plain is a CORS simple request, so a browser
        # sends it cross-origin with no preflight. Rejecting it is what stops
        # any open page from spending the user's subscription.
        status, body, _ = self.request(
            headers={"Content-Type": "text/plain;charset=UTF-8"})
        self.assertEqual(status, 415)
        self.assertEqual(json.loads(body)["error_type"],
                         "UnsupportedMediaType")
        self.generate.assert_not_called()

    def test_missing_content_type_is_415(self):
        status, _, _ = self.request()
        self.assertEqual(status, 415)
        self.generate.assert_not_called()

    def test_charset_parameter_is_accepted(self):
        status, _, _ = self.request(
            headers={"Content-Type": "application/json; charset=utf-8"})
        self.assertEqual(status, 200)

    def test_header_name_case_does_not_matter(self):
        # examples/curl.sh sends a lowercase `content-type`. HTTP header
        # names are case-insensitive and the 415 check must honour that.
        status, _, _ = self.request(
            headers={"content-type": "application/json"})
        self.assertEqual(status, 200)


class TestHandlerAuthOverHTTP(_LiveServerCase):
    """A token is configured, so /generate and /health must enforce it."""

    token = "s3cret"

    def test_no_authorization_header_is_401(self):
        status, body, _ = self.request(headers=self.JSON)
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)["error_type"], "Unauthorized")
        self.generate.assert_not_called()

    def test_wrong_token_is_401(self):
        status, _, _ = self.request(
            headers={**self.JSON, "Authorization": "Bearer wrong"})
        self.assertEqual(status, 401)
        self.generate.assert_not_called()

    def test_correct_bearer_token_is_200(self):
        status, body, _ = self.request(
            headers={**self.JSON, "Authorization": "Bearer s3cret"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        self.generate.assert_called_once()

    def test_health_is_gated_too(self):
        status, _, _ = self.request(method="GET", path="/health", body=None)
        self.assertEqual(status, 401)

    def test_root_page_is_not_gated(self):
        # Deliberate: the page is how a new user proves the server answers.
        # Its own fetch cannot authenticate, which the README warns about.
        status, _, _ = self.request(method="GET", path="/", body=None)
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()

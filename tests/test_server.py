"""Tests for the entry point's argument and safety handling."""

import unittest
from unittest import mock

import server


class TestBuildConfig(unittest.TestCase):
    def _config(self, argv, env=None):
        args = server.parse_args(argv)
        return server.build_config(args, env or {})

    @mock.patch("server.resolve_default_backend", return_value="claude")
    def test_defaults(self, _resolve):
        config = self._config([])
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 8765)
        self.assertEqual(config.concurrency, 2)
        self.assertEqual(config.timeout, 300)
        self.assertEqual(config.backend, "claude")
        self.assertIsNone(config.token)

    @mock.patch("server.resolve_default_backend", return_value="claude")
    def test_flags_override_defaults(self, _resolve):
        config = self._config(["--port", "9000", "--concurrency", "1",
                               "--timeout", "60", "--backend", "codex"])
        self.assertEqual(config.port, 9000)
        self.assertEqual(config.concurrency, 1)
        self.assertEqual(config.timeout, 60)
        self.assertEqual(config.backend, "codex")

    @mock.patch("server.resolve_default_backend", return_value=None)
    def test_backend_is_none_when_nothing_installed(self, _resolve):
        self.assertIsNone(self._config([]).backend)

    @mock.patch("server.resolve_default_backend", return_value="claude")
    def test_token_comes_from_the_environment(self, _resolve):
        config = self._config([], {"DEVLLM_TOKEN": "s3cret"})
        self.assertEqual(config.token, "s3cret")


class TestRemoteBindingGuard(unittest.TestCase):
    """Binding off localhost exposes a paid subscription, so it takes three
    deliberate actions: --host, --allow-remote and a token."""

    @mock.patch("server.resolve_default_backend", return_value="claude")
    def test_non_local_host_without_allow_remote_is_refused(self, _resolve):
        with self.assertRaises(SystemExit):
            server.build_config(server.parse_args(["--host", "0.0.0.0"]), {})

    @mock.patch("server.resolve_default_backend", return_value="claude")
    def test_allow_remote_without_a_token_is_refused(self, _resolve):
        with self.assertRaises(SystemExit):
            server.build_config(
                server.parse_args(["--host", "0.0.0.0", "--allow-remote"]), {})

    @mock.patch("server.resolve_default_backend", return_value="claude")
    def test_allow_remote_with_a_token_is_permitted(self, _resolve):
        config = server.build_config(
            server.parse_args(["--host", "0.0.0.0", "--allow-remote"]),
            {"DEVLLM_TOKEN": "s3cret"})
        self.assertEqual(config.host, "0.0.0.0")

    @mock.patch("server.resolve_default_backend", return_value="claude")
    def test_localhost_needs_no_ceremony(self, _resolve):
        self.assertEqual(self._localhost().host, "127.0.0.1")

    @staticmethod
    def _localhost():
        return server.build_config(server.parse_args([]), {})


class TestCheckFlag(unittest.TestCase):
    @mock.patch("server.run_doctor", return_value=True)
    def test_check_runs_the_report_and_exits_zero(self, doctor):
        self.assertEqual(server.main(["--check"]), 0)
        doctor.assert_called_once()

    @mock.patch("server.run_doctor", return_value=False)
    def test_check_exits_one_when_no_backend_works(self, _doctor):
        self.assertEqual(server.main(["--check"]), 1)

    @mock.patch("server.serve")
    @mock.patch("server.run_doctor")
    def test_check_does_not_start_the_server(self, _doctor, serve_):
        server.main(["--check"])
        serve_.assert_not_called()


class TestConcurrencyAndTimeoutValidation(unittest.TestCase):
    """RequestGate(concurrency=0) constructs fine and then blocks every
    request forever. That must be rejected here, where the untrusted CLI
    input is parsed, not left to surface as a silent hang later."""

    @mock.patch("server.resolve_default_backend", return_value="claude")
    def test_concurrency_below_one_is_refused(self, _resolve):
        with self.assertRaises(SystemExit):
            server.build_config(
                server.parse_args(["--concurrency", "0"]), {})

    @mock.patch("server.resolve_default_backend", return_value="claude")
    def test_timeout_below_one_is_refused(self, _resolve):
        with self.assertRaises(SystemExit):
            server.build_config(
                server.parse_args(["--timeout", "0"]), {})


if __name__ == "__main__":
    unittest.main()

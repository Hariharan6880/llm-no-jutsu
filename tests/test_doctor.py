"""Offline tests for the backend state report."""

import unittest
from unittest import mock

from devllm.base import LLMError
from devllm.doctor import check_backends, run_doctor


class TestCheckBackends(unittest.TestCase):
    @mock.patch("devllm.doctor.resolve_executable", return_value=None)
    def test_reports_missing_backends(self, _which):
        report = check_backends()
        self.assertEqual(set(report), {"claude", "codex"})
        self.assertFalse(report["claude"]["installed"])
        self.assertIsNone(report["claude"]["path"])
        self.assertIsInstance(report["claude"]["login"], str)

    @mock.patch("devllm.doctor._codex_login", return_value="subscription: X")
    @mock.patch("devllm.doctor._claude_login", return_value="subscription: pro")
    @mock.patch("devllm.doctor.resolve_executable", return_value="/usr/bin/claude")
    def test_reports_installed_backends(self, _which, _cl, _co):
        report = check_backends()
        self.assertTrue(report["claude"]["installed"])
        self.assertEqual(report["claude"]["path"], "/usr/bin/claude")
        self.assertEqual(report["claude"]["login"], "subscription: pro")

    @mock.patch("devllm.doctor.resolve_executable", return_value=None)
    def test_run_doctor_returns_false_with_no_backends(self, _which):
        with mock.patch("builtins.print"):
            self.assertFalse(run_doctor(live=False))

    # _codex_login must be patched too: left alone it reads the developer's
    # real ~/.codex/auth.json, so the test's result depends on the machine.
    @mock.patch("devllm.doctor._codex_login", return_value="subscription: X")
    @mock.patch("devllm.doctor._claude_login", return_value="subscription: pro")
    @mock.patch("devllm.doctor.resolve_executable", return_value="/usr/bin/claude")
    def test_run_doctor_returns_true_when_installed(self, _which, _cl, _co):
        with mock.patch("builtins.print"):
            self.assertTrue(run_doctor(live=False))


class TestRunDoctorLive(unittest.TestCase):
    """`--check`'s exit code is this return value, and it is the first command
    the README tells a stranger to run. An installed-but-logged-out CLI is
    exactly what it exists to catch, so a failed live call must not report
    success."""

    def setUp(self):
        for target, value in (
            ("devllm.doctor.resolve_executable", "/usr/bin/cli"),
            ("devllm.doctor._claude_login", "subscription: pro"),
            ("devllm.doctor._codex_login", "subscription: X"),
        ):
            patcher = mock.patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.backends = []
        for target in ("devllm.doctor.ClaudeCLI", "devllm.doctor.CodexCLI"):
            patcher = mock.patch(target)
            self.backends.append(patcher.start())
            self.addCleanup(patcher.stop)

        quiet = mock.patch("builtins.print")
        quiet.start()
        self.addCleanup(quiet.stop)

    def test_returns_false_when_the_live_call_fails(self):
        for cls in self.backends:
            cls.return_value.generate.side_effect = LLMError("not logged in")
        self.assertFalse(run_doctor(live=True))

    def test_returns_true_when_the_live_call_succeeds(self):
        for cls in self.backends:
            cls.return_value.generate.return_value = mock.Mock(
                duration_s=1.2, text="ok")
        self.assertTrue(run_doctor(live=True))


if __name__ == "__main__":
    unittest.main()

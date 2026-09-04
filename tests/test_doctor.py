"""Offline tests for the backend state report."""

import unittest
from unittest import mock

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

    @mock.patch("devllm.doctor._claude_login", return_value="subscription: pro")
    @mock.patch("devllm.doctor.resolve_executable", return_value="/usr/bin/claude")
    def test_run_doctor_returns_true_when_installed(self, _which, _cl):
        with mock.patch("builtins.print"):
            self.assertTrue(run_doctor(live=False))


if __name__ == "__main__":
    unittest.main()

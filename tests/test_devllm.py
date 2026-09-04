"""Offline tests. No CLI, no network, no tokens spent.

Run with:  python -m unittest discover -s tests -v
"""

import json
import subprocess
import unittest
from unittest import mock

from devllm import (
    LLM,
    BackendNotFoundError,
    ClaudeCLI,
    CodexCLI,
    OutputParseError,
    UNSET,
    get_backend,
)
from devllm.base import (
    DEFAULT_SYSTEM,
    BackendInvocationError,
    BackendTimeoutError,
)


def _completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


CLAUDE_ENVELOPE = {
    "type": "result",
    "is_error": False,
    "result": "Samsung, Apple, and Xiaomi.",
    "structured_output": {"picks": ["Samsung"]},
    "usage": {
        "input_tokens": 3,
        "output_tokens": 19,
        "cache_read_input_tokens": 4218,
        "cache_creation_input_tokens": 720,
    },
}


class TestFactory(unittest.TestCase):
    def test_named_backends(self):
        self.assertIsInstance(get_backend("claude"), ClaudeCLI)
        self.assertIsInstance(get_backend("codex"), CodexCLI)

    def test_defaults_to_claude(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsInstance(get_backend(), ClaudeCLI)

    def test_env_var_selects_backend(self):
        with mock.patch.dict("os.environ", {"DEVLLM_BACKEND": "codex"}):
            self.assertIsInstance(get_backend(), CodexCLI)

    def test_unknown_backend_is_rejected(self):
        with self.assertRaises(ValueError):
            get_backend("gpt-9")


class TestClaude(unittest.TestCase):
    @mock.patch("devllm.claude.resolve_executable", return_value=None)
    def test_missing_executable(self, _which):
        with self.assertRaises(BackendNotFoundError):
            ClaudeCLI().generate("hi")

    @mock.patch("devllm.claude.resolve_executable", return_value="claude.CMD")
    @mock.patch("devllm.claude.run_process")
    def test_parses_envelope(self, run, _which):
        run.return_value = (_completed(json.dumps(CLAUDE_ENVELOPE)), 1.5)
        r = ClaudeCLI().generate("hi")
        self.assertEqual(r.text, "Samsung, Apple, and Xiaomi.")
        self.assertEqual(r.duration_s, 1.5)
        self.assertEqual(r.usage.output_tokens, 19)
        self.assertEqual(r.usage.total_input, 3 + 4218 + 720)

    @mock.patch("devllm.claude.resolve_executable", return_value="claude.CMD")
    @mock.patch("devllm.claude.run_process")
    def test_structured_output_only_when_schema_given(self, run, _which):
        run.return_value = (_completed(json.dumps(CLAUDE_ENVELOPE)), 1.0)
        # The envelope always carries `result` prose; `structured_output` must
        # only be surfaced when the caller actually asked for a schema.
        self.assertIsNone(ClaudeCLI().generate("hi").structured)
        self.assertEqual(
            ClaudeCLI().generate("hi", schema={"type": "object"}).structured,
            {"picks": ["Samsung"]},
        )

    @mock.patch("devllm.claude.resolve_executable", return_value="claude.CMD")
    @mock.patch("devllm.claude.run_process")
    def test_prompt_never_goes_in_argv(self, run, _which):
        run.return_value = (_completed(json.dumps(CLAUDE_ENVELOPE)), 1.0)
        secret = "a very long prompt " * 500
        ClaudeCLI().generate(secret)
        argv, prompt = run.call_args[0][0], run.call_args[0][1]
        self.assertEqual(prompt, secret)
        self.assertNotIn(secret, argv)

    @mock.patch("devllm.claude.resolve_executable", return_value="claude.CMD")
    @mock.patch("devllm.claude.run_process")
    def test_nonzero_exit(self, run, _which):
        run.return_value = (_completed("", returncode=1, stderr="boom"), 0.2)
        with self.assertRaises(BackendInvocationError):
            ClaudeCLI().generate("hi")

    @mock.patch("devllm.claude.resolve_executable", return_value="claude.CMD")
    @mock.patch("devllm.claude.run_process")
    def test_unparseable_stdout(self, run, _which):
        run.return_value = (_completed("not json"), 0.2)
        with self.assertRaises(OutputParseError):
            ClaudeCLI().generate("hi")

    @mock.patch("devllm.claude.resolve_executable", return_value="claude.CMD")
    @mock.patch("devllm.claude.run_process")
    def test_tools_disabled_by_default(self, run, _which):
        run.return_value = (_completed(json.dumps(CLAUDE_ENVELOPE)), 1.0)
        ClaudeCLI().generate("hi")
        self.assertIn("--disallowedTools", run.call_args[0][0])
        run.reset_mock()
        ClaudeCLI(tools=True).generate("hi")
        self.assertNotIn("--disallowedTools", run.call_args[0][0])


class TestDefaultSystemPrompt(unittest.TestCase):
    """Both CLIs are coding agents. Without a replacement system prompt,
    Claude Code refuses ordinary questions with "I'm a software engineering
    assistant", which makes it useless as a general backend."""

    @mock.patch("devllm.claude.resolve_executable", return_value="claude.CMD")
    @mock.patch("devllm.claude.run_process")
    def test_claude_replaces_the_stock_prompt(self, run, _which):
        run.return_value = (_completed(json.dumps(CLAUDE_ENVELOPE)), 1.0)
        ClaudeCLI().generate("hi")
        argv = run.call_args[0][0]
        self.assertIn("--system-prompt", argv)
        self.assertIn(DEFAULT_SYSTEM, argv)

    @mock.patch("devllm.claude.resolve_executable", return_value="claude.CMD")
    @mock.patch("devllm.claude.run_process")
    def test_claude_system_none_restores_stock_prompt(self, run, _which):
        # system=None means "send no system prompt at all", which is what
        # restores Claude Code's own stock prompt -- it is not a synonym for
        # "use the instance default".
        run.return_value = (_completed(json.dumps(CLAUDE_ENVELOPE)), 1.0)
        ClaudeCLI(system=None).generate("hi")
        self.assertNotIn("--system-prompt", run.call_args[0][0])

    @mock.patch("devllm.claude.resolve_executable", return_value="claude.CMD")
    @mock.patch("devllm.claude.run_process")
    def test_per_call_system_overrides_instance(self, run, _which):
        run.return_value = (_completed(json.dumps(CLAUDE_ENVELOPE)), 1.0)
        ClaudeCLI().generate("hi", system="BE A PIRATE")
        argv = run.call_args[0][0]
        self.assertIn("BE A PIRATE", argv)
        self.assertNotIn(DEFAULT_SYSTEM, argv)


class TestCodex(unittest.TestCase):
    @staticmethod
    def _writes(text):
        """Mock run_process by writing `text` to the path after -o."""
        def side_effect(argv, prompt, timeout, backend):
            path = argv[argv.index("-o") + 1]
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            return _completed(""), 2.0
        return side_effect

    @mock.patch("devllm.codex.resolve_executable", return_value=None)
    def test_missing_executable(self, _which):
        with self.assertRaises(BackendNotFoundError):
            CodexCLI().generate("hi")

    @mock.patch("devllm.codex.resolve_executable", return_value="codex.CMD")
    @mock.patch("devllm.codex.run_process")
    def test_default_system_is_prepended(self, run, _which):
        run.side_effect = self._writes("ok")
        CodexCLI().generate("hi")
        self.assertTrue(run.call_args[0][1].startswith(DEFAULT_SYSTEM))

    @mock.patch("devllm.codex.resolve_executable", return_value="codex.CMD")
    @mock.patch("devllm.codex.run_process")
    def test_reads_last_message_file(self, run, _which):
        run.side_effect = self._writes("Samsung and Xiaomi.\n")
        r = CodexCLI().generate("hi")
        self.assertEqual(r.text, "Samsung and Xiaomi.")
        self.assertEqual(r.backend, "codex")

    @mock.patch("devllm.codex.resolve_executable", return_value="codex.CMD")
    @mock.patch("devllm.codex.run_process")
    def test_low_reasoning_effort_by_default(self, run, _which):
        run.side_effect = self._writes("ok")
        CodexCLI().generate("hi")
        self.assertIn("model_reasoning_effort=low", run.call_args[0][0])

    @mock.patch("devllm.codex.resolve_executable", return_value="codex.CMD")
    @mock.patch("devllm.codex.run_process")
    def test_schema_requires_json(self, run, _which):
        run.side_effect = self._writes("sorry, here is some prose instead")
        with self.assertRaises(OutputParseError):
            CodexCLI().generate("hi", schema={"type": "object"})

    @mock.patch("devllm.codex.resolve_executable", return_value="codex.CMD")
    @mock.patch("devllm.codex.run_process")
    def test_system_prompt_is_prepended(self, run, _which):
        run.side_effect = self._writes("ok")
        CodexCLI(system="BE TERSE").generate("hi")
        self.assertTrue(run.call_args[0][1].startswith("BE TERSE"))


class TestUnsetSentinel(unittest.TestCase):
    """`None` and "unset" must be distinguishable: the HTTP API gives
    `"system": null` the meaning "no system prompt", which the old
    None-means-default behaviour made impossible to express."""

    @mock.patch("devllm.codex.resolve_executable", return_value="codex.CMD")
    @mock.patch("devllm.codex.run_process")
    def test_codex_per_call_none_suppresses_system_prompt(self, run, _which):
        run.side_effect = TestCodex._writes("ok")
        CodexCLI().generate("hi", system=None)
        self.assertEqual(run.call_args[0][1], "hi")

    @mock.patch("devllm.claude.resolve_executable", return_value="claude.CMD")
    @mock.patch("devllm.claude.run_process")
    def test_per_call_none_suppresses_system_prompt(self, run, _which):
        run.return_value = (_completed(json.dumps(CLAUDE_ENVELOPE)), 1.0)
        ClaudeCLI().generate("hi", system=None)
        self.assertNotIn("--system-prompt", run.call_args[0][0])

    @mock.patch("devllm.claude.resolve_executable", return_value="claude.CMD")
    @mock.patch("devllm.claude.run_process")
    def test_omitted_uses_instance_default(self, run, _which):
        run.return_value = (_completed(json.dumps(CLAUDE_ENVELOPE)), 1.0)
        ClaudeCLI(system="INSTANCE").generate("hi")
        self.assertIn("INSTANCE", run.call_args[0][0])

    @mock.patch("devllm.codex.resolve_executable", return_value="codex.CMD")
    @mock.patch("devllm.codex.run_process")
    def test_codex_none_sends_bare_prompt(self, run, _which):
        run.side_effect = TestCodex._writes("ok")
        CodexCLI(system=None).generate("hi")
        self.assertEqual(run.call_args[0][1], "hi")

    def test_executable_is_a_plain_attribute(self):
        # A subclass that is not a subprocess must not explode on available().
        class Fake(LLM):
            name = "fake"
            def generate(self, prompt, *, schema=None, system=UNSET):
                raise NotImplementedError
        self.assertFalse(Fake().available())


class TestRunProcess(unittest.TestCase):
    @mock.patch("subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1))
    def test_timeout_is_wrapped(self, _run):
        from devllm.base import run_process
        with self.assertRaises(BackendTimeoutError):
            run_process(["x"], "hi", 1, "test")

    @mock.patch("subprocess.run", return_value=_completed("out"))
    def test_prompt_is_sent_on_stdin(self, run):
        from devllm.base import run_process
        run_process(["x"], "the prompt", 30, "test")
        self.assertEqual(run.call_args.kwargs["input"], "the prompt")


if __name__ == "__main__":
    unittest.main()

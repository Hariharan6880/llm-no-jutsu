"""Live tests. These spend real tokens and take 10-40 seconds each.

Every other test mocks run_process, so nothing else in the suite would notice
if a CLI changed its flags — the most likely real-world breakage. These make
one genuine call per installed backend.

Skipped automatically when the CLI is not installed, so CI stays green.
"""

import unittest

from devllm.api import RequestGate, ServerConfig, handle_generate
from devllm.base import resolve_executable
from devllm.claude import ClaudeCLI
from devllm.codex import CodexCLI

HAS_CLAUDE = resolve_executable("claude") is not None
HAS_CODEX = resolve_executable("codex") is not None

PROMPT = "Reply with exactly the word: ok"
SCHEMA = {
    "type": "object",
    "properties": {"word": {"type": "string"}},
    "required": ["word"],
    "additionalProperties": False,
}


@unittest.skipUnless(HAS_CLAUDE, "claude CLI not installed")
class TestClaudeLive(unittest.TestCase):
    def test_plain_text_call(self):
        r = ClaudeCLI().generate(PROMPT)
        self.assertIn("ok", r.text.lower())
        self.assertGreater(r.duration_s, 0)
        self.assertIsNotNone(r.usage)

    def test_schema_call_returns_parsed_structure(self):
        r = ClaudeCLI().generate("Return the word ok.", schema=SCHEMA)
        self.assertIsInstance(r.structured, dict)
        self.assertIn("word", r.structured)

    def test_answers_a_non_coding_question(self):
        # Regression: with its stock system prompt Claude Code refuses
        # ordinary questions as "not something I can help with".
        r = ClaudeCLI().generate("Name one phone brand sold in India. "
                                 "Reply with the brand name only.")
        self.assertGreater(len(r.text.strip()), 0)
        self.assertNotIn("software engineering assistant", r.text.lower())


@unittest.skipUnless(HAS_CODEX, "codex CLI not installed")
class TestCodexLive(unittest.TestCase):
    def test_plain_text_call(self):
        r = CodexCLI().generate(PROMPT)
        self.assertIn("ok", r.text.lower())

    def test_schema_call_returns_parsed_structure(self):
        r = CodexCLI().generate("Return the word ok.", schema=SCHEMA)
        self.assertIsInstance(r.structured, dict)


@unittest.skipUnless(HAS_CLAUDE, "claude CLI not installed")
class TestEndpointLive(unittest.TestCase):
    def test_handle_generate_end_to_end(self):
        status, body = handle_generate(
            {"prompt": PROMPT},
            ServerConfig(backend="claude", model="sonnet"),
            RequestGate(),
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIn("ok", body["text"].lower())


if __name__ == "__main__":
    unittest.main()

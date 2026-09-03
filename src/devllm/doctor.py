"""Environment check: is each CLI installed, logged in, and answering?"""

from __future__ import annotations

import json
import os

from .base import LLMError, resolve_executable
from .claude import ClaudeCLI
from .codex import CodexCLI

_PROMPT = "Reply with exactly the word: ok"


def _claude_login() -> str:
    path = os.path.expanduser("~/.claude/.credentials.json")
    if not os.path.exists(path):
        return "no credentials file (run `claude` once to log in)"
    try:
        with open(path, encoding="utf-8") as handle:
            oauth = json.load(handle).get("claudeAiOauth") or {}
    except (OSError, json.JSONDecodeError):
        return "credentials file unreadable"
    plan = oauth.get("subscriptionType")
    return f"subscription: {plan}" if plan else "logged in"


def _codex_login() -> str:
    path = os.path.expanduser("~/.codex/auth.json")
    if not os.path.exists(path):
        return "no auth file (run `codex login`)"
    try:
        with open(path, encoding="utf-8") as handle:
            auth = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return "auth file unreadable"
    if auth.get("tokens"):
        return "subscription: ChatGPT OAuth"
    if auth.get("OPENAI_API_KEY"):
        return "API key (billed per token)"
    return "logged in"


def run_doctor(live: bool = True) -> bool:
    """Print a report. Returns True if at least one backend works.

    Args:
        live: Also send a real one-word prompt to each installed backend.
              Costs a few tokens and 10-30 seconds, but it is the only check
              that proves the whole path actually works.
    """
    checks = [
        ("claude", ClaudeCLI, _claude_login),
        ("codex", CodexCLI, _codex_login),
    ]

    print("devllm doctor\n" + "-" * 52)
    any_working = False

    for name, cls, login_check in checks:
        path = resolve_executable(name)
        if path is None:
            print(f"  {name:8s} NOT FOUND on PATH")
            continue
        print(f"  {name:8s} {path}")
        print(f"  {'':8s} {login_check()}")

        if not live:
            any_working = True
            continue

        try:
            response = cls().generate(_PROMPT)
        except LLMError as exc:
            print(f"  {'':8s} live call FAILED: {exc}")
        else:
            any_working = True
            print(f"  {'':8s} live call OK in {response.duration_s:.1f}s "
                  f"-> {response.text.strip()[:40]!r}")
        print()

    print("-" * 52)
    if any_working:
        print("At least one backend is working. You are good to go.")
    else:
        print("No working backend. Install one:")
        print("  npm install -g @anthropic-ai/claude-code   then run `claude`")
        print("  npm install -g @openai/codex               then `codex login`")
    return any_working

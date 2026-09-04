"""Environment check: is each CLI installed, logged in, and answering?"""

from __future__ import annotations

import json
import os

from .base import BACKEND_NAMES, LLMError, resolve_executable
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


_LOGIN_CHECKS = {"claude": _claude_login, "codex": _codex_login}
_INSTALL_HINTS = {
    "claude": "npm install -g @anthropic-ai/claude-code   then run `claude`",
    "codex": "npm install -g @openai/codex               then `codex login`",
}


def check_backends() -> dict[str, dict]:
    """Installed/login state for every backend. Performs no model call."""
    report: dict[str, dict] = {}
    for name in BACKEND_NAMES:
        path = resolve_executable(name)
        report[name] = {
            "installed": path is not None,
            "path": path,
            "login": _LOGIN_CHECKS[name]() if path else "not installed",
        }
    return report


def run_doctor(live: bool = True) -> bool:
    """Print a report. Returns True if at least one backend is installed.

    Args:
        live: Also send a real one-word prompt to each installed backend.
              Costs a few tokens and 10-30 seconds, but it is the only check
              that proves the whole path actually works.
    """
    classes = {"claude": ClaudeCLI, "codex": CodexCLI}
    report = check_backends()

    print("devllm check\n" + "-" * 52)
    any_installed = False

    for name, state in report.items():
        if not state["installed"]:
            print(f"  {name:8s} NOT FOUND on PATH")
            continue
        any_installed = True
        print(f"  {name:8s} {state['path']}")
        print(f"  {'':8s} {state['login']}")

        if live:
            try:
                response = classes[name]().generate(_PROMPT)
            except LLMError as exc:
                print(f"  {'':8s} live call FAILED: {exc}")
            else:
                print(f"  {'':8s} live call OK in {response.duration_s:.1f}s "
                      f"-> {response.text.strip()[:40]!r}")
        print()

    print("-" * 52)
    if any_installed:
        print("At least one backend is installed. You are good to go.")
    else:
        print("No backend installed. Install one:")
        for hint in _INSTALL_HINTS.values():
            print(f"  {hint}")
    return any_installed

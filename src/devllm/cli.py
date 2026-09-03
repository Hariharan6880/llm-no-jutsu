"""Command line entry point: `devllm ask | play | doctor`."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__, get_backend
from .base import LLMError


def _use_utf8_stdout() -> None:
    """Windows consoles default to cp1252 and raise on characters like the
    rupee sign. Every CLI path prints model output, so fix it up front."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass


def main(argv: list[str] | None = None) -> int:
    _use_utf8_stdout()

    parser = argparse.ArgumentParser(
        prog="devllm",
        description="Use your installed Claude Code / Codex CLI as an LLM "
                    "backend during development.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    ask = sub.add_parser("ask", help="send one prompt and print the answer")
    ask.add_argument("prompt", nargs="?",
                     help="the prompt; omit to read from stdin")
    ask.add_argument("-b", "--backend", default=None,
                     choices=["claude", "codex"])
    ask.add_argument("-m", "--model", default=None)
    ask.add_argument("-s", "--system", default=None)
    ask.add_argument("--schema", default=None,
                     help="path to a JSON Schema file for structured output")
    ask.add_argument("--json", action="store_true",
                     help="print the full response as JSON")
    ask.add_argument("--timeout", type=int, default=300)

    play = sub.add_parser("play", help="open the browser playground")
    play.add_argument("-p", "--port", type=int, default=8765)
    play.add_argument("--no-browser", action="store_true")

    doc = sub.add_parser("doctor", help="check installs, logins and a live call")
    doc.add_argument("--offline", action="store_true",
                     help="skip the live test call")

    args = parser.parse_args(argv)

    if args.command == "play":
        from .playground import serve
        serve(port=args.port, open_browser=not args.no_browser)
        return 0

    if args.command == "doctor":
        from .doctor import run_doctor
        return 0 if run_doctor(live=not args.offline) else 1

    if args.command == "ask":
        prompt = args.prompt if args.prompt is not None else sys.stdin.read()
        if not prompt.strip():
            parser.error("no prompt given (pass an argument or pipe stdin)")

        schema = None
        if args.schema:
            with open(args.schema, encoding="utf-8") as handle:
                schema = json.load(handle)

        kwargs = {"timeout": args.timeout, "system": args.system}
        if args.model:
            kwargs["model"] = args.model

        try:
            llm = get_backend(args.backend, **kwargs)
            response = llm.generate(prompt, schema=schema)
        except LLMError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        if args.json:
            print(json.dumps({
                "text": response.text,
                "structured": response.structured,
                "backend": response.backend,
                "model": response.model,
                "duration_s": round(response.duration_s, 2),
                "argv": response.argv,
            }, indent=2))
        elif response.structured is not None:
            print(json.dumps(response.structured, indent=2))
        else:
            print(response.text)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

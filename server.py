#!/usr/bin/env python3
"""devllm — a local LLM endpoint backed by your Claude Code / Codex CLI.

    python server.py --check     confirm a CLI is installed and logged in
    python server.py             start the server on http://127.0.0.1:8765

Standard library only. Development use, on your own machine.
"""

from __future__ import annotations

import argparse
import os
import sys

from devllm.api import ServerConfig, resolve_default_backend, serve
from devllm.doctor import run_doctor

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="server.py",
        description="Serve your Claude Code / Codex CLI as a local HTTP endpoint.",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--backend", choices=["claude", "codex"], default=None,
                        help="default backend; auto-detected when omitted")
    parser.add_argument("--model", default=None,
                        help="default model, e.g. sonnet")
    parser.add_argument("--concurrency", type=int, default=2,
                        help="concurrent CLI processes (each is heavy)")
    parser.add_argument("--timeout", type=int, default=300,
                        help="per-request ceiling in seconds")
    parser.add_argument("--allow-remote", action="store_true",
                        help="permit binding off localhost; requires DEVLLM_TOKEN")
    parser.add_argument("--check", action="store_true",
                        help="report install and login state, then exit")
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace, env: dict) -> ServerConfig:
    """Turn parsed arguments into a config, refusing unsafe combinations."""
    token = env.get("DEVLLM_TOKEN") or None

    if args.host not in _LOCAL_HOSTS:
        # This endpoint spawns subprocesses using paid credentials. Exposing it
        # beyond the machine takes three deliberate actions, not one flag.
        if not args.allow_remote:
            raise SystemExit(
                f"refusing to bind {args.host}: pass --allow-remote if you "
                f"really mean to expose this beyond your machine"
            )
        if not token:
            raise SystemExit(
                "refusing to bind off localhost without a token: set "
                "DEVLLM_TOKEN in the environment"
            )
        print(f"WARNING: binding {args.host} exposes your CLI subscription to "
              f"anyone who can reach this port.")

    if args.concurrency < 1:
        raise SystemExit(
            f"--concurrency must be at least 1, got {args.concurrency}"
        )
    if args.timeout < 1:
        raise SystemExit(
            f"--timeout must be at least 1, got {args.timeout}"
        )

    return ServerConfig(
        host=args.host,
        port=args.port,
        backend=args.backend or resolve_default_backend(),
        model=args.model,
        concurrency=args.concurrency,
        timeout=args.timeout,
        token=token,
    )


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252 and raise on characters like the
    # rupee sign, which model output routinely contains.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    args = parse_args(argv)
    if args.check:
        return 0 if run_doctor(live=True) else 1

    serve(build_config(args, dict(os.environ)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

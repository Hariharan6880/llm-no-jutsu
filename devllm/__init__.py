"""Internals of the devllm server. Not a library, and not an import surface.

The only thing you run is `server.py`; the only thing you call is
`POST /generate`. These modules exist to serve that endpoint:

    base.py        shared types, subprocess handling, error classes
    claude.py      the `claude -p` backend
    codex.py       the `codex exec` backend
    doctor.py      the install/login report behind `--check` and `/health`
    api.py         request logic, routing, auth
    playground.py  the browser smoke-test page

Import from those modules directly. This package deliberately re-exports
nothing: a stable public surface is a promise a development tool should not
make.
"""

from __future__ import annotations

"""A browser playground for driving the backends by hand.

Standard library only. Run it with `devllm play`.
"""

from __future__ import annotations

import dataclasses
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .base import LLMError
from .claude import ClaudeCLI
from .codex import CodexCLI

DEFAULT_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "picks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "price_inr": {"type": "integer"},
                        "why": {"type": "string"},
                    },
                    "required": ["name", "price_inr", "why"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["picks"],
        "additionalProperties": False,
    },
    indent=2,
)

PAGE = """<!doctype html>
<meta charset="utf-8"><title>devllm playground</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; background:#14161a; color:#e6e6e6;
         font:14px ui-monospace,SFMono-Regular,Consolas,monospace; }
  header { padding:12px 18px; background:#1b1e24; border-bottom:1px solid #2c313a; }
  header b { color:#fff; } header span { color:#8b93a1; margin-left:10px; }
  .wrap { display:grid; grid-template-columns:minmax(320px,1fr) minmax(320px,1.15fr);
          gap:16px; padding:16px; align-items:start; }
  @media (max-width:880px){ .wrap{ grid-template-columns:1fr; } }
  .card { background:#1b1e24; border:1px solid #2c313a; border-radius:8px; padding:14px; }
  label { display:block; color:#8b93a1; margin:12px 0 5px; font-size:12px;
          text-transform:uppercase; letter-spacing:.5px; }
  label:first-child { margin-top:0; }
  textarea,select,input { width:100%; background:#0f1114; color:#e6e6e6;
      border:1px solid #333a45; border-radius:6px; padding:9px; font:inherit; }
  textarea { resize:vertical; }
  .row { display:flex; gap:10px; } .row>div { flex:1; min-width:0; }
  button { margin-top:14px; width:100%; padding:11px; background:#3b82f6; color:#fff;
      border:0; border-radius:6px; font:600 14px inherit; cursor:pointer; }
  button:disabled { background:#333a45; color:#8b93a1; cursor:not-allowed; }
  .chk { display:flex; align-items:center; gap:8px; margin-top:14px; color:#8b93a1; }
  .chk input { width:auto; }
  pre { background:#0f1114; border:1px solid #2c313a; border-radius:6px; padding:10px;
        white-space:pre-wrap; word-break:break-word; margin:0; max-height:320px;
        overflow:auto; }
  h3 { font-size:12px; color:#8b93a1; text-transform:uppercase; letter-spacing:.5px;
       margin:14px 0 6px; } h3:first-child { margin-top:0; }
  .pill { display:inline-block; padding:3px 9px; border-radius:99px; font-size:12px;
          margin-right:6px; }
  .ok { background:#14532d; color:#86efac; } .bad { background:#5b1717; color:#fca5a5; }
  .meta { background:#243044; color:#93c5fd; }
  .idle { color:#8b93a1; padding:8px 0; }
</style>
<header><b>devllm</b><span>playground &mdash; your CLI subscription as a local LLM backend</span></header>
<div class="wrap">
  <div class="card">
    <label>Prompt</label>
    <textarea id="prompt" rows="8">Recommend the best phones under 50000 INR in India.</textarea>
    <label>System prompt (optional)</label>
    <input id="system" placeholder="You are a product recommendation engine.">
    <div class="row">
      <div><label>Backend</label>
        <select id="backend">
          <option value="claude">claude</option>
          <option value="codex">codex</option>
        </select></div>
      <div><label>Model</label>
        <input id="model" value="sonnet" placeholder="blank = default"></div>
      <div><label>Reasoning (codex)</label>
        <select id="reasoning">
          <option value="low">low</option>
          <option value="none">none</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
          <option value="xhigh">xhigh</option>
        </select></div>
    </div>
    <div class="chk"><input type="checkbox" id="useSchema">
      <label for="useSchema" style="margin:0">Constrain output to JSON schema</label></div>
    <div id="schemaBox" hidden><label>Schema</label>
      <textarea id="schema" rows="9">__SCHEMA__</textarea></div>
    <button id="go">Send</button>
  </div>
  <div class="card">
    <div id="status" class="idle">Idle &mdash; nothing sent yet.</div>
    <div id="out"></div>
  </div>
</div>
<script>
const $ = id => document.getElementById(id);
$('useSchema').onchange = e => { $('schemaBox').hidden = !e.target.checked; };
$('backend').onchange = e => {
  $('model').value = e.target.value === 'claude' ? 'sonnet' : '';
};
const esc = s => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

$('go').onclick = async () => {
  const body = {
    prompt: $('prompt').value,
    system: $('system').value.trim(),
    backend: $('backend').value,
    model: $('model').value.trim(),
    reasoning: $('reasoning').value,
    schema: $('useSchema').checked ? $('schema').value : null,
  };
  $('go').disabled = true; $('out').innerHTML = '';
  const t0 = Date.now();
  const tick = setInterval(() => {
    $('status').className = 'idle';
    $('status').textContent = 'Running ' + body.backend + '... '
      + ((Date.now() - t0) / 1000).toFixed(1)
      + 's (cold CLI process, 10-40s is normal)';
  }, 100);

  let d;
  try {
    const r = await fetch('/api/generate',
      {method: 'POST', body: JSON.stringify(body)});
    d = await r.json();
  } catch (err) {
    d = {ok: false, error: String(err), duration_s: (Date.now() - t0) / 1000};
  }
  clearInterval(tick); $('go').disabled = false;

  $('status').className = '';
  $('status').innerHTML =
      '<span class="pill ' + (d.ok ? 'ok' : 'bad') + '">'
    + (d.ok ? 'OK' : 'FAILED') + '</span>'
    + '<span class="pill meta">' + esc(d.backend || body.backend) + '</span>'
    + '<span class="pill meta">' + (d.duration_s || 0).toFixed(1) + 's</span>';

  let h = '';
  if (d.error) h += '<h3>Error</h3><pre>' + esc(d.error) + '</pre>';
  if (d.argv && d.argv.length)
    h += '<h3>Command executed</h3><pre>' + esc(d.argv.join(' ')) + '</pre>';
  if (d.text) h += '<h3>Text</h3><pre>' + esc(d.text) + '</pre>';
  if (d.structured != null)
    h += '<h3>Structured output</h3><pre>'
       + esc(JSON.stringify(d.structured, null, 2)) + '</pre>';
  if (d.usage) h += '<h3>Tokens</h3><pre>'
       + esc(JSON.stringify(d.usage, null, 2)) + '</pre>';
  $('out').innerHTML = h;
};
</script>
""".replace("__SCHEMA__", DEFAULT_SCHEMA)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the console readable
        pass

    def _send(self, code: int, ctype: str, payload: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, obj: dict) -> None:
        self._send(200, "application/json", json.dumps(obj).encode("utf-8"))

    def do_GET(self):  # noqa: N802 - name required by BaseHTTPRequestHandler
        if self.path.split("?")[0] != "/":
            return self._send(404, "text/plain", b"not found")
        self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))

    def do_POST(self):  # noqa: N802
        if self.path != "/api/generate":
            return self._send(404, "text/plain", b"not found")

        length = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(length) or b"{}")

        schema = None
        if req.get("schema"):
            try:
                schema = json.loads(req["schema"])
            except json.JSONDecodeError as exc:
                return self._json({"ok": False,
                                   "error": f"schema is not valid JSON: {exc}"})

        model = req.get("model") or None
        system = req.get("system") or None
        if req.get("backend") == "codex":
            llm = CodexCLI(model=model, system=system,
                           reasoning_effort=req.get("reasoning") or "low")
        else:
            llm = ClaudeCLI(model=model or "sonnet", system=system)

        print(f"  -> {llm.name}  schema={schema is not None}  "
              f"prompt={req.get('prompt', '')[:60]!r}")
        try:
            r = llm.generate(req.get("prompt", ""), schema=schema)
        except LLMError as exc:
            print(f"  <- FAILED: {exc}")
            return self._json({"ok": False, "error": str(exc),
                               "backend": llm.name})

        print(f"  <- ok in {r.duration_s:.1f}s")
        self._json({
            "ok": True,
            "text": r.text,
            "structured": r.structured,
            "error": "",
            "backend": r.backend,
            "duration_s": r.duration_s,
            "argv": r.argv,
            "usage": dataclasses.asdict(r.usage) if r.usage else None,
        })


def serve(port: int = 8765, open_browser: bool = True) -> None:
    """Start the playground and block until interrupted."""
    url = f"http://localhost:{port}"
    print(f"devllm playground -> {url}   (ctrl-c to stop)")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()

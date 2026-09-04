"""The browser smoke-test page. Serving is handled by api.py."""

from __future__ import annotations

import json

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
    backend: $('backend').value,
    model: $('model').value.trim(),
    reasoning: $('reasoning').value,
  };
  const sys = $('system').value.trim();
  if (sys) body.system = sys;
  if ($('useSchema').checked) {
    try {
      body.schema = JSON.parse($('schema').value);
    } catch (err) {
      $('status').className = '';
      $('status').innerHTML = '<span class="pill bad">FAILED</span>';
      $('out').innerHTML = '<h3>Error</h3><pre>'
        + esc('Schema is not valid JSON: ' + err) + '</pre>';
      return;
    }
  }
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
    // The Content-Type is required: the server rejects anything else with a
    // 415, which is what stops a drive-by POST from another origin.
    const r = await fetch('/generate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
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

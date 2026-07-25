"""Print Wizard -- full web UI (Phase 4).

Flow: pick filament + plate + quality, upload an STL, slice with CuraEngine,
review the estimate + generated G-code (the "cool?" step), send to OctoPrint,
then a gated "Start print" that refuses while the printer is busy.

Bound to 127.0.0.1 only (localhost). For Pi deployment, bind to the Pi's LAN
address and put it behind the same care. Reuses the compiler/slicer/octoprint
modules and ~/.octoprint/creds.

Run:  python3 src/server.py    then open http://127.0.0.1:8765
"""
from __future__ import annotations

import os
import pathlib
import tempfile

from flask import Flask, request, jsonify, Response

from compiler import compile_all, list_profiles, compile_profile, start_gcode, end_gcode
from slicer import slice_model
from octoprint_client import OctoPrintClient

app = Flask(__name__)

UPLOAD_DIR = pathlib.Path(__file__).resolve().parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# Single-user local wizard: remember the last slice so /api/send can upload it.
_LAST = {"gcode": None, "filename": None, "merged": None}
_OCTO = None


def octo():
    global _OCTO
    if _OCTO is None:
        _OCTO = OctoPrintClient()
    return _OCTO


PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Print Wizard</title>
<style>
  :root { color-scheme: light dark; }
  body { margin: 0; font: 14px/1.5 -apple-system, system-ui, sans-serif;
         background: #f6f7f9; color: #1a1a1a; }
  header { background: #111; color: #fff; padding: 14px 20px; }
  header b { font-size: 16px; } header span { opacity: .55; margin-left: 8px; }
  main { max-width: 1100px; margin: 0 auto; padding: 20px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  @media (max-width: 820px) { .grid { grid-template-columns: 1fr; } }
  .panel { background: #fff; border: 1px solid #e2e4e8; border-radius: 10px; padding: 16px; }
  .row { display: flex; gap: 12px; flex-wrap: wrap; align-items: end; }
  label { display: block; font-weight: 600; margin: 10px 0 4px; }
  select, input[type=file] { padding: 8px 10px; font-size: 14px; border: 1px solid #ccc;
           border-radius: 6px; background: #fff; color: #1a1a1a; min-width: 180px; }
  button { padding: 9px 18px; font-size: 14px; font-weight: 600; border: 0;
           border-radius: 6px; background: #2563eb; color: #fff; cursor: pointer; }
  button:disabled { background: #999; cursor: not-allowed; }
  button.green { background: #16a34a; }
  button.amber { background: #d97706; }
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
           gap: 8px; margin: 10px 0; }
  .card { background: #f0f2f5; border-radius: 8px; padding: 8px 10px; }
  .card .k { font-size: 10px; text-transform: uppercase; color: #667; letter-spacing: .04em; }
  .card .v { font-size: 16px; font-weight: 600; }
  pre { background: #0d1117; color: #c9d1d9; padding: 12px; border-radius: 8px;
        overflow-x: auto; white-space: pre-wrap; word-break: break-word; max-height: 240px; }
  h3 { margin: 16px 0 6px; font-size: 13px; text-transform: uppercase; color: #555; }
  .msg { padding: 8px 12px; border-radius: 6px; margin: 8px 0; }
  .ok { background: #dcfce7; color: #166534; } .err { background: #fee2e2; color: #991b1b; }
  .warn { background: #fef3c7; color: #92400e; }
  .status { font-size: 13px; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-weight: 600; }
  .pill.busy { background: #fef3c7; color: #92400e; } .pill.idle { background: #dcfce7; color: #166534; }
</style></head><body>
<header><b>Print Wizard</b><span>local &middot; Ender-3-2024 &middot; OctoPrint</span></header>
<main>
<div class="grid">
  <div class="panel">
    <h3>1. Choose</h3>
    <div class="row">
      <div><label>Filament</label><select id="fil">__FIL_OPTS__</select></div>
      <div><label>Plate</label><select id="pla">__PLA_OPTS__</select></div>
      <div><label>Quality</label><select id="qua">__QUA_OPTS__</select></div>
    </div>
    <label>Model (STL)</label><input type="file" id="stl" accept=".stl">
    <div style="margin-top:14px"><button id="slice" onclick="doSlice()">Slice</button></div>
    <div id="sliceMsg"></div>

    <h3>2. Review</h3>
    <div class="cards" id="cards"></div>
    <h3>start.gcode</h3><pre id="start"></pre>
    <h3>end.gcode</h3><pre id="end"></pre>
    <h3>sliced gcode (head)</h3><pre id="head"></pre>
  </div>

  <div class="panel">
    <h3>Printer status</h3>
    <div id="status" class="status">loading...</div>

    <h3>3. Send to OctoPrint</h3>
    <button id="send" class="amber" onclick="doSend()" disabled>Send G-code</button>
    <div id="sendMsg"></div>

    <h3>4. Print it?</h3>
    <p style="font-size:13px;color:#555">Insert the filament tip into the feeder, then confirm.</p>
    <button id="start" class="green" onclick="doStart()" disabled>Yes, start print</button>
    <div id="startMsg"></div>
  </div>
</div>
</main>
<script>
function el(id){return document.getElementById(id);}
function setMsg(id, cls, text){ el(id).innerHTML = '<div class="msg '+cls+'">'+text+'</div>'; }

async function doSlice(){
  const stl = el('stl').files[0];
  if (!stl) { setMsg('sliceMsg','err','Pick an STL first.'); return; }
  const fd = new FormData();
  fd.append('file', stl);
  fd.append('filament', el('fil').value);
  fd.append('plate', el('pla').value);
  fd.append('quality', el('qua').value);
  el('slice').disabled = true; setMsg('sliceMsg','warn','Slicing...');
  try {
    const r = await fetch('/api/slice', {method:'POST', body: fd});
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || ('HTTP '+r.status));
    const m = d.merged;
    const cards = ['nozzle_temp','bed_temp','layer_height','speed','infill','walls','adhesion','removal_temp'];
    el('cards').innerHTML = cards.map(k => '<div class="card"><div class="k">'+k+'</div><div class="v">'+m[k]+'</div></div>').join('');
    el('start').textContent = d.start_gcode;
    el('end').textContent = d.end_gcode;
    el('head').textContent = d.gcode_head;
    setMsg('sliceMsg','ok','Sliced: ' + d.estimates.layers + ' layers, est ' + fmtTime(d.estimates.time_seconds) + ', ' + (d.estimates.filament_g||d.estimates.filament_m||'?') + (d.estimates.filament_g?'g':'m') + ' &rarr; ' + d.filename);
    el('send').disabled = false; el('start').disabled = true;
    window._lastFile = d.octoprint_name;
  } catch(e){ setMsg('sliceMsg','err', e.message); }
  finally { el('slice').disabled = false; }
}
async function doSend(){
  el('send').disabled = true; setMsg('sendMsg','warn','Uploading...');
  try {
    const r = await fetch('/api/send', {method:'POST'});
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || ('HTTP '+r.status));
    setMsg('sendMsg','ok','Sent as <b>'+d.octoprint_file+'</b> on OctoPrint.');
    el('start').disabled = false;
    window._lastFile = d.octoprint_file;
  } catch(e){ setMsg('sendMsg','err', e.message); }
  finally { el('send').disabled = false; }
}
async function doStart(){
  el('start').disabled = true; setMsg('startMsg','warn','Checking printer & starting...');
  try {
    const r = await fetch('/api/start', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({target: window._lastFile})});
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || ('HTTP '+r.status));
    setMsg('startMsg','ok','Print started.');
  } catch(e){ setMsg('startMsg','err', e.message); }
  finally { el('start').disabled = false; }
}
async function pollStatus(){
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    const cls = d.busy ? 'busy' : 'idle';
    let html = '<span class="pill '+cls+'">'+d.state+'</span> ';
    if (d.busy && d.completion != null) html += (d.completion.toFixed(1)+'% done, ' + fmtTime(d.print_time_left) + ' left &middot; ' + (d.file||''));
    const t = d.temps || {};
    const temps = Object.keys(t).map(k => k+': '+t[k].actual+'C/'+(t[k].target ?? '-')+'C').join(' &nbsp; ');
    el('status').innerHTML = html + '<br><span style="color:#555">'+temps+'</span>';
  } catch(e){ el('status').innerHTML = '<span class="err">'+e.message+'</span>'; }
}
function fmtTime(s){ if (s==null) return '?'; const m=Math.round(s/60); return Math.floor(m/60)+'h'+(m%60)+'m'; }
pollStatus(); setInterval(pollStatus, 4000);
</script>
</body></html>"""


def _opts(names, selected=None):
    return "".join(
        f'<option value="{n}"' + (" selected" if n == selected else "") + f">{n}</option>"
        for n in names
    )


@app.get("/")
def index():
    html = (PAGE
            .replace("__FIL_OPTS__", _opts(list_profiles("filaments"), list_profiles("filaments")[0]))
            .replace("__PLA_OPTS__", _opts(list_profiles("plates"), "glass-stock"))
            .replace("__QUA_OPTS__", _opts(list_profiles("qualities"), "standard")))
    return Response(html, mimetype="text/html")


@app.post("/api/slice")
def api_slice():
    f = request.files.get("file")
    if not f:
        return jsonify(error="no file"), 400
    filament = request.form.get("filament")
    plate = request.form.get("plate")
    quality = request.form.get("quality")
    bundle = compile_all(filament, plate, quality)
    m = bundle["merged"]

    stl_path = UPLOAD_DIR / f"{pathlib.Path(f.filename).stem}.stl"
    f.save(stl_path)
    octo_name = f"{pathlib.Path(f.filename).stem}__{filament}_{plate}_{quality}.gcode"
    gcode_path = UPLOAD_DIR / octo_name
    try:
        res = slice_model(m, bundle["start_gcode"], bundle["end_gcode"], str(stl_path), str(gcode_path))
    except Exception as e:
        return jsonify(error=str(e)), 500

    _LAST.update(gcode=str(gcode_path), filename=octo_name, merged=m)
    head = pathlib.Path(gcode_path).read_text(errors="replace").splitlines()[:25]
    return jsonify(
        merged=m,
        start_gcode=bundle["start_gcode"],
        end_gcode=bundle["end_gcode"],
        gcode_head="\n".join(head),
        estimates={k: res.get(k) for k in ("time_seconds", "layers", "filament_m", "filament_g")},
        filename=octo_name,
        octoprint_name=octo_name,
    )


@app.post("/api/send")
def api_send():
    if not _LAST["gcode"]:
        return jsonify(error="nothing sliced yet"), 400
    try:
        out = octo().upload(_LAST["gcode"], select=False, print=False)
    except Exception as e:
        return jsonify(error=str(e)), 502
    op_file = out.get("files", {}).get("local", {}).get("path", _LAST["filename"])
    _LAST["filename"] = op_file
    return jsonify(ok=True, octoprint_file=op_file)


@app.post("/api/start")
def api_start():
    target = (request.get_json(silent=True) or {}).get("target") or _LAST["filename"]
    if not target:
        return jsonify(error="no file to start"), 400
    try:
        octo().start_print(target)
    except RuntimeError as e:
        return jsonify(error=str(e), state=octo().status().get("state")), 409
    except Exception as e:
        return jsonify(error=str(e)), 502
    return jsonify(ok=True, target=target)


@app.get("/api/status")
def api_status():
    try:
        return jsonify(octo().status())
    except Exception as e:
        return jsonify(error=str(e)), 502


if __name__ == "__main__":
    host = os.environ.get("WIZARD_HOST", "127.0.0.1")
    port = int(os.environ.get("WIZARD_PORT", "8765"))
    app.run(host=host, port=port, debug=False)

"""Print Wizard -- web UI (Tailwind), on the Pi.

Flow: choose filament/plate/quality/adhesion/supports + orient the model +
upload STL -> slice (CuraEngine) -> review -> send to OctoPrint -> gated start.
Live machine panel: layer-stack progress, thermal strip, camera snapshot.

Bound to 0.0.0.0:8765 on the Pi (WIZARD_HOST/WIZARD_PORT env).
"""
from __future__ import annotations

import os
import pathlib

from flask import Flask, request, jsonify, Response

from compiler import compile_all, list_profiles
from slicer import slice_model
from octoprint_client import OctoPrintClient
import stl_transform

app = Flask(__name__)
UPLOAD_DIR = pathlib.Path(__file__).resolve().parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

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
<script src="https://cdn.tailwindcss.com"></script>
<script>
tailwind.config = { theme: { extend: { colors: {
  ink:'#0E1116', panel:'#161B22', panel2:'#1F2630', steel:'#9AA4B2',
  molten:'#FF6A3D', filament:'#E8D9B5', ok:'#5FB47A', line:'#2A313C'
}, fontFamily: { display:['"Space Grotesk"','ui-sans-serif','system-ui'],
  mono:['"JetBrains Mono"','ui-monospace','monospace'] } } } }
</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
  html,body{background:#0E1116}
  ::selection{background:#FF6A3D;color:#0E1116}
  .stack-seg{transition:background .4s ease}
  pre{scrollbar-width:thin;scrollbar-color:#2A313C transparent}
  pre::-webkit-scrollbar{height:8px;width:8px}
  pre::-webkit-scrollbar-thumb{background:#2A313C;border-radius:4px}
  input[type=file]::file-selector-button{background:#1F2630;color:#E8D9B5;border:1px solid #2A313C;border-radius:6px;padding:6px 10px;margin-right:10px}
  @media (prefers-reduced-motion: reduce){ .stack-seg{transition:none} }
</style>
</head><body class="bg-ink text-steel font-display min-h-screen">
<header class="border-b border-line">
  <div class="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between gap-4 flex-wrap">
    <div class="flex items-baseline gap-3">
      <span class="text-filament text-xl font-700 tracking-tight">PRINT WIZARD</span>
      <span class="text-steel/60 text-xs font-mono">Ender-3-2024 &middot; OctoPrint</span>
    </div>
    <div class="flex items-center gap-4 font-mono text-xs">
      <div id="hdrState" class="px-2 py-1 rounded-full border border-line text-steel">--</div>
      <div id="hdrTemp" class="text-steel/70">--/--</div>
    </div>
  </div>
</header>

<main class="max-w-6xl mx-auto px-6 py-8 grid lg:grid-cols-[1fr_360px] gap-8">

  <!-- LEFT: workflow -->
  <div class="space-y-8">
    <section>
      <h2 class="text-steel/50 text-xs font-mono uppercase tracking-[0.2em] mb-4">01 / Choose</h2>
      <div class="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <div><label class="block text-steel/60 text-xs mb-1">Filament</label><select id="fil" class="w-full bg-panel border border-line rounded-lg px-3 py-2 text-sm text-filament">__FIL_OPTS__</select></div>
        <div><label class="block text-steel/60 text-xs mb-1">Plate</label><select id="pla" class="w-full bg-panel border border-line rounded-lg px-3 py-2 text-sm text-filament">__PLA_OPTS__</select></div>
        <div><label class="block text-steel/60 text-xs mb-1">Quality</label><select id="qua" class="w-full bg-panel border border-line rounded-lg px-3 py-2 text-sm text-filament">__QUA_OPTS__</select></div>
        <div><label class="block text-steel/60 text-xs mb-1">Adhesion</label><select id="adh" class="w-full bg-panel border border-line rounded-lg px-3 py-2 text-sm text-filament"><option value="auto">auto (plate)</option><option value="none">none</option><option value="skirt">skirt</option><option value="brim">brim</option><option value="raft">raft</option></select></div>
        <div><label class="block text-steel/60 text-xs mb-1">Supports</label><select id="sup" class="w-full bg-panel border border-line rounded-lg px-3 py-2 text-sm text-filament"><option value="off">off</option><option value="on">on</option></select></div>
        <div class="flex items-end"><div class="text-steel/40 text-[11px] font-mono leading-tight">plate adjusts bed temp,<br>adhesion &amp; removal cool</div></div>
      </div>

      <div class="mt-5">
        <label class="block text-steel/60 text-xs mb-2">Orientation <span id="rotLabel" class="font-mono text-steel/40">as imported</span></label>
        <div class="flex flex-wrap gap-2">
          <button onclick="rot('x',1)" class="px-3 py-1.5 text-xs font-mono bg-panel2 border border-line rounded-md hover:border-molten">Rot X +90</button>
          <button onclick="rot('y',1)" class="px-3 py-1.5 text-xs font-mono bg-panel2 border border-line rounded-md hover:border-molten">Rot Y +90</button>
          <button onclick="rot('z',1)" class="px-3 py-1.5 text-xs font-mono bg-panel2 border border-line rounded-md hover:border-molten">Rot Z +90</button>
          <button onclick="layFlat()" class="px-3 py-1.5 text-xs font-mono bg-filament/10 text-filament border border-filament/30 rounded-md hover:border-filament">Lay flat</button>
          <button onclick="rotReset()" class="px-3 py-1.5 text-xs font-mono text-steel/50 border border-line rounded-md hover:text-filament">Reset</button>
        </div>
      </div>

      <div class="mt-5">
        <label class="block text-steel/60 text-xs mb-2">Model (STL)</label>
        <input type="file" id="stl" accept=".stl" class="text-sm text-steel w-full">
      </div>
      <div class="mt-5 flex items-center gap-3">
        <button id="slice" onclick="doSlice()" class="px-5 py-2.5 rounded-lg bg-molten text-ink font-600 text-sm hover:brightness-110">Slice</button>
        <span id="sliceMsg" class="text-sm"></span>
      </div>
    </section>

    <section id="review" class="hidden">
      <h2 class="text-steel/50 text-xs font-mono uppercase tracking-[0.2em] mb-4">02 / Review</h2>
      <div id="cards" class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4"></div>
      <div class="grid sm:grid-cols-3 gap-3">
        <div><h3 class="text-steel/40 text-[11px] font-mono mb-1">start.gcode</h3><pre id="start" class="bg-panel border border-line rounded-lg p-3 text-[11px] font-mono text-steel/90 max-h-48 overflow-auto"></pre></div>
        <div><h3 class="text-steel/40 text-[11px] font-mono mb-1">end.gcode</h3><pre id="end" class="bg-panel border border-line rounded-lg p-3 text-[11px] font-mono text-steel/90 max-h-48 overflow-auto"></pre></div>
        <div><h3 class="text-steel/40 text-[11px] font-mono mb-1">sliced (head)</h3><pre id="head" class="bg-panel border border-line rounded-lg p-3 text-[11px] font-mono text-steel/90 max-h-48 overflow-auto"></pre></div>
      </div>
    </section>

    <section id="sendSection" class="hidden">
      <h2 class="text-steel/50 text-xs font-mono uppercase tracking-[0.2em] mb-4">03 / Send &amp; print</h2>
      <div class="flex flex-wrap items-center gap-3">
        <button id="send" onclick="doSend()" class="px-4 py-2 rounded-lg bg-panel2 border border-line text-filament text-sm font-600 hover:border-filament">Send G-code to OctoPrint</button>
        <button id="start" onclick="doStart()" class="px-4 py-2 rounded-lg bg-ok/15 border border-ok/40 text-ok text-sm font-600 hover:bg-ok/25">Print it? Yes</button>
      </div>
      <p class="text-steel/50 text-xs mt-3 max-w-md">Insert the filament tip into the feeder, then confirm. Start is blocked while the printer is busy.</p>
      <div id="sendMsg" class="text-sm mt-2"></div>
      <div id="startMsg" class="text-sm mt-2"></div>
    </section>
  </div>

  <!-- RIGHT: machine -->
  <aside class="space-y-6">
    <section class="bg-panel border border-line rounded-xl p-5">
      <h2 class="text-steel/50 text-xs font-mono uppercase tracking-[0.2em] mb-4">Machine</h2>
      <div class="flex gap-5">
        <div id="stack" class="flex flex-col-reverse gap-[2px] w-12 h-40 bg-panel2/40 rounded-md p-1"></div>
        <div class="flex-1">
          <div class="text-filament text-3xl font-700 font-mono" id="compPct">--%</div>
          <div class="text-steel/50 text-xs font-mono" id="compState">idle</div>
          <div class="text-steel/60 text-xs font-mono mt-2" id="compFile">&nbsp;</div>
          <div class="text-steel/60 text-xs font-mono" id="compLeft">&nbsp;</div>
        </div>
      </div>
      <div id="thermal" class="mt-5 space-y-3"></div>
    </section>

    <section class="bg-panel border border-line rounded-xl p-5">
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-steel/50 text-xs font-mono uppercase tracking-[0.2em]">Camera</h2>
        <label class="text-steel/60 text-[11px] font-mono flex items-center gap-1"><input type="checkbox" id="camAuto" checked class="accent-molten"> auto</label>
      </div>
      <div class="relative aspect-video bg-ink rounded-lg overflow-hidden border border-line">
        <img id="cam" class="w-full h-full object-cover" alt="webcam">
        <div id="camOff" class="absolute inset-0 flex items-center justify-center text-steel/40 text-xs font-mono">camera offline</div>
      </div>
      <button onclick="camRefresh()" class="mt-2 text-[11px] font-mono text-steel/50 hover:text-filament">refresh now</button>
    </section>
  </aside>
</main>

<script>
const el = id => document.getElementById(id);
let rot = {x:0,y:0,z:0}, flatMode = false;

function setMsg(id, cls, text){ el(id).innerHTML = '<span class="'+cls+'">'+text+'</span>'; }
function okCls(t){return '<span class="text-ok">'+t+'</span>';}
function errCls(t){return '<span class="text-molten">'+t+'</span>';}
function warnCls(t){return '<span class="text-filament">'+t+'</span>';}

function rotLabel(){
  if (flatMode) return 'lay flat';
  const parts = [];
  if(rot.x) parts.push('X'+rot.x); if(rot.y) parts.push('Y'+rot.y); if(rot.z) parts.push('Z'+rot.z);
  return parts.length ? parts.join(' ')+' ×90°' : 'as imported';
}
function updateRotLabel(){ el('rotLabel').textContent = rotLabel(); }
function rot(axis,n){ flatMode=false; rot[axis]=(rot[axis]+n)%4; updateRotLabel(); if(el('stl').files[0]) doSlice(); }
function rotReset(){ flatMode=false; rot={x:0,y:0,z:0}; updateRotLabel(); if(el('stl').files[0]) doSlice(); }
function layFlat(){ flatMode=true; rot={x:0,y:0,z:0}; updateRotLabel(); if(el('stl').files[0]) doSlice(); }

async function doSlice(){
  const stl = el('stl').files[0];
  if(!stl){ setMsg('sliceMsg','text-molten','pick an STL first.'); return; }
  const fd = new FormData();
  fd.append('file', stl);
  fd.append('filament', el('fil').value); fd.append('plate', el('pla').value); fd.append('quality', el('qua').value);
  fd.append('adhesion', el('adh').value); fd.append('supports', el('sup').value);
  fd.append('rx', rot.x); fd.append('ry', rot.y); fd.append('rz', rot.z);
  fd.append('flat', flatMode ? '1' : '0');
  el('slice').disabled = true; setMsg('sliceMsg','text-filament','slicing…');
  try{
    const r = await fetch('/api/slice',{method:'POST',body:fd});
    const d = await r.json(); if(!r.ok) throw new Error(d.error||('HTTP '+r.status));
    const m = d.merged;
    const cards = [['layers',d.estimates.layers],['est. time',fmtTime(d.estimates.time_seconds)],
      ['nozzle',m.nozzle_temp+'°C'],['bed',m.bed_temp+'°C'],
      ['adhesion',m.adhesion],['supports',m.support_enable?'on':'off']];
    el('cards').innerHTML = cards.map(c=>'<div class="bg-panel border border-line rounded-lg p-3"><div class="text-steel/40 text-[10px] font-mono uppercase tracking-wider">'+c[0]+'</div><div class="text-filament text-lg font-mono">'+c[1]+'</div></div>').join('');
    el('start').textContent = d.start_gcode; el('end').textContent = d.end_gcode; el('head').textContent = d.gcode_head;
    el('review').classList.remove('hidden'); el('sendSection').classList.remove('hidden');
    setMsg('sliceMsg','text-ok','sliced → '+d.filename);
    el('send').disabled=false; el('start').disabled=true; window._lastFile=d.octoprint_name;
  }catch(e){ setMsg('sliceMsg','text-molten',e.message); }
  finally{ el('slice').disabled=false; }
}
async function doSend(){
  el('send').disabled=true; setMsg('sendMsg','text-filament','uploading…');
  try{
    const r = await fetch('/api/send',{method:'POST'}); const d = await r.json();
    if(!r.ok) throw new Error(d.error||('HTTP '+r.status));
    setMsg('sendMsg','text-ok','sent as '+d.octoprint_file); el('start').disabled=false; window._lastFile=d.octoprint_file;
  }catch(e){ setMsg('sendMsg','text-molten',e.message); } finally{ el('send').disabled=false; }
}
async function doStart(){
  el('start').disabled=true; setMsg('startMsg','text-filament','checking printer…');
  try{
    const r = await fetch('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target:window._lastFile})});
    const d = await r.json(); if(!r.ok) throw new Error(d.error||('HTTP '+r.status));
    setMsg('startMsg','text-ok','print started');
  }catch(e){ setMsg('startMsg','text-molten',e.message); } finally{ el('start').disabled=false; }
}

function fmtTime(s){ if(s==null) return '?'; const m=Math.round(s/60); return Math.floor(m/60)+'h '+(m%60)+'m'; }
function renderStack(comp){ const N=40, f=Math.round((comp||0)/100*N); let h=''; for(let i=0;i<N;i++) h+='<div class="stack-seg h-1 rounded-sm '+(i<f?'bg-molten':'bg-panel2')+'"></div>'; el('stack').innerHTML=h; }
function renderThermal(t){
  const rows = [['tool0','hotend'],['bed','bed']];
  el('thermal').innerHTML = rows.map(([k,label])=>{
    const g=t[k]||{actual:0,target:0}; const tgt=g.target||0; const a=g.actual||0;
    const pct = tgt>0 ? Math.min(100,Math.round(a/tgt*100)) : (a>0?100:0);
    return '<div><div class="flex justify-between text-[11px] font-mono mb-1"><span class="text-steel/50">'+label+'</span><span class="text-filament">'+a+'° / '+tgt+'°</span></div><div class="h-1.5 bg-panel2 rounded-full overflow-hidden"><div class="h-full '+(label==='hotend'?'bg-molten':'bg-filament/70')+'" style="width:'+pct+'%"></div></div></div>';
  }).join('');
}
async function pollStatus(){
  try{
    const d = await (await fetch('/api/status')).json();
    const cls = d.busy ? 'text-filament border-filament/40' : 'text-ok border-ok/40';
    el('hdrState').className = 'px-2 py-1 rounded-full border font-mono text-xs ' + cls;
    el('hdrState').textContent = d.state;
    const h = (d.temps.tool0||{}), b=(d.temps.bed||{});
    el('hdrTemp').textContent = 'hotend '+(h.actual||0)+'° / bed '+(b.actual||0)+'°';
    el('compPct').textContent = (d.completion!=null?d.completion.toFixed(1):'--')+'%';
    el('compState').textContent = d.busy ? 'printing' : 'idle';
    el('compFile').textContent = d.file||'';
    el('compLeft').textContent = d.busy ? fmtTime(d.print_time_left)+' left' : '';
    renderStack(d.completion); renderThermal(d.temps);
  }catch(e){}
}
pollStatus(); setInterval(pollStatus,4000);

function camRefresh(){ el('cam').src = '/api/camera/snapshot?ts='+Date.now(); }
el('cam').addEventListener('error', ()=>{ el('camOff').classList.remove('hidden'); el('cam').style.opacity=0; });
el('cam').addEventListener('load', ()=>{ el('camOff').classList.add('hidden'); el('cam').style.opacity=1; });
camRefresh(); setInterval(()=>{ if(el('camAuto').checked) camRefresh(); }, 8000);
updateRotLabel();
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
    adhesion = request.form.get("adhesion") or "auto"
    supports = request.form.get("supports") == "on"
    rx = int(request.form.get("rx", 0) or 0)
    ry = int(request.form.get("ry", 0) or 0)
    rz = int(request.form.get("rz", 0) or 0)
    flat = request.form.get("flat") == "1"

    bundle = compile_all(filament, plate, quality)
    m = bundle["merged"]
    if adhesion != "auto":
        m["adhesion"] = adhesion
    m["support_enable"] = supports

    stl_path = UPLOAD_DIR / f"{pathlib.Path(f.filename).stem}.stl"
    f.save(stl_path)
    if flat:
        stl_path.write_bytes(stl_transform.lay_flat(stl_path.read_bytes()))
    elif rx or ry or rz:
        stl_path.write_bytes(stl_transform.rotate(stl_path.read_bytes(), rx, ry, rz))

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


@app.get("/api/camera/snapshot")
def api_camera():
    try:
        r = octo().s.get(f"{octo().host}/webcam/?action=snapshot", timeout=8)
        if r.status_code != 200 or not r.content:
            return Response(b"", status=502, mimetype="image/jpeg")
        return Response(r.content, mimetype="image/jpeg")
    except Exception:
        return Response(b"", status=502, mimetype="image/jpeg")


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

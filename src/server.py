"""Print Wizard -- web UI (Tailwind + Three.js STL viewer), on the Pi.

Flow: choose filament/plate/quality/adhesion/supports -> load STL into the 3D
viewer -> orient (rotate / lay flat,所见即所印) -> slice (CuraEngine) -> review
-> send to OctoPrint -> gated start. Live machine panel: layer-stack progress,
thermal strip, camera snapshot.

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
  #viewer canvas{display:block}
  @media (prefers-reduced-motion: reduce){ .stack-seg{transition:none} }
</style>
<script type="importmap">
{ "imports": {
  "three": "https://esm.sh/three@0.160.0",
  "three/addons/": "https://esm.sh/three@0.160.0/examples/jsm/"
}}
</script>
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

      <div class="mt-5 grid sm:grid-cols-[1fr_280px] gap-5">
        <div>
          <label class="block text-steel/60 text-xs mb-2">Model (STL)</label>
          <input type="file" id="stl" accept=".stl" class="text-sm text-steel w-full">
          <div class="mt-4">
            <label class="block text-steel/60 text-xs mb-2">Orientation <span id="rotLabel" class="font-mono text-steel/40">as imported</span></label>
            <div class="flex flex-wrap gap-2">
              <button id="rotX" class="px-3 py-1.5 text-xs font-mono bg-panel2 border border-line rounded-md hover:border-molten">Rot X +90</button>
              <button id="rotY" class="px-3 py-1.5 text-xs font-mono bg-panel2 border border-line rounded-md hover:border-molten">Rot Y +90</button>
              <button id="rotZ" class="px-3 py-1.5 text-xs font-mono bg-panel2 border border-line rounded-md hover:border-molten">Rot Z +90</button>
              <button id="layFlat" class="px-3 py-1.5 text-xs font-mono bg-filament/10 text-filament border border-filament/30 rounded-md hover:border-filament">Lay flat</button>
              <button id="rotReset" class="px-3 py-1.5 text-xs font-mono text-steel/50 border border-line rounded-md hover:text-filament">Reset</button>
            </div>
            <p class="text-steel/40 text-[11px] font-mono mt-2">drag to orbit &middot; scroll to zoom &middot; what you see is what prints</p>
          </div>
          <div class="mt-5 flex items-center gap-3">
            <button id="slice" class="px-5 py-2.5 rounded-lg bg-molten text-ink font-600 text-sm hover:brightness-110">Slice</button>
            <span id="sliceMsg" class="text-sm"></span>
          </div>
        </div>
        <div>
          <label class="block text-steel/60 text-xs mb-2">Preview</label>
          <div id="viewer" class="aspect-square bg-panel border border-line rounded-lg overflow-hidden"></div>
        </div>
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
        <button id="send" class="px-4 py-2 rounded-lg bg-panel2 border border-line text-filament text-sm font-600 hover:border-filament">Send G-code to OctoPrint</button>
        <button id="start" class="px-4 py-2 rounded-lg bg-ok/15 border border-ok/40 text-ok text-sm font-600 hover:bg-ok/25">Print it? Yes</button>
      </div>
      <p class="text-steel/50 text-xs mt-3 max-w-md">Insert the filament tip into the feeder, then confirm. Start is blocked while the printer is busy.</p>
      <div id="sendMsg" class="text-sm mt-2"></div>
      <div id="startMsg" class="text-sm mt-2"></div>
    </section>
  </div>

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
      <button id="camRefresh" class="mt-2 text-[11px] font-mono text-steel/50 hover:text-filament">refresh now</button>
    </section>
  </aside>
</main>

<script type="module">
import * as THREE from 'three';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const el = id => document.getElementById(id);
const BED = 235;
let rot = {x:0,y:0,z:0}, flatMode = false;

function setMsg(id, cls, text){ el(id).innerHTML = '<span class="'+cls+'">'+text+'</span>'; }
function fmtTime(s){ if(s==null) return '?'; const m=Math.round(s/60); return Math.floor(m/60)+'h '+(m%60)+'m'; }

/* ---- 3D viewer (所见即所印: same rotations as the slicer) ---- */
let scene, camera, renderer, controls, mesh, baseGeo=null;
const rxV=(v,t)=>{t%=4;const[x,y,z]=v;return t===0?[x,y,z]:t===1?[x,-z,y]:t===2?[x,-y,-z]:[x,z,-y]};
const ryV=(v,t)=>{t%=4;const[x,y,z]=v;return t===0?[x,y,z]:t===1?[z,y,-x]:t===2?[-x,y,-z]:[-z,y,x]};
const rzV=(v,t)=>{t%=4;const[x,y,z]=v;return t===0?[x,y,z]:t===1?[-y,x,z]:t===2?[-x,-y,z]:[y,-x,z]};
function applyRot(pos, x,y,z){ const out=new Float32Array(pos.length); for(let i=0;i<pos.length;i+=3){ let v=pos.subarray(i,i+3); v=rxV(v,x); v=ryV(v,y); v=rzV(v,z); out[i]=v[0];out[i+1]=v[1];out[i+2]=v[2]; } return out; }
function bboxZ(pos){ let mn=Infinity,mx=-Infinity; for(let i=2;i<pos.length;i+=3){ if(pos[i]<mn)mn=pos[i]; if(pos[i]>mx)mx=pos[i]; } return mx-mn; }

function initViewer(){
  const wrap = el('viewer');
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x161B22);
  camera = new THREE.PerspectiveCamera(45, 1, 0.1, 2000);
  camera.position.set(180, 200, 260);
  renderer = new THREE.WebGLRenderer({antialias:true});
  renderer.setSize(wrap.clientWidth, wrap.clientWidth);
  wrap.appendChild(renderer.domElement);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(BED/2, 0, BED/2);
  controls.update();
  scene.add(new THREE.AmbientLight(0xffffff, 0.55));
  const dl = new THREE.DirectionalLight(0xffffff, 1.0); dl.position.set(150,250,180); scene.add(dl);
  const dl2 = new THREE.DirectionalLight(0xff6a3d, 0.25); dl2.position.set(-150,120,-120); scene.add(dl2);
  // bed
  const bed = new THREE.Mesh(new THREE.PlaneGeometry(BED,BED), new THREE.MeshStandardMaterial({color:0x0E1116,metalness:0,roughness:1}));
  bed.rotation.x = -Math.PI/2; bed.position.set(BED/2,0,BED/2); scene.add(bed);
  const grid = new THREE.GridHelper(BED, 23, 0x2A313C, 0x2A313C); grid.position.set(BED/2,0.1,BED/2); scene.add(grid);
  const resize=()=>{ const w=wrap.clientWidth; renderer.setSize(w,w); camera.aspect=1; camera.updateProjectionMatrix(); };
  new ResizeObserver(resize).observe(wrap);
  (function animate(){ requestAnimationFrame(animate); controls.update(); renderer.render(scene,camera); })();
}

function loadSTL(file){
  const r = new FileReader();
  r.onload = e => {
    let geo;
    try { geo = new STLLoader().parse(e.target.result); }
    catch(err){ setMsg('sliceMsg','text-molten','could not parse STL: '+err.message); return; }
    geo.deleteAttribute('normal'); geo.computeVertexNormals();
    baseGeo = geo.attributes.position.array.slice();  // original STL coords
    if (mesh) scene.remove(mesh);
    rot={x:0,y:0,z:0}; flatMode=false; updateRotLabel();
    applyOrientation();
  };
  r.readAsArrayBuffer(file);
}

function applyOrientation(){
  if (!baseGeo) return;
  let pos = applyRot(baseGeo, rot.x, rot.y, rot.z);
  // normalize: minZ->0, center XY on bed (matches server _normalize)
  let mnX=Infinity,mxX=-Infinity,mnY=Infinity,mxY=-Infinity,mnZ=Infinity;
  for(let i=0;i<pos.length;i+=3){ const x=pos[i],y=pos[i+1],z=pos[i+2];
    if(x<mnX)mnX=x; if(x>mxX)mxX=x; if(y<mnY)mnY=y; if(y>mxY)mxY=y; if(z<mnZ)mnZ=z; }
  const dx = (BED-(mxX-mnX))/2 - mnX, dy = (BED-(mxY-mnY))/2 - mnY, dz = -mnZ;
  for(let i=0;i<pos.length;i+=3){ pos[i]+=dx; pos[i+1]+=dy; pos[i+2]+=dz; }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos,3));
  geo.computeVertexNormals();
  if (mesh) scene.remove(mesh);
  mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({color:0xE8D9B5, metalness:0.1, roughness:0.6, flatShading:false}));
  mesh.rotation.x = -Math.PI/2;  // STL Z-up -> three Y-up for display
  scene.add(mesh);
}

function layFlatCompute(){
  if (!baseGeo) return;
  let best=null;
  for(let x=0;x<4;x++) for(let y=0;y<4;y++){
    const h = bboxZ(applyRot(baseGeo, x, y, 0));
    if (best===null || h < best.h) best={h,x,y};
  }
  return best;
}

function updateRotLabel(){
  let s = flatMode ? 'lay flat' : (()=>{const p=[];if(rot.x)p.push('X'+rot.x);if(rot.y)p.push('Y'+rot.y);if(rot.z)p.push('Z'+rot.z);return p.length?p.join(' ')+' x90':'as imported';})();
  el('rotLabel').textContent = s;
}
function doRot(axis){ flatMode=false; rot[axis]=(rot[axis]+1)%4; updateRotLabel(); applyOrientation(); }
function doReset(){ flatMode=false; rot={x:0,y:0,z:0}; updateRotLabel(); applyOrientation(); }
function doLayFlat(){ const b=layFlatCompute(); if(!b)return; flatMode=true; rot={x:b.x,y:b.y,z:0}; updateRotLabel(); applyOrientation(); }

/* ---- slice / send / start ---- */
async function doSlice(){
  const stl = el('stl').files[0];
  if(!stl){ setMsg('sliceMsg','text-molten','pick an STL first.'); return; }
  const fd = new FormData();
  fd.append('file', stl);
  fd.append('filament', el('fil').value); fd.append('plate', el('pla').value); fd.append('quality', el('qua').value);
  fd.append('adhesion', el('adh').value); fd.append('supports', el('sup').value);
  fd.append('rx', rot.x); fd.append('ry', rot.y); fd.append('rz', rot.z);
  fd.append('flat', flatMode ? '1' : '0');
  el('slice').disabled=true; setMsg('sliceMsg','text-filament','slicing…');
  try{
    const r = await fetch('/api/slice',{method:'POST',body:fd});
    const d = await r.json(); if(!r.ok) throw new Error(d.error||('HTTP '+r.status));
    const m=d.merged;
    const cards=[['layers',d.estimates.layers],['est. time',fmtTime(d.estimates.time_seconds)],['nozzle',m.nozzle_temp+'°C'],['bed',m.bed_temp+'°C'],['adhesion',m.adhesion],['supports',m.support_enable?'on':'off']];
    el('cards').innerHTML=cards.map(c=>'<div class="bg-panel border border-line rounded-lg p-3"><div class="text-steel/40 text-[10px] font-mono uppercase tracking-wider">'+c[0]+'</div><div class="text-filament text-lg font-mono">'+c[1]+'</div></div>').join('');
    el('start').textContent=d.start_gcode; el('end').textContent=d.end_gcode; el('head').textContent=d.gcode_head;
    el('review').classList.remove('hidden'); el('sendSection').classList.remove('hidden');
    setMsg('sliceMsg','text-ok','sliced → '+d.filename);
    el('send').disabled=false; el('start').disabled=true; window._lastFile=d.octoprint_name;
  }catch(e){ setMsg('sliceMsg','text-molten',e.message); } finally{ el('slice').disabled=false; }
}
async function doSend(){
  el('send').disabled=true; setMsg('sendMsg','text-filament','uploading…');
  try{
    const r=await fetch('/api/send',{method:'POST'}); const d=await r.json();
    if(!r.ok) throw new Error(d.error||('HTTP '+r.status));
    setMsg('sendMsg','text-ok','sent as '+d.octoprint_file); el('start').disabled=false; window._lastFile=d.octoprint_file;
  }catch(e){ setMsg('sendMsg','text-molten',e.message); } finally{ el('send').disabled=false; }
}
async function doStart(){
  el('start').disabled=true; setMsg('startMsg','text-filament','checking printer…');
  try{
    const r=await fetch('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target:window._lastFile})});
    const d=await r.json(); if(!r.ok) throw new Error(d.error||('HTTP '+r.status));
    setMsg('startMsg','text-ok','print started');
  }catch(e){ setMsg('startMsg','text-molten',e.message); } finally{ el('start').disabled=false; }
}

/* ---- status ---- */
function renderStack(comp){ const N=40,f=Math.round((comp||0)/100*N); let h=''; for(let i=0;i<N;i++) h+='<div class="stack-seg h-1 rounded-sm '+(i<f?'bg-molten':'bg-panel2')+'"></div>'; el('stack').innerHTML=h; }
function renderThermal(t){
  const rows=[['tool0','hotend'],['bed','bed']];
  el('thermal').innerHTML=rows.map(([k,label])=>{
    const g=t[k]||{actual:0,target:0}; const tgt=g.target||0,a=g.actual||0;
    const pct=tgt>0?Math.min(100,Math.round(a/tgt*100)):(a>0?100:0);
    return '<div><div class="flex justify-between text-[11px] font-mono mb-1"><span class="text-steel/50">'+label+'</span><span class="text-filament">'+a+'° / '+tgt+'°</span></div><div class="h-1.5 bg-panel2 rounded-full overflow-hidden"><div class="h-full '+(label==='hotend'?'bg-molten':'bg-filament/70')+'" style="width:'+pct+'%"></div></div></div>';
  }).join('');
}
async function pollStatus(){
  try{
    const d=await (await fetch('/api/status')).json();
    const cls=d.busy?'text-filament border-filament/40':'text-ok border-ok/40';
    el('hdrState').className='px-2 py-1 rounded-full border font-mono text-xs '+cls;
    el('hdrState').textContent=d.state;
    const h=(d.temps.tool0||{}),b=(d.temps.bed||{});
    el('hdrTemp').textContent='hotend '+(h.actual||0)+'° / bed '+(b.actual||0)+'°';
    el('compPct').textContent=(d.completion!=null?d.completion.toFixed(1):'--')+'%';
    el('compState').textContent=d.busy?'printing':'idle';
    el('compFile').textContent=d.file||'';
    el('compLeft').textContent=d.busy?fmtTime(d.print_time_left)+' left':'';
    renderStack(d.completion); renderThermal(d.temps);
  }catch(e){}
}

/* ---- camera ---- */
function camRefresh(){ el('cam').src='/api/camera/snapshot?ts='+Date.now(); }
el('cam').addEventListener('error',()=>{ el('camOff').classList.remove('hidden'); el('cam').style.opacity=0; });
el('cam').addEventListener('load',()=>{ el('camOff').classList.add('hidden'); el('cam').style.opacity=1; });

/* ---- wire up ---- */
el('stl').addEventListener('change', e=>{ if(e.target.files[0]) loadSTL(e.target.files[0]); });
el('rotX').addEventListener('click',()=>doRot('x'));
el('rotY').addEventListener('click',()=>doRot('y'));
el('rotZ').addEventListener('click',()=>doRot('z'));
el('layFlat').addEventListener('click',doLayFlat);
el('rotReset').addEventListener('click',doReset);
el('slice').addEventListener('click',doSlice);
el('send').addEventListener('click',doSend);
el('start').addEventListener('click',doStart);
el('camRefresh').addEventListener('click',camRefresh);

initViewer();
updateRotLabel();
pollStatus(); setInterval(pollStatus,4000);
camRefresh(); setInterval(()=>{ if(el('camAuto').checked) camRefresh(); },8000);
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

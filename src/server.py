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
import json
import re

from flask import Flask, request, jsonify, Response

from compiler import compile_all, list_profiles, get_filament
from slicer import slice_model
from octoprint_client import OctoPrintClient
import stl_transform
import aiclient

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
  "three": "https://esm.sh/three@0.183.2",
  "three/addons/": "https://esm.sh/three@0.183.2/examples/jsm/",
  "@jgphilpott/polyslice": "https://unpkg.com/@jgphilpott/polyslice/dist/index.browser.esm.js"
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

      <div class="mt-5 flex flex-wrap items-end gap-2">
        <div class="flex-1 min-w-[200px]"><label class="block text-steel/60 text-xs mb-1">Look up filament <span class="text-molten/70">· AI web search</span></label><input id="filQuery" type="text" placeholder="e.g. Polymaker PolyTerra PLA Black" class="w-full bg-panel border border-line rounded-lg px-3 py-2 text-sm text-filament"></div>
        <button id="filSearch" class="px-3 py-2 rounded-lg bg-panel2 border border-line text-filament text-sm hover:border-molten">Ask Claude</button>
      </div>
      <div id="filResult" class="mt-3 hidden">
        <div class="text-steel/40 text-[11px] font-mono mb-1">review / edit specs (JSON), then save</div>
        <textarea id="filJson" class="w-full bg-panel border border-line rounded-lg p-3 text-[11px] font-mono text-steel/90 h-40"></textarea>
        <div class="flex items-center gap-2 mt-2">
          <input id="filSlug" type="text" placeholder="slug: polymaker-polyterra-pla-black" class="flex-1 bg-panel border border-line rounded-lg px-3 py-2 text-xs font-mono text-filament">
          <button id="filSave" class="px-3 py-2 rounded-lg bg-filament/15 border border-filament/40 text-filament text-sm hover:bg-filament/25">Save profile</button>
        </div>
        <div id="filMsg" class="text-sm mt-2"></div>
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
          <div class="mt-4 flex items-center gap-2">
            <span class="text-steel/50 text-xs font-mono mr-1">slicer</span>
            <button id="slCura" class="px-3 py-1 text-xs font-mono rounded-md border border-molten bg-molten/15 text-filament">CuraEngine (Pi)</button>
            <button id="slPoly" class="px-3 py-1 text-xs font-mono rounded-md border border-line text-steel/60">Polyslice (browser)</button>
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
        <div><h3 class="text-steel/40 text-[11px] font-mono mb-1">start.gcode</h3><pre id="startGcode" class="bg-panel border border-line rounded-lg p-3 text-[11px] font-mono text-steel/90 max-h-48 overflow-auto"></pre></div>
        <div><h3 class="text-steel/40 text-[11px] font-mono mb-1">end.gcode</h3><pre id="end" class="bg-panel border border-line rounded-lg p-3 text-[11px] font-mono text-steel/90 max-h-48 overflow-auto"></pre></div>
        <div><h3 class="text-steel/40 text-[11px] font-mono mb-1">sliced (head)</h3><pre id="head" class="bg-panel border border-line rounded-lg p-3 text-[11px] font-mono text-steel/90 max-h-48 overflow-auto"></pre></div>
      </div>
    </section>

    <section id="sendSection" class="hidden">
      <h2 class="text-steel/50 text-xs font-mono uppercase tracking-[0.2em] mb-4">03 / Print</h2>
      <div id="removalGate" class="hidden mb-3 p-3 rounded-lg border border-filament/40 bg-filament/10 flex items-center justify-between gap-3 flex-wrap">
        <span class="text-filament text-sm">Previous print finished — confirm the bed is clear before starting the next one.</span>
        <button id="printRemoved" class="px-3 py-1.5 rounded-md bg-ok/20 border border-ok/40 text-ok text-sm font-600 hover:bg-ok/30 whitespace-nowrap">Print removed</button>
      </div>
      <div id="sendMsg" class="text-sm mb-2"></div>
      <div class="flex flex-wrap items-center gap-3">
        <button id="start" class="px-4 py-2 rounded-lg bg-ok/15 border border-ok/40 text-ok text-sm font-600 hover:bg-ok/25">Print it? Yes</button>
      </div>
      <p class="text-steel/50 text-xs mt-3 max-w-md">Insert the filament tip into the feeder, then confirm. Start is blocked while the printer is busy.</p>
      <div id="startNote" class="text-steel/40 text-xs font-mono mt-1"></div>
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
        <h2 class="text-steel/50 text-xs font-mono uppercase tracking-[0.2em]">Filament change</h2>
        <button id="filCooldown" class="text-[11px] font-mono text-steel/50 hover:text-molten">cooldown</button>
      </div>
      <div class="flex flex-wrap gap-2">
        <button id="filEject" class="px-3 py-2 rounded-lg bg-panel2 border border-line text-filament text-sm hover:border-molten">Eject</button>
        <button id="filLoad" class="px-3 py-2 rounded-lg bg-panel2 border border-line text-filament text-sm hover:border-molten">Load</button>
      </div>
      <p class="text-steel/50 text-xs mt-3">Uses the filament selected in Step 01 — set it to what's currently loaded before Eject, and to what you're inserting before Load. Insert the tip before clicking Load. Both heat first, then run auto-unload (M702) / auto-load (M701). Blocked while printing.</p>
      <div id="filChangeMsg" class="text-sm mt-2"></div>
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
      <div class="flex items-center justify-between mt-2">
        <button id="camRefresh" class="text-[11px] font-mono text-steel/50 hover:text-filament">refresh now</button>
        <button id="camReview" class="text-[11px] font-mono text-steel/50 hover:text-molten">AI review</button>
      </div>
      <div id="camReviewMsg" class="text-sm mt-2"></div>
    </section>

    <section class="bg-panel border border-line rounded-xl p-5">
      <h2 class="text-steel/50 text-xs font-mono uppercase tracking-[0.2em] mb-3">Assistant <span class="text-molten/70 normal-case tracking-normal">· Claude</span></h2>
      <div id="chatLog" class="h-48 overflow-y-auto space-y-2 mb-3 text-sm"></div>
      <div class="flex gap-2">
        <input id="chatInput" type="text" placeholder="ask about filament, adhesion, troubleshooting…" class="flex-1 bg-panel2 border border-line rounded-lg px-3 py-2 text-sm text-filament">
        <button id="chatSend" class="px-3 py-2 rounded-lg bg-panel2 border border-line text-filament text-sm hover:border-molten">Send</button>
      </div>
      <div id="chatMsg" class="text-sm mt-2"></div>
    </section>
  </aside>
</main>

<script type="module">
import * as THREE from 'three';
window.THREE = THREE;  // Polyslice's browser bundle reads window.THREE
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import Polyslice from '@jgphilpott/polyslice';

const el = id => document.getElementById(id);
const BED = 235;
let rot = {x:0,y:0,z:0}, flatMode = false;
let slicerChoice = 'cura';

function setMsg(id, cls, text){ el(id).innerHTML = '<span class="'+cls+'">'+text+'</span>'; }
function fmtTime(s){ if(s==null) return '?'; const m=Math.round(s/60); return Math.floor(m/60)+'h '+(m%60)+'m'; }

/* ---- AI calls: shared error handling so a low-balance/no-key failure always reads clearly ---- */
async function fetchJsonOrThrow(url, opts){
  const r = await fetch(url, opts);
  const d = await r.json();
  if(!r.ok){ const err = new Error(d.error||('HTTP '+r.status)); err.lowCredits=!!d.low_credits; err.noKey=!!d.no_key; throw err; }
  return d;
}
function aiErrorMsg(err){
  if(err.lowCredits) return '⚠ Anthropic account is out of credits — add funds at console.anthropic.com, then try again.';
  if(err.noKey) return '⚠ AI features are not configured (no API key on the Pi).';
  return err.message;
}

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
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  wrap.appendChild(renderer.domElement);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(BED/2, 0, BED/2);
  controls.update();
  scene.add(new THREE.AmbientLight(0xffffff, 0.55));
  const dl = new THREE.DirectionalLight(0xffffff, 1.0); dl.position.set(150,250,180); scene.add(dl);
  dl.castShadow = true;
  dl.shadow.mapSize.set(1024,1024);
  Object.assign(dl.shadow.camera, {left:-130, right:130, top:130, bottom:-130, near:50, far:600});
  const dl2 = new THREE.DirectionalLight(0xff6a3d, 0.25); dl2.position.set(-150,120,-120); scene.add(dl2);
  // bed -- receives the model's contact shadow so a floating vs. flush model is visually obvious
  const bed = new THREE.Mesh(new THREE.PlaneGeometry(BED,BED), new THREE.MeshStandardMaterial({color:0x0E1116,metalness:0,roughness:1}));
  bed.rotation.x = -Math.PI/2; bed.position.set(BED/2,0,BED/2); bed.receiveShadow = true; scene.add(bed);
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
  const dx = -(mnX+mxX)/2, dy = -(mnY+mxY)/2, dz = -mnZ;  // center at origin, minZ=0
  for(let i=0;i<pos.length;i+=3){ pos[i]+=dx; pos[i+1]+=dy; pos[i+2]+=dz; }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos,3));
  geo.computeVertexNormals();
  if (mesh) scene.remove(mesh);
  mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({color:0xE8D9B5, metalness:0.1, roughness:0.6, flatShading:false}));
  mesh.rotation.x = -Math.PI/2;  // STL Z-up -> three Y-up for display
  mesh.position.set(BED/2, 0, BED/2);  // sit centered on the bed
  mesh.castShadow = true;
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
let sliced=false, liveBusy=false, awaitingRemoval=false, wasBusy=null;
function refreshStartButton(){
  el('start').disabled = !sliced || liveBusy || awaitingRemoval;
  let note='';
  if(!sliced) note='blocked: slice a model first';
  else if(liveBusy) note='blocked: printer is currently busy';
  else if(awaitingRemoval) note='blocked: confirm the bed is clear (see above)';
  el('startNote').textContent=note;
}
function updateRemovalBanner(){ el('removalGate').classList.toggle('hidden', !awaitingRemoval); }
el('printRemoved').addEventListener('click',()=>{ awaitingRemoval=false; updateRemovalBanner(); refreshStartButton(); });
function refreshFilButtons(){ el('filEject').disabled = liveBusy; el('filLoad').disabled = liveBusy; el('filCooldown').disabled = liveBusy; }
async function doSlice(){
  if (slicerChoice==='poly') return sliceWithPolyslice();
  window._browserGcode = null;
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
    el('startGcode').textContent=d.start_gcode; el('end').textContent=d.end_gcode; el('head').textContent=d.gcode_head;
    el('review').classList.remove('hidden'); el('sendSection').classList.remove('hidden');
    setMsg('sliceMsg','text-ok','sliced → '+d.filename);
    window._lastFile=d.octoprint_name; sliced=true; refreshStartButton();
  }catch(e){ setMsg('sliceMsg','text-molten',e.message); } finally{ el('slice').disabled=false; }
}
function setSlicer(choice){
  slicerChoice=choice;
  const on='px-3 py-1 text-xs font-mono rounded-md border border-molten bg-molten/15 text-filament';
  const off='px-3 py-1 text-xs font-mono rounded-md border border-line text-steel/60';
  el('slCura').className = choice==='cura'?on:off;
  el('slPoly').className = choice==='poly'?on:off;
}
async function sliceWithPolyslice(){
  const stl = el('stl').files[0];
  if(!stl){ setMsg('sliceMsg','text-molten','pick an STL first.'); return; }
  if(!mesh){ setMsg('sliceMsg','text-molten','load the STL into the viewer first.'); return; }
  el('slice').disabled=true; setMsg('sliceMsg','text-filament','slicing in browser…');
  try{
    const pr=await fetch('/api/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filament:el('fil').value,plate:el('pla').value,quality:el('qua').value,adhesion:el('adh').value,supports:el('sup').value})});
    const P=await pr.json(); if(!pr.ok) throw new Error(P.error||('HTTP '+pr.status));
    const m=P.merged;
    const Printer=Polyslice.Printer, Filament=Polyslice.Filament;
    const sl=new Polyslice({printer:new Printer('Ender3'),filament:new Filament('GenericPLA'),nozzleTemperature:m.nozzle_temp,bedTemperature:m.bed_temp,fanSpeed:m.fan_max,infillDensity:m.infill,autohome:false,progressCallback:p=>setMsg('sliceMsg','text-filament','slicing… '+Math.round((p||0)*100)+'%')});
    const sg=mesh.geometry.clone();
    const bb=new THREE.Box3().setFromBufferAttribute(sg.attributes.position);
    const cx=(bb.min.x+bb.max.x)/2, cy=(bb.min.y+bb.max.y)/2, cz=(bb.min.z+bb.max.z)/2;
    sg.translate(-cx,-cy,-cz);
    const sliceMesh=new THREE.Mesh(sg,new THREE.MeshBasicMaterial());
    let gcode; try{ gcode=sl.slice(sliceMesh); }catch(e){ throw new Error('Polyslice: '+e.message); }
    gcode=P.start_gcode+'\\n'+gcode+'\\n'+P.end_gcode;
    const lm=gcode.match(/; Total Layers: (\d+)/); const fm=gcode.match(/; Filament Length: ([\d.]+)mm/);
    const est={layers:lm?Number(lm[1]):null,time_seconds:null,filament_m:fm?Number(fm[1]):null,filament_g:null};
    const cards=[['layers',est.layers||'?'],['filament',(est.filament_m?est.filament_m+'mm':'?')],['nozzle',m.nozzle_temp+'°C'],['bed',m.bed_temp+'°C'],['adhesion',m.adhesion],['supports',m.support_enable?'on':'off']];
    el('cards').innerHTML=cards.map(c=>'<div class="bg-panel border border-line rounded-lg p-3"><div class="text-steel/40 text-[10px] font-mono uppercase tracking-wider">'+c[0]+'</div><div class="text-filament text-lg font-mono">'+c[1]+'</div></div>').join('');
    el('startGcode').textContent=P.start_gcode; el('end').textContent=P.end_gcode;
    el('head').textContent=gcode.split('\\n').slice(0,25).join('\\n');
    el('review').classList.remove('hidden'); el('sendSection').classList.remove('hidden');
    const filename=stl.name.replace(/\.stl$/i,'')+'__'+m.filament+'_'+m.plate+'_'+m.quality+'_polyslice.gcode';
    window._browserGcode=gcode; window._browserFilename=filename; window._lastFile=filename;
    setMsg('sliceMsg','text-ok','sliced (Polyslice) → '+filename);
    sliced=true; refreshStartButton();
  }catch(e){ setMsg('sliceMsg','text-molten',e.message); } finally{ el('slice').disabled=false; }
}
async function doSend(){
  setMsg('sendMsg','text-filament','uploading…');
  const url = window._browserGcode ? '/api/upload-gcode' : '/api/send';
  const body = window._browserGcode ? JSON.stringify({filename:window._browserFilename,gcode:window._browserGcode}) : null;
  const r=await fetch(url,{method:'POST',headers:body?{'Content-Type':'application/json'}:{},body:body});
  const d=await r.json();
  if(!r.ok) throw new Error(d.error||('HTTP '+r.status));
  setMsg('sendMsg','text-ok','sent as '+d.octoprint_file); window._lastFile=d.octoprint_file;
}
async function doStart(){
  el('start').disabled=true; setMsg('sendMsg','',''); setMsg('startMsg','text-filament','uploading…');
  try{
    await doSend();
    setMsg('startMsg','text-filament','checking printer…');
    const r=await fetch('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target:window._lastFile})});
    const d=await r.json(); if(!r.ok) throw new Error(d.error||('HTTP '+r.status));
    setMsg('startMsg','text-ok','print started');
  }catch(e){ setMsg('startMsg','text-molten',e.message); } finally{ refreshStartButton(); }
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
    const r=await fetch('/api/status');
    const d=await r.json();
    if(!r.ok || d.error) throw new Error(d.error||('HTTP '+r.status));
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
    liveBusy=!!d.busy;
    if(wasBusy===true && !liveBusy) awaitingRemoval=true;
    wasBusy=liveBusy;
    updateRemovalBanner(); refreshStartButton(); refreshFilButtons();
  }catch(e){
    el('hdrState').className='px-2 py-1 rounded-full border font-mono text-xs text-molten border-molten/40';
    el('hdrState').textContent='connection lost';
    el('compState').textContent='status unavailable';
  }
}

/* ---- camera ---- */
function camRefresh(){ el('cam').src='/api/camera/snapshot?ts='+Date.now(); }
el('cam').addEventListener('error',()=>{ el('camOff').classList.remove('hidden'); el('cam').style.opacity=0; });
el('cam').addEventListener('load',()=>{ el('camOff').classList.add('hidden'); el('cam').style.opacity=1; });
async function camReview(){
  el('camReview').disabled=true; setMsg('camReviewMsg','text-filament','looking…');
  try{
    const d=await fetchJsonOrThrow('/api/camera/review',{method:'POST'});
    setMsg('camReviewMsg','text-steel/90',d.review);
  }catch(e){ setMsg('camReviewMsg','text-molten',aiErrorMsg(e)); } finally{ el('camReview').disabled=false; }
}

/* ---- chat ---- */
let chatHistory=[];
function renderChat(){
  el('chatLog').innerHTML=chatHistory.map(m=>
    '<div><span class="text-steel/40 text-[10px] font-mono uppercase mr-1">'+(m.role==='user'?'you':'claude')+'</span><span class="'+(m.role==='user'?'text-steel/80':'text-filament')+'">'+m.content.replace(/</g,'&lt;')+'</span></div>'
  ).join('');
  el('chatLog').scrollTop=el('chatLog').scrollHeight;
}
async function chatSend(){
  const q=el('chatInput').value.trim(); if(!q) return;
  chatHistory.push({role:'user',content:q}); renderChat(); el('chatInput').value='';
  el('chatSend').disabled=true; setMsg('chatMsg','text-filament','thinking…');
  try{
    const d=await fetchJsonOrThrow('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({messages:chatHistory})});
    chatHistory.push({role:'assistant',content:d.reply}); renderChat();
    setMsg('chatMsg','','');
  }catch(e){ setMsg('chatMsg','text-molten',aiErrorMsg(e)); } finally{ el('chatSend').disabled=false; }
}

async function filSearch(){
  const q=el('filQuery').value.trim(); if(!q) return;
  el('filSearch').disabled=true; setMsg('filMsg','text-filament','asking Claude…');
  try{
    const d=await fetchJsonOrThrow('/api/filament/lookup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q})});
    el('filResult').classList.remove('hidden');
    el('filJson').value=JSON.stringify(d.profile,null,2);
    el('filSlug').value=q.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'');
    setMsg('filMsg','text-ok','found specs — review and save');
  }catch(e){ setMsg('filMsg','text-molten',aiErrorMsg(e)); } finally{ el('filSearch').disabled=false; }
}
async function filSave(){
  let profile; try{ profile=JSON.parse(el('filJson').value); }catch(e){ setMsg('filMsg','text-molten','invalid JSON'); return; }
  const slug=el('filSlug').value.trim(); if(!slug){ setMsg('filMsg','text-molten','need a slug'); return; }
  try{
    const r=await fetch('/api/filament/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slug,profile})});
    const d=await r.json(); if(!r.ok) throw new Error(d.error||('HTTP '+r.status));
    setMsg('filMsg','text-ok','saved as '+slug+' — reloading…');
    setTimeout(()=>location.reload(),800);
  }catch(e){ setMsg('filMsg','text-molten',e.message); }
}
/* ---- filament change ---- */
async function filEject(){
  const filament=el('fil').value;
  el('filEject').disabled=true; setMsg('filChangeMsg','text-filament','heating & ejecting…');
  try{
    const d=await fetchJsonOrThrow('/api/filament/eject',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filament})});
    setMsg('filChangeMsg','text-ok','heating to '+d.temp+'° then auto-unload (M702) — watch the hotend temp above');
  }catch(e){ setMsg('filChangeMsg','text-molten',e.message); } finally{ refreshFilButtons(); }
}
async function filLoad(){
  const filament=el('fil').value;
  el('filLoad').disabled=true; setMsg('filChangeMsg','text-filament','heating & loading…');
  try{
    const d=await fetchJsonOrThrow('/api/filament/load',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filament})});
    setMsg('filChangeMsg','text-ok','heating to '+d.temp+'° then auto-load (M701) — watch the hotend temp above');
  }catch(e){ setMsg('filChangeMsg','text-molten',e.message); } finally{ refreshFilButtons(); }
}
async function filCooldown(){
  setMsg('filChangeMsg','text-filament','cooling down…');
  try{ await fetchJsonOrThrow('/api/filament/cooldown',{method:'POST'}); setMsg('filChangeMsg','text-ok','hotend off'); }
  catch(e){ setMsg('filChangeMsg','text-molten',e.message); }
}

/* ---- wire up ---- */
el('stl').addEventListener('change', e=>{ if(e.target.files[0]) loadSTL(e.target.files[0]); });
el('rotX').addEventListener('click',()=>doRot('x'));
el('rotY').addEventListener('click',()=>doRot('y'));
el('rotZ').addEventListener('click',()=>doRot('z'));
el('layFlat').addEventListener('click',doLayFlat);
el('rotReset').addEventListener('click',doReset);
el('slCura').addEventListener('click',()=>setSlicer('cura'));
el('slPoly').addEventListener('click',()=>setSlicer('poly'));
el('slice').addEventListener('click',doSlice);
el('start').addEventListener('click',doStart);
el('camRefresh').addEventListener('click',camRefresh);
el('camReview').addEventListener('click',camReview);
el('filSearch').addEventListener('click',filSearch);
el('filSave').addEventListener('click',filSave);
el('filEject').addEventListener('click',filEject);
el('filLoad').addEventListener('click',filLoad);
el('filCooldown').addEventListener('click',filCooldown);
el('chatSend').addEventListener('click',chatSend);
el('chatInput').addEventListener('keydown', e=>{ if(e.key==='Enter') chatSend(); });

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


def _camera_snapshot_bytes():
    r = octo().s.get(f"{octo().host}/webcam/?action=snapshot", timeout=8)
    if r.status_code != 200 or not r.content:
        raise RuntimeError("camera snapshot unavailable")
    return r.content


@app.get("/api/camera/snapshot")
def api_camera():
    try:
        return Response(_camera_snapshot_bytes(), mimetype="image/jpeg")
    except Exception:
        return Response(b"", status=502, mimetype="image/jpeg")


@app.post("/api/camera/review")
def api_camera_review():
    try:
        img = _camera_snapshot_bytes()
    except Exception as e:
        return jsonify(error=str(e)), 502
    try:
        review = aiclient.review_camera(img)
    except aiclient.NoKeyError as e:
        return jsonify(error=str(e), no_key=True), 503
    except aiclient.InsufficientCreditsError as e:
        return jsonify(error=str(e), low_credits=True), 503
    except Exception as e:
        return jsonify(error=str(e)), 502
    return jsonify(review=review)


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


def _safe_temp(value, label: str) -> float:
    """Validates a temperature pulled from a filament profile before it's
    interpolated into raw G-code sent over serial. Profiles are user-editable
    (via /api/filament/save), so a non-numeric or out-of-range value here would
    otherwise land verbatim in an M104/M109 line on the live printer."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number, got {value!r}")
    if not (0 <= value <= 320):
        raise ValueError(f"{label} out of range: {value!r}")
    return value


@app.post("/api/filament/eject")
def api_filament_eject():
    filament = (request.get_json(silent=True) or {}).get("filament")
    if not filament:
        return jsonify(error="filament required"), 400
    try:
        octo().require_idle("eject filament")
        f = get_filament(filament)
        temp = _safe_temp(f["unload_temp"], "unload_temp")
        octo().send_gcode([f"M104 S{temp}", f"M109 S{temp}", "M702"])
    except FileNotFoundError as e:
        return jsonify(error=str(e)), 404
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except RuntimeError as e:
        return jsonify(error=str(e), state=octo().status().get("state")), 409
    except Exception as e:
        return jsonify(error=str(e)), 502
    return jsonify(ok=True, temp=temp)


@app.post("/api/filament/load")
def api_filament_load():
    filament = (request.get_json(silent=True) or {}).get("filament")
    if not filament:
        return jsonify(error="filament required"), 400
    try:
        octo().require_idle("load filament")
        f = get_filament(filament)
        temp = _safe_temp(f["load_temp"], "load_temp")
        octo().send_gcode([f"M104 S{temp}", f"M109 S{temp}", "M701"])
    except FileNotFoundError as e:
        return jsonify(error=str(e)), 404
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except RuntimeError as e:
        return jsonify(error=str(e), state=octo().status().get("state")), 409
    except Exception as e:
        return jsonify(error=str(e)), 502
    return jsonify(ok=True, temp=temp)


@app.post("/api/filament/cooldown")
def api_filament_cooldown():
    try:
        octo().require_idle("cool down")
        octo().send_gcode(["M104 S0"])
    except RuntimeError as e:
        return jsonify(error=str(e), state=octo().status().get("state")), 409
    except Exception as e:
        return jsonify(error=str(e)), 502
    return jsonify(ok=True)


@app.post("/api/filament/lookup")
def api_filament_lookup():
    q = (request.get_json(silent=True) or {}).get("query")
    if not q:
        return jsonify(error="query required"), 400
    try:
        profile = aiclient.lookup_filament(q)
    except aiclient.NoKeyError as e:
        return jsonify(error=str(e), no_key=True), 503
    except aiclient.InsufficientCreditsError as e:
        return jsonify(error=str(e), low_credits=True), 503
    except Exception as e:
        return jsonify(error=str(e)), 502
    return jsonify(profile=profile)


@app.post("/api/chat")
def api_chat():
    messages = (request.get_json(silent=True) or {}).get("messages")
    if not messages:
        return jsonify(error="messages required"), 400
    try:
        reply = aiclient.chat(messages)
    except aiclient.NoKeyError as e:
        return jsonify(error=str(e), no_key=True), 503
    except aiclient.InsufficientCreditsError as e:
        return jsonify(error=str(e), low_credits=True), 503
    except Exception as e:
        return jsonify(error=str(e)), 502
    return jsonify(reply=reply)


@app.post("/api/filament/save")
def api_filament_save():
    d = request.get_json(silent=True) or {}
    slug = (d.get("slug") or "").strip()
    profile = d.get("profile")
    if not slug or not profile:
        return jsonify(error="slug and profile required"), 400
    if not re.match(r"^[a-z0-9-]+$", slug):
        return jsonify(error="slug must be lowercase a-z, 0-9, -"), 400
    path = pathlib.Path(__file__).resolve().parent.parent / "profiles" / "filaments" / f"{slug}.json"
    path.write_text(json.dumps(profile, indent=2))
    return jsonify(ok=True, slug=slug, name=profile.get("name"))


@app.post("/api/profile")
def api_profile():
    d = request.get_json(silent=True) or {}
    filament = d.get("filament"); plate = d.get("plate"); quality = d.get("quality")
    adhesion = d.get("adhesion") or "auto"; supports = d.get("supports") == "on"
    bundle = compile_all(filament, plate, quality)
    m = bundle["merged"]
    if adhesion != "auto":
        m["adhesion"] = adhesion
    m["support_enable"] = supports
    return jsonify(merged=m, start_gcode=bundle["start_gcode"], end_gcode=bundle["end_gcode"])


@app.post("/api/upload-gcode")
def api_upload_gcode():
    d = request.get_json(silent=True) or {}
    filename = d.get("filename"); gcode = d.get("gcode")
    if not filename or not gcode:
        return jsonify(error="filename and gcode required"), 400
    path = UPLOAD_DIR / pathlib.Path(filename).name
    path.write_text(gcode)
    try:
        out = octo().upload(str(path), select=False, print=False)
    except Exception as e:
        return jsonify(error=str(e)), 502
    op_file = out.get("files", {}).get("local", {}).get("path", filename)
    _LAST["filename"] = op_file
    return jsonify(ok=True, octoprint_file=op_file)


if __name__ == "__main__":
    host = os.environ.get("WIZARD_HOST", "127.0.0.1")
    port = int(os.environ.get("WIZARD_PORT", "8765"))
    app.run(host=host, port=port, debug=False)

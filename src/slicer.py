"""CuraEngine slicer adapter (Phase 2).

Maps a merged profile (from compiler.compile_profile) to CuraEngine `-s` flags
and invokes the bundled CuraEngine to produce G-code. The compiler's generated
start/end G-code is injected via `machine_start_gcode` / `machine_end_gcode`,
so auto-load (M701) and auto-unload (M702) are part of the sliced output -- the
slicer and the printer run one continuous file, no splicing.

Headless CuraEngine 5.13 gotchas (all handled here, all learned the hard way):
- CURA_ENGINE_SEARCH_PATH must point at Cura's definitions + extruders dirs so
  the `inherits` chain and extruder defs resolve.
- `roofing_layer_count` and `flooring_layer_count` have no headless default and
  abort the slice; both set to 0 (disables those skin subtypes).
- Start/end G-code must be passed with REAL newlines. CuraEngine writes literal
  `\n` if you pass them escaped, which turns the whole block into one comment.
- CuraEngine writes `[error]` lines to STDOUT (mixed with the info dump), not
  stderr. We scan stdout for them.

Paths default to the macOS Cura 5.13 bundle. On the Pi these point at a
CuraEngine built for ARM + its resources dir (set via CURA_ENGINE / CURA_RESOURCES env).
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess

CURA_ENGINE = os.environ.get(
    "CURA_ENGINE", "/Applications/UltiMaker Cura.app/Contents/Resources/CuraEngine"
)
CURA_RESOURCES = os.environ.get(
    "CURA_RESOURCES",
    "/Applications/UltiMaker Cura.app/Contents/Resources/share/cura/resources",
)
MACHINE_DEF = "creality_ender3.def.json"  # base; tuned on the real printer in Phase 3

# Cura 5.13 settings with no headless default that abort the slice if unset.
_HEADLESS_FIXES = {"roofing_layer_count": "0", "flooring_layer_count": "0"}


def _settings_from_merged(m: dict, start_gcode: str, end_gcode: str) -> dict:
    flow = m["flow"]
    flow_pct = int(round(flow * 100)) if flow <= 1.5 else int(round(flow))
    s = {
        "layer_height": str(m["layer_height"]),
        "layer_height_0": str(m["first_layer_height"]),
        "infill_sparse_density": str(m["infill"]),
        "speed_print": str(m["speed"]),
        "speed_print_layer_0": str(m["first_layer_speed"]),
        "wall_line_count": str(m["walls"]),
        "material_print_temperature": str(m["nozzle_temp"]),
        "material_bed_temperature": str(m["bed_temp"]),
        "material_bed_temperature_layer_0": str(m["bed_temp"]),
        "material_flow": str(flow_pct),
        "material_diameter": str(m["filament_diameter"]),
        "retraction_amount": str(m["retraction_dist"]),
        "retraction_retract_speed": str(m["retraction_speed"]),
        "cool_fan_speed_max": str(m["fan_max"]),
        "cool_fan_speed_0": str(m["layer1_fan"]),
        "adhesion_type": m["adhesion"],
        # Keep CuraEngine from prepending its own heat commands around our
        # start_gcode -- we handle heating ourselves, deterministically.
        "material_print_temp_prepend": "false",
        "material_bed_temp_prepend": "false",
        "machine_start_gcode": start_gcode,
        "machine_end_gcode": end_gcode,
    }
    if m.get("max_volumetric_speed"):
        s["material_max_flowrate"] = str(m["max_volumetric_speed"])
    if m["adhesion"] == "brim":
        s["brim_line_count"] = str(m.get("brim_lines", 6))
    s.update(_HEADLESS_FIXES)
    return s


def slice_model(
    merged: dict,
    start_gcode: str,
    end_gcode: str,
    model_path: str,
    out_path: str,
    cura_engine: str = CURA_ENGINE,
    resources: str = CURA_RESOURCES,
) -> dict:
    if not pathlib.Path(cura_engine).exists():
        raise FileNotFoundError(f"CuraEngine not found at {cura_engine}")
    if not pathlib.Path(model_path).exists():
        raise FileNotFoundError(f"model not found: {model_path}")

    env = os.environ.copy()
    env["CURA_ENGINE_SEARCH_PATH"] = f"{resources}/definitions:{resources}/extruders"

    args = [cura_engine, "slice", "-j", f"{resources}/definitions/{MACHINE_DEF}"]
    for k, v in _settings_from_merged(merged, start_gcode, end_gcode).items():
        args += ["-s", f"{k}={v}"]
    args += ["-o", str(out_path), "-l", str(model_path)]

    proc = subprocess.run(args, env=env, capture_output=True, text=True)
    errors = [ln for ln in proc.stdout.splitlines() if "[error]" in ln]
    out = pathlib.Path(out_path)
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        detail = "\n".join(errors[:6]) or (proc.stderr or proc.stdout)[:800]
        raise RuntimeError(f"CuraEngine failed (exit {proc.returncode}):\n{detail}")

    est = parse_estimates(out_path)
    return {"path": str(out), "bytes": out.stat().st_size, "errors": errors, **est}


def parse_estimates(gcode_path: str) -> dict:
    """Pull time / filament / layer count from the Cura header comments."""
    est = {"time_seconds": None, "layers": 0, "filament_m": None, "filament_g": None}
    text = pathlib.Path(gcode_path).read_text(errors="replace")
    m = re.search(r"^;TIME:(\d+)", text, re.M)
    if m:
        est["time_seconds"] = int(m.group(1))
    m = re.search(r"^;Filament used:\s*([\d.]+)\s*m", text, re.M)
    if m:
        est["filament_m"] = float(m.group(1))
    m = re.search(r"^;Filament used:\s*([\d.]+)\s*g", text, re.M)
    if m:
        est["filament_g"] = float(m.group(1))
    est["layers"] = len(re.findall(r"^;LAYER:", text, re.M))
    return est


if __name__ == "__main__":
    import json
    import sys

    from compiler import compile_all

    if len(sys.argv) < 5:
        print("usage: slicer.py <filament> <plate> <quality> <model.stl> [out.gcode]", file=sys.stderr)
        sys.exit(2)
    filament, plate, quality, model = sys.argv[1:5]
    out = sys.argv[5] if len(sys.argv) > 5 else str(
        pathlib.Path(model).with_suffix(".wizard.gcode")
    )
    bundle = compile_all(filament, plate, quality)
    res = slice_model(bundle["merged"], bundle["start_gcode"], bundle["end_gcode"], model, out)
    print(json.dumps(res, indent=2))

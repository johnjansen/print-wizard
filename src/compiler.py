"""Profile compiler for the Print Wizard.

Compiles a (filament, plate, quality) selection into a slicer-agnostic
merged profile plus start/end G-code.

Design notes:
- Slicer-agnostic. The OrcaSlicer adapter that turns `merged` into a slicer
  profile lives in Phase 2. This module owns the brain, not any one slicer's
  format.
- Filament stays loaded across prints. Loading/unloading is a separate,
  explicit action (the Eject/Load endpoints), never part of a print's own
  start/end sequence -- start.gcode only primes what's already in the
  hotend, end.gcode only parks and cools.
- No schema library. Profiles are small and author-owned; missing keys raise
  loudly with the profile name so typos surface immediately.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"

# Profile names reach _load() from HTTP request bodies (filament/plate/quality
# selectors). Restricting to this charset before building a path blocks
# traversal (e.g. "../../etc/passwd") regardless of caller.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def _load(kind: str, name: str) -> dict:
    if not name or not _SAFE_NAME.match(name):
        raise FileNotFoundError(f"no {kind} profile named {name!r}")
    path = PROFILES_DIR / kind / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"no {kind} profile named {name!r} at {path}")
    return json.loads(path.read_text())


def list_profiles(kind: str) -> list[str]:
    d = PROFILES_DIR / kind
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


def get_filament(name: str) -> dict:
    """Raw filament profile, for standalone actions (e.g. filament change)
    that need load_temp/unload_temp without compiling a full print profile."""
    return _load("filaments", name)


def compile_profile(filament: str, plate: str, quality: str) -> dict:
    """Merge a filament, plate, and quality into one slicer-agnostic profile.

    Plate modifiers apply *on top of* filament baselines (e.g. bed_temp =
    filament.bed_temp + plate.bed_temp_offset), so a single filament works
    across both plates without per-plate duplicates.
    """
    f = _load("filaments", filament)
    pl = _load("plates", plate)
    q = _load("qualities", quality)

    bed_temp = f["bed_temp"] + pl.get("bed_temp_offset", 0)

    return {
        # identity (for logging / filenames / UI)
        "filament": filament,
        "plate": plate,
        "quality": quality,
        # filament
        "filament_diameter": f.get("filament_diameter", 1.75),
        "nozzle_temp": f["nozzle_temp"],
        "bed_temp": bed_temp,
        "flow": f.get("flow", 1.0),
        "retraction_dist": f["retraction"]["dist"],
        "retraction_speed": f["retraction"]["speed"],
        "fan_max": f["cooling"]["fan_max"],
        "layer1_fan": f["cooling"]["layer1_fan"],
        "max_volumetric_speed": f.get("max_volumetric_speed"),
        "load_temp": f.get("load_temp", f["nozzle_temp"]),
        "unload_temp": f.get("unload_temp", f["nozzle_temp"]),
        # plate
        "z_offset": pl.get("z_offset", 0.0),
        "first_layer_height": pl.get("first_layer_height", 0.2),
        "first_layer_speed": pl.get("first_layer_speed", 20),
        "adhesion": pl.get("adhesion", "none"),
        "brim_lines": pl.get("brim_lines", 0),
        "removal_temp": pl.get("removal_temp", 0),
        # quality
        "layer_height": q["layer_height"],
        "speed": q["speed"],
        "infill": q["infill"],
        "walls": q["walls"],
        "top_layers": q["top_layers"],
        "bottom_layers": q["bottom_layers"],
    }


def start_gcode(m: dict) -> str:
    """Generated start G-code. Heats bed and hotend, then primes.

    Assumes filament is already loaded -- a normal print never feeds or
    retracts a whole spool's worth of filament. Use the Load endpoint first
    if the hotend is actually empty."""
    bed, nozzle = m["bed_temp"], m["nozzle_temp"]
    lines = [
        f"; start.gcode -- {m['filament']} / {m['plate']} / {m['quality']}",
        f"M140 S{bed}        ; heat bed -> {bed}C",
        f"M190 S{bed}        ; wait for bed",
        f"M104 S{nozzle}     ; heat hotend -> {nozzle}C (print temp)",
        f"M109 S{nozzle}     ; wait for print temp",
        "G28                ; home all axes",
        "G1 Z5 F3000        ; lift nozzle",
        "G92 E0             ; reset extruder",
        "G1 X20 Y20 Z0.2 F1500  ; move to prime start",
        "G1 E20 F200        ; prime / purge",
        "G92 E0             ; reset extruder",
        # Without this, the sliced file's first travel move drags the nozzle
        # (still at Z0.2, right after a 20mm purge) in a straight line across
        # open bed to wherever the model's first layer actually starts --
        # smearing the purge blob into a visible scratch near the print.
        "G1 Z5 F3000        ; lift clear of the purge blob before slicing takes over",
        f"M117 printing {m['filament']}",
    ]
    return "\n".join(lines)


def end_gcode(m: dict) -> str:
    """Generated end G-code. Parks and cools; does NOT unload filament -- that's
    a separate, explicit action (the Eject endpoint), not part of every
    print's finish. Cools bed to a safe removal temp when the plate needs it
    (glass); PEX flex just turns off."""
    lines = [
        f"; end.gcode -- {m['filament']} / {m['plate']}",
        "M400               ; finish pending moves",
        "G91                ; relative positioning",
        "G1 Z10 F3000       ; lift",
        "G90                ; absolute positioning",
        "G28 X Y            ; home XY (nozzle stays lifted)",
        "M83                ; extruder relative mode (G91/G90 don't reliably cover E)",
        "G1 E-2 F300        ; break tack, small pull away from nozzle",
        "M82                ; extruder absolute mode",
        "M104 S0            ; hotend off",
    ]
    rt = m["removal_temp"]
    if rt and rt > 0:
        lines += [
            f"M140 R{rt}        ; cool bed -> {rt}C for removal",
            f"M190 R{rt}        ; wait for bed to cool",
        ]
    else:
        lines.append("M140 S0           ; bed off (flex plate, remove hot)")
    lines += [
        "M84                ; disable motors",
        f"M117 done -- {m['plate']} ready to remove",
    ]
    return "\n".join(lines)


def compile_all(filament: str, plate: str, quality: str) -> dict:
    m = compile_profile(filament, plate, quality)
    return {"merged": m, "start_gcode": start_gcode(m), "end_gcode": end_gcode(m)}


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 2 and sys.argv[1] == "list":
        for kind in ("filaments", "plates", "qualities"):
            print(f"{kind}: {', '.join(list_profiles(kind))}")
        sys.exit(0)
    if len(sys.argv) != 4:
        print("usage: compiler.py <filament> <plate> <quality>   |   compiler.py list", file=sys.stderr)
        sys.exit(2)
    out = compile_all(sys.argv[1], sys.argv[2], sys.argv[3])
    print(json.dumps(out["merged"], indent=2))
    print("\n--- start.gcode ---")
    print(out["start_gcode"])
    print("\n--- end.gcode ---")
    print(out["end_gcode"])
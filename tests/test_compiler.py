"""Tests for the profile compiler. Run directly: python3 tests/test_compiler.py

No test framework dependency. Each check raises on failure; the harness at the
bottom counts and exits nonzero on any failure so CI/scripts can detect it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from compiler import compile_profile, start_gcode, end_gcode, compile_all, list_profiles, get_filament


def test_glass_pla_standard():
    m = compile_profile("polymaker-polyterra-pla-black", "glass-stock", "standard")
    # plate offset applied: 60 + 10 = 70
    assert m["bed_temp"] == 70, f"glass bed_temp {m['bed_temp']}"
    assert m["nozzle_temp"] == 210
    assert m["adhesion"] == "brim"
    assert m["removal_temp"] == 45
    assert m["layer_height"] == 0.16  # standard quality
    s = start_gcode(m)
    assert "M701" in s, "start must auto-load"
    assert "S70" in s and "S210" in s, f"start temps wrong:\n{s}"
    e = end_gcode(m)
    assert "M702" in e, "end must auto-unload"
    assert "R45" in e, f"glass must cool to removal temp:\n{e}"
    assert "M140 S0" not in e, "glass should cool-and-hold, not bed-off"


def test_pex_draft_no_cooldown():
    m = compile_profile("polymaker-polyterra-pla-black", "whambam-pex-textured", "draft")
    # PEX (WhamBam spec, PLA): +10 bed offset -> 70C, squished first layer, no adhesion aid, no cooldown
    assert m["bed_temp"] == 70, f"PEX bed_temp {m['bed_temp']}"
    assert m["adhesion"] == "none"
    assert m["removal_temp"] == 0
    assert m["layer_height"] == 0.2  # draft
    assert m["first_layer_height"] == 0.1, "PEX PLA wants a squished (half-height) first layer"
    e = end_gcode(m)
    assert "M702" in e
    assert "M140 S0" in e, "PEX should bed-off (remove hot)"
    assert "R0" not in e, "PEX should not emit a cooldown wait"


def test_load_temp_defaults_to_nozzle():
    m = compile_profile("polymaker-polyterra-pla-black", "glass-stock", "fine")
    assert m["load_temp"] == 210
    assert m["unload_temp"] == 210


def test_compile_all_shape():
    out = compile_all("polymaker-polyterra-pla-black", "glass-stock", "standard")
    assert set(out.keys()) == {"merged", "start_gcode", "end_gcode"}
    assert out["start_gcode"].startswith("; start.gcode")
    assert out["end_gcode"].startswith("; end.gcode")


def test_list_profiles():
    assert "polymaker-polyterra-pla-black" in list_profiles("filaments")
    assert "glass-stock" in list_profiles("plates")
    assert "whambam-pex-textured" in list_profiles("plates")
    assert {"draft", "standard", "fine"} <= set(list_profiles("qualities"))


def test_get_filament_standalone():
    f = get_filament("polymaker-polyterra-pla-black")
    assert f["load_temp"] == 210
    assert f["unload_temp"] == 210


def test_missing_profile_raises():
    try:
        compile_profile("nope", "glass-stock", "standard")
    except FileNotFoundError:
        return
    raise AssertionError("missing filament should raise FileNotFoundError")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
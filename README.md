# Print Wizard

A local SimplyPrint replacement for the Creality Ender-3-2024 on OctoPrint
(`192.168.1.200`). Pick filament + plate + quality, slice, review, confirm,
and it hands off to OctoPrint. Knows about a stock glass plate and a WhamBam
PEI flex plate and adjusts bed temp / adhesion / removal cooldown accordingly.

## Layers

1. **Profile store** — `profiles/{filaments,plates,qualities}/*.json`. The brain.
2. **Compiler** (`src/compiler.py`) — merges `filament x plate x quality` into a
   slicer-agnostic profile + generated start/end G-code. Slicer-agnostic on
   purpose; the OrcaSlicer adapter is Phase 2.
3. **Slicer** — headless OrcaSlicer (Phase 2).
4. **Wizard + handoff** — web UI -> OctoPrint upload + select/print (Phases 3-4).
5. **Cura export** — `.cfg` from the same store (Phase 5).

## Auto load / unload

Always-empty-at-start: `end.gcode` runs `M702` (unload) on every finish, so
`start.gcode` can always run `M701` (load) with no state tracking. The operator
inserts the new spool's tip into the feeder before confirming "print it? yes".

## Phase 1 — profile store + compiler

```
python3 src/compiler.py list
python3 src/compiler.py polymaker-polyterra-pla-black glass-stock standard
python3 tests/test_compiler.py
```

## OctoPrint access

Helper + creds live in `~/.octoprint/` (see `~/docs/octoprint-192.168.1.200.md`).
The handoff layer (Phase 3) reuses that session/CSRF handling.
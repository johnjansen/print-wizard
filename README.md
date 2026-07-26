# Print Wizard

A local SimplyPrint replacement for the Creality Ender-3-2024 on OctoPrint.
Pick filament + plate + quality (+ adhesion + supports), slice, review, confirm,
and it hands off to OctoPrint. Knows about a stock glass plate and a WhamBam
PEX flex plate and adjusts bed temp / adhesion / removal cooldown accordingly.

Runs entirely on the Pi (OctoPrint's own host), self-provisioned via Ansible.
Open from any browser on the LAN: `http://192.168.1.200:8765`.

## Layers

1. **Profile store** — `profiles/{filaments,plates,qualities}/*.json`. The brain.
2. **Compiler** (`src/compiler.py`) — merges `filament x plate x quality` into a
   slicer-agnostic profile + generated start/end G-code.
3. **Slicer** (`src/slicer.py`) — CuraEngine adapter. Env-configurable so the
   same code runs on macOS (Cura 5.13 bundle) and the Pi (apt cura-engine 4.13
   + extracted defs). Injects start/end G-code (M701 load / M702 unload).
4. **Handoff** (`src/octoprint_client.py`) — session+CSRF auth from
   `~/.octoprint/creds`, upload + gated start (refuses while printer busy).
5. **Wizard** (`src/server.py`) — Flask web UI, the flow above.

## Auto load / unload

Always-empty-at-start: `end.gcode` runs `M702` (unload) on every finish, so
`start.gcode` can always run `M701` (load) with no state tracking. Insert the
new spool's tip into the feeder before confirming "print it? yes".

## Deploy on the Pi (Ansible, runs on the Pi itself)

```bash
# on the Pi:
cat > ~/.wizard-secrets.yml <<YAML      # not in repo
octopass: <octoprint password>
ansible_become_password: <sudo password>
YAML
chmod 600 ~/.wizard-secrets.yml
git clone https://github.com/johnjansen/print-wizard.git ~/print-wizard
cd ~/print-wizard
ansible-playbook ansible/provision.yml -e @~/.wizard-secrets.yml
```

The playbook installs CuraEngine + the Cura 4.13 defs, clones the app, makes a
venv, writes `~/.octoprint/creds`, and installs + starts the `print-wizard`
systemd service (bound to `0.0.0.0:8765`). Re-run anytime to update
(`git pull && ansible-playbook ...`).

## Dev on macOS

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python src/server.py        # http://127.0.0.1:8765
python3 tests/test_compiler.py
python3 src/slicer.py polymaker-polyterra-pla-black glass-stock standard tests/fixtures/cube.stl
```

CuraEngine defaults to the Cura.app bundle; override with `CURA_ENGINE` /
`CURA_RESOURCES` env if needed.

## OctoPrint access

Helper + creds live in `~/.octoprint/` (see `~/docs/octoprint-192.168.1.200.md`).
On the Pi the wizard's creds point at `http://127.0.0.1:80` (same machine).

## Status

- Filaments: 1 (Polymaker PolyTerra PLA Black). Add more in `profiles/filaments/`.
- Plates: glass-stock (+10C bed, brim, cool-to-45 removal), whambam-pex-textured
  (+10C bed per WhamBam spec, squished first layer, no adhesion, remove-hot).
- Qualities: draft / standard / fine.
- UI overrides: adhesion (auto/none/skirt/brim/raft), supports (on/off).
- M701/M702 firmware support: confirm with M115 once the printer is idle.

"""OctoPrint REST client for the Print Wizard handoff (Phase 3).

Reuses the credentials in ~/.octoprint/creds (HOST/USER/PASS, chmod 600) that
the octo.sh helper uses. Session-cookie auth with CSRF on writes:

- GET /login/ seeds the csrf_token_<port> cookie.
- POST /api/login (JSON {user,pass}) with header X-CSRF-Token = that cookie
  value authenticates the session. (Header must be the hyphenated form;
  X-CSRFToken does not work.)
- Writes (upload, select) repeat the X-CSRF-Token header.

Safety: start_print() refuses unless the printer is Operational (idle). It will
not interrupt an active print -- the printer is busy during development, so the
wizard can upload + slice freely but "print it? yes" is gated on idle state.
"""
from __future__ import annotations

import os
import pathlib
import urllib.parse

import requests

CREDS_PATH = os.path.expanduser("~/.octoprint/creds")


def _load_creds(path: str = CREDS_PATH) -> dict:
    creds = {}
    for line in pathlib.Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        creds[k.strip()] = v.strip()
    for required in ("HOST", "USER", "PASS"):
        if required not in creds:
            raise RuntimeError(f"missing {required} in {path}")
    return creds


class OctoPrintClient:
    def __init__(self, creds: dict | None = None):
        c = creds or _load_creds()
        self.host = c["HOST"].rstrip("/")
        self.user = c["USER"]
        self.pwd = c["PASS"]
        self.s = requests.Session()
        self._logged_in = False

    def _csrf(self) -> str:
        for name, value in self.s.cookies.items():
            if name.startswith("csrf_token"):
                return value
        return ""

    def login(self) -> None:
        # Seed csrf cookie, then authenticate, capturing the session cookie.
        self.s.get(f"{self.host}/login/", timeout=10)
        tok = self._csrf()
        r = self.s.post(
            f"{self.host}/api/login",
            json={"user": self.user, "pass": self.pwd},
            headers={"X-CSRF-Token": tok},
            timeout=10,
        )
        r.raise_for_status()
        self._logged_in = True

    def _ensure(self) -> None:
        if not self._logged_in:
            self.login()

    def _write_headers(self) -> dict:
        return {"X-CSRF-Token": self._csrf()}

    # --- read ---
    def get(self, path: str) -> dict:
        self._ensure()
        r = self.s.get(f"{self.host}/api/{path.lstrip('/')}", timeout=15)
        r.raise_for_status()
        return r.json()

    def status(self) -> dict:
        """Printer + job state, plus a convenience `busy` flag."""
        job = self.get("job")
        printer = self.get("printer")
        state = job.get("state", {})
        text = state if isinstance(state, str) else state.get("text", "")
        busy = text not in ("Operational", "Offline", "")
        temps = {}
        for k, v in (printer.get("temperature") or {}).items():
            if isinstance(v, dict):
                temps[k] = {"actual": v.get("actual"), "target": v.get("target")}
        progress = job.get("progress") or {}
        return {
            "state": text,
            "busy": busy,
            "completion": progress.get("completion"),
            "print_time_left": progress.get("printTimeLeft"),
            "file": (job.get("job") or {}).get("file", {}).get("name"),
            "temps": temps,
        }

    # --- write ---
    def upload(self, gcode_path: str, select: bool = False, print: bool = False) -> dict:
        self._ensure()
        p = pathlib.Path(gcode_path)
        with p.open("rb") as fh:
            files = {"file": (p.name, fh, "text/plain")}
            data = {"select": "true" if select else "false", "print": "true" if print else "false"}
            r = self.s.post(
                f"{self.host}/api/files/local",
                files=files,
                data=data,
                headers=self._write_headers(),
                timeout=120,
            )
        r.raise_for_status()
        return r.json()

    def select_and_print(self, target_path: str, print: bool = True) -> dict:
        """target_path is the OctoPrint local path, e.g. 'myprint.gcode'."""
        self._ensure()
        url = f"{self.host}/api/files/local/{urllib.parse.quote(target_path)}"
        r = self.s.post(
            url,
            json={"command": "select", "print": print},
            headers=self._write_headers(),
            timeout=15,
        )
        r.raise_for_status()
        return r.json() if r.content else {}

    def start_print(self, target_path: str) -> dict:
        """Gated start: refuses while the printer is busy. Returns the select
        response, or raises RuntimeError with the current state if not idle."""
        st = self.status()
        if st["busy"]:
            raise RuntimeError(f"printer is {st['state']!r}, not idle -- refusing to start")
        return self.select_and_print(target_path, print=True)


if __name__ == "__main__":
    import json
    import sys

    cli = OctoPrintClient()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(cli.status(), indent=2))
    elif cmd == "upload" and len(sys.argv) >= 3:
        print(json.dumps(cli.upload(sys.argv[2]), indent=2))
    elif cmd == "start" and len(sys.argv) >= 3:
        print(json.dumps(cli.start_print(sys.argv[2]), indent=2))
    else:
        print("usage: octoprint_client.py {status|upload <gcode>|start <path>}", file=sys.stderr)
        sys.exit(2)

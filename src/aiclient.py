"""Anthropic API client for the wizard's AI features.

Shared by filament lookup (web search), the chat panel, and camera review.
Reads ANTHROPIC_API_KEY from env (the provision playbook sets it from the
anthropic_api_key secret in ~/.wizard-secrets.yml). No SDK -- just requests.

Model defaults to claude-sonnet-5; override with ANTHROPIC_MODEL.
"""
from __future__ import annotations

import base64
import json
import os
import re

import requests

API = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
WEB_SEARCH = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}


class NoKeyError(RuntimeError):
    pass


class InsufficientCreditsError(RuntimeError):
    pass


def _call(messages, system=None, model=None, tools=None, max_tokens=4096):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise NoKeyError(
            "ANTHROPIC_API_KEY not set -- add `anthropic_api_key:` to "
            "~/.wizard-secrets.yml and re-run the playbook"
        )
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {"model": model or MODEL, "max_tokens": max_tokens, "messages": messages}
    if system:
        body["system"] = system
    if tools:
        body["tools"] = tools
    r = requests.post(API, headers=headers, json=body, timeout=120)
    if r.status_code != 200:
        if "credit balance is too low" in r.text.lower():
            raise InsufficientCreditsError(
                "Anthropic account is out of credits -- add funds at "
                "console.anthropic.com and try again"
            )
        raise RuntimeError(f"Anthropic API {r.status_code}: {r.text[:500]}")
    return r.json()


def _text(resp):
    return "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")


def lookup_filament(query: str) -> dict:
    """Web-search the manufacturer spec sheet for a filament and return a
    profile JSON matching profiles/filaments/*.json shape."""
    system = (
        "You are a 3D-printing filament expert. Use web search to find the "
        "manufacturer's official spec sheet for the requested filament. Return "
        "ONLY a JSON object (no prose, no markdown fences) with exactly these "
        "keys: name, material, filament_diameter, nozzle_temp, bed_temp, flow, "
        "retraction (object with dist, speed), cooling (object with fan_max, "
        "layer1_fan), max_volumetric_speed, load_temp, unload_temp. "
        "nozzle_temp/bed_temp/load_temp/unload_temp in Celsius; retraction dist "
        "in mm; speed in mm/s; flow as a ratio (e.g. 0.98). If a value isn't "
        "published, use a safe default for the material."
    )
    resp = _call(
        [{"role": "user", "content": f"Look up filament specs for: {query}"}],
        system=system,
        tools=[WEB_SEARCH],
        max_tokens=4096,
    )
    text = _text(resp)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError("could not parse specs from response: " + text[:300])
    return json.loads(m.group(0))


CHAT_SYSTEM = (
    "You are the assistant embedded in Print Wizard, a control panel for a "
    "single Ender 3 running OctoPrint. Help with filament, slicing, "
    "adhesion, and troubleshooting questions. Be concise -- this renders in "
    "a small sidebar panel, not a document."
)


def chat(messages: list[dict]) -> str:
    """Multi-turn chat. `messages` is the full history as sent by the client,
    each item shaped {role: 'user'|'assistant', content: str}."""
    return _text(_call(messages, system=CHAT_SYSTEM, max_tokens=1024))


CAMERA_SYSTEM = (
    "You are reviewing a live webcam snapshot of an active 3D print on an "
    "Ender 3. Look for signs of print failure: spaghetti, warping, layer "
    "shift, nozzle collision, stringing, poor bed adhesion. Answer in 2-3 "
    "short sentences: what you see, and whether it looks healthy or needs "
    "attention. If the image is unclear or shows an empty bed, say so "
    "plainly instead of guessing."
)


def review_camera(image_bytes: bytes) -> str:
    """Ask Claude to look at a camera snapshot and flag print problems."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    resp = _call(
        [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": "How does this print look?"},
            ],
        }],
        system=CAMERA_SYSTEM,
        max_tokens=512,
    )
    return _text(resp)


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) or "Polymaker PolyTerra PLA Black"
    print(json.dumps(lookup_filament(q), indent=2))

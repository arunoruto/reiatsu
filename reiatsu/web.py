"""Web UI: floor plan editor and live map, served by the daemon (stdlib http.server).

Relative URLs only, so it also works behind a path prefix (HA ingress). No auth:
keep it on the LAN or behind ingress. The plan YAML stays the source of truth;
saving writes it (keeping ``<plan>.bak``) and restarts tracking on the new plan.

    GET  /                      the UI
    GET  /api/plan              {plan, derived, materials}
    POST /api/preview           plan -> derived (walls, doors, grid), or 400 {error}
    PUT  /api/plan              validate, back up, save, reload -> {plan, derived}
    GET  /api/state?device=ID   live snapshot, with the belief grid of device ID
    PUT  /api/background        image body -> {image}; GET /api/background/<image>
    POST /api/import/espresense ESPresense companion config.yaml body -> {plan}
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

import numpy as np
import yaml

from .channel import LOSS_PRIOR
from .plan import load_plan, make_grid

log = logging.getLogger("reiatsu.web")
STATIC = resources.files("reiatsu") / "web"
BACKGROUND = re.compile(r"^background\.(png|jpg)$")
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg"}
MAX_UPLOAD = 20 * 1024 * 1024


def derive(raw: dict) -> dict:
    """What the editor draws but can't compute itself: derived walls, door positions, grid cells."""
    plan = load_plan(raw)
    out = {
        "walls": [{"a": w.a.round(3).tolist(), "b": w.b.round(3).tolist(), "material": w.material, "rooms": list(w.rooms)} for w in plan.walls],
        "doors": [{"rooms": list(d.rooms), "at": d.at.round(3).tolist(), "width": d.width} for d in plan.doors],
        "receivers": {r.id: r.room for r in plan.receivers},
        "rooms": list(plan.rooms),
        "cells": [],
        "res": 0.2,
    }
    if plan.rooms:
        out["cells"] = make_grid(plan).xy.round(3).tolist()
    return out


def from_espresense(cfg: dict, floor: str | None = None) -> dict:
    """ESPresense companion config (floors -> rooms -> points, nodes -> point) to a plan dict.

    ESPresense has no doors, so every pair of rooms sharing a wall gets one in the middle:
    move or delete them in the editor.
    """
    floors = cfg.get("floors") or []
    if not floors:
        raise ValueError("no floors in the ESPresense config")
    fl = next((f for f in floors if floor in (None, f.get("id"), f.get("name"))), None)
    if fl is None:
        raise ValueError(f"floor {floor!r} not found")
    rooms = {r["name"]: [[float(x), float(y)] for x, y, *_ in r["points"]] for r in fl.get("rooms") or []}
    plan: dict = {"max_gap": 0.4, "rooms": rooms, "doors": []}
    pairs = {tuple(sorted(w.rooms)) for w in load_plan(plan).walls if len(w.rooms) == 2}
    plan["doors"] = [{"rooms": list(p), "width": 0.8} for p in sorted(pairs)]
    plan["receivers"] = [
        {"id": re.sub(r"\W+", "_", n["name"].lower()).strip("_"), "x": float(n["point"][0]), "y": float(n["point"][1]), "z": float(n["point"][2]) if len(n["point"]) > 2 else 1.0}
        for n in cfg.get("nodes") or []
        if n.get("point") and (not n.get("floors") or fl.get("id") in n["floors"])
    ]
    return plan


class App:
    """Shared between the HTTP threads and the tracking loop.

    The tracking loop publishes ``snapshot`` (a fresh dict per tick, swapped
    atomically); a successful save calls ``on_save(raw)`` from the HTTP thread.
    """

    def __init__(self, plan_path: str | Path, on_save: Callable[[dict], None]):
        self.path = Path(plan_path)
        self.raw = yaml.safe_load(self.path.read_text()) if self.path.exists() else {}
        self.raw = self.raw or {}
        self.on_save = on_save
        self.snapshot: dict = {}
        self.lock = threading.Lock()  # one save at a time

    def save(self, raw: dict) -> dict:
        derived = derive(raw)  # raises ValueError on a broken plan
        with self.lock:
            if self.path.exists():
                shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
            self.path.write_text(yaml.safe_dump(raw, sort_keys=False, default_flow_style=None, allow_unicode=True))
            self.raw = raw
            self.on_save(raw)
        return derived

    def save_background(self, data: bytes, ctype: str) -> str:
        ext = IMAGE_TYPES.get(ctype.split(";")[0].strip())
        if ext is None:
            raise ValueError(f"unsupported image type {ctype!r}, use PNG or JPEG")
        name = f"background.{ext}"
        (self.path.parent / name).write_bytes(data)
        return name

    def state(self, device: str | None) -> dict:
        snap = self.snapshot
        out = {k: v for k, v in snap.items() if k != "beliefs"}
        belief = snap.get("beliefs", {}).get(device)
        if belief is not None:
            out["belief"] = np.round(belief / max(belief.max(), 1e-12), 3).tolist()  # scaled to max 1
        return out


class Handler(BaseHTTPRequestHandler):
    app: App

    def log_message(self, format, *args):
        log.debug(format, *args)

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj).encode())

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length", 0))
        if n > MAX_UPLOAD:
            raise ValueError("upload too large")
        return self.rfile.read(n)

    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path.rstrip("/").split("/")[-2:] if url.path.startswith("/api/") else None
        if url.path in ("", "/", "/index.html"):
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        if url.path.startswith("/vendor/"):
            f = STATIC / "vendor" / Path(url.path).name
            if f.is_file():
                return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        elif url.path == "/api/plan":
            try:
                derived, error = derive(self.app.raw), None
            except ValueError as err:
                derived, error = None, str(err)
            return self._json({"plan": self.app.raw, "derived": derived, "error": error, "materials": sorted(LOSS_PRIOR)})
        elif url.path == "/api/state":
            return self._json(self.app.state(parse_qs(url.query).get("device", [None])[0]))
        elif path and path[0] == "background" and BACKGROUND.match(path[1]):
            f = self.app.path.parent / path[1]
            if f.is_file():
                return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "image/png")
        self._json({"error": "not found"}, 404)

    def _handle(self, fn):
        try:
            self._json(fn())
        except (ValueError, KeyError, TypeError, yaml.YAMLError) as err:
            self._json({"error": f"{type(err).__name__}: {err}" if not isinstance(err, ValueError) else str(err)}, 400)

    def do_POST(self):
        if self.path == "/api/preview":
            return self._handle(lambda: derive(_plan_body(self._body())))
        if self.path == "/api/import/espresense":
            return self._handle(lambda: {"plan": from_espresense(yaml.safe_load(self._body()))})
        self._json({"error": "not found"}, 404)

    def do_PUT(self):
        if self.path == "/api/plan":
            return self._handle(lambda: (lambda raw: {"plan": raw, "derived": self.app.save(raw)})(_plan_body(self._body())))
        if self.path == "/api/background":
            return self._handle(lambda: {"image": self.app.save_background(self._body(), self.headers.get("Content-Type", ""))})
        self._json({"error": "not found"}, 404)


def _plan_body(body: bytes) -> dict:
    raw = json.loads(body)
    if not isinstance(raw, dict):
        raise ValueError("plan must be a JSON object")
    return raw


def serve(app: App, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="reiatsu-http").start()
    log.info("web UI on http://%s:%d/ (no auth: keep it on the LAN or behind HA ingress)", host, server.server_address[1])
    return server

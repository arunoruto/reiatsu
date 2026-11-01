"""Calibration survey: live capture, the survey file, guided targets, accuracy, calibration file.

Files next to the plan:

- ``survey.json``: captured points, raw RSSI samples per receiver
- ``calibration.json``: fitted channel parameters (``.bak`` keeps the previous one)

Capture protocol: stand at the point, start, turn slowly once during the 30 s.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from scipy.spatial.distance import cdist

from .calibrate import Calibration, SurveyPoint, fit, next_point, residual_gp, starter_targets, summarize
from .channel import ChannelParams, Likelihood
from .locator import Advert
from .plan import Grid, Plan

SURVEY = "survey.json"
CALIBRATION = "calibration.json"
P_DROP = 0.1
ACTIVE_POINTS = 10  # planner points after the starter set, for the progress estimate
DONE_DIST = 0.75  # a starter target this close to a captured point counts as done
REFERENCE_WINDOW = 600.0  # seconds of a reference device's adverts used for calibration


@dataclass
class Capture:
    device: str
    x: float
    y: float
    held: str
    seconds: float
    t_start: float
    z: float = 1.0
    samples: dict[str, list[float]] = field(default_factory=dict)

    @property
    def t_end(self) -> float:
        return self.t_start + self.seconds


def n_sent(samples: dict[str, list[float]]) -> int:
    """Adverts the phone sent, estimated from the receiver that heard the most."""
    return round(max((len(v) for v in samples.values()), default=0) / (1 - P_DROP))


def to_point(entry: dict, receivers: list[str]) -> SurveyPoint:
    p = summarize([entry["x"], entry["y"]], entry["samples"], n_sent(entry["samples"]), receivers, P_DROP)
    p.z, p.device = entry.get("z", 1.0), entry["device"]
    return p


class Survey:
    """Survey file plus the capture in progress; ``on_advert`` runs in the tracking thread."""

    def __init__(self, folder: Path):
        self.path = folder / SURVEY
        self.entries: list[dict] = json.loads(self.path.read_text()) if self.path.exists() else []
        self.capture: Capture | None = None
        self.recent: dict[str, deque] = {}  # device -> (receipt time, receiver, rssi)
        self.lock = threading.Lock()

    def on_advert(self, a: Advert, now: float):
        with self.lock:
            c = self.capture
            if c and a.device_id == c.device and c.t_start <= now <= c.t_end:
                c.samples.setdefault(a.receiver_id, []).append(a.rssi)
            self.recent.setdefault(a.device_id, deque(maxlen=20000)).append((now, a.receiver_id, a.rssi))

    def start(self, device: str, x: float, y: float, seconds: float = 30, held: str = "hand") -> None:
        if not device:
            raise ValueError("pick the device you are holding")
        with self.lock:
            self.capture = Capture(device, float(x), float(y), held, float(seconds), time.time())

    def cancel(self):
        with self.lock:
            self.capture = None

    def status(self, receivers: list[str]) -> dict | None:
        """The capture in progress; once its time is up it is saved and reported as done."""
        with self.lock:
            c = self.capture
            if c is None:
                return None
            if time.time() >= c.t_end:
                self.capture = None
                entry = asdict(c) | {"id": str(int(c.t_start * 1000)), "t_end": c.t_end}
                if not any(c.samples.values()):
                    return {"done": True, "saved": False, "error": f"no adverts from {c.device} were heard"}
                self.entries.append(entry)
                self._write()
                return {"done": True, "saved": True, "id": entry["id"]}
            samples = {r: list(v) for r, v in c.samples.items()}
        per_rx = {r: {"n": len(samples.get(r, [])), "median": float(np.median(samples[r])) if samples.get(r) else None} for r in sorted(set(receivers) | set(samples))}
        return {
            "done": False, "device": c.device, "x": c.x, "y": c.y,
            "remaining": round(c.t_end - time.time(), 1), "seconds": c.seconds,
            "receivers": per_rx, "silent": [r for r in receivers if not samples.get(r)],
        }  # fmt: skip

    def delete(self, entry_id: str) -> bool:
        with self.lock:
            n = len(self.entries)
            self.entries = [e for e in self.entries if e["id"] != entry_id]
            self._write()
            return len(self.entries) < n

    def _write(self):
        self.path.write_text(json.dumps(self.entries))

    def points(self, plan: Plan) -> list[SurveyPoint]:
        ids = [r.id for r in plan.receivers]
        return [to_point(e, ids) for e in self.entries]

    def reference_points(self, plan: Plan, window: float = REFERENCE_WINDOW) -> list[SurveyPoint]:
        """Each reference device's recent adverts, as a survey point at its known spot."""
        ids, now, out = [r.id for r in plan.receivers], time.time(), []
        with self.lock:
            recent = {dev: list(self.recent.get(dev, ())) for dev in plan.references}
        for dev, xy in plan.references.items():
            samples: dict[str, list[float]] = {}
            for t, rx, rssi in recent[dev]:
                if now - t <= window:
                    samples.setdefault(rx, []).append(rssi)
            if any(samples.values()):
                out.append(to_point({"x": xy[0], "y": xy[1], "samples": samples, "device": dev}, ids))
        return out


# --- calibration file ---------------------------------------------------------


def load_calibration(folder: Path, plan: Plan) -> Calibration | None:
    """Fitted parameters from calibration.json (priors fill in receivers or materials added
    since), with the residual GP rebuilt from the survey."""
    path = folder / CALIBRATION
    if not path.exists() or not plan.receivers:
        return None
    saved = json.loads(path.read_text())
    prior = ChannelParams.prior(plan)
    params = ChannelParams(
        {r: saved["p0"].get(r, p) for r, p in prior.p0.items()},
        float(saved["n"]),
        {m: saved["loss"].get(m, l) for m, l in prior.loss.items()},
        float(saved.get("tx_offset", 0.0)),
    )
    points = Survey(folder).points(plan)
    return Calibration(params, residual_gp(plan, params, points) if points else None, saved.get("offsets", {}))


def save_calibration(folder: Path, cal: Calibration, n_points: int):
    path = folder / CALIBRATION
    if path.exists():
        shutil.copy2(path, path.with_name(CALIBRATION + ".bak"))
    p = cal.params
    path.write_text(json.dumps({"created": time.time(), "points": n_points, "p0": p.p0, "n": p.n, "loss": p.loss, "tx_offset": p.tx_offset, "offsets": cal.offsets}, indent=1))


# --- accuracy and guidance ------------------------------------------------------


def locate(plan: Plan, grid: Grid, cal: Calibration, p: SurveyPoint, mu: np.ndarray | None = None) -> int:
    """Most likely cell for one survey point's medians."""
    mu = cal.predict(plan, grid.xy) if mu is None else mu
    y = np.array([p.rssi.get(r.id, np.nan) for r in plan.receivers])
    return int(np.argmax(Likelihood()(mu + cal.offsets.get(p.device, 0.0), y)))


def _score(plan: Plan, grid: Grid, points: list[SurveyPoint], cals: list[Calibration]) -> dict:
    hits, errs, res = [], [], []
    for p, cal in zip(points, cals):
        cell = locate(plan, grid, cal, p)
        hits.append(grid.room[cell] == grid.room[grid.nearest(p.xy)])
        errs.append(float(np.linalg.norm(grid.xy[cell] - p.xy)))
        mu = cal.predict(plan, p.xy[None])[:, 0] + cal.offsets.get(p.device, 0.0)
        res += [abs(p.rssi[r.id] - mu[i]) for i, r in enumerate(plan.receivers) if r.id in p.rssi]
    if not points:
        return {"n": 0, "room_acc": None, "err_m": None, "rssi_err_db": None}
    return {"n": len(points), "room_acc": float(np.mean(hits)), "err_m": float(np.median(errs)), "rssi_err_db": float(np.mean(res)) if res else None}


def evaluate(plan: Plan, grid: Grid, cal: Calibration, points: list[SurveyPoint]) -> dict:
    """How well a given calibration locates the points (in-sample if it was fitted on them)."""
    return _score(plan, grid, points, [cal] * len(points))


def leave_one_out(plan: Plan, grid: Grid, points: list[SurveyPoint]) -> dict:
    """Each point located with a calibration fitted on all the others: an honest accuracy."""
    cals = [fit(plan, points[:i] + points[i + 1 :]) for i in range(len(points))] if len(points) > 1 else []
    return _score(plan, grid, points[: len(cals)], cals)


def residuals(plan: Plan, cal: Calibration, points: list[SurveyPoint]) -> list[float | None]:
    """Mean |measured − model| per point, over the receivers that heard it."""
    out = []
    for p in points:
        mu = cal.predict(plan, p.xy[None])[:, 0] + cal.offsets.get(p.device, 0.0)
        r = [abs(p.rssi[rx.id] - mu[i]) for i, rx in enumerate(plan.receivers) if rx.id in p.rssi]
        out.append(round(float(np.mean(r)), 1) if r else None)
    return out


def next_target(plan: Plan, grid: Grid, points: list[SurveyPoint]) -> dict:
    """Where to stand next: the nearest unvisited starter target, then the planner's pick."""
    starters = starter_targets(plan, grid)
    done = np.array([p.xy for p in points]) if points else np.zeros((0, 2))
    todo = [(p, label) for p, label in starters if not len(done) or cdist([p], done).min() >= DONE_DIST]
    if todo:
        last = done[-1] if len(done) else todo[0][0]
        xy, hint = min(todo, key=lambda t: np.linalg.norm(t[0] - last))
        phase = "starter"
    else:
        cal = fit(plan, points) if points else Calibration(ChannelParams.prior(plan))
        xy, hint, phase = next_point(plan, grid, cal, list(done)), "where the map is least certain", "active"
    return {
        "x": round(float(xy[0]), 2), "y": round(float(xy[1]), 2), "room": grid.rooms[grid.room[grid.nearest(xy)]],
        "hint": hint, "phase": phase, "done": len(points), "total": len(starters) + ACTIVE_POINTS, "starter_left": len(todo),
    }  # fmt: skip

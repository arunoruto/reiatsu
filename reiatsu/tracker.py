"""Grid HMM tracker: door-constrained motion, RSSI + mmWave evidence."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import dijkstra

from .channel import Likelihood
from .plan import Grid

MAX_SILENT_TICKS = 3600  # beyond an hour of silence the belief has long mixed


@dataclass
class Estimate:
    ts: float
    room: str
    room_probs: dict[str, float]
    x: float
    y: float
    sigma: float  # RMS position spread within the reported room, metres
    device_id: str | None = None


def motion_kernel(grid: Grid, sd: float = 0.7, chunk: int = 512) -> sparse.csr_matrix:
    """One-second move kernel: Gaussian in walking (geodesic) distance, so walls block it.

    Returned transposed, so ``kernel @ belief`` is the predicted belief.
    """
    rows = []
    for start in range(0, len(grid.xy), chunk):
        idx = np.arange(start, min(start + chunk, len(grid.xy)))
        d = dijkstra(grid.graph, indices=idx, limit=3 * sd)
        k = np.where(np.isfinite(d), np.exp(-0.5 * (d / sd) ** 2), 0.0)
        rows.append(sparse.csr_matrix(k / k.sum(1, keepdims=True)))
    return sparse.vstack(rows).T.tocsr()


@dataclass
class Tracker:
    """Belief over grid cells for one device; call :meth:`step` about once per second.

    Each second the device stays put with probability ``1 - p_move`` or takes
    one walking step through the kernel. Once nothing has been heard for
    ``silent_after`` seconds (a locked iPhone), ``p_move_silent`` applies, so
    the belief holds and only slowly spreads. ``p_jump`` leaks a little mass
    everywhere so evidence from a far room can still win after a silent walk.
    """

    grid: Grid
    mu: np.ndarray  # (R, C) expected RSSI per receiver and cell
    receivers: list[str]
    kernel: sparse.csr_matrix | None = None
    likelihood: Likelihood = field(default_factory=Likelihood)
    p_move: float = 0.15
    p_move_silent: float = 0.01
    p_jump: float = 3e-3  # per second: moved somewhere unseen, keeps far rooms recoverable
    silent_after: float = 30.0
    hysteresis: float = 0.3  # room switches only when another room leads by this much probability
    mmwave_fnr: float = 0.15  # P(sensor reports empty | device's person is in the area)
    mmwave_p_other: float = 0.5  # P(area reported occupied | device not there): other residents, false alarms
    mmwave_tau: float = 5.0  # seconds of a persisting sensor state worth one independent observation
    offsets: np.ndarray = field(default_factory=lambda: np.arange(-12.0, 12.1, 2.0))  # dB, this device vs the calibration phone
    offset_sd: float = 6.0  # prior on the device's tx offset
    offset_forget: float = 1e-3  # per heard step, lets the offset estimate move again
    # Per heard step, old evidence decays: readings of a device that doesn't move repeat the
    # same shadowing/map error, so they saturate instead of piling up into false certainty.
    evidence_forget: float = 0.1
    # False for a device nobody carries (a door lock): it doesn't move, and mmWave, which
    # sees people, says nothing about it.
    carried: bool = True

    def __post_init__(self):
        if self.kernel is None:
            self.kernel = motion_kernel(self.grid)
        self.rx_index = {r: i for i, r in enumerate(self.receivers)}
        self.belief = np.full(len(self.grid.xy), 1 / len(self.grid.xy))
        self.ts: float | None = None
        self.heard_ts: float | None = None
        self.room: int | None = None
        self.occupancy: dict[str, bool] = {}
        self.offset_w = np.exp(-0.5 * (self.offsets / self.offset_sd) ** 2)
        self.offset_w /= self.offset_w.sum()
        self._occ_factor = np.ones(len(self.grid.xy))
        self._listening = np.ones(len(self.receivers), bool)

    def set_listening(self, receivers: set[str]):
        """Receivers currently online: only those count as "not heard" when silent."""
        self._listening = np.array([r in receivers for r in self.receivers])

    def set_occupancy(self, area: str, occupied: bool) -> bool:
        """Fold in an mmWave state; False (and ignored) if ``area`` is no room or zone of the plan."""
        if area not in self.grid.areas:
            return False
        self.occupancy[area] = occupied
        if not self.carried:
            return True
        f, miss, other = np.ones(len(self.grid.xy)), self.mmwave_fnr, self.mmwave_p_other
        for name, occ in self.occupancy.items():
            mask = self.grid.areas[name]
            f *= np.where(mask, 1 - miss, other) if occ else np.where(mask, miss, 1 - other)
        self._occ_factor = f ** (1 / self.mmwave_tau)
        return True

    def hint(self, room: str, confidence: float = 0.9):
        """Weak label, e.g. "phone is charging" => bedroom."""
        self.belief *= np.where(self.grid.areas[room], confidence, 1 - confidence)
        self.belief /= self.belief.sum()

    def step(self, ts: float, readings: dict[str, float]) -> Estimate:
        """Advance to ``ts`` and fold in RSSI readings heard since the last step."""
        if self.ts is None:
            self.ts = ts
        ticks = int(round(ts - self.ts))
        for k in range(min(ticks, MAX_SILENT_TICKS)):
            silent = self.heard_ts is None or self.ts + k - self.heard_ts > self.silent_after
            p = 0.0 if not self.carried else self.p_move_silent if silent else self.p_move
            jump = self.p_jump if self.carried else self.p_jump / 100  # a lock isn't carried off
            b = (1 - p) * self.belief + p * (self.kernel @ self.belief) + jump / len(self.belief)
            b *= self._occ_factor
            self.belief = b / b.sum()
        self.ts += ticks
        y = np.full(len(self.receivers), np.nan)
        for rx, rssi in readings.items():
            if rx in self.rx_index:
                y[self.rx_index[rx]] = rssi
        if not np.isnan(y).all():
            # joint posterior over (cell, tx offset), offset kept as a small discrete set
            use = ~np.isnan(y) | self._listening
            ll = np.array([self.likelihood(self.mu[use] + o, y[use]) for o in self.offsets])  # (K, C)
            prior = self.belief ** (1 - self.evidence_forget)
            joint = np.exp(ll - ll.max()) * prior * self.offset_w[:, None]
            w = joint.sum(1)
            self.offset_w = (1 - self.offset_forget) * w / w.sum() + self.offset_forget / len(w)
            self.belief = joint.sum(0) / joint.sum()
            self.heard_ts = ts
        return self.estimate(ts)

    @property
    def offset(self) -> float:
        return float(self.offset_w @ self.offsets)

    def estimate(self, ts: float) -> Estimate:
        g = self.grid
        probs = np.bincount(g.room, weights=self.belief, minlength=len(g.rooms))
        best = int(np.argmax(probs))
        if self.room is None or probs[best] > probs[self.room] + self.hysteresis:
            self.room = best
        cells = g.room == self.room
        w = self.belief[cells]
        w = w / w.sum() if w.sum() > 0 else np.full(len(w), 1 / len(w))
        xy = w @ g.xy[cells]
        sigma = float(np.sqrt(w @ ((g.xy[cells] - xy) ** 2).sum(1)))
        return Estimate(ts, g.rooms[self.room], dict(zip(g.rooms, probs.round(4).tolist())), float(xy[0]), float(xy[1]), sigma)

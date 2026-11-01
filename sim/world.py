"""Simulated world: hidden channel truth, a person walking through doors, their phone's adverts."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.sparse.csgraph import dijkstra

from reiatsu.calibrate import NodeLink, SurveyPoint, summarize
from reiatsu.channel import LOSS_PRIOR, ChannelParams, predict, rf_position, geometry
from reiatsu.locator import Advert, Occupancy
from reiatsu.plan import Grid, Plan


@dataclass
class Truth:
    params: ChannelParams
    field: list[tuple[np.ndarray, np.ndarray]]  # per receiver: bump centres, weights
    field_len: float = 1.0
    body_db: float = 12.0  # shadowing when the body is between phone and receiver
    fading_sd: float = 5.0
    sensitivity: float = -100.0
    p_drop: float = 0.1

    def shadow(self, pts: np.ndarray) -> np.ndarray:
        """(R, N) spatially correlated shadowing the channel model doesn't know about."""
        out = []
        for centres, w in self.field:
            d2 = ((pts[:, None] - centres[None]) ** 2).sum(-1)
            out.append(np.exp(-0.5 * d2 / self.field_len**2) @ w)
        return np.array(out)

    def mean(self, plan: Plan, pts: np.ndarray, z: float = 1.0) -> np.ndarray:
        return predict(plan, self.params, pts, z) + self.shadow(pts)

    def body(self, plan: Plan, pts: np.ndarray, facing: np.ndarray) -> np.ndarray:
        """(R, N) 0 dB facing the receiver, body_db with the back to it."""
        rx = np.array([rf_position(plan, r) for r in plan.receivers])
        to = rx[:, None] - pts[None]
        cos = (to[..., 0] * np.cos(facing) + to[..., 1] * np.sin(facing)) / np.linalg.norm(to, axis=-1).clip(1e-6)
        return self.body_db * (1 - cos) / 2

    def hear(self, mean: np.ndarray, rng) -> np.ndarray:
        rssi = np.round(mean + rng.normal(0, self.fading_sd, mean.shape))
        heard = (rssi >= self.sensitivity) & (rng.random(mean.shape) > self.p_drop)
        return np.where(heard, rssi, np.nan)


def make_truth(plan: Plan, rng, field_sd: float = 3.0) -> Truth:
    prior = ChannelParams.prior(plan)
    params = ChannelParams(
        {r: p + rng.uniform(-5, 5) for r, p in prior.p0.items()},
        rng.uniform(1.8, 2.6),
        {m: LOSS_PRIOR[m][0] * rng.uniform(0.6, 1.5) for m in prior.loss},
        rng.uniform(-6, 6),
    )
    pts = np.vstack(list(plan.rooms.values()))
    lo, hi = pts.min(0), pts.max(0)
    field = [(rng.uniform(lo, hi, (40, 2)), rng.normal(0, 1, 40)) for _ in plan.receivers]
    truth = Truth(params, field)
    sample = truth.shadow(rng.uniform(lo, hi, (500, 2)))
    scale = field_sd / sample.std(1).clip(1e-9)
    truth.field = [(c, w * s) for (c, w), s in zip(field, scale)]
    return truth


@dataclass
class Trajectory:
    t: np.ndarray
    xy: np.ndarray
    room: np.ndarray  # index into grid.rooms
    facing: np.ndarray
    awake: np.ndarray


def walk(grid: Grid, rng, duration: float, dt: float = 0.5, dwell_mean: float = 180.0) -> Trajectory:
    """Dwell somewhere in a room, walk to a random spot in another room via the doors, repeat."""
    n = int(duration / dt)
    xy, room, facing = np.zeros((n, 2)), np.zeros(n, int), np.zeros(n)
    cell, head, i = int(rng.integers(len(grid.xy))), rng.uniform(0, 2 * np.pi), 0
    while i < n:
        for _ in range(int(max(20.0, rng.exponential(dwell_mean)) / dt)):
            if i == n:
                break
            head = rng.uniform(0, 2 * np.pi) if rng.random() < 0.01 else head + rng.normal(0, 0.05)
            xy[i], room[i], facing[i] = grid.xy[cell], grid.room[cell], head
            i += 1
        target_room = rng.choice([r for r in range(len(grid.rooms)) if r != grid.room[cell]])
        target = int(rng.choice(np.flatnonzero(grid.room == target_room)))
        _, pred = dijkstra(grid.graph, indices=cell, return_predecessors=True)
        path = [target]
        while path[-1] != cell:
            path.append(pred[path[-1]])
        path = path[::-1]
        speed, s = rng.uniform(0.6, 1.2), 0.0
        for a, b in zip(path, path[1:]):
            seg = grid.xy[b] - grid.xy[a]
            length = np.linalg.norm(seg)
            head = np.arctan2(seg[1], seg[0])
            while s < length and i < n:
                xy[i], facing[i] = grid.xy[a] + seg * s / length, head
                room[i] = grid.room[a] if s < length / 2 else grid.room[b]
                s += speed * dt
                i += 1
            s -= length
        cell = target
    # phone: awake ~90 s, locked ~5 min
    awake = np.zeros(n, bool)
    state = True
    for k in range(n):
        if rng.random() < dt / (90.0 if state else 300.0):
            state = not state
        awake[k] = state
    return Trajectory(np.arange(n) * dt, xy, room, facing, awake)


def adverts(plan: Plan, truth: Truth, traj: Trajectory, rng, device: str = "phone", locked_rate: float = 0.02) -> list[Advert]:
    """Awake: 0.5-2 Hz (rate drawn per awake spell); locked: sparse, often silent for minutes."""
    dt = traj.t[1] - traj.t[0]
    mean = truth.mean(plan, traj.xy) - truth.body(plan, traj.xy, traj.facing)
    ids = [r.id for r in plan.receivers]
    out, rate = [], 1.0
    for k in range(len(traj.t)):
        if traj.awake[k] and (k == 0 or not traj.awake[k - 1]):
            rate = rng.uniform(0.5, 2.0)
        for _ in range(rng.poisson((rate if traj.awake[k] else locked_rate) * dt)):
            ts = traj.t[k] + rng.uniform(0, dt)
            rssi = truth.hear(mean[:, k], rng)
            out += [Advert(ts, ids[r], device, float(v)) for r, v in enumerate(rssi) if not np.isnan(v)]
    return sorted(out, key=lambda a: a.ts)


def mmwave(grid: Grid, traj: Trajectory, rng, lag: float = 1.5, dropout: float = 0.15) -> list[Occupancy]:
    """Per-room presence sensors: lagged, and sometimes lose a person sitting still."""
    dt = traj.t[1] - traj.t[0]
    out = []
    for r, name in enumerate(grid.rooms):
        occ = np.roll(traj.room == r, int(lag / dt))
        occ[: int(lag / dt)] = False
        edges = np.flatnonzero(np.diff(np.r_[0, occ.astype(int), 0]))
        for start, end in zip(edges[::2], edges[1::2]):
            if (end - start) * dt > 90 and rng.random() < dropout:
                a = start + int(60 / dt)
                occ[a : min(end, a + int(rng.uniform(30, 120) / dt))] = False
        prev = False
        for k in np.flatnonzero(np.diff(np.r_[0, occ.astype(int)])):
            prev = not prev
            out.append(Occupancy(float(traj.t[k]), name, prev))
    return sorted(out, key=lambda o: o.ts)


def survey(plan: Plan, truth: Truth, pts: list[np.ndarray], rng, seconds: int = 25) -> list[SurveyPoint]:
    """Survey protocol: 1 Hz adverts while turning slowly through 360 degrees."""
    ids = [r.id for r in plan.receivers]
    out = []
    for p in pts:
        facing = np.linspace(0, 2 * np.pi, seconds, endpoint=False)
        here = np.repeat(np.asarray(p)[None], seconds, axis=0)
        rssi = truth.hear(truth.mean(plan, here) - truth.body(plan, here, facing), rng)
        samples = {ids[r]: row[~np.isnan(row)].tolist() for r, row in enumerate(rssi)}
        out.append(summarize(p, samples, seconds, ids))
    return out


def node_links(plan: Plan, truth: Truth, rng, samples: int = 30, min_frac: float = 0.3) -> list[NodeLink]:
    """Every receiver beacons for a while; every other receiver reports the median."""
    out = []
    for tx in plan.receivers:
        pos = rf_position(plan, tx)[None]
        for r, rx in enumerate(plan.receivers):
            if rx is tx:
                continue
            logd, counts = geometry(plan, rx, pos, tx.z)
            loss = np.array([truth.params.loss[m] for m in plan.materials])
            mean = truth.params.p0[rx.id] + truth.params.tx_offset - 10 * truth.params.n * logd[0] - counts[0] @ loss + truth.shadow(pos)[r, 0]
            rssi = truth.hear(np.full((1, samples), mean), rng)[0]
            heard = rssi[~np.isnan(rssi)]
            if len(heard) >= min_frac * samples:
                out.append(NodeLink(tx.id, rx.id, float(np.median(heard))))
    return out

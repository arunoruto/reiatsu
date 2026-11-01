"""Run a simulated scenario and print the metrics table.

    python -m sim.run --scenario default
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

from reiatsu.calibrate import Calibration, fit, next_point, starter_points
from reiatsu.channel import ChannelParams
from reiatsu.locator import Locator
from reiatsu.plan import load_plan, make_grid
from reiatsu.tracker import Tracker, motion_kernel

from . import world

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = {
    "default": dict(plan=ROOT / "examples/flat.yaml", minutes=120, active_points=10),
    "quick": dict(plan=ROOT / "examples/flat.yaml", minutes=15, active_points=3),
}
WARMUP = 30  # seconds ignored at the start of every run


class NearestReceiver:
    """Bermuda-like baseline: room of the receiver with the strongest smoothed RSSI."""

    def __init__(self, plan, alpha=0.3, timeout=30.0):
        self.rx = {r.id: r for r in plan.receivers}
        self.alpha, self.timeout = alpha, timeout
        self.ema, self.seen = {}, {}
        self.room, self.xy = None, (np.nan, np.nan)

    def advert(self, a):
        old = self.ema.get(a.receiver_id, a.rssi)
        self.ema[a.receiver_id] = old + self.alpha * (a.rssi - old)
        self.seen[a.receiver_id] = a.ts

    def tick(self, ts):
        fresh = {r: v for r, v in self.ema.items() if ts - self.seen[r] < self.timeout}
        if fresh:
            best = self.rx[max(fresh, key=fresh.get)]
            self.room, self.xy = best.room, tuple(best.xy)
        return self.room, self.xy


def replay(events, occupancy, seconds, step):
    """Feed events in time order, call ``step(ts)`` every second; returns per-second (room, x, y)."""
    out, ei, oi = [], 0, 0
    for ts in range(1, seconds + 1):
        while ei < len(events) and events[ei].ts < ts:
            step.advert(events[ei])
            ei += 1
        while occupancy is not None and oi < len(occupancy) and occupancy[oi].ts < ts:
            step.set_occupancy(occupancy[oi])
            oi += 1
        out.append(step.tick(ts))
    return out


class _LocatorRun:
    def __init__(self, locator):
        self.loc = locator
        self.advert, self.set_occupancy = locator.advert, locator.set_occupancy

    def tick(self, ts):
        est = self.loc.tick(ts)
        return (est[0].room, (est[0].x, est[0].y)) if est else (None, (np.nan, np.nan))


def metrics(true_room, pred_room, true_xy, pred_xy, min_dwell=30):
    s = slice(WARMUP, None)
    true_room, pred_room, true_xy, pred_xy = true_room[s], pred_room[s], true_xy[s], pred_xy[s]
    correct = pred_room == true_room
    changes = np.flatnonzero(true_room[1:] != true_room[:-1]) + 1
    ttc = []
    for c, end in zip(changes, np.r_[changes[1:], len(true_room)]):
        if end - c >= min_dwell:
            hit = np.flatnonzero(correct[c:end])
            ttc.append(hit[0] if len(hit) else end - c)
    flips = np.flatnonzero(pred_room[1:] != pred_room[:-1]) + 1
    false_flips = int((pred_room[flips] != true_room[flips]).sum())
    err = np.linalg.norm(pred_xy - true_xy, axis=1)
    return {
        "room_acc": correct.mean(),
        "ttc_median_s": float(np.median(ttc)) if ttc else np.nan,
        "false_flips_h": false_flips / (len(true_room) / 3600),
        "pos_err_m": float(np.nanmedian(err)),
    }


def run(scenario: str = "default", seed: int = 1, verbose: bool = True) -> dict[str, dict]:
    cfg = SCENARIOS[scenario]
    rng = np.random.default_rng(seed)
    plan = load_plan(cfg["plan"])
    grid = make_grid(plan)
    seconds = cfg["minutes"] * 60
    log = print if verbose else (lambda *a: None)

    truth = world.make_truth(plan, rng)
    traj = world.walk(grid, rng, seconds + 1)
    ads = world.adverts(plan, truth, traj, rng)
    occ = world.mmwave(grid, traj, rng)
    links = world.node_links(plan, truth, rng)
    log(f"plan: {len(plan.rooms)} rooms, {len(plan.walls)} walls, {len(plan.doors)} doors, {len(plan.receivers)} receivers, {len(grid.xy)} cells")
    log(f"sim: {cfg['minutes']} min, {len(ads)} receptions, {len(occ)} mmWave events, {len(links)} node links")

    starter = starter_points(plan, grid)
    obs = world.survey(plan, truth, starter, rng)
    cal_starter = fit(plan, obs, links)
    done, cal = list(starter), cal_starter
    for _ in range(cfg["active_points"]):
        p = next_point(plan, grid, cal, done)
        done.append(p)
        obs += world.survey(plan, truth, [p], rng)
        cal = fit(plan, obs, links)
    cal_active = cal
    n_s, n_a = len(starter), len(done)
    cals = {
        "priors only": Calibration(ChannelParams.prior(plan)),
        "node-to-node only": fit(plan, [], links),
        f"survey {n_s} pts": cal_starter,
        f"survey {n_a} pts": cal_active,
        f"survey {n_a} pts, no walls": fit(plan, obs, links, walls=False),
    }
    configs = [(name, c, False) for name, c in cals.items()] + [(f"survey {n_a} pts + mmWave", cal_active, True)]

    k = (np.arange(1, seconds + 1) / (traj.t[1] - traj.t[0])).astype(int)
    true_room = np.array([grid.rooms[r] for r in traj.room[k]])
    true_xy = traj.xy[k]
    kernel = motion_kernel(grid)
    rx_ids = [r.id for r in plan.receivers]

    results = {}
    t0 = time.time()
    out = replay(ads, None, seconds, NearestReceiver(plan))
    results["baseline: nearest receiver"] = metrics(true_room, np.array([o[0] for o in out]), true_xy, np.array([o[1] for o in out]))
    for name, c, use_mmwave in configs:
        mu = c.predict(plan, grid.xy)
        loc = Locator(lambda mu=mu: Tracker(grid, mu, rx_ids, kernel))
        out = replay(ads, occ if use_mmwave else None, seconds, _LocatorRun(loc))
        results[name] = metrics(true_room, np.array([o[0] for o in out]), true_xy, np.array([o[1] for o in out]))
    log(f"evaluated {len(results)} configs in {time.time() - t0:.0f}s\n")

    log(param_table(plan, truth, cal_active))
    log(table(results))
    return results


def param_table(plan, truth, cal) -> str:
    # Survey medians include the average body shadowing of a 360 degree turn.
    body = truth.body_db / 2
    rows = [("n", truth.params.n, cal.params.n)]
    rows += [(f"loss {m} dB", truth.params.loss[m], cal.params.loss[m]) for m in plan.materials]
    rows += [(f"P0 {r} (-body)", truth.params.p0[r] - body, cal.params.p0[r]) for r in truth.params.p0]
    lines = ["| parameter | hidden truth | fitted |", "|---|---:|---:|"]
    lines += [f"| {n} | {t:.2f} | {f:.2f} |" for n, t, f in rows]
    return "\n".join(lines) + "\n"


def table(results: dict[str, dict]) -> str:
    lines = [
        "| config | room acc | time to correct room (median s) | false flips/h | pos err (median m) |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, m in results.items():
        lines.append(f"| {name} | {m['room_acc']:.1%} | {m['ttc_median_s']:.0f} | {m['false_flips_h']:.1f} | {m['pos_err_m']:.2f} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", default="default", choices=sorted(SCENARIOS))
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    run(args.scenario, args.seed)


if __name__ == "__main__":
    main()

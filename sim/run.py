"""Run a simulated scenario over several seeds and print the metrics table.

    python -m sim.run --scenario default --seeds 5
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from reiatsu.calibrate import Calibration, fit, next_point, starter_points
from reiatsu.channel import ChannelParams
from reiatsu.locator import Locator
from reiatsu.plan import load_plan, make_grid
from reiatsu.tracker import Tracker, motion_kernel

from . import world

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = Path(__file__).with_name("expected.json")
FLAT = ROOT / "examples/flat.yaml"
# The real flat: mmWave in four rooms (none in bedroom and work), a second,
# untracked resident who also triggers the sensors, occasional false alarms.
REAL_SENSORS = dict(mmwave_rooms=["Bathroom", "Hallway", "Kitchen", "Living Room"], second_person=True, fp_per_hour=1.0)
SCENARIOS = {
    "default": dict(plan=FLAT, minutes=120, active_points=10, **REAL_SENSORS),
    "quick": dict(plan=FLAT, minutes=15, active_points=3, **REAL_SENSORS),
    "ideal-mmwave": dict(plan=FLAT, minutes=120, active_points=10, mmwave_rooms=None, second_person=False, fp_per_hour=0.0),
}
WARMUP = 30  # seconds ignored at the start of every run
CHECK_TOLERANCE = 0.03  # allowed drop in mean room accuracy vs expected.json


class NearestReceiver:
    """Bermuda-like baseline: nearest receiver by smoothed distance.

    Distance from RSSI with a fixed reference power; readings can't bring a
    receiver closer faster than ``max_speed``; increases are smoothed. The
    area switches only when another receiver is clearly closer (``margin``)
    for ``min_dwell`` seconds, or the current one goes stale.
    """

    def __init__(self, plan, ref_power=-59.0, n=2.0, alpha=0.3, timeout=30.0, max_speed=3.0, margin=0.8, min_dwell=3.0):
        self.rx = {r.id: r for r in plan.receivers}
        self.ref_power, self.n, self.alpha, self.timeout = ref_power, n, alpha, timeout
        self.max_speed, self.margin, self.min_dwell = max_speed, margin, min_dwell
        self.dist, self.seen = {}, {}
        self.cur, self.cand, self.cand_since = None, None, 0.0

    def advert(self, a):
        d = 10 ** ((self.ref_power - a.rssi) / (10 * self.n))
        old = self.dist.get(a.receiver_id)
        if old is not None:
            d = max(d, old - self.max_speed * (a.ts - self.seen[a.receiver_id]))
            d = old + self.alpha * (d - old) if d > old else d
        self.dist[a.receiver_id], self.seen[a.receiver_id] = d, a.ts

    def tick(self, ts):
        fresh = {r: d for r, d in self.dist.items() if ts - self.seen[r] < self.timeout}
        if fresh:
            best = min(fresh, key=fresh.__getitem__)
            if self.cur not in fresh:
                self.cur, self.cand = best, None
            elif best != self.cur and fresh[best] < self.margin * fresh[self.cur]:
                if self.cand != best:
                    self.cand, self.cand_since = best, ts
                if ts - self.cand_since >= self.min_dwell:
                    self.cur, self.cand = best, None
            else:
                self.cand = None
        if self.cur is None:
            return None, (np.nan, np.nan)
        rx = self.rx[self.cur]
        return rx.room, tuple(rx.xy)


class _LocatorRun:
    def __init__(self, locator):
        self.loc = locator
        self.advert, self.set_occupancy = locator.advert, locator.set_occupancy

    def tick(self, ts):
        est = self.loc.tick(ts)
        return (est[0].room, (est[0].x, est[0].y)) if est else (None, (np.nan, np.nan))


def replay(events, occupancy, seconds, sink):
    """Feed events in time order and call ``sink.tick(ts)`` every second."""
    out, ei, oi = [], 0, 0
    for ts in range(1, seconds + 1):
        while ei < len(events) and events[ei].ts < ts:
            sink.advert(events[ei])
            ei += 1
        while occupancy and oi < len(occupancy) and occupancy[oi].ts < ts:
            sink.set_occupancy(occupancy[oi])
            oi += 1
        out.append(sink.tick(ts))
    return out


def metrics(true_room, pred_room, true_xy, pred_xy, awake, min_dwell=30):
    s = slice(WARMUP, None)
    true_room, pred_room, true_xy, pred_xy, awake = true_room[s], pred_room[s], true_xy[s], pred_xy[s], awake[s]
    correct = pred_room == true_room
    changes = np.flatnonzero(true_room[1:] != true_room[:-1]) + 1
    ttc = []
    for c, end in zip(changes, np.r_[changes[1:], len(true_room)]):
        if end - c >= min_dwell:
            hit = np.flatnonzero(correct[c:end])
            ttc.append(hit[0] if len(hit) else end - c)
    flips = np.flatnonzero(pred_room[1:] != pred_room[:-1]) + 1
    false_flips = int((pred_room[flips] != true_room[flips]).sum())
    return {
        "room_acc": correct.mean(),
        "acc_awake": correct[awake].mean() if awake.any() else np.nan,
        "acc_locked": correct[~awake].mean() if (~awake).any() else np.nan,
        "ttc_median_s": float(np.median(ttc)) if ttc else np.nan,
        "false_flips_h": false_flips / (len(true_room) / 3600),
        "pos_err_m": float(np.nanmedian(np.linalg.norm(pred_xy - true_xy, axis=1))),
    }


def run(scenario: str = "default", seed: int = 1) -> dict:
    """One seed: returns {"metrics": {config: {...}}, "params": {...}, "info": str}."""
    cfg = SCENARIOS[scenario]
    rng = np.random.default_rng(seed)
    plan = load_plan(cfg["plan"])
    grid = make_grid(plan)
    seconds = cfg["minutes"] * 60

    truth = world.make_truth(plan, rng)
    traj = world.walk(grid, rng, seconds + 1)
    people = [traj] + ([world.walk(grid, rng, seconds + 1)] if cfg["second_person"] else [])
    ads = world.adverts(plan, truth, traj, rng)
    occ = world.mmwave(grid, people, rng, cfg["mmwave_rooms"], fp_per_hour=cfg["fp_per_hour"])
    links = world.node_links(plan, truth, rng)

    starter = starter_points(plan, grid)
    obs = world.survey(plan, truth, starter, rng)
    cal_starter = fit(plan, obs, links)
    done, cal, active_obs = list(starter), cal_starter, list(obs)
    n_extra = cfg["active_points"]
    for _ in range(n_extra):
        p = next_point(plan, grid, cal, done)
        done.append(p)
        active_obs += world.survey(plan, truth, [p], rng)
        cal = fit(plan, active_obs, links)
    random_pts = [grid.xy[i] for i in rng.choice(len(grid.xy), n_extra, replace=False)]
    cal_random = fit(plan, obs + world.survey(plan, truth, random_pts, rng), links)
    cals = {
        "priors only": Calibration(ChannelParams.prior(plan)),
        "node-to-node only": fit(plan, [], links),
        "survey: starter": cal_starter,
        f"survey: +{n_extra} active": cal,
        f"survey: +{n_extra} random": cal_random,
        f"survey: +{n_extra} active, no walls": fit(plan, active_obs, links, walls=False),
    }
    configs = [(name, c, False) for name, c in cals.items()] + [(f"survey: +{n_extra} active + mmWave", cal, True)]

    k = (np.arange(1, seconds + 1) / (traj.t[1] - traj.t[0])).astype(int)
    true_room = np.array([grid.rooms[r] for r in traj.room[k]])
    true_xy, awake = traj.xy[k], traj.awake[k]
    # what a perfect calibration would predict: survey medians include half the body loss
    true_mu = truth.mean(plan, grid.xy) - truth.body_db / 2
    kernel = motion_kernel(grid)
    rx_ids = [r.id for r in plan.receivers]

    def score(out):
        return metrics(true_room, np.array([o[0] for o in out]), true_xy, np.array([o[1] for o in out]), awake)

    results = {"baseline: nearest receiver (Bermuda-like)": score(replay(ads, None, seconds, NearestReceiver(plan))) | {"map_err_db": np.nan}}
    for name, c, use_mmwave in configs:
        mu = c.predict(plan, grid.xy)
        loc = Locator(lambda mu=mu: Tracker(grid, mu, rx_ids, kernel))
        res = score(replay(ads, occ if use_mmwave else None, seconds, _LocatorRun(loc)))
        results[name] = res | {"map_err_db": float(np.abs(mu - true_mu).mean())}

    p = cal.params
    params = {"n": p.n - truth.params.n}
    params |= {f"loss {m}": p.loss[m] - truth.params.loss[m] for m in plan.materials}
    params["P0 (all receivers)"] = [p.p0[r] - (truth.params.p0[r] - truth.body_db / 2) for r in p.p0]
    info = (
        f"{len(plan.rooms)} rooms, {len(plan.walls)} walls, {len(plan.doors)} doors, {len(plan.receivers)} receivers, "
        f"{len(grid.xy)} cells; {cfg['minutes']} min, starter survey {len(starter)} pts, "
        f"mmWave in {cfg['mmwave_rooms'] or 'all rooms'}, second person: {cfg['second_person']}, "
        f"false alarms/h/sensor: {cfg['fp_per_hour']}"
    )
    return {"metrics": results, "params": params, "info": info}


def _run_args(a):
    return run(*a)


def aggregate(runs: list[dict]) -> dict[str, dict[str, tuple[float, float]]]:
    out = {}
    for name in runs[0]["metrics"]:
        out[name] = {}
        for key in runs[0]["metrics"][name]:
            v = np.array([r["metrics"][name][key] for r in runs], float)
            out[name][key] = (np.nanmean(v), np.nanstd(v)) if not np.isnan(v).all() else (np.nan, np.nan)
    return out


COLUMNS = [
    ("room_acc", "room acc", "{:.1%}"),
    ("acc_awake", "acc awake", "{:.1%}"),
    ("acc_locked", "acc locked", "{:.1%}"),
    ("ttc_median_s", "time to correct room (s)", "{:.0f}"),
    ("false_flips_h", "false flips/h", "{:.1f}"),
    ("pos_err_m", "pos err (m)", "{:.2f}"),
    ("map_err_db", "RSSI map err (dB)", "{:.1f}"),
]


def _fmt(fmt, m, s):
    if np.isnan(m):
        return "–"
    if "%" in fmt:
        return f"{m:.1%} ± {s * 100:.1f}"
    return f"{fmt.format(m)} ± {fmt.format(s)}"


def table(agg) -> str:
    lines = ["| config | " + " | ".join(c[1] for c in COLUMNS) + " |", "|---|" + "---:|" * len(COLUMNS)]
    for name, m in agg.items():
        lines.append(f"| {name} | " + " | ".join(_fmt(fmt, *m[key]) for key, _, fmt in COLUMNS) + " |")
    return "\n".join(lines)


def param_table(runs) -> str:
    lines = ["| fitted − truth (survey +active) | mean | sd |", "|---|---:|---:|"]
    for key in runs[0]["params"]:
        v = np.ravel([r["params"][key] for r in runs])
        lines.append(f"| {key} | {v.mean():+.2f} | {v.std():.2f} |")
    return "\n".join(lines)


def check(scenario: str, agg) -> list[str]:
    expected = json.loads(EXPECTED.read_text()).get(scenario, {})
    return [
        f"{name}: room acc {agg[name]['room_acc'][0]:.1%} < expected {exp:.1%} - {CHECK_TOLERANCE:.0%}"
        for name, exp in expected.items()
        if name in agg and agg[name]["room_acc"][0] < exp - CHECK_TOLERANCE
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", default="default", choices=sorted(SCENARIOS))
    ap.add_argument("--seeds", type=int, default=5, help="number of seeds, starting at --seed")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=None, help="parallel processes (default: all cores)")
    ap.add_argument("--check", action="store_true", help=f"fail if mean room accuracy drops below {EXPECTED.name}")
    ap.add_argument("--save-expected", action="store_true", help=f"write mean room accuracy to {EXPECTED.name}")
    args = ap.parse_args()

    t0 = time.time()
    seeds = [(args.scenario, s) for s in range(args.seed, args.seed + args.seeds)]
    with ProcessPoolExecutor(args.jobs) as pool:
        runs = list(pool.map(_run_args, seeds))
    agg = aggregate(runs)
    print(f"{runs[0]['info']}\nseeds {args.seed}..{args.seed + args.seeds - 1}, mean ± sd, {time.time() - t0:.0f}s\n")
    print(table(agg) + "\n")
    print(param_table(runs))

    if args.save_expected:
        data = json.loads(EXPECTED.read_text()) if EXPECTED.exists() else {}
        data[args.scenario] = {name: round(float(m["room_acc"][0]), 3) for name, m in agg.items()}
        EXPECTED.write_text(json.dumps(data, indent=2) + "\n")
    if args.check:
        failures = check(args.scenario, agg)
        print("\n" + ("\n".join(f"REGRESSION {f}" for f in failures) or "check: ok"))
        sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

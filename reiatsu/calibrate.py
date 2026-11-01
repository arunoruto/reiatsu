"""Calibration: robust channel fit, GP residual map, and the survey planner.

Survey protocol per point: stand at the point for 20-30 s holding the phone
as usual and turn slowly through 360 degrees. Per receiver keep the median
RSSI over *all* adverts sent, unheard ones counting as below sensitivity
(the median of only the heard ones is biased high for weak receivers). If
the median itself is below sensitivity the receiver counts as "not heard",
a censored observation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.distance import cdist

from .channel import LOSS_PRIOR, N_PRIOR, P0_PRIOR, ChannelParams, Likelihood, geometry, predict, rf_position
from .plan import Grid, Plan

TX_OFFSET_PRIOR = (0.0, 10.0)


@dataclass
class SurveyPoint:
    xy: np.ndarray
    rssi: dict[str, float]  # median per receiver that heard the phone
    missing: frozenset[str] = frozenset()  # receivers that did not
    weight: float = 1.0
    z: float = 1.0


@dataclass
class NodeLink:
    """Receiver ``rx`` hearing receiver ``tx`` beaconing (median RSSI)."""

    tx: str
    rx: str
    rssi: float


@dataclass
class WeakLabel:
    """Opportunistic label with a room but no position (phone charging => bedroom)."""

    room: str
    rssi: dict[str, float]
    missing: frozenset[str] = frozenset()


def censored_median(heard: list[float], n_sent: int, p_drop: float = 0.1) -> float | None:
    """Median RSSI over all ``n_sent`` adverts when only ``heard`` were received.

    Randomly dropped adverts (``p_drop``) say nothing about signal strength;
    the rest of the unheard ones were below sensitivity, so they sit at the
    bottom of the ranking. None if the median is below sensitivity.
    """
    rank = int(0.5 * n_sent * (1 - p_drop))
    v = sorted(heard, reverse=True)
    return float(v[rank]) if rank < len(v) else None


def summarize(xy, samples: dict[str, list[float]], n_adverts: int, receivers: list[str], p_drop: float = 0.1) -> SurveyPoint:
    med = {r: censored_median(samples.get(r, []), n_adverts, p_drop) for r in receivers}
    heard = {r: m for r, m in med.items() if m is not None}
    return SurveyPoint(np.asarray(xy, float), heard, frozenset(set(receivers) - set(heard)))


# --- residual map -----------------------------------------------------------


class ResidualGP:
    """Per-receiver Gaussian process on fit residuals: the fingerprint part of the hybrid.

    ponytail: isotropic RBF ignores walls; a wall-aware kernel if residuals jump at walls.
    """

    def __init__(self, data: dict[int, tuple[np.ndarray, np.ndarray]], n_rx: int, length=1.5, sd=3.0, noise=2.0):
        self.n_rx, self.length, self.sd = n_rx, length, sd
        self.fits = {}
        for r, (x, y) in data.items():
            chol = np.linalg.cholesky(self._k(x, x) + noise**2 * np.eye(len(x)))
            self.fits[r] = (x, chol, np.linalg.solve(chol.T, np.linalg.solve(chol, y)))

    def _k(self, a, b):
        return self.sd**2 * np.exp(-0.5 * cdist(a, b, "sqeuclidean") / self.length**2)

    def mean(self, pts):
        out = np.zeros((self.n_rx, len(pts)))
        for r, (x, _, alpha) in self.fits.items():
            out[r] = self._k(pts, x) @ alpha
        return out

    def var(self, pts):
        out = np.full((self.n_rx, len(pts)), self.sd**2)
        for r, (x, chol, _) in self.fits.items():
            v = np.linalg.solve(chol, self._k(x, pts))
            out[r] -= (v**2).sum(0)
        return out


@dataclass
class Calibration:
    params: ChannelParams
    gp: ResidualGP | None = None

    def predict(self, plan: Plan, pts: np.ndarray) -> np.ndarray:
        mu = predict(plan, self.params, pts)
        return mu + self.gp.mean(pts) if self.gp else mu

    def var(self, plan: Plan, pts: np.ndarray) -> np.ndarray:
        if self.gp:
            return self.gp.var(pts)
        return np.full((len(plan.receivers), len(pts)), 9.0)


# --- robust fit -------------------------------------------------------------


def _rows(plan: Plan, points: list[SurveyPoint], links: list[NodeLink]):
    """Observation table: receiver index, log10 d, wall counts, is_link, rssi (NaN = censored), weight."""
    ids = [r.id for r in plan.receivers]
    by_id = {r.id: r for r in plan.receivers}
    cols = {k: [] for k in ("rx", "logd", "counts", "link", "y", "w")}

    def add(r, logd, counts, link, y, w):
        for k, v in zip(cols, (r, logd, counts, link, y, w)):
            cols[k].append(v)

    for p in points:
        for r, rx in enumerate(plan.receivers):
            if rx.id in p.rssi or rx.id in p.missing:
                logd, counts = geometry(plan, rx, p.xy[None], p.z)
                add(r, logd[0], counts[0], False, p.rssi.get(rx.id, np.nan), p.weight)
    for lk in links:
        tx, rx = by_id[lk.tx], by_id[lk.rx]
        logd, counts = geometry(plan, rx, rf_position(plan, tx)[None], tx.z)
        add(ids.index(lk.rx), logd[0], counts[0], True, lk.rssi, 1.0)
    out = {k: np.array(v, dtype=int if k == "rx" else float) for k, v in cols.items()}
    out["counts"] = out["counts"].reshape(len(out["y"]), len(plan.materials))
    return out


def _fit_params(plan, rows, walls, sigma, sensitivity) -> ChannelParams:
    R, mats = len(plan.receivers), plan.materials if walls else []
    M = len(mats)
    p0 = [P0_PRIOR[0] if r.p0 is None else r.p0 for r in plan.receivers]
    mean = np.r_[p0, N_PRIOR[0], [LOSS_PRIOR[m][0] for m in mats], TX_OFFSET_PRIOR[0]]
    sd = np.r_[np.full(R, P0_PRIOR[1]), N_PRIOR[1], [LOSS_PRIOR[m][1] for m in mats], TX_OFFSET_PRIOR[1]]
    counts = rows["counts"][:, [plan.materials.index(m) for m in mats]] if M else np.zeros((len(rows["y"]), 0))
    heard = ~np.isnan(rows["y"])

    def residuals(th):
        p0, n, loss, tx = th[:R], th[R], th[R + 1 : R + 1 + M], th[-1]
        pred = p0[rows["rx"]] + tx * rows["link"] - 10 * n * rows["logd"] - counts @ loss
        res = np.where(heard, pred - np.nan_to_num(rows["y"]), np.maximum(0, pred - sensitivity))
        return np.r_[rows["w"] * res / sigma, (th - mean) / sd]

    lo = np.r_[np.full(R, -100.0), 1.2, np.zeros(M), -30.0]
    hi = np.r_[np.full(R, -20.0), 5.0, np.full(M, 40.0), 30.0]
    th = least_squares(residuals, mean, bounds=(lo, hi), loss="huber", f_scale=1.0).x
    loss = dict(zip(mats, th[R + 1 : R + 1 + M])) if walls else {m: 0.0 for m in plan.materials}
    return ChannelParams(dict(zip([r.id for r in plan.receivers], th[:R])), float(th[R]), loss, float(th[-1]))


def _place_weak(plan, grid, params, label: WeakLabel) -> SurveyPoint:
    """Put a weak label at its most likely cell inside the labelled room."""
    cells = grid.xy[grid.areas[label.room]]
    y = np.array([label.rssi.get(r.id, np.nan) for r in plan.receivers])
    best = cells[np.argmax(Likelihood()(predict(plan, params, cells), y))]
    return SurveyPoint(best, label.rssi, label.missing, weight=0.3)


def fit(
    plan: Plan,
    points: list[SurveyPoint],
    links: Sequence[NodeLink] = (),
    weak: Sequence[WeakLabel] = (),
    grid: Grid | None = None,
    *,
    walls: bool = True,
    use_gp: bool = True,
    sigma: float = 3.0,
    sensitivity: float = -100.0,
) -> Calibration:
    """Huber fit of P0 per receiver, path-loss exponent and per-material wall loss,
    with priors; then a GP on the survey residuals."""
    points, links = list(points), list(links)
    params = _fit_params(plan, _rows(plan, points, links), walls, sigma, sensitivity)
    if weak:
        if grid is None:
            raise ValueError("weak labels need the grid")
        points += [_place_weak(plan, grid, params, w) for w in weak]
        params = _fit_params(plan, _rows(plan, points, links), walls, sigma, sensitivity)
    if not use_gp or not points:
        return Calibration(params)
    mu = predict(plan, params, np.array([p.xy for p in points]))
    data = {}
    for r, rx in enumerate(plan.receivers):
        idx = [i for i, p in enumerate(points) if rx.id in p.rssi]
        if idx:
            data[r] = (np.array([points[i].xy for i in idx]), np.array([points[i].rssi[rx.id] for i in idx]) - mu[r, idx])
    return Calibration(params, ResidualGP(data, len(plan.receivers)))


# --- survey planner ---------------------------------------------------------


def _dedupe(pts, min_dist=0.5):
    kept = []
    for p in pts:
        if all(np.linalg.norm(p - q) >= min_dist for q in kept):
            kept.append(p)
    return kept


def starter_points(plan: Plan, grid: Grid) -> list[np.ndarray]:
    """Room centres, ~1 m from each receiver, pairs straddling each interior wall, doorways."""
    pts = []
    for i in range(len(grid.rooms)):
        cells = grid.xy[grid.room == i]
        pts.append(cells[np.argmin(np.linalg.norm(cells - cells.mean(0), axis=1))])
    for rx in plan.receivers:
        cells = grid.xy[grid.areas[rx.room]]
        pts.append(cells[np.argmin(abs(np.linalg.norm(cells - rx.xy, axis=1) - 1.0))])
    for w in plan.walls:
        length = np.linalg.norm(w.b - w.a)
        if len(w.rooms) != 2 or length < 0.6:
            continue
        u = (w.b - w.a) / length
        for s in (1, -1):
            target = (w.a + w.b) / 2 + s * 0.6 * np.array([-u[1], u[0]])
            c = grid.nearest(target)
            if np.linalg.norm(grid.xy[c] - target) < 0.3:
                pts.append(grid.xy[c])
    pts += [grid.xy[grid.nearest(d.at)] for d in plan.doors]
    return _dedupe(pts)


def next_point(plan: Plan, grid: Grid, cal: Calibration, done: list[np.ndarray], sigma: float = 5.0, min_dist: float = 0.75) -> np.ndarray:
    """Active learning: the cell whose fingerprint is most uncertain and most
    easily confused with another room."""
    mu = cal.predict(plan, grid.xy)  # (R, C)
    sq = (mu**2).sum(0)
    d2 = np.maximum(sq[:, None] + sq[None, :] - 2 * mu.T @ mu, 0)
    lik = np.exp(-d2 / (2 * sigma**2))
    same = grid.room[:, None] == grid.room[None, :]
    confusion = 1 - (lik * same).sum(1) / lik.sum(1)
    score = (confusion + 0.05) * cal.var(plan, grid.xy).mean(0)
    if done:
        score[cdist(grid.xy, np.array(done)).min(1) < min_dist] = -np.inf
    return grid.xy[np.argmax(score)]

"""Wall-aware channel model and the RSSI observation likelihood.

    RSSI(rx, p) = P0_rx - 10 n log10(d) - sum(loss[material] for walls crossed by rx -> p)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.special import gammaln, stdtr

from .plan import Plan, Receiver, boundary_dist, crosses

# (mean, sd) priors at 2.4 GHz
P0_PRIOR = (-59.0, 6.0)  # dBm at 1 m, phone advert as heard by an ESPHome proxy
N_PRIOR = (2.0, 0.4)
LOSS_PRIOR = {
    "drywall": (3.0, 1.5),
    "brick": (10.0, 3.0),
    "concrete": (15.0, 4.0),
    "glass": (2.0, 1.0),
}
MIN_DIST = 0.3


@dataclass
class ChannelParams:
    p0: dict[str, float]
    n: float
    loss: dict[str, float]
    tx_offset: float = 0.0  # receiver-as-beacon tx power relative to the phone

    @classmethod
    def prior(cls, plan: Plan) -> ChannelParams:
        unknown = set(plan.materials) - set(LOSS_PRIOR)
        if unknown:
            raise ValueError(f"unknown wall materials {sorted(unknown)}, known: {sorted(LOSS_PRIOR)}")
        return cls(
            {r.id: P0_PRIOR[0] for r in plan.receivers},
            N_PRIOR[0],
            {m: LOSS_PRIOR[m][0] for m in plan.materials},
        )


def rf_position(plan: Plan, rx: Receiver) -> np.ndarray:
    """Receivers listed on a wall or corner are mounted just inside their room."""
    poly = plan.rooms[rx.room]
    if boundary_dist(poly, rx.xy[None])[0] > 0.1:
        return rx.xy
    toward = poly.mean(0) - rx.xy
    return rx.xy + 0.15 * toward / np.linalg.norm(toward)


def wall_counts(plan: Plan, src: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """(N, M) number of walls of each material in ``plan.materials`` crossed by src -> pts."""
    mats = plan.materials
    out = np.zeros((len(pts), len(mats)))
    for w in plan.walls:
        out[:, mats.index(w.material)] += crosses(src, pts, w.a, w.b)
    return out


def geometry(plan: Plan, rx: Receiver, pts: np.ndarray, z: float = 1.0):
    """log10 distance (N,) and wall counts (N, M) from receiver to points at height z."""
    src = rf_position(plan, rx)
    d = np.sqrt(((pts - src) ** 2).sum(1) + (z - rx.z) ** 2)
    return np.log10(np.maximum(d, MIN_DIST)), wall_counts(plan, src, pts)


def predict(plan: Plan, params: ChannelParams, pts: np.ndarray, z: float = 1.0) -> np.ndarray:
    """(R, N) mean RSSI at points for every receiver."""
    loss = np.array([params.loss.get(m, 0.0) for m in plan.materials])
    rows = []
    for rx in plan.receivers:
        logd, counts = geometry(plan, rx, pts, z)
        rows.append(params.p0[rx.id] - 10 * params.n * logd - counts @ loss)
    return np.array(rows)


@dataclass
class Likelihood:
    """Student-t RSSI noise plus an explicit "not heard" term.

    A receiver hears an advert with probability (1 - p_drop) * P(rssi > sensitivity);
    the t-distribution keeps body shadowing and multipath outliers from
    dominating the posterior.
    """

    sigma: float = 6.0
    nu: float = 4.0
    sensitivity: float = -100.0
    p_drop: float = 0.1
    _const: float = field(init=False)

    def __post_init__(self):
        nu = self.nu
        self._const = gammaln((nu + 1) / 2) - gammaln(nu / 2) - 0.5 * np.log(nu * np.pi) - np.log(self.sigma)

    def __call__(self, mu: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Log-likelihood over cells. mu: (R, C) predictions, y: (R,) RSSI, NaN = not heard."""
        heard = ~np.isnan(y)
        z = (y[heard, None] - mu[heard]) / self.sigma
        ll = (np.log1p(-self.p_drop) + self._const - (self.nu + 1) / 2 * np.log1p(z**2 / self.nu)).sum(0)
        if (~heard).any():
            below = stdtr(self.nu, (self.sensitivity - mu[~heard]) / self.sigma)
            ll = ll + np.log(self.p_drop + (1 - self.p_drop) * below).sum(0)
        return ll

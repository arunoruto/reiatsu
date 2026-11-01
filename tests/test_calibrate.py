from pathlib import Path

import numpy as np
import pytest

from reiatsu.calibrate import WeakLabel, fit, next_point, starter_points, summarize
from reiatsu.plan import load_plan, make_grid
from sim import world

FLAT = Path(__file__).parent.parent / "sim/flat.yaml"


@pytest.fixture(scope="module")
def setup():
    rng = np.random.default_rng(7)
    plan = load_plan(FLAT)
    grid = make_grid(plan)
    truth = world.make_truth(plan, rng, field_sd=0.0)
    pts = starter_points(plan, grid)
    obs = world.survey(plan, truth, pts, rng)
    links = world.node_links(plan, truth, rng)
    return plan, grid, truth, pts, obs, links


def test_fit_recovers_hidden_parameters(setup):
    plan, _, truth, _, obs, links = setup
    cal = fit(plan, obs, links)
    body = truth.body_db / 2  # mean body shadowing over a 360 degree turn
    assert cal.params.n == pytest.approx(truth.params.n, abs=0.25)
    for m in plan.materials:
        assert cal.params.loss[m] == pytest.approx(truth.params.loss[m], abs=1.5), m
    for r, p0 in truth.params.p0.items():
        assert cal.params.p0[r] == pytest.approx(p0 - body, abs=2.5), r
    # calibrated beats the priors on predicting the truth
    grid_pts = make_grid(plan).xy
    true_mu = truth.mean(plan, grid_pts) - body
    prior = fit(plan, [], [], use_gp=False)
    assert np.abs(cal.predict(plan, grid_pts) - true_mu).mean() < np.abs(prior.predict(plan, grid_pts) - true_mu).mean()


def test_gp_corrects_unmodelled_shadowing():
    rng = np.random.default_rng(3)
    plan = load_plan(FLAT)
    grid = make_grid(plan)
    truth = world.make_truth(plan, rng, field_sd=4.0)
    pts = starter_points(plan, grid)
    pts += [grid.xy[i] for i in rng.choice(len(grid.xy), 40, replace=False)]
    obs = world.survey(plan, truth, pts, rng)
    true_mu = truth.mean(plan, grid.xy) - truth.body_db / 2
    err = {gp: np.abs(fit(plan, obs, use_gp=gp).predict(plan, grid.xy) - true_mu).mean() for gp in (False, True)}
    assert err[True] < err[False]


def test_summarize_uses_censored_median():
    # "b" heard only its 6 strongest of 10 adverts: the plain median of those (-90.5)
    # overstates it, the censored median is the 5th strongest of the 9 not dropped
    samples = {"a": [-60.0] * 9, "b": [-88, -89, -90, -91, -92, -93], "c": [-97, -98]}
    p = summarize([1, 2], samples, 10, ["a", "b", "c", "d"], p_drop=0.1)
    assert p.rssi == {"a": -60, "b": -92}
    assert p.missing == {"c", "d"}


def test_weak_labels_are_used(setup):
    plan, grid, truth, _, obs, links = setup
    label = obs[0]  # pretend we only know the room
    room = grid.rooms[grid.room[grid.nearest(label.xy)]]
    cal = fit(plan, obs, links, [WeakLabel(room, label.rssi, label.missing)], grid)
    assert cal.params.n == pytest.approx(truth.params.n, abs=0.3)
    with pytest.raises(ValueError):
        fit(plan, obs, links, [WeakLabel(room, label.rssi)])


def test_survey_planner(setup):
    plan, grid, _, pts, obs, links = setup
    assert len(pts) >= len(plan.rooms) + len(plan.receivers)
    covered = {grid.room[grid.nearest(p)] for p in pts}
    assert covered == set(range(len(grid.rooms)))
    cal = fit(plan, obs, links)
    p = next_point(plan, grid, cal, pts)
    assert min(np.linalg.norm(p - q) for q in pts) >= 0.75

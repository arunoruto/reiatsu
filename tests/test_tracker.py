from pathlib import Path

import numpy as np
import pytest

from reiatsu.calibrate import Calibration
from reiatsu.channel import ChannelParams
from reiatsu.locator import Advert, Locator, Occupancy
from reiatsu.plan import load_plan, make_grid
from reiatsu.tracker import Tracker, motion_kernel

FLAT = Path(__file__).parent.parent / "sim/flat.yaml"


@pytest.fixture(scope="module")
def world():
    plan = load_plan(FLAT)
    grid = make_grid(plan)
    mu = Calibration(ChannelParams.prior(plan)).predict(plan, grid.xy)
    return plan, grid, mu, motion_kernel(grid)


def tracker(world, **kw):
    plan, grid, mu, kernel = world
    return Tracker(grid, mu, [r.id for r in plan.receivers], kernel, **kw)


def test_motion_does_not_cross_walls(world):
    _, grid, _, kernel = world
    # a cell in the bathroom right next to the kitchen wall: kitchen is 0.3 m away
    # through brick, but ~5 m away through the doors
    start = grid.nearest([1.45, 1.5])
    b = np.zeros(len(grid.xy))
    b[start] = 1
    moved = kernel @ b
    assert moved[grid.areas["Kitchen"]].sum() == 0
    assert moved[grid.areas["Bathroom"]].sum() > 0.99
    assert np.isclose(moved.sum(), 1)


def test_converges_to_true_room_from_model_readings(world):
    plan, grid, mu, _ = world
    t = tracker(world)
    for room in ["Bedroom", "Kitchen", "Living Room"]:
        c = np.flatnonzero(grid.areas[room])[len(np.flatnonzero(grid.areas[room])) // 2]
        readings = {r.id: float(mu[i, c]) for i, r in enumerate(plan.receivers)}
        for _ in range(30):
            est = t.step((t.ts or 0) + 1, readings)
        assert est.room == room
        assert np.hypot(est.x - grid.xy[c, 0], est.y - grid.xy[c, 1]) < 1.0


def test_silence_holds_room_and_grows_uncertainty(world):
    plan, grid, mu, _ = world
    t = tracker(world)
    c = grid.nearest([8.8, 2.3])  # bedroom
    readings = {r.id: float(mu[i, c]) for i, r in enumerate(plan.receivers)}
    for s in range(1, 21):
        est = t.step(s, readings)
    held = t.step(20 + 300, {})  # five silent minutes
    assert held.room == "Bedroom"
    assert held.room_probs["Bedroom"] > 0.5
    assert held.sigma > est.sigma


def test_mmwave_empty_room_pushes_belief_out(world):
    t = tracker(world)
    t.hint("Kitchen", 0.99)
    t.set_occupancy("Kitchen", False)
    t.set_occupancy("Work", True)
    est = None
    for s in range(1, 61):
        est = t.step(s, {})
    assert est.room == "Work"


def test_locator_windows_adverts_per_device(world):
    plan, grid, mu, kernel = world
    loc = Locator(lambda: Tracker(grid, mu, [r.id for r in plan.receivers], kernel))
    loc.set_occupancy(Occupancy(0, "Desk", True))
    loc.advert(Advert(0.2, "bedroom", "phone", -60))
    loc.advert(Advert(0.4, "bedroom", "phone", -62))
    loc.advert(Advert(0.5, "work", "watch", -55))
    out = loc.tick(1)
    assert [e.device_id for e in out] == ["phone", "watch"]
    assert loc.trackers["phone"].occupancy == {"Desk": True}
    assert loc.tick(2)[0].ts == 2  # silent devices keep reporting


def test_learns_device_tx_offset(world):
    plan, grid, mu, _ = world
    t = tracker(world)
    c = grid.nearest([8.8, 2.3])  # bedroom
    readings = {r.id: float(mu[i, c]) + 8 for i, r in enumerate(plan.receivers)}  # louder phone
    for s in range(1, 121):
        est = t.step(s, readings)
    assert est.room == "Bedroom"
    assert t.offset == pytest.approx(8, abs=2)


def test_single_weak_receiver_stays_uncertain(world):
    # a static device (a door lock) heard only by the living room receiver at -87 dBm, every
    # second for 10 min: the repeats carry the same error, so the room must stay ambiguous
    plan, grid, mu, kernel = world
    ids = [r.id for r in plan.receivers]
    alone = Tracker(grid, mu[[ids.index("living_room")]], ["living_room"], kernel)
    offline = tracker(world)
    offline.set_listening({"living_room"})  # the other five are offline: their silence means nothing
    for t in (alone, offline):
        for ts in range(1, 601):
            est = t.step(ts, {"living_room": -87.0})
        assert max(est.room_probs.values()) < 0.75, est.room_probs
    assert offline.belief == pytest.approx(alone.belief)

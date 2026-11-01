from pathlib import Path

import numpy as np
import pytest
from scipy.sparse.csgraph import connected_components

from reiatsu.channel import wall_counts
from reiatsu.plan import crosses, load_plan, make_grid

FLAT = Path(__file__).parent.parent / "examples/flat.yaml"

TWO_ROOMS = {
    "rooms": {"A": [0, 0, 3, 3], "B": [3.1, 0, 6, 3]},
    "materials": {"exterior": "concrete"},
    "walls": [{"rooms": ["A", "B"], "material": "brick"}],
    "doors": [{"rooms": ["A", "B"], "at": [3.05, 0.5], "width": 0.8}],
    "receivers": [{"id": "a", "x": 1.5, "y": 1.5, "room": "A"}],
}


def test_crosses():
    p, q = np.array([0.0, 0.0]), np.array([[2.0, 0.0], [2.0, 1.5], [0.5, 0.0]])
    assert crosses(p, q, np.array([1.0, -1.0]), np.array([1.0, 1.0])).tolist() == [True, True, False]


def test_shared_edges_become_one_midline_wall_with_door_gap():
    plan = load_plan(TWO_ROOMS)
    shared = [w for w in plan.walls if set(w.rooms) == {"A", "B"}]
    assert all(np.isclose(w.a[0], 3.05) and np.isclose(w.b[0], 3.05) for w in shared)
    assert all(w.material == "brick" for w in shared)
    # door 0.8 wide centred at y=0.5 cuts out [0.1, 0.9]
    spans = sorted(tuple(sorted((w.a[1], w.b[1]))) for w in shared)
    assert np.allclose(spans, [(0.0, 0.1), (0.9, 3.0)])
    exterior = [w for w in plan.walls if len(w.rooms) == 1]
    assert len(exterior) == 6  # 3 outer edges per room


def test_wall_counts_per_material():
    plan = load_plan(TWO_ROOMS)
    src = np.array([1.5, 1.5])
    pts = np.array([[2.0, 2.0], [4.5, 2.0], [4.5, 0.1], [8.0, 1.5]])
    counts = wall_counts(plan, src, pts)
    mats = plan.materials
    brick, exterior = counts[:, mats.index("brick")], counts.sum(1) - counts[:, mats.index("brick")]
    assert brick.tolist() == [0, 1, 0, 1]  # third point is seen through the door
    assert exterior.tolist() == [0, 0, 0, 1]  # last point is outside room B


def test_flat_derives_expected_walls():
    plan = load_plan(FLAT)
    pairs = {frozenset(w.rooms) for w in plan.walls if len(w.rooms) == 2}
    for a, b in [("Bathroom", "Kitchen"), ("Kitchen", "Work"), ("Work", "Bedroom"), ("Bedroom", "Living Room"), ("Hallway", "Living Room")]:
        assert frozenset((a, b)) in pairs
    assert frozenset(("Bathroom", "Living Room")) not in pairs


def test_grid_moves_between_rooms_only_through_doors():
    plan = load_plan(FLAT)
    grid = make_grid(plan)
    assert connected_components(grid.graph)[0] == 1
    i, j = grid.graph.nonzero()
    cross = grid.room[i] != grid.room[j]
    for a, b in zip(i[cross], j[cross]):
        mid = (grid.xy[a] + grid.xy[b]) / 2
        assert min(np.linalg.norm(mid - d.at) for d in plan.doors) < 0.8


def test_room_without_door_is_unreachable():
    cfg = dict(TWO_ROOMS, doors=[])
    grid = make_grid(load_plan(cfg))
    assert connected_components(grid.graph)[0] == 2


def test_bad_config_is_rejected():
    with pytest.raises(ValueError):
        load_plan(dict(TWO_ROOMS, doors=[{"rooms": ["A", "C"]}]))
    with pytest.raises(ValueError):
        load_plan(dict(TWO_ROOMS, receivers=[{"id": "x", "x": 0, "y": 0, "room": "Nope"}]))

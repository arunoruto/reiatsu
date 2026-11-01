"""Floor plan: rooms, walls, doors, receivers and the walkable grid.

Coordinates are metres on one floor. Walls are derived from room polygons:
parallel edges of two different rooms closer than ``max_gap`` become a single
interior wall on their midline, every other edge is an exterior wall. Doors
cut a gap into the wall between two rooms and are the only way to walk from
one room to another.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from pathlib import Path

import numpy as np
import yaml
from scipy import sparse
from scipy.spatial import cKDTree


@dataclass
class Wall:
    a: np.ndarray
    b: np.ndarray
    material: str
    rooms: tuple[str, ...]  # two rooms for interior walls, one for exterior


@dataclass
class Door:
    rooms: tuple[str, str]
    at: np.ndarray  # centre, on the wall midline
    width: float


@dataclass
class Receiver:
    id: str
    xy: np.ndarray
    z: float
    room: str
    p0: float | None = None  # dBm at 1 m, overrides the prior (e.g. an adapter inside a cabinet)


@dataclass
class Plan:
    rooms: dict[str, np.ndarray]  # name -> (k, 2) polygon
    walls: list[Wall]
    doors: list[Door]
    receivers: list[Receiver]
    zones: dict[str, np.ndarray] = field(default_factory=dict)
    references: dict[str, np.ndarray] = field(default_factory=dict)  # device -> xy of a stationary device

    @property
    def materials(self) -> list[str]:
        return sorted({w.material for w in self.walls})


# --- geometry ---------------------------------------------------------------


def _cross(u, v):
    return u[..., 0] * v[..., 1] - u[..., 1] * v[..., 0]


def crosses(p, q, a, b) -> np.ndarray:
    """Whether segments p->q properly intersect segment a->b (broadcasts over p, q)."""
    d, e = q - p, b - a
    o1, o2 = _cross(d, a - p), _cross(d, b - p)
    o3, o4 = _cross(e, p - a), _cross(e, q - a)
    return (o1 * o2 < 0) & (o3 * o4 < 0)


def inside(poly: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Even-odd point-in-polygon test for (N, 2) points."""
    x, y = pts[:, 0], pts[:, 1]
    res = np.zeros(len(pts), bool)
    for (x1, y1), (x2, y2) in zip(poly, np.roll(poly, -1, axis=0)):
        if y1 == y2:
            continue
        hit = (y1 > y) != (y2 > y)
        res ^= hit & (x < x1 + (y - y1) * (x2 - x1) / (y2 - y1))
    return res


def seg_dist(pts: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ab = b - a
    t = np.clip((pts - a) @ ab / (ab @ ab), 0, 1)
    return np.linalg.norm(pts - (a + t[:, None] * ab), axis=1)


def edges(poly: np.ndarray):
    return list(zip(poly, np.roll(poly, -1, axis=0)))


def boundary_dist(poly: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return np.min([seg_dist(pts, a, b) for a, b in edges(poly)], axis=0)


# --- walls and doors --------------------------------------------------------


def _intervals_complement(covered, length, eps=1e-6):
    out, pos = [], 0.0
    for lo, hi in sorted(covered):
        if lo > pos + eps:
            out.append((pos, lo))
        pos = max(pos, hi)
    if length > pos + eps:
        out.append((pos, length))
    return out


def derive_walls(rooms, max_gap=0.4, interior="drywall", exterior="brick", pair_material=None):
    pair_material = pair_material or {}
    all_edges = [(name, a, b) for name, poly in rooms.items() for a, b in edges(poly)]
    walls = []
    for i, (ra, a0, a1) in enumerate(all_edges):
        length = np.linalg.norm(a1 - a0)
        u = (a1 - a0) / length
        nrm = np.array([-u[1], u[0]])
        covered = []
        for j, (rb, b0, b1) in enumerate(all_edges):
            if rb == ra:
                continue
            v = (b1 - b0) / np.linalg.norm(b1 - b0)
            off = (b0 - a0) @ nrm
            if abs(_cross(u, v)) > 0.05 or abs(off) > max_gap:
                continue
            t0, t1 = sorted([(b0 - a0) @ u, (b1 - a0) @ u])
            lo, hi = max(t0, 0.0), min(t1, length)
            if hi - lo < 1e-6:
                continue
            covered.append((lo, hi))
            if i < j:  # emit each shared wall once, on the midline
                mid = a0 + nrm * off / 2
                mat = pair_material.get(frozenset((ra, rb)), interior)
                walls.append(Wall(mid + u * lo, mid + u * hi, mat, (ra, rb)))
        for lo, hi in _intervals_complement(covered, length):
            walls.append(Wall(a0 + u * lo, a0 + u * hi, pair_material.get(frozenset((ra,)), exterior), (ra,)))
    return walls


def cut_door(walls: list[Wall], rooms: tuple[str, str], at, width) -> tuple[list[Wall], Door]:
    shared = [w for w in walls if set(w.rooms) == set(rooms)]
    if not shared:
        raise ValueError(f"door {rooms}: rooms share no wall")
    if at is None:
        wall = max(shared, key=lambda w: np.linalg.norm(w.b - w.a))
        at = (wall.a + wall.b) / 2
    at = np.asarray(at, float)
    wall = min(shared, key=lambda w: seg_dist(at[None], w.a, w.b)[0])
    length = np.linalg.norm(wall.b - wall.a)
    u = (wall.b - wall.a) / length
    t = np.clip((at - wall.a) @ u, 0, length)
    pieces = [(0.0, t - width / 2), (t + width / 2, length)]
    rest = [Wall(wall.a + u * lo, wall.a + u * hi, wall.material, wall.rooms) for lo, hi in pieces if hi - lo > 1e-3]
    walls = [w for w in walls if w is not wall] + rest
    return walls, Door(tuple(rooms), wall.a + u * t, width)


def _polygon(v) -> np.ndarray:
    arr = np.asarray(v, float)
    if arr.shape == (4,):
        x0, y0, x1, y1 = arr
        return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
    return arr


def load_plan(src: str | Path | dict) -> Plan:
    cfg = src if isinstance(src, dict) else yaml.safe_load(Path(src).read_text())
    # The engine is mirror-invariant; y_axis only tells the web UI how to draw (default up).
    if cfg.get("y_axis", "up") not in ("up", "down"):
        raise ValueError(f"y_axis must be up or down, not {cfg['y_axis']!r}")
    rooms = {name: _polygon(v) for name, v in (cfg.get("rooms") or {}).items()}
    mats = {"interior": "drywall", "exterior": "brick", **cfg.get("materials", {})}
    pair_mat = {frozenset(w["rooms"]): w["material"] for w in cfg.get("walls") or []}  # one room: its exterior walls
    walls = derive_walls(rooms, cfg.get("max_gap", 0.4), mats["interior"], mats["exterior"], pair_mat)
    walls += [Wall(np.asarray(w["a"], float), np.asarray(w["b"], float), w["material"], ()) for w in cfg.get("extra_walls") or []]
    doors = []
    for d in cfg.get("doors") or []:
        walls, door = cut_door(walls, tuple(d["rooms"]), d.get("at"), float(d.get("width", 0.8)))
        doors.append(door)
    receivers = []
    for r in cfg.get("receivers") or []:
        xy = np.array([r["x"], r["y"]], float)
        room = r.get("room") or room_at(rooms, xy)
        if room not in rooms:
            raise ValueError(f"receiver {r['id']}: unknown room {room!r}")
        p0 = r.get("p0")
        receivers.append(Receiver(str(r["id"]), xy, float(r.get("z", 1.0)), room, None if p0 is None else float(p0)))
    zones = {name: _polygon(v) for name, v in (cfg.get("zones") or {}).items()}
    refs = {str(r["device"]): np.array([r["x"], r["y"]], float) for r in cfg.get("references") or []}
    return Plan(rooms, walls, doors, receivers, zones, refs)


def room_at(rooms: dict[str, np.ndarray], xy) -> str | None:
    """The room containing ``xy``, else the one with the nearest edge."""
    if not rooms:
        return None
    pt = np.asarray(xy, float)[None]
    for name, poly in rooms.items():
        if inside(poly, pt)[0]:
            return name
    return min(rooms, key=lambda n: boundary_dist(rooms[n], pt)[0])


# --- walkable grid ----------------------------------------------------------


@dataclass
class Grid:
    xy: np.ndarray  # (C, 2) cell centres
    room: np.ndarray  # (C,) index into rooms
    rooms: list[str]
    graph: sparse.csr_matrix  # walkable edges, weight = distance in metres
    areas: dict[str, np.ndarray]  # rooms and zones -> (C,) bool mask

    def nearest(self, p) -> int:
        return int(np.argmin(np.linalg.norm(self.xy - np.asarray(p), axis=1)))


def make_grid(plan: Plan, res: float = 0.2) -> Grid:
    """Cells inside rooms; neighbours connect within a room, doors connect rooms."""
    pts = np.vstack(list(plan.rooms.values()))
    lo, hi = pts.min(0), pts.max(0)
    X, Y = np.meshgrid(np.arange(lo[0] + res / 2, hi[0], res), np.arange(lo[1] + res / 2, hi[1], res))
    cand = np.c_[X.ravel(), Y.ravel()]
    names = list(plan.rooms)
    room = np.full(len(cand), -1)
    for i, name in enumerate(names):
        room[(room < 0) & inside(plan.rooms[name], cand)] = i
    xy, room = cand[room >= 0], room[room >= 0]

    pairs = cKDTree(xy).query_pairs(res * 1.5, output_type="ndarray")
    pairs = pairs[room[pairs[:, 0]] == room[pairs[:, 1]]]
    links = [pairs]
    for door in plan.doors:
        if len(door.rooms) < 2:  # a door to the outside connects no cells
            continue
        near = np.linalg.norm(xy - door.at, axis=1) <= door.width / 2 + res
        a = np.flatnonzero(near & (room == names.index(door.rooms[0])))
        b = np.flatnonzero(near & (room == names.index(door.rooms[1])))
        if not len(a) or not len(b):
            raise ValueError(f"door {door.rooms} at {door.at.round(2)} reaches no cells on both sides")
        links.append(np.array(list(product(a, b))))
    pairs = np.vstack(links)
    w = np.linalg.norm(xy[pairs[:, 0]] - xy[pairs[:, 1]], axis=1)
    g = sparse.coo_matrix((w, (pairs[:, 0], pairs[:, 1])), shape=(len(xy),) * 2).tocsr()
    areas = {name: room == i for i, name in enumerate(names)}
    areas |= {name: inside(poly, xy) for name, poly in plan.zones.items()}
    return Grid(xy, room, names, g + g.T, areas)

import json
from pathlib import Path

import numpy as np

from reiatsu.daemon import Publisher, build_locator, parse, read_log, to_line, track
from reiatsu.locator import Advert, Occupancy, Receivers
from reiatsu.plan import load_plan, make_grid
from sim import world
from sim.run import replay

FLAT = Path(__file__).parent.parent / "sim/flat.yaml"


def test_parse_topics():
    ads = parse("reiatsu/adverts", b'[{"ts": 1, "receiver_id": "kitchen", "device_id": "p", "rssi": -70}]', "reiatsu")
    assert ads == [Advert(1.0, "kitchen", "p", -70.0)]
    assert parse("reiatsu/adverts", b'{"ts": 2, "receiver_id": "k", "device_id": "p", "rssi": -1}', "reiatsu")[0].ts == 2
    occ = parse("reiatsu/occupancy/living_room", b'{"ts": 3, "area": "Living Room", "occupied": true}', "reiatsu")
    assert occ == [Occupancy(3.0, "Living Room", True)]
    rx = parse("reiatsu/receivers", b'{"ts": 4, "receivers": ["a", "b"]}', "reiatsu")
    assert rx == [Receivers(4.0, ("a", "b"))]
    assert list(read_log([to_line(rx[0])])) == rx
    assert parse("other/adverts", b"[]", "reiatsu") == []


def test_record_replay_matches_direct_run():
    rng = np.random.default_rng(0)
    plan = load_plan(FLAT)
    grid = make_grid(plan)
    truth = world.make_truth(plan, rng)
    traj = world.walk(grid, rng, 301)
    events = world.adverts(plan, truth, traj, rng) + world.mmwave(grid, [traj], rng)
    events.sort(key=lambda e: e.ts)
    log = [to_line(e) for e in events]
    assert list(read_log(log)) == events

    out = []
    locator, _ = build_locator(FLAT)
    track(((e.ts, e) for e in read_log(log)), locator, out.extend)
    direct, _ = build_locator(FLAT)

    class Sink:
        advert, set_occupancy = direct.advert, direct.set_occupancy
        tick = staticmethod(direct.tick)

    ads = [e for e in events if isinstance(e, Advert)]
    occ = [e for e in events if isinstance(e, Occupancy)]
    ref = [est for ests in replay(ads, occ, int(events[-1].ts), Sink) for est in ests]
    n = min(len(out), len(ref))
    assert n > 250
    assert [e.room for e in out[:n]] == [e.room for e in ref[:n]]


def test_publisher_announces_once_and_throttles():
    sent = []

    class Client:
        def publish(self, topic, payload, retain=False):
            sent.append((topic, json.loads(payload), retain))

    locator, _ = build_locator(FLAT)
    pub = Publisher(Client(), "reiatsu", "homeassistant", interval=5)
    locator.advert(Advert(0.5, "kitchen", "phone", -60))
    for ts in range(1, 5):
        pub(locator.tick(ts))
    configs = [s for s in sent if s[0].endswith("/config")]
    states = [s for s in sent if s[0] == "reiatsu/phone/state"]
    assert {c[0].split("/")[-2] for c in configs} == {"room", "x", "y", "sigma"}
    assert all(c[2] for c in configs)  # retained
    assert configs[0][1]["state_topic"] == "reiatsu/phone/state"
    assert len(states) == 1 and set(states[0][1]) == {"ts", "room", "x", "y", "sigma", "room_probs"}

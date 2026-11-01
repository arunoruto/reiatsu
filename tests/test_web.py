import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest
import yaml

from reiatsu.daemon import build_locator, snapshot
from reiatsu.locator import Advert
from reiatsu.plan import load_plan
from reiatsu.web import App, from_espresense, serve

EXAMPLE = Path(__file__).parent.parent / "examples/flat.yaml"


@pytest.fixture
def server(tmp_path):
    plan = tmp_path / "plan.yaml"
    shutil.copy(EXAMPLE, plan)
    saved = []
    app = App(plan, saved.append)
    srv = serve(app, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{srv.server_address[1]}/"

    def call(path, method="GET", body=None, ctype="application/json"):
        data = json.dumps(body).encode() if isinstance(body, dict) else body
        req = urllib.request.Request(base + path, data, method=method, headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                return r.status, json.loads(raw) if r.headers["Content-Type"] == "application/json" else raw
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    yield call, app, plan, saved
    srv.shutdown()


def test_ui_and_plan(server):
    call, *_ = server
    status, html = call("")
    assert status == 200 and b"leaflet" in html and b'src="/' not in html  # relative URLs only
    assert call("vendor/leaflet-geoman.js")[0] == 200
    status, res = call("api/plan")
    assert res["error"] is None and "Hallway" in res["derived"]["rooms"]
    assert len(res["derived"]["cells"]) > 1000 and "concrete" in res["materials"]


def test_save_validates_backs_up_and_reloads(server):
    call, app, path, saved = server
    raw = app.raw | {"receivers": [{"id": "ha_usb", "x": 3.0, "y": 4.0, "z": 1.5, "p0": -70}]}
    bad = raw | {"doors": [{"rooms": ["Bathroom", "Living Room"]}]}  # no shared wall
    status, res = call("api/preview", "POST", bad)
    assert status == 400 and "share no wall" in res["error"]
    assert call("api/plan", "PUT", bad)[0] == 400 and not saved

    status, res = call("api/plan", "PUT", raw)
    assert status == 200 and res["derived"]["receivers"] == {"ha_usb": "Hallway"}
    assert yaml.safe_load(path.read_text())["receivers"][0]["p0"] == -70
    assert "receivers: []" in path.with_name("plan.yaml.bak").read_text()
    assert saved == [raw]
    assert load_plan(path).receivers[0].p0 == -70


def test_background_upload(server):
    call, _, path, _ = server
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    status, res = call("api/background", "PUT", png, "image/png")
    assert status == 200 and res["image"] == "background.png"
    assert path.with_name("background.png").read_bytes() == png
    assert call("api/background/background.png")[1] == png
    assert call("api/background", "PUT", b"x", "image/gif")[0] == 400
    assert call("api/background/plan.yaml")[0] == 404  # only background images are served


def test_live_state_snapshot(server):
    call, app, *_ = server
    locator, ids = build_locator(EXAMPLE)
    locator.advert(Advert(0.5, "ha_usb", "nuki", -87))
    est = locator.tick(1)
    app.snapshot = snapshot(locator, est, ids, {"ha_usb": {"nuki": (-87, 0.5)}})
    status, st = call("api/state?device=nuki")
    assert st["devices"]["nuki"]["room"] and st["rssi"] == {"ha_usb": {"nuki": [-87, 0.5]}}
    assert len(st["belief"]) == len(call("api/plan")[1]["derived"]["cells"]) and max(st["belief"]) == 1
    assert "belief" not in call("api/state")[1]


def test_espresense_import():
    cfg = {
        "floors": [
            {"id": "ground", "name": "Ground", "rooms": [
                {"name": "Kitchen", "points": [[0, 0], [3, 0], [3, 3], [0, 3]]},
                {"name": "Living", "points": [[3, 0], [8, 0], [8, 3], [3, 3]]},
                {"name": "Office", "points": [[0, 5], [3, 5], [3, 8], [0, 8]]},
            ]},
            {"id": "upstairs", "rooms": []},
        ],
        "nodes": [
            {"name": "Kitchen Node", "point": [1, 1, 1.2], "floors": ["ground"]},
            {"name": "attic", "point": [1, 1, 5], "floors": ["upstairs"]},
        ],
    }
    plan = from_espresense(cfg)
    assert [d["rooms"] for d in plan["doors"]] == [["Kitchen", "Living"]]  # Office shares no wall
    assert plan["receivers"] == [{"id": "kitchen_node", "x": 1.0, "y": 1.0, "z": 1.2}]
    p = load_plan(plan)
    assert p.receivers[0].room == "Kitchen" and len(p.doors) == 1
    with pytest.raises(ValueError):
        from_espresense(cfg, floor="cellar")


def test_plan_extensions():
    p = load_plan(EXAMPLE)
    entrance = next(d for d in p.doors if d.rooms == ("Hallway",))
    assert np.allclose(entrance.at, [0, 4.0])
    assert np.allclose(p.references["nuki"], [0.1, 4.0])
    raw = yaml.safe_load(EXAMPLE.read_text())
    raw["walls"].append({"rooms": ["Living Room"], "material": "glass"})  # one room: its exterior walls
    walls = load_plan(raw).walls
    assert {w.material for w in walls if w.rooms == ("Living Room",)} == {"glass"}
    assert {w.material for w in walls if w.rooms == ("Kitchen",)} == {"brick"}

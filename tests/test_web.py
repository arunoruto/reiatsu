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

    call.base = base
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


def test_ingress_base_path(server):
    call, *_ = server
    req = urllib.request.Request(call.base, headers={"X-Ingress-Path": "/api/hassio_ingress/abc"})
    with urllib.request.urlopen(req) as r:
        assert b'<base href="/api/hassio_ingress/abc/">' in r.read()


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
    assert plan["y_axis"] == "up" and plan["rooms"]["Office"][0] == [0.0, 5.0]  # copied, not mirrored
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
    with pytest.raises(ValueError):
        load_plan(raw | {"y_axis": "sideways"})
    with pytest.raises(ValueError, match="negative"):
        load_plan(raw | {"receivers": [{"id": "ha_usb", "x": 3, "y": 4, "p0": 70}]})


def test_survey_capture_guidance_and_calibration(tmp_path):
    import time

    from reiatsu.calibrate import starter_points
    from reiatsu.plan import make_grid
    from sim import world

    path = tmp_path / "plan.yaml"
    shutil.copy(Path(__file__).parent.parent / "sim/flat.yaml", path)
    saved = []
    app = App(path, saved.append)
    srv = serve(app, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{srv.server_address[1]}/"

    def call(p, method="GET", body=None):
        req = urllib.request.Request(base + p, json.dumps(body).encode() if body is not None else None, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    # live capture: adverts of the held device count, others don't
    assert call("api/survey/start", "POST", {"device": "phone", "x": 1.0, "y": 1.5, "seconds": 0.3})[0] == 200
    for rssi in (-60, -62, -61):
        app.survey.on_advert(Advert(0, "kitchen", "phone", rssi), time.time())
    app.survey.on_advert(Advert(0, "kitchen", "watch", -40), time.time())
    status, cap = call("api/survey/capture")
    assert not cap["capture"]["done"] and cap["capture"]["receivers"]["kitchen"]["n"] == 3 and "bedroom" in cap["capture"]["silent"]
    time.sleep(0.35)
    assert call("api/survey/capture")[1]["capture"] == {"done": True, "saved": True, "id": app.survey.entries[0]["id"]}
    assert call(f"api/survey/{app.survey.entries[0]['id']}", "DELETE")[1] == {"deleted": True}
    assert call("api/calibrate", "POST")[0] == 400  # not enough points

    # a simulated survey of the starter targets, raw samples as the capture stores them
    rng = np.random.default_rng(2)
    plan = load_plan(path)
    truth = world.make_truth(plan, rng, field_sd=0.0)
    ids = [r.id for r in plan.receivers]
    for i, p in enumerate(starter_points(plan, make_grid(plan))):
        here = np.repeat(p[None], 25, axis=0)
        rssi = truth.hear(truth.mean(plan, here) - truth.body(plan, here, np.linspace(0, 2 * np.pi, 25)), rng)
        samples = {ids[r]: row[~np.isnan(row)].tolist() for r, row in enumerate(rssi)}
        app.survey.entries.append({"id": str(i), "device": "phone", "x": float(p[0]), "y": float(p[1]), "held": "hand", "samples": samples})
    status, st = call("api/survey")
    assert status == 200 and st["next"]["phase"] == "active" and st["next"]["starter_left"] == 0
    assert st["loo"]["room_acc"] > 0.7 and all(pt["residual"] is not None for pt in st["points"])

    status, prev = call("api/calibrate", "POST")
    assert status == 200 and prev["after"]["room_acc"] > 0.7
    assert prev["after"]["rssi_err_db"] < prev["before"]["rssi_err_db"]  # priors vs fitted (left out)
    assert call("api/calibrate/apply", "POST")[1] == {"ok": True} and len(saved) == 1
    cal = json.loads((tmp_path / "calibration.json").read_text())
    assert abs(cal["n"] - truth.params.n) < 0.3
    assert call("api/survey")[1]["calibrated"]
    srv.shutdown()

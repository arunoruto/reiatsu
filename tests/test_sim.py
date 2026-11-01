from sim.run import run


def test_quick_scenario_runs_and_reiatsu_beats_baseline():
    res = run("quick", seed=1)["metrics"]
    base = res["baseline: nearest receiver (Bermuda-like)"]
    full = res[next(k for k in res if k.endswith("+ mmWave"))]
    assert full["room_acc"] > base["room_acc"]
    assert full["pos_err_m"] < base["pos_err_m"]

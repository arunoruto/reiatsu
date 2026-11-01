import json
import os
import tomllib
from pathlib import Path

import yaml

from reiatsu import addon

ROOT = Path(__file__).parent.parent


def test_versions_match():
    # the add-on pulls ghcr.io/arunoruto/reiatsu:<version>, which the v<version> tag publishes
    cfg = yaml.safe_load((ROOT / "addon/config.yaml").read_text())
    assert cfg["version"] == tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert set(cfg["options"]) == set(cfg["schema"])


def test_main_reads_options_and_mqtt_service(tmp_path, monkeypatch):
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"record": True, "topic_prefix": "rz", "log_level": "debug"}))
    monkeypatch.setattr(addon, "OPTIONS", options)
    monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
    monkeypatch.setattr(addon, "mqtt_service", lambda token: {"host": "core-mosquitto", "port": 1883, "username": "addons", "password": "pw", "ssl": False})
    called = []
    monkeypatch.setattr(addon.daemon, "main", lambda argv=None: called.append(argv))
    addon.main()
    argv = called[0]
    assert argv[argv.index("--mqtt-host") + 1] == "core-mosquitto" and argv[argv.index("--prefix") + 1] == "rz"
    assert argv[argv.index("--record") + 1] == "/config/record.jsonl" and "pw" not in argv
    assert os.environ["REIATSU_MQTT_USER"] == "addons" and os.environ["REIATSU_MQTT_PASSWORD"] == "pw"

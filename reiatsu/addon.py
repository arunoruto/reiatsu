"""Home Assistant add-on entrypoint: Supervisor options and MQTT service -> daemon arguments.

Outside an add-on (no /data/options.json) it is the plain daemon, configured by env.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from pathlib import Path

from . import daemon

OPTIONS = Path("/data/options.json")
CONFIG = Path("/config")  # addon_config:rw


def mqtt_service(token: str) -> dict:
    """Broker host, port and credentials from the Supervisor (needs ``services: [mqtt:need]``)."""
    req = urllib.request.Request("http://supervisor/services/mqtt", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)["data"]


def arguments(options: dict, mqtt: dict) -> list[str]:
    args = [
        "--plan", str(CONFIG / "plan.yaml"),
        "--prefix", options.get("topic_prefix", "reiatsu"),
        "--mqtt-host", str(mqtt["host"]),
        "--mqtt-port", str(mqtt["port"]),
        "--log-level", options.get("log_level", "info"),
        "--http-port", "8099",
    ]  # fmt: skip
    if options.get("record", True):
        args += ["--record", str(CONFIG / "record.jsonl")]
    return args


def main():
    if not OPTIONS.exists():
        return daemon.main()
    options = json.loads(OPTIONS.read_text())
    mqtt = mqtt_service(os.environ["SUPERVISOR_TOKEN"])
    if mqtt.get("ssl"):
        logging.warning("the MQTT service asks for TLS, which Reiatsu doesn't support yet; connecting without")
    if mqtt.get("username"):  # via env, so the password stays out of the process list
        os.environ["REIATSU_MQTT_USER"] = mqtt["username"]
        os.environ["REIATSU_MQTT_PASSWORD"] = mqtt.get("password") or ""
    return daemon.main(arguments(options, mqtt))


if __name__ == "__main__":
    main()

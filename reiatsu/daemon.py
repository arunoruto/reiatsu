"""MQTT daemon: adverts and occupancy in, room estimates out (with HA MQTT discovery).

    reiatsu --plan plan.yaml [--record log.jsonl]   # live, optionally logging raw input
    reiatsu --plan plan.yaml --replay log.jsonl     # offline: estimates as JSONL on stdout

Input topics (published by the reiatsu_bridge HA integration, or anything else):

- ``<prefix>/adverts``: one {ts, receiver_id, device_id, rssi} object or a list of them
- ``<prefix>/occupancy/<area>``: {ts, area, occupied}

Output: ``<prefix>/<device_id>/state`` = {ts, room, x, y, sigma, room_probs}, plus
``<prefix>/status`` online/offline.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import queue
import sys
import time
from dataclasses import asdict
from typing import Callable, Iterable, Iterator

from .channel import ChannelParams, predict
from .locator import Advert, Locator, Occupancy
from .plan import load_plan, make_grid
from .tracker import Estimate, Tracker, motion_kernel

log = logging.getLogger("reiatsu")
type Event = Advert | Occupancy


def parse(topic: str, payload: bytes, prefix: str) -> list[Event]:
    data = json.loads(payload)
    if topic == f"{prefix}/adverts":
        items = data if isinstance(data, list) else [data]
        return [Advert(float(a["ts"]), str(a["receiver_id"]), str(a["device_id"]), float(a["rssi"])) for a in items]
    if topic.startswith(f"{prefix}/occupancy/"):
        return [Occupancy(float(data["ts"]), str(data["area"]), bool(data["occupied"]))]
    return []


def to_line(ev: Event) -> str:
    return json.dumps({"type": "advert" if isinstance(ev, Advert) else "occupancy"} | asdict(ev))


def read_log(lines: Iterable[str]) -> Iterator[Event]:
    for line in lines:
        if line.strip():
            d = json.loads(line)
            yield (Advert if d.pop("type") == "advert" else Occupancy)(**d)


def track(events: Iterable[tuple[float, Event | None]], locator: Locator, emit: Callable[[list[Estimate]], None]):
    """Feed (ts, event-or-None) in time order; tick the locator every whole second of ts."""
    next_tick = None
    for ts, ev in events:
        if next_tick is None:
            next_tick = math.floor(ts) + 1
        while ts >= next_tick:
            emit(locator.tick(next_tick))
            next_tick += 1
        if isinstance(ev, Advert):
            locator.advert(ev)
        elif isinstance(ev, Occupancy):
            locator.set_occupancy(ev)


def build_locator(plan_path: str) -> tuple[Locator, set[str]]:
    plan = load_plan(plan_path)
    grid = make_grid(plan)
    # ponytail: channel priors only; load fitted ChannelParams once there is a calibration CLI
    mu = predict(plan, ChannelParams.prior(plan), grid.xy)
    kernel, ids = motion_kernel(grid), [r.id for r in plan.receivers]
    return Locator(lambda: Tracker(grid, mu, ids, kernel)), set(ids)


def state(e: Estimate) -> dict:
    return {"ts": e.ts, "room": e.room, "x": round(e.x, 2), "y": round(e.y, 2), "sigma": round(e.sigma, 2), "room_probs": e.room_probs}


def discovery(prefix: str, disc: str, dev: str) -> Iterator[tuple[str, dict]]:
    topic = f"{prefix}/{dev}/state"
    base = {
        "state_topic": topic,
        "availability_topic": f"{prefix}/status",
        "device": {"identifiers": [f"reiatsu_{dev}"], "name": f"Reiatsu {dev}", "manufacturer": "Reiatsu"},
    }
    sensors = {
        "room": {
            "name": "Room",
            "icon": "mdi:map-marker",
            "value_template": "{{ value_json.room }}",
            "json_attributes_topic": topic,
            "json_attributes_template": "{{ value_json.room_probs | tojson }}",
        },
        **{
            k: {"name": name, "value_template": f"{{{{ value_json.{k} }}}}", "unit_of_measurement": "m", "state_class": "measurement"}
            for k, name in [("x", "X"), ("y", "Y"), ("sigma", "Position uncertainty")]
        },
    }
    for key, cfg in sensors.items():
        yield f"{disc}/sensor/reiatsu_{dev}/{key}/config", base | cfg | {"unique_id": f"reiatsu_{dev}_{key}"}


class Publisher:
    """Publishes discovery once per device, then state on room change or every ``interval`` seconds."""

    def __init__(self, client, prefix: str, disc: str, interval: float):
        self.client, self.prefix, self.disc, self.interval = client, prefix, disc, interval
        self.last: dict[str, tuple[str, float]] = {}

    def __call__(self, estimates: list[Estimate]):
        for e in estimates:
            prev = self.last.get(e.device_id)
            if prev is None:
                for topic, cfg in discovery(self.prefix, self.disc, e.device_id):
                    self.client.publish(topic, json.dumps(cfg), retain=True)
            if prev is None or prev[0] != e.room or e.ts - prev[1] >= self.interval:
                self.client.publish(f"{self.prefix}/{e.device_id}/state", json.dumps(state(e)))
                self.last[e.device_id] = (e.room, e.ts)


def live(args, locator: Locator, receivers: set[str]):
    import paho.mqtt.client as mqtt

    inbox: queue.Queue = queue.Queue()
    status = f"{args.prefix}/status"
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"reiatsu-{args.prefix}")
    if args.mqtt_user:
        client.username_pw_set(args.mqtt_user, args.mqtt_password)
    client.will_set(status, "offline", retain=True)

    def on_connect(c, _userdata, _flags, reason, _props):
        log.info("MQTT connected (%s)", reason)
        c.subscribe([(f"{args.prefix}/adverts", 0), (f"{args.prefix}/occupancy/+", 0)])
        c.publish(status, "online", retain=True)

    client.on_connect = on_connect
    client.on_message = lambda _c, _u, msg: inbox.put((msg.topic, msg.payload))
    client.connect(args.mqtt_host, args.mqtt_port)
    client.loop_start()
    record = open(args.record, "a", buffering=1) if args.record else None
    warned: set[str] = set()

    def events():
        while True:
            try:
                topic, payload = inbox.get(timeout=0.5)
            except queue.Empty:
                yield time.time(), None
                continue
            try:
                evs = parse(topic, payload, args.prefix)
            except (ValueError, KeyError, TypeError) as err:
                log.warning("bad message on %s: %s", topic, err)
                continue
            for ev in evs:
                if record:
                    record.write(to_line(ev) + "\n")
                if isinstance(ev, Advert) and ev.receiver_id not in receivers and ev.receiver_id not in warned:
                    warned.add(ev.receiver_id)
                    log.warning("receiver %r is not in the floor plan; its adverts are ignored", ev.receiver_id)
                yield time.time(), ev

    try:
        track(events(), locator, Publisher(client, args.prefix, args.discovery_prefix, args.publish_interval))
    finally:
        client.publish(status, "offline", retain=True)
        client.loop_stop()


def print_estimates(estimates: list[Estimate]):
    for e in estimates:
        print(json.dumps({"device_id": e.device_id} | state(e)))


def main(argv: list[str] | None = None):
    env = os.environ.get
    ap = argparse.ArgumentParser(description="Reiatsu MQTT daemon")
    ap.add_argument("--plan", default=env("REIATSU_PLAN"), required=not env("REIATSU_PLAN"), help="floor plan YAML [REIATSU_PLAN]")
    ap.add_argument("--replay", metavar="LOG", help="replay a recorded JSONL log instead of connecting to MQTT")
    ap.add_argument("--record", metavar="LOG", default=env("REIATSU_RECORD"), help="append raw input to a JSONL log [REIATSU_RECORD]")
    ap.add_argument("--mqtt-host", default=env("REIATSU_MQTT_HOST", "localhost"), help="[REIATSU_MQTT_HOST]")
    ap.add_argument("--mqtt-port", type=int, default=int(env("REIATSU_MQTT_PORT", "1883")), help="[REIATSU_MQTT_PORT]")
    ap.add_argument("--mqtt-user", default=env("REIATSU_MQTT_USER"), help="[REIATSU_MQTT_USER]")
    ap.add_argument("--mqtt-password", default=env("REIATSU_MQTT_PASSWORD"), help="prefer the env var [REIATSU_MQTT_PASSWORD]")
    ap.add_argument("--prefix", default=env("REIATSU_PREFIX", "reiatsu"), help="topic prefix [REIATSU_PREFIX]")
    ap.add_argument("--discovery-prefix", default=env("REIATSU_DISCOVERY_PREFIX", "homeassistant"), help="[REIATSU_DISCOVERY_PREFIX]")
    ap.add_argument("--publish-interval", type=float, default=5.0, help="seconds between state updates while the room is unchanged")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    locator, receivers = build_locator(args.plan)
    if args.replay:
        with open(args.replay) as f:
            track(((ev.ts, ev) for ev in read_log(f)), locator, print_estimates)
    else:
        live(args, locator, receivers)


if __name__ == "__main__":
    sys.exit(main())

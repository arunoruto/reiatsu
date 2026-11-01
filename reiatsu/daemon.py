"""MQTT daemon: adverts and occupancy in, room estimates out (with HA MQTT discovery).

    reiatsu --plan plan.yaml [--record log.jsonl]   # live, optionally logging raw input
    reiatsu --plan plan.yaml --replay log.jsonl     # offline: estimates as JSONL on stdout

Live mode also serves the web UI (plan editor, live map) on --http-port (default 8099).

Input topics (published by the reiatsu_bridge HA integration, or anything else):

- ``<prefix>/adverts``: one {ts, receiver_id, device_id, rssi} object or a list of them
- ``<prefix>/occupancy/<area>``: {ts, area, occupied}
- ``<prefix>/receivers``: {ts, receivers: [ids online]}; until it arrives, all plan receivers count as online

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
from .locator import Advert, Locator, Occupancy, Receivers
from .plan import load_plan, make_grid
from .tracker import Estimate, Tracker, motion_kernel

log = logging.getLogger("reiatsu")
type Event = Advert | Occupancy | Receivers
KINDS = {"advert": Advert, "occupancy": Occupancy, "receivers": Receivers}


def parse(topic: str, payload: bytes, prefix: str) -> list[Event]:
    data = json.loads(payload)
    if topic == f"{prefix}/adverts":
        items = data if isinstance(data, list) else [data]
        return [Advert(float(a["ts"]), str(a["receiver_id"]), str(a["device_id"]), float(a["rssi"])) for a in items]
    if topic.startswith(f"{prefix}/occupancy/"):
        return [Occupancy(float(data["ts"]), str(data["area"]), bool(data["occupied"]))]
    if topic == f"{prefix}/receivers":
        return [Receivers(float(data["ts"]), tuple(map(str, data["receivers"])))]
    return []


def to_line(ev: Event) -> str:
    kind = next(k for k, cls in KINDS.items() if isinstance(ev, cls))
    return json.dumps({"type": kind} | asdict(ev))


def read_log(lines: Iterable[str]) -> Iterator[Event]:
    for line in lines:
        if line.strip():
            d = json.loads(line)
            cls = KINDS[d.pop("type")]
            if cls is Receivers:
                d["ids"] = tuple(d["ids"])
            yield cls(**d)


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
        elif isinstance(ev, Receivers):
            locator.set_receivers(ev)


def tracker_factory(src: str | dict) -> tuple[Callable[[], Tracker] | None, set[str]]:
    """Plan (path or dict) -> tracker factory and receiver ids; None while the plan has no rooms."""
    plan = load_plan(src)
    if not plan.rooms:
        return None, set()
    grid = make_grid(plan)
    # ponytail: channel priors (plus per-receiver p0 overrides); load fitted ChannelParams once there is a calibration CLI
    mu = predict(plan, ChannelParams.prior(plan), grid.xy)
    kernel, ids = motion_kernel(grid), [r.id for r in plan.receivers]
    return (lambda: Tracker(grid, mu, ids, kernel)), set(ids)


def build_locator(src: str | dict) -> tuple[Locator, set[str]]:
    factory, ids = tracker_factory(src)
    return Locator(factory), ids


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


RELOAD = "\0reload"  # inbox topic: the web UI saved a new plan


def live(args, locator: Locator, receivers: set[str]):
    import paho.mqtt.client as mqtt

    inbox: queue.Queue = queue.Queue()
    app = None
    if args.http_port:
        from .web import App, serve

        app = App(args.plan, lambda raw: inbox.put((RELOAD, tracker_factory(raw))))
        serve(app, args.http_bind, args.http_port)
    rssi: dict[str, dict[str, tuple[float, float]]] = {}  # receiver -> device -> (rssi, ts)
    status = f"{args.prefix}/status"
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"reiatsu-{args.prefix}")
    if args.mqtt_user:
        client.username_pw_set(args.mqtt_user, args.mqtt_password)
    client.will_set(status, "offline", retain=True)

    def on_connect(c, _userdata, _flags, reason, _props):
        log.info("MQTT connected (%s)", reason)
        c.subscribe([(f"{args.prefix}/adverts", 0), (f"{args.prefix}/occupancy/+", 0), (f"{args.prefix}/receivers", 0)])
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
            if topic == RELOAD:
                factory, ids = payload
                locator.reset(factory)
                receivers.clear()
                receivers.update(ids)
                warned.clear()
                log.info("plan reloaded: %d receivers", len(ids))
                continue
            try:
                evs = parse(topic, payload, args.prefix)
            except (ValueError, KeyError, TypeError) as err:
                log.warning("bad message on %s: %s", topic, err)
                continue
            for ev in evs:
                if record:
                    record.write(to_line(ev) + "\n")
                if isinstance(ev, Advert):
                    rssi.setdefault(ev.receiver_id, {})[ev.device_id] = (ev.rssi, ev.ts)
                if isinstance(ev, Advert) and ev.receiver_id not in receivers and ev.receiver_id not in warned:
                    warned.add(ev.receiver_id)
                    log.warning("receiver %r is not in the floor plan; its adverts are ignored", ev.receiver_id)
                yield time.time(), ev

    publish = Publisher(client, args.prefix, args.discovery_prefix, args.publish_interval)

    def emit(estimates: list[Estimate]):
        publish(estimates)
        if app:
            app.snapshot = snapshot(locator, estimates, receivers, rssi)

    try:
        track(events(), locator, emit)
    finally:
        client.publish(status, "offline", retain=True)
        client.loop_stop()


def snapshot(locator: Locator, estimates: list[Estimate], receivers: set[str], rssi: dict) -> dict:
    """Everything the live map shows, as of this tick (built in the tracking thread)."""
    return {
        "ts": estimates[0].ts if estimates else time.time(),
        "devices": {e.device_id: state(e) for e in estimates},
        "beliefs": {dev: t.belief for dev, t in locator.trackers.items()},
        "occupancy": dict(locator.occupancy),
        "online": sorted(locator.listening) if locator.listening is not None else None,
        "plan_receivers": sorted(receivers),
        "rssi": {rx: {dev: list(v) for dev, v in d.items()} for rx, d in rssi.items()},
    }


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
    ap.add_argument("--http-port", type=int, default=int(env("REIATSU_HTTP_PORT", "8099")), help="web UI port, 0 = off [REIATSU_HTTP_PORT]")
    ap.add_argument("--http-bind", default=env("REIATSU_HTTP_BIND", "0.0.0.0"), help="web UI address; no auth, keep it on the LAN [REIATSU_HTTP_BIND]")
    ap.add_argument("--log-level", default=env("REIATSU_LOG_LEVEL", "info"), choices=["debug", "info", "warning", "error"], help="[REIATSU_LOG_LEVEL]")
    ap.add_argument("--publish-interval", type=float, default=5.0, help="seconds between state updates while the room is unchanged")
    args = ap.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s")

    if not args.replay and args.http_port and not os.path.exists(args.plan):
        log.info("no plan at %s yet: draw one in the web UI", args.plan)
        locator, receivers = build_locator({})
    else:
        locator, receivers = build_locator(args.plan)
    if args.replay:
        with open(args.replay) as f:
            track(((ev.ts, ev) for ev in read_log(f)), locator, print_estimates)
    else:
        live(args, locator, receivers)


if __name__ == "__main__":
    sys.exit(main())

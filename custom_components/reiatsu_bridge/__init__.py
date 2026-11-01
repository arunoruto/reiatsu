"""Reiatsu Bridge: forward per-scanner BLE sightings and mmWave states to MQTT.

HA's own advertisement stream keeps only the best scanner per device. This
subscribes to every advertisement of the tracked devices, from every scanner,
and publishes them for the Reiatsu daemon:

- ``<prefix>/adverts``: JSON list of {ts, receiver_id, device_id, rssi},
  flushed every ``poll_interval`` seconds
- ``<prefix>/occupancy/<area>``: retained {ts, area, occupied}

IRKs never leave the config entry: devices are published by the slug of their
friendly name.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from functools import partial
import json
import time

from bluetooth_data_tools import get_cipher_for_irk, resolve_private_address

from homeassistant.components import bluetooth, mqtt
from homeassistant.components.bluetooth.match import BluetoothCallbackMatcher
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import slugify

from .const import CONF_DEVICES, CONF_INTERVAL, CONF_MMWAVE, CONF_PREFIX, CONF_RECEIVERS

MAX_UNKNOWN = 50_000  # addresses that resolved to no IRK; cleared when full


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await mqtt.async_wait_for_mqtt_client(hass):
        raise ConfigEntryNotReady("MQTT integration is not available")
    entry.runtime_data = bridge = Bridge(hass, dict(entry.options))
    bridge.start()
    entry.async_on_unload(bridge.stop)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True


class Bridge:
    def __init__(self, hass: HomeAssistant, opts: dict) -> None:
        self.hass = hass
        self.prefix = opts[CONF_PREFIX]
        self.interval = opts[CONF_INTERVAL]
        self.receivers: dict[str, str] = dict(opts.get(CONF_RECEIVERS, {}))
        self.mmwave: dict[str, str] = opts.get(CONF_MMWAVE, {})
        devices = opts.get(CONF_DEVICES, [])
        self.macs = {d["mac"].upper(): slugify(d["name"]) for d in devices if "mac" in d}
        self.ciphers = [(get_cipher_for_irk(bytes.fromhex(d["irk"])), slugify(d["name"])) for d in devices if "irk" in d]
        self.following: dict[str, tuple[str, Callable[[], None]]] = {}  # address -> (device_id, cancel)
        self.unknown: set[str] = set()
        self.buffer: list[dict] = []
        self.areas: dict[str, bool] = {}
        self.unsubs: list = []

    def start(self) -> None:
        for mac, dev in self.macs.items():
            self._follow(mac, dev)
        if self.ciphers:
            # fires on new/changed data per address: enough to spot a fresh RPA
            self.unsubs.append(
                bluetooth.async_register_callback(
                    self.hass, self._seen, BluetoothCallbackMatcher(connectable=False), bluetooth.BluetoothScanningMode.PASSIVE
                )
            )
        self.unsubs.append(async_track_time_interval(self.hass, self._flush, timedelta(seconds=self.interval)))
        if self.mmwave:
            self.unsubs.append(async_track_state_change_event(self.hass, list(self.mmwave), self._state_changed))
            self._publish_areas()

    @callback
    def stop(self) -> None:
        for unsub in self.unsubs:
            unsub()
        for _, cancel in self.following.values():
            cancel()
        self.unsubs, self.following = [], {}

    @callback
    def _seen(self, info: bluetooth.BluetoothServiceInfoBleak, change: bluetooth.BluetoothChange) -> None:
        addr = info.address
        if addr in self.following or addr in self.unknown:
            return
        for cipher, dev in self.ciphers:
            if resolve_private_address(cipher, addr):
                # the phone rotated its address: drop the old one(s)
                for old in [a for a, (d, _) in self.following.items() if d == dev]:
                    self.following.pop(old)[1]()
                self._follow(addr, dev)
                self._advert(dev, info)
                return
        if len(self.unknown) >= MAX_UNKNOWN:
            self.unknown.clear()
        self.unknown.add(addr)

    def _follow(self, addr: str, dev: str) -> None:
        cancel = bluetooth.async_register_advertisement_callback(self.hass, partial(self._advert, dev), addr)
        self.following[addr] = (dev, cancel)

    @callback
    def _advert(self, dev: str, info: bluetooth.BluetoothServiceInfoBleak) -> None:
        # one call per packet per scanner, so every call is a fresh sighting
        self.buffer.append({"ts": round(time.time(), 3), "receiver_id": self._receiver(info.source), "device_id": dev, "rssi": info.rssi})

    def _receiver(self, source: str) -> str:
        if source not in self.receivers:
            scanner = bluetooth.async_scanner_by_source(self.hass, source)
            self.receivers[source] = slugify(scanner.name) if scanner else slugify(source)
        return self.receivers[source]

    async def _flush(self, _now) -> None:
        if self.buffer:
            batch, self.buffer = self.buffer, []
            await mqtt.async_publish(self.hass, f"{self.prefix}/adverts", json.dumps(batch))

    @callback
    def _state_changed(self, event: Event[EventStateChangedData]) -> None:
        self._publish_areas()

    @callback
    def _publish_areas(self) -> None:
        states: dict[str, list[bool]] = {}
        for entity_id, area in self.mmwave.items():
            state = self.hass.states.get(entity_id)
            if state and state.state in ("on", "off"):
                states.setdefault(area, []).append(state.state == "on")
        for area, values in states.items():
            occupied = any(values)
            if self.areas.get(area) != occupied:
                self.areas[area] = occupied
                payload = json.dumps({"ts": round(time.time(), 3), "area": area, "occupied": occupied})
                self.hass.async_create_task(
                    mqtt.async_publish(self.hass, f"{self.prefix}/occupancy/{slugify(area)}", payload, retain=True)
                )

"""Stream interface: adverts and occupancy in, per-device estimates out (see daemon.py)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from statistics import median
from typing import Callable

from .tracker import Estimate, Tracker


@dataclass(frozen=True)
class Advert:
    ts: float
    receiver_id: str
    device_id: str
    rssi: float


@dataclass(frozen=True)
class Occupancy:
    ts: float
    area: str  # room or zone name
    occupied: bool


@dataclass(frozen=True)
class Receivers:
    ts: float
    ids: tuple[str, ...]  # receivers currently online


class Locator:
    """One tracker per device. Call :meth:`tick` about once per second."""

    def __init__(self, new_tracker: Callable[[], Tracker]):
        self.new_tracker = new_tracker
        self.trackers: dict[str, Tracker] = {}
        self.occupancy: dict[str, bool] = {}
        self.listening: set[str] | None = None  # None: assume every receiver in the plan is online
        self._buf: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    def advert(self, a: Advert):
        self._buf[a.device_id][a.receiver_id].append(a.rssi)

    def set_occupancy(self, o: Occupancy):
        self.occupancy[o.area] = o.occupied
        for t in self.trackers.values():
            t.set_occupancy(o.area, o.occupied)

    def set_receivers(self, r: Receivers):
        self.listening = set(r.ids)
        for t in self.trackers.values():
            t.set_listening(self.listening)

    def tick(self, ts: float) -> list[Estimate]:
        out = []
        for dev in sorted(set(self._buf) | set(self.trackers)):
            if dev not in self.trackers:
                self.trackers[dev] = t = self.new_tracker()
                for area, occ in self.occupancy.items():
                    t.set_occupancy(area, occ)
                if self.listening is not None:
                    t.set_listening(self.listening)
            readings = {rx: median(v) for rx, v in self._buf.pop(dev, {}).items()}
            out.append(replace(self.trackers[dev].step(ts, readings), device_id=dev))
        return out

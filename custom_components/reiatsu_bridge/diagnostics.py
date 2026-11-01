from __future__ import annotations

from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

TO_REDACT = {"irk", "mac"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    bridge = entry.runtime_data
    return {
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "following_addresses": len(bridge.following),
        "receivers": bridge.receivers,
        "scanners": {s.source: s.name for s in bluetooth.async_current_scanners(hass)},
        "areas": bridge.areas,
    }

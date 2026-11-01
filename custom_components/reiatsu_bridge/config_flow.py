from __future__ import annotations

import base64
import binascii
import re
from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import slugify

from .const import CONF_DEVICES, CONF_INTERVAL, CONF_MMWAVE, CONF_PREFIX, CONF_RECEIVERS, DEFAULT_INTERVAL, DEFAULT_PREFIX, DOMAIN

MAC = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
TOPIC = re.compile(r"^[^#+\s]+$")


def parse_irk(irk: str) -> bytes | None:
    """Hex or (byte-reversed) base64, as accepted by HA's private_ble_device."""
    irk = irk.strip().removeprefix("irk:")
    try:
        raw = bytes(reversed(base64.b64decode(irk))) if irk.endswith("=") else binascii.unhexlify(irk)
    except (binascii.Error, ValueError):
        return None
    return raw if len(raw) == 16 else None


def _settings_schema(prefix: str, interval: float) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_PREFIX, default=prefix): str,
            vol.Required(CONF_INTERVAL, default=interval): vol.All(vol.Coerce(float), vol.Range(min=0.2, max=60)),
        }
    )


class ReiatsuBridgeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            if TOPIC.match(user_input[CONF_PREFIX]):
                return self.async_create_entry(
                    title="Reiatsu Bridge", data={}, options={**user_input, CONF_DEVICES: [], CONF_RECEIVERS: {}, CONF_MMWAVE: {}}
                )
            errors[CONF_PREFIX] = "invalid_topic"
        return self.async_show_form(step_id="user", data_schema=_settings_schema(DEFAULT_PREFIX, DEFAULT_INTERVAL), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return OptionsFlow()


class OptionsFlow(OptionsFlowWithReload):
    """Menu: settings, add/remove device, receiver names, mmWave sensors."""

    def __init__(self) -> None:
        self._mmwave_entities: list[str] = []

    def _save(self, **changes: Any) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["add_device", "remove_device", "receivers", "mmwave", "settings"])

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        opts, errors = self.config_entry.options, {}
        if user_input is not None:
            if TOPIC.match(user_input[CONF_PREFIX]):
                return self._save(**user_input)
            errors[CONF_PREFIX] = "invalid_topic"
        return self.async_show_form(
            step_id="settings", data_schema=_settings_schema(opts[CONF_PREFIX], opts[CONF_INTERVAL]), errors=errors
        )

    async def async_step_add_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        devices, errors = list(self.config_entry.options.get(CONF_DEVICES, [])), {}
        if user_input is not None:
            name, ident = user_input["name"].strip(), user_input["identifier"].strip()
            if not slugify(name) or slugify(name) in {slugify(d["name"]) for d in devices}:
                errors["name"] = "name_taken"
            elif MAC.match(ident):
                return self._save(**{CONF_DEVICES: devices + [{"name": name, "mac": ident.upper()}]})
            elif irk := parse_irk(ident):
                return self._save(**{CONF_DEVICES: devices + [{"name": name, "irk": irk.hex()}]})
            else:
                errors["identifier"] = "invalid_identifier"
        schema = vol.Schema(
            {
                vol.Required("name"): str,
                vol.Required("identifier"): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
            }
        )
        return self.async_show_form(step_id="add_device", data_schema=schema, errors=errors)

    async def async_step_remove_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        devices = self.config_entry.options.get(CONF_DEVICES, [])
        if not devices:
            return self.async_abort(reason="no_devices")
        if user_input is not None:
            return self._save(**{CONF_DEVICES: [d for d in devices if d["name"] not in user_input["names"]]})
        names = [d["name"] for d in devices]
        schema = vol.Schema({vol.Required("names"): SelectSelector(SelectSelectorConfig(options=names, multiple=True))})
        return self.async_show_form(step_id="remove_device", data_schema=schema)

    async def async_step_receivers(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = self.config_entry.options.get(CONF_RECEIVERS, {})
        scanners = {s.source: s.name for s in bluetooth.async_current_scanners(self.hass)}
        if user_input is not None:
            return self._save(**{CONF_RECEIVERS: {**current, **{k: slugify(v) for k, v in user_input.items()}}})
        if not scanners:
            return self.async_abort(reason="no_scanners")
        schema = vol.Schema({vol.Required(src, default=current.get(src, slugify(name))): str for src, name in scanners.items()})
        listing = "\n".join(f"- `{src}`: {name}" for src, name in scanners.items())
        return self.async_show_form(step_id="receivers", data_schema=schema, description_placeholders={"scanners": listing})

    async def async_step_mmwave(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = self.config_entry.options.get(CONF_MMWAVE, {})
        if user_input is not None:
            self._mmwave_entities = user_input["entities"]
            if not self._mmwave_entities:
                return self._save(**{CONF_MMWAVE: {}})
            return await self.async_step_mmwave_areas()
        schema = vol.Schema(
            {
                vol.Optional("entities", default=list(current)): EntitySelector(
                    EntitySelectorConfig(domain="binary_sensor", multiple=True)
                )
            }
        )
        return self.async_show_form(step_id="mmwave", data_schema=schema)

    async def async_step_mmwave_areas(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(**{CONF_MMWAVE: {e: user_input[e].strip() for e in self._mmwave_entities}})
        current = self.config_entry.options.get(CONF_MMWAVE, {})
        schema = vol.Schema({vol.Required(e, default=current.get(e) or self._ha_area(e)): str for e in self._mmwave_entities})
        return self.async_show_form(step_id="mmwave_areas", data_schema=schema)

    def _ha_area(self, entity_id: str) -> str:
        """Default to the entity's HA area name; must match a room/zone name in the floor plan."""
        entry = er.async_get(self.hass).async_get(entity_id)
        area_id = entry and (entry.area_id or (entry.device_id and self._device_area(entry.device_id)))
        area = area_id and ar.async_get(self.hass).async_get_area(area_id)
        return area.name if area else ""

    def _device_area(self, device_id: str) -> str | None:
        device = dr.async_get(self.hass).async_get(device_id)
        return device.area_id if device else None

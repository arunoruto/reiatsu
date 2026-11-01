"""reiatsu_bridge against a real HA bluetooth manager with fake remote scanners.

    nix develop .#ha -c pytest tests_ha
"""

import json
import time
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from habluetooth import BaseHaRemoteScanner

from homeassistant.components import bluetooth
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.reiatsu_bridge.const import DOMAIN
from custom_components.reiatsu_bridge.diagnostics import async_get_config_entry_diagnostics

IRK = bytes(range(16))
IPHONE = {76: b"\x10\x05\x01\x18\x00\x00\x00"}  # Apple "nearby" frame: passes HA's Apple pre-filter


def rpa(irk: bytes, prand: bytes) -> str:
    enc = Cipher(algorithms.AES(irk), modes.ECB()).encryptor()
    h = (enc.update(b"\x00" * 13 + prand) + enc.finalize())[13:]
    return ":".join(f"{b:02X}" for b in prand + h)


def scanner(hass, source):
    s = BaseHaRemoteScanner(source, source, None, False)
    s.async_setup()
    bluetooth.async_register_scanner(hass, s)
    return s


def advertise(s, address, rssi, mfr=IPHONE):
    s._async_on_advertisement(address, rssi, None, [], {}, mfr, None, {}, time.monotonic())


async def setup_bridge(hass, **options):
    opts = {"topic_prefix": "reiatsu", "poll_interval": 1.0, "devices": [], "receivers": {}, "mmwave": {}} | options
    entry = MockConfigEntry(domain=DOMAIN, options=opts)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def published(mqtt_mock, topic):
    return [json.loads(c.args[1]) for c in mqtt_mock.async_publish.call_args_list if c.args[0] == topic]


async def flush(hass, entry):
    await entry.runtime_data._flush(None)
    await hass.async_block_till_done()


async def test_irk_device_every_scanner_and_rotation(hass, enable_bluetooth, mqtt_mock):
    a, b = scanner(hass, "AA:00:00:00:00:01"), scanner(hass, "AA:00:00:00:00:02")
    entry = await setup_bridge(
        hass, devices=[{"name": "Mirza iPhone", "irk": IRK.hex()}], receivers={"AA:00:00:00:00:01": "living_room"}
    )
    addr = rpa(IRK, b"\x55\x12\x34")
    advertise(a, addr, -60)  # first sighting resolves the RPA and subscribes
    advertise(b, addr, -80)  # weaker scanner: HA's own stream would drop this one
    advertise(a, addr, -61)  # identical data again: still a fresh packet
    advertise(a, rpa(bytes(16), b"\x55\x00\x01"), -50)  # someone else's phone
    await flush(hass, entry)
    batches = published(mqtt_mock, "reiatsu/adverts")
    assert len(batches) == 1
    got = [(x["receiver_id"], x["device_id"], x["rssi"]) for x in batches[0]]
    assert got == [("living_room", "mirza_iphone", -60), ("aa_00_00_00_00_02", "mirza_iphone", -80), ("living_room", "mirza_iphone", -61)]

    new = rpa(IRK, b"\x66\x00\x01")  # the phone rotated its address
    advertise(a, new, -58)
    advertise(a, addr, -40)  # old address is no longer followed
    await flush(hass, entry)
    assert [x["rssi"] for x in published(mqtt_mock, "reiatsu/adverts")[-1]] == [-58]
    assert len(entry.runtime_data.following) == 1

    # nothing new: nothing published
    n = mqtt_mock.async_publish.call_count
    await flush(hass, entry)
    assert mqtt_mock.async_publish.call_count == n

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert IRK.hex() not in json.dumps(diag)
    assert all(IRK.hex() not in str(c) for c in mqtt_mock.async_publish.call_args_list)


async def test_mac_device_and_mmwave(hass, enable_bluetooth, mqtt_mock):
    a = scanner(hass, "AA:00:00:00:00:01")
    hass.states.async_set("binary_sensor.lr_fp2", "off")
    hass.states.async_set("binary_sensor.lr_ld2410", "off")
    entry = await setup_bridge(
        hass,
        devices=[{"name": "Watch", "mac": "11:22:33:44:55:66"}],
        mmwave={"binary_sensor.lr_fp2": "Living Room", "binary_sensor.lr_ld2410": "Living Room"},
    )
    advertise(a, "11:22:33:44:55:66", -70, mfr={})
    await flush(hass, entry)
    assert published(mqtt_mock, "reiatsu/adverts")[0][0]["device_id"] == "watch"

    topic = "reiatsu/occupancy/living_room"
    hass.states.async_set("binary_sensor.lr_fp2", "on")
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.lr_ld2410", "on")  # area already occupied: no new message
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.lr_fp2", "off")
    hass.states.async_set("binary_sensor.lr_ld2410", "off")
    await hass.async_block_till_done()
    assert [m["occupied"] for m in published(mqtt_mock, topic)] == [False, True, False]
    assert all(c.args[3] for c in mqtt_mock.async_publish.call_args_list if c.args[0] == topic)  # retained


async def test_config_and_options_flow(hass, enable_bluetooth, mqtt_mock):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"topic_prefix": "bad #", "poll_interval": 1})
    assert result["errors"] == {"topic_prefix": "invalid_topic"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"topic_prefix": "reiatsu", "poll_interval": 1})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    await hass.async_block_till_done()

    async def option(step, data):
        r = await hass.config_entries.options.async_init(entry.entry_id)
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": step})
        r = await hass.config_entries.options.async_configure(r["flow_id"], data)
        await hass.async_block_till_done()
        return r

    r = await option("add_device", {"name": "Phone", "identifier": "nope"})
    assert r["errors"] == {"identifier": "invalid_identifier"}
    r = await option("add_device", {"name": "Mirza iPhone", "identifier": IRK.hex()})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    r = await option("add_device", {"name": "mirza iphone", "identifier": "11:22:33:44:55:66"})
    assert r["errors"] == {"name": "name_taken"}
    assert entry.options["devices"] == [{"name": "Mirza iPhone", "irk": IRK.hex()}]

    scanner(hass, "AA:00:00:00:00:01")
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "receivers"})
    form = {k.schema: k.default() for k in r["data_schema"].schema}
    assert form["AA:00:00:00:00:01"] == "aa_00_00_00_00_01"  # default: slug of the scanner name
    r = await hass.config_entries.options.async_configure(r["flow_id"], form | {"AA:00:00:00:00:01": "Living Room"})
    await hass.async_block_till_done()
    assert entry.options["receivers"]["AA:00:00:00:00:01"] == "living_room"

    hass.states.async_set("binary_sensor.kitchen_presence", "off")
    r = await option("mmwave", {"entities": ["binary_sensor.kitchen_presence"]})
    assert r["step_id"] == "mmwave_areas"
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"binary_sensor.kitchen_presence": "Kitchen"})
    await hass.async_block_till_done()
    assert entry.options["mmwave"] == {"binary_sensor.kitchen_presence": "Kitchen"}

    with patch("custom_components.reiatsu_bridge.Bridge.start"):
        r = await option("remove_device", {"names": ["Mirza iPhone"]})
    assert entry.options["devices"] == []

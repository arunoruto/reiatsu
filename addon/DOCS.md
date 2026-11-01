# Reiatsu

Reiatsu locates phones (and other BLE devices) room by room from the adverts your
Bluetooth proxies hear. This add-on runs the Reiatsu daemon and its web UI (plan editor
and live map), which opens from the sidebar through ingress.

## Setup

1. Install the MQTT broker (Mosquitto add-on) and the MQTT integration. The add-on gets
   the broker address and credentials from the Supervisor, so you configure nothing.
2. Install the **Reiatsu Bridge** integration (HACS, custom repository
   `https://github.com/arunoruto/reiatsu`, type Integration). It forwards every proxy's
   adverts of your tracked devices, and your mmWave sensors, to MQTT. Use the same
   topic prefix in both.
3. Start the add-on and open **Reiatsu** in the sidebar. Draw your floor plan (or upload
   an image and trace it), then place the receivers. Each device appears in HA as
   `sensor.reiatsu_<device>_room` with x, y and position uncertainty.

## Files

Everything lives in the add-on config folder, `/addon_configs/<id>_reiatsu/` on the host
(visible through the Samba or SSH add-ons):

- `plan.yaml`: the floor plan; the UI writes it and keeps `plan.yaml.bak`
- `background.png` / `background.jpg`: the uploaded floor plan image
- `survey.json`, `calibration.json`: survey points and the applied calibration
  (from the UI's Survey tab)
- `record.jsonl`: raw input, when **Record** is on; replay it with
  `reiatsu --plan plan.yaml --replay record.jsonl`

## Moving from a hand-run daemon

Stop the old daemon, then copy its files into the add-on config folder before
starting the add-on, from any terminal add-on that maps `/addon_configs` (the
*Advanced SSH & Web Terminal* add-on does; otherwise use the Samba share
`addon_configs`):

```sh
cp /config/reiatsu/plan.yaml /config/reiatsu/background.* /addon_configs/*_reiatsu/ 2>/dev/null
cat /config/reiatsu/record.jsonl >> /addon_configs/*_reiatsu/record.jsonl
```

The recording continues where it left off: the add-on appends to `record.jsonl`.

## Options

- **Record**: log raw input to `record.jsonl`.
- **Topic prefix**: MQTT topic prefix, default `reiatsu`.
- **Log level**.

The web UI is reachable through ingress only. The `8099/tcp` port under *Network* opens
it directly on your LAN without authentication; leave it empty unless you need it.

# Installation

You need the **bridge** in Home Assistant, which forwards every proxy's adverts, and the
**daemon**, which does the locating, either as a Home Assistant add-on or anywhere else
that can reach your MQTT broker.

## 1. Bridge (Home Assistant integration)

Requirements: Home Assistant ≥ 2026.10 with the Bluetooth and MQTT integrations (for
example the Mosquitto add-on).

1. HACS → ⋮ → *Custom repositories* → `https://github.com/arunoruto/reiatsu`, type
   *Integration*. Install **Reiatsu Bridge** and restart Home Assistant. (Without HACS:
   copy `custom_components/reiatsu_bridge` into your config directory.)
2. *Settings → Devices & services → Add integration → Reiatsu Bridge*. Pick the topic
   prefix (default `reiatsu`) and publish interval (default 1 s).
3. *Configure*:
   - **Add a device**: a friendly name plus an iPhone IRK (hex or base64, as for *Private BLE Device*) or a fixed MAC. It is published as the slug of the name (`Mirza iPhone` → `mirza_iphone`). The IRK never leaves the config entry.
   - **Name receivers**: one id per Bluetooth scanner. These are the ids you place on the floor plan.
   - **mmWave sensors**: presence binary sensors, each mapped to a room or zone name from the plan. The name must match exactly. An area the plan doesn't have (or a room you renamed later) is ignored, with a warning in the daemon log.

The bridge only reads from Home Assistant's Bluetooth manager, so Bermuda and *Private BLE
Device* keep working.

## 2. Daemon

### Home Assistant add-on (recommended)

*Settings → Add-ons → Add-on store → ⋮ → Repositories* → add
`https://github.com/arunoruto/reiatsu`, install **Reiatsu** and start it. It gets the
broker credentials from the Supervisor, and its UI opens in the sidebar through ingress.
Options: record raw input, topic prefix, log level. Files live in
`/addon_configs/<id>_reiatsu/`.

### Docker

```yaml
services:
  reiatsu:
    image: ghcr.io/arunoruto/reiatsu:latest   # or a version, e.g. :0.4
    restart: unless-stopped
    ports: ["8099:8099"]                     # web UI: no auth, keep it on the LAN
    volumes: ["./config:/config"]            # plan.yaml, survey, calibration, recordings
    environment:
      REIATSU_MQTT_HOST: 192.168.1.10
      REIATSU_MQTT_USER: reiatsu
      REIATSU_MQTT_PASSWORD: ${REIATSU_MQTT_PASSWORD}
      REIATSU_RECORD: /config/record.jsonl
```

### Nix

```sh
nix run github:arunoruto/reiatsu -- --plan plan.yaml --mqtt-host broker.lan
```

### Python

```sh
pip install git+https://github.com/arunoruto/reiatsu
reiatsu --plan plan.yaml --mqtt-host broker.lan
```

Every option also reads an environment variable: `REIATSU_PLAN`, `REIATSU_MQTT_HOST`,
`REIATSU_MQTT_PORT`, `REIATSU_MQTT_USER`, `REIATSU_MQTT_PASSWORD`, `REIATSU_PREFIX`,
`REIATSU_DISCOVERY_PREFIX`, `REIATSU_RECORD`, `REIATSU_HTTP_PORT`, `REIATSU_HTTP_BIND`,
`REIATSU_LOG_LEVEL`. If the plan file doesn't exist yet, the daemon starts empty and you
draw the plan in the web UI.

```{warning}
The web UI has no authentication and can overwrite your plan. Keep it on the LAN, bind it
to `127.0.0.1`, or use the add-on, where it sits behind ingress.
```

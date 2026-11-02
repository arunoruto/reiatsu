# Reiatsu

Reiatsu locates phones (and other BLE devices) room by room from the adverts that
ESPHome Bluetooth proxies already hear. It's a small service in the style of
zigbee2mqtt that speaks MQTT. Home Assistant is the main target but not a requirement.

Bermuda and ESPresense mostly go by "the strongest receiver wins". Reiatsu instead keeps a
probability over every 20 cm cell of your floor plan and updates it with a physical
channel model that knows about walls, motion that can only pass through doors, and
mmWave presence sensors. A short survey calibrates it to your home.

```{toctree}
:caption: User guide
:maxdepth: 2

overview
guide/install
guide/receivers
guide/survey
guide/troubleshooting
```

```{toctree}
:caption: Theory
:maxdepth: 2

theory/notation
theory/geometry
theory/channel
theory/tracking
theory/fusion
theory/calibration
theory/learning
theory/simulation
theory/limitations
references
```

```{toctree}
:caption: Reference
:maxdepth: 2

api
```

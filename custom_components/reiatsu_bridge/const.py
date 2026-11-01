DOMAIN = "reiatsu_bridge"

CONF_PREFIX = "topic_prefix"
CONF_INTERVAL = "poll_interval"
CONF_DEVICES = "devices"  # [{"name": str, "irk": hex} | {"name": str, "mac": "AA:BB:.."}]
CONF_RECEIVERS = "receivers"  # {scanner source: receiver_id}
CONF_MMWAVE = "mmwave"  # {entity_id: area}

DEFAULT_PREFIX = "reiatsu"
DEFAULT_INTERVAL = 1.0

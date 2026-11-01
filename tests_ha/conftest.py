import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def expected_lingering_timers() -> bool:
    return True  # HA's MQTT client leaves its periodic misc timer behind

from __future__ import annotations

from typing import List
import logging

import voluptuous as vol

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
import homeassistant.helpers.config_validation as cv
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import DOMAIN
from .client import WattBoxClient, DeviceMetrics

_LOGGER = logging.getLogger(__name__)

CONF_VERIFY_SSL = "verify_ssl"

PLATFORM_SCHEMA = cv.PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_SCAN_INTERVAL): cv.time_period,
        vol.Optional(CONF_VERIFY_SSL, default=True): cv.boolean,
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    host: str = config[CONF_HOST]
    username: str = config[CONF_USERNAME]
    password: str = config[CONF_PASSWORD]
    verify_ssl: bool = config[CONF_VERIFY_SSL]

    if host.startswith("http://") or host.startswith("https://"):
        base_url = host.rstrip("/")
    else:
        base_url = f"http://{host}"

    client = WattBoxClient(
        base_url=base_url,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
    )

    try:
        metrics = await client.async_fetch_metrics()
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to initialize WB-800 metrics at %s: %s", host, exc)
        return

    add_entities(
        [
            WattBoxMetricSensor(client, host, "Voltage", "voltage", metrics.voltage, "V"),
            WattBoxMetricSensor(client, host, "Power", "total_watts", metrics.total_watts, "W"),
            WattBoxMetricSensor(client, host, "Current", "total_amps", metrics.total_amps, "A"),
        ],
        update_before_add=True,
    )


class WattBoxMetricSensor(SensorEntity):
    def __init__(
        self,
        client: WattBoxClient,
        host: str,
        name: str,
        key: str,
        initial_value: float | None,
        unit: str,
    ) -> None:
        self._client = client
        self._host = host
        self._name = name
        self._key = key
        self._unit = unit
        self._state = initial_value
        self._attr_unique_id = f"wb800-{host}-metric-{key}"

    @property
    def name(self) -> str:
        return f"WattBox {self._name}"

    @property
    def native_value(self):
        return self._state

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self._unit

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=f"WattBox WB-800 ({self._host})",
            manufacturer="SnapAV",
            model="WattBox WB-800",
        )

    async def async_update(self) -> None:
        try:
            metrics: DeviceMetrics = await self._client.async_fetch_metrics()
            value = getattr(metrics, self._key)
            self._state = value
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to update metrics %s: %s", self._key, exc)


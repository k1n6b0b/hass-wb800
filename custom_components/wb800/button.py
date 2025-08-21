from __future__ import annotations

import logging
from typing import List

import voluptuous as vol

from homeassistant.components.button import ButtonEntity
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
import homeassistant.helpers.config_validation as cv
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.entity import DeviceInfo

from . import DOMAIN
from .client import WattBoxClient, OutletInfo

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
        outlets: List[OutletInfo] = await client.async_fetch_outlets()
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to initialize WB-800 at %s: %s", host, exc)
        return

    # Create one reset button per outlet (including reset-only outlets)
    entities: List[WattBoxResetButton] = []
    for outlet in outlets:
        entities.append(WattBoxResetButton(client, host, outlet))

    add_entities(entities)


class WattBoxResetButton(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, client: WattBoxClient, host: str, outlet: OutletInfo) -> None:
        self._client = client
        self._host = host
        self._number = outlet.number
        self._name = outlet.name
        self._attr_unique_id = f"wb800-{host}-outlet-{self._number}-reset"
        self._attr_name = f"{self._name} Reset" if self._name else f"Outlet {self._number} Reset"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=f"WattBox WB-800 ({self._host})",
            manufacturer="SnapAV",
            model="WattBox WB-800",
        )

    async def async_press(self) -> None:
        await self._client.async_reset(self._number)


from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any, Dict, List

import voluptuous as vol

from homeassistant.components.switch import SwitchEntity, PLATFORM_SCHEMA
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .client import WattBoxClient, OutletInfo
from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


CONF_VERIFY_SSL = "verify_ssl"

DEFAULT_VERIFY_SSL = True
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): cv.time_period,
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): cv.boolean,
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

    # Allow full URL in host. If no scheme, default to http.
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

    entities: List[WattBoxSwitch] = []
    for outlet in outlets:
        # Skip creating switches for reset-only outlets
        if outlet.is_reset_only:
            continue
        entities.append(WattBoxSwitch(client, host, outlet))

    add_entities(entities, update_before_add=True)


class WattBoxSwitch(SwitchEntity):
    _attr_has_entity_name = True

    def __init__(self, client: WattBoxClient, host: str, outlet: OutletInfo) -> None:
        self._client = client
        self._host = host
        self._number = outlet.number
        self._name = outlet.name
        self._is_on = outlet.is_on
        self._is_reset_only = outlet.is_reset_only
        self._watts = outlet.watts
        self._amps = outlet.amps
        self._attr_unique_id = f"wb800-{host}-outlet-{self._number}"

    @property
    def name(self) -> str:
        return self._name or f"Outlet {self._number}"

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def available(self) -> bool:
        return True

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=f"WattBox WB-800 ({self._host})",
            manufacturer="SnapAV",
            model="WattBox WB-800",
        )

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "outlet_number": self._number,
            "reset_only": self._is_reset_only,
        }
        if self._watts is not None:
            data["watts"] = self._watts
        if self._amps is not None:
            data["amps"] = self._amps
        return data

    async def async_update(self) -> None:
        try:
            outlets = await self._client.async_fetch_outlets()
            for outlet in outlets:
                if outlet.number == self._number:
                    self._is_on = outlet.is_on
                    self._is_reset_only = outlet.is_reset_only
                    self._watts = outlet.watts
                    self._amps = outlet.amps
                    break
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Update failed for outlet %s: %s", self._number, exc)

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002
        await self._client.async_turn_on(self._number)
        self._is_on = True

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        await self._client.async_turn_off(self._number)
        self._is_on = False

    async def async_reset(self) -> None:
        await self._client.async_reset(self._number)


from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
import homeassistant.helpers.config_validation as cv

from . import get_or_create_client, get_runtime_data
from .client import WattBoxClient
from .const import (
    CONF_VERIFY_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    get_scan_interval_seconds,
    host_label_from_base_url,
    normalize_base_url,
)
from .coordinator import WattBoxCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = cv.PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_SCAN_INTERVAL): cv.time_period,
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): cv.boolean,
    }
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = get_runtime_data(hass, entry)
    async_add_entities(_build_buttons(runtime.coordinator, runtime.host_label, runtime.client))


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    host_input: str = config[CONF_HOST]
    base_url = normalize_base_url(host_input)
    host_label = host_label_from_base_url(base_url)

    client = get_or_create_client(
        hass,
        base_url=base_url,
        username=config[CONF_USERNAME],
        password=config[CONF_PASSWORD],
        verify_ssl=config.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
    )

    coordinator = WattBoxCoordinator(
        hass,
        client=client,
        host_label=host_label,
        scan_interval_seconds=get_scan_interval_seconds(config),
    )

    await coordinator.async_refresh()
    if not coordinator.data:
        _LOGGER.error("Failed to initialize WB-800 buttons at %s", host_input)
        return

    async_add_entities(_build_buttons(coordinator, host_label, client))


def _build_buttons(
    coordinator: WattBoxCoordinator,
    host_label: str,
    client: WattBoxClient,
) -> list[ButtonEntity]:
    return [
        WattBoxResetButton(
            client=client,
            coordinator=coordinator,
            host_label=host_label,
            outlet_number=outlet.number,
            outlet_name=outlet.name,
        )
        for outlet in coordinator.data.outlets
    ]


class WattBoxResetButton(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        client: WattBoxClient,
        coordinator: WattBoxCoordinator,
        host_label: str,
        outlet_number: int,
        outlet_name: str,
    ) -> None:
        self._client = client
        self._coordinator = coordinator
        self._host_label = host_label
        self._number = outlet_number
        self._name = outlet_name or f"Outlet {outlet_number}"
        self._attr_unique_id = f"wb800-{host_label}-outlet-{outlet_number}-reset"
        self._attr_name = f"{self._name} Reset"

    @property
    def available(self) -> bool:
        return self._coordinator.get_outlet(self._number) is not None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._host_label)},
            name=f"WattBox WB-800 ({self._host_label})",
            manufacturer="SnapAV",
            model="WattBox WB-800",
        )

    async def async_press(self) -> None:
        await self._client.async_reset(self._number)
        await self._coordinator.async_request_refresh()

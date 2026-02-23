from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.switch import PLATFORM_SCHEMA, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import get_or_create_client, get_runtime_data
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

PARALLEL_UPDATES = 1

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
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
    async_add_entities(_build_switches(runtime.coordinator, runtime.host_label))


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
        _LOGGER.error("Failed to initialize WB-800 at %s", host_input)
        return

    async_add_entities(_build_switches(coordinator, host_label))


def _build_switches(coordinator: WattBoxCoordinator, host_label: str) -> list[SwitchEntity]:
    entities: list[SwitchEntity] = []
    for outlet in coordinator.data.outlets:
        if outlet.is_reset_only:
            continue
        entities.append(WattBoxSwitch(coordinator, host_label, outlet.number, outlet.name))
    return entities


class WattBoxSwitch(CoordinatorEntity[WattBoxCoordinator], SwitchEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WattBoxCoordinator,
        host_label: str,
        outlet_number: int,
        outlet_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._host_label = host_label
        self._number = outlet_number
        self._name = outlet_name or f"Outlet {outlet_number}"
        self._is_on = False
        self._watts: float | None = None
        self._amps: float | None = None
        self._attr_unique_id = f"wb800-{host_label}-outlet-{outlet_number}"

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._host_label)},
            name=f"WattBox WB-800 ({self._host_label})",
            manufacturer="SnapAV",
            model="WattBox WB-800",
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "outlet_number": self._number,
            "watts": self._watts,
            "amps": self._amps,
        }

    def _handle_coordinator_update(self) -> None:
        outlet = self.coordinator.get_outlet(self._number)
        if outlet is None:
            self._attr_available = False
            self.async_write_ha_state()
            return

        self._attr_available = True
        self._is_on = outlet.is_on
        self._watts = outlet.watts
        self._amps = outlet.amps
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002
        await self.coordinator.client.async_turn_on(self._number)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        await self.coordinator.client.async_turn_off(self._number)
        await self.coordinator.async_request_refresh()

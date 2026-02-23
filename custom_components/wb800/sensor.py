from __future__ import annotations

from datetime import datetime
import logging
from typing import Any, Optional

import voluptuous as vol

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import get_or_create_client, get_runtime_data
from .client import OutletInfo
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
    async_add_entities(_build_entities(runtime.coordinator, runtime.host_label))


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
        _LOGGER.error("Coordinator data is None after initial refresh")
        return

    async_add_entities(_build_entities(coordinator, host_label))


def _build_entities(coordinator: WattBoxCoordinator, host_label: str) -> list[SensorEntity]:
    entities: list[SensorEntity] = [
        WattBoxVoltageSensor(coordinator, host_label),
        WattBoxTotalPowerSensor(coordinator, host_label),
        WattBoxTotalCurrentSensor(coordinator, host_label),
        WattBoxTotalEnergySensor(coordinator, host_label),
    ]

    for outlet in coordinator.data.outlets:
        outlet_name = outlet.name or f"Outlet {outlet.number}"
        entities.extend(
            [
                WattBoxOutletPowerSensor(coordinator, host_label, outlet.number, outlet_name),
                WattBoxOutletCurrentSensor(coordinator, host_label, outlet.number, outlet_name),
                WattBoxOutletEnergySensor(coordinator, host_label, outlet.number, outlet_name),
            ]
        )

    return entities


class _WB800BaseSensor(CoordinatorEntity[WattBoxCoordinator], SensorEntity):
    def __init__(self, coordinator: WattBoxCoordinator, host_label: str) -> None:
        super().__init__(coordinator)
        self._host_label = host_label

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._host_label)},
            name=f"WattBox WB-800 ({self._host_label})",
            manufacturer="SnapAV",
            model="WattBox WB-800",
        )


class WattBoxVoltageSensor(_WB800BaseSensor):
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT

    def __init__(self, coordinator: WattBoxCoordinator, host_label: str) -> None:
        super().__init__(coordinator, host_label)
        self._attr_unique_id = f"wb800-{host_label}-metric-voltage"
        self._attr_name = "WattBox Voltage"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.metrics.voltage if self.coordinator.data else None


class WattBoxTotalPowerSensor(_WB800BaseSensor):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: WattBoxCoordinator, host_label: str) -> None:
        super().__init__(coordinator, host_label)
        self._attr_unique_id = f"wb800-{host_label}-metric-total_watts"
        self._attr_name = "WattBox Power"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.metrics.total_watts if self.coordinator.data else None


class WattBoxTotalCurrentSensor(_WB800BaseSensor):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator: WattBoxCoordinator, host_label: str) -> None:
        super().__init__(coordinator, host_label)
        self._attr_unique_id = f"wb800-{host_label}-metric-total_amps"
        self._attr_name = "WattBox Current"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.metrics.total_amps if self.coordinator.data else None


class _WB800EnergyBaseSensor(RestoreEntity, _WB800BaseSensor):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator: WattBoxCoordinator, host_label: str) -> None:
        super().__init__(coordinator, host_label)
        self._last_power: Optional[float] = None
        self._last_update: Optional[datetime] = None
        self._total_energy: float = 0.0

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is None:
            return
        if last_state.state in ("unknown", "unavailable"):
            return
        try:
            self._total_energy = float(last_state.state)
        except (TypeError, ValueError):
            return

        if last_state.last_updated:
            self._last_update = last_state.last_updated

        last_power = last_state.attributes.get("last_power") if last_state.attributes else None
        if last_power is not None:
            try:
                self._last_power = float(last_power)
            except (TypeError, ValueError):
                pass

    @property
    def native_value(self) -> float:
        return round(self._total_energy, 3)

    @property
    def extra_state_attributes(self) -> dict[str, float | None]:
        return {"last_power": self._last_power}

    def _integrate_power(self, current_power: float | None) -> None:
        if current_power is None:
            return

        now = dt_util.utcnow()
        max_gap_hours = 24.0

        if self._last_power is not None and self._last_update is not None:
            dt_hours = (now - self._last_update).total_seconds() / 3600.0
            if 0 < dt_hours <= max_gap_hours:
                avg_power = (current_power + self._last_power) / 2.0
                self._total_energy += (avg_power * dt_hours) / 1000.0
            elif dt_hours > max_gap_hours:
                self._total_energy += (current_power * max_gap_hours) / 1000.0

        self._last_power = current_power
        self._last_update = now


class WattBoxTotalEnergySensor(_WB800EnergyBaseSensor):
    def __init__(self, coordinator: WattBoxCoordinator, host_label: str) -> None:
        super().__init__(coordinator, host_label)
        self._attr_unique_id = f"wb800-{host_label}-metric-total_energy"
        self._attr_name = "WattBox Energy"

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None

    def _handle_coordinator_update(self) -> None:
        power = self.coordinator.data.metrics.total_watts if self.coordinator.data else None
        self._integrate_power(power)
        self.async_write_ha_state()


class _WB800OutletBaseSensor(_WB800BaseSensor):
    def __init__(
        self,
        coordinator: WattBoxCoordinator,
        host_label: str,
        outlet_number: int,
        outlet_name: str,
    ) -> None:
        super().__init__(coordinator, host_label)
        self._outlet_number = outlet_number
        self._outlet_name = outlet_name

    def _outlet(self) -> OutletInfo | None:
        return self.coordinator.get_outlet(self._outlet_number)


class WattBoxOutletPowerSensor(_WB800OutletBaseSensor):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self,
        coordinator: WattBoxCoordinator,
        host_label: str,
        outlet_number: int,
        outlet_name: str,
    ) -> None:
        super().__init__(coordinator, host_label, outlet_number, outlet_name)
        self._attr_unique_id = f"wb800-{host_label}-outlet-{outlet_number}-watts"
        self._attr_name = f"{outlet_name} Watts"

    @property
    def native_value(self) -> float | None:
        outlet = self._outlet()
        if outlet is None:
            return None
        return outlet.watts if outlet.watts is not None else 0.0


class WattBoxOutletCurrentSensor(_WB800OutletBaseSensor):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(
        self,
        coordinator: WattBoxCoordinator,
        host_label: str,
        outlet_number: int,
        outlet_name: str,
    ) -> None:
        super().__init__(coordinator, host_label, outlet_number, outlet_name)
        self._attr_unique_id = f"wb800-{host_label}-outlet-{outlet_number}-amps"
        self._attr_name = f"{outlet_name} Amps"

    @property
    def native_value(self) -> float | None:
        outlet = self._outlet()
        if outlet is None:
            return None
        return outlet.amps if outlet.amps is not None else 0.0


class WattBoxOutletEnergySensor(RestoreEntity, _WB800OutletBaseSensor):
    def __init__(
        self,
        coordinator: WattBoxCoordinator,
        host_label: str,
        outlet_number: int,
        outlet_name: str,
    ) -> None:
        super().__init__(coordinator, host_label, outlet_number, outlet_name)
        self._last_power: Optional[float] = None
        self._last_update: Optional[datetime] = None
        self._total_energy: float = 0.0
        self._attr_unique_id = f"wb800-{host_label}-outlet-{outlet_number}-energy"
        self._attr_name = f"{outlet_name} Energy"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    @property
    def available(self) -> bool:
        return super().available and self._outlet() is not None

    @property
    def native_value(self) -> float:
        return round(self._total_energy, 3)

    @property
    def extra_state_attributes(self) -> dict[str, float | None]:
        return {"last_power": self._last_power}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is None:
            return
        if last_state.state in ("unknown", "unavailable"):
            return
        try:
            self._total_energy = float(last_state.state)
        except (TypeError, ValueError):
            return

        if last_state.last_updated:
            self._last_update = last_state.last_updated

        last_power = last_state.attributes.get("last_power") if last_state.attributes else None
        if last_power is not None:
            try:
                self._last_power = float(last_power)
            except (TypeError, ValueError):
                pass

    def _handle_coordinator_update(self) -> None:
        outlet = self._outlet()
        power = outlet.watts if outlet else None
        if power is not None:
            now = dt_util.utcnow()
            max_gap_hours = 24.0
            if self._last_power is not None and self._last_update is not None:
                dt_hours = (now - self._last_update).total_seconds() / 3600.0
                if 0 < dt_hours <= max_gap_hours:
                    avg_power = (power + self._last_power) / 2.0
                    self._total_energy += (avg_power * dt_hours) / 1000.0
                elif dt_hours > max_gap_hours:
                    self._total_energy += (power * max_gap_hours) / 1000.0
            self._last_power = power
            self._last_update = now
        self.async_write_ha_state()

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional
import logging

import voluptuous as vol

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME, UnitOfEnergy, UnitOfPower
import homeassistant.helpers.config_validation as cv
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util

from . import DOMAIN
from .client import WattBoxClient, DeviceMetrics, OutletInfo

_LOGGER = logging.getLogger(__name__)

CONF_VERIFY_SSL = "verify_ssl"

DEFAULT_VERIFY_SSL = True
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)

PLATFORM_SCHEMA = cv.PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): cv.time_period,
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): cv.boolean,
    }
)


@dataclass
class WattBoxData:
    """Data structure for WattBox coordinator data."""

    metrics: DeviceMetrics
    outlets: List[OutletInfo]


def _create_update_method(client: WattBoxClient):
    """Create an update method that captures the client."""
    async def _async_update_data() -> WattBoxData:
        """Fetch data from WattBox device."""
        try:
            metrics = await client.async_fetch_metrics()
            outlets = await client.async_fetch_outlets()
            return WattBoxData(metrics=metrics, outlets=outlets)
        except Exception as exc:  # noqa: BLE001
            raise UpdateFailed(f"Error fetching WattBox data: {exc}") from exc
    return _async_update_data


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
    scan_interval: timedelta = config.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

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

    _LOGGER.info("Setting up WB-800 sensor platform for %s", host)
    
    # Create coordinator to centralize data fetching
    coordinator: DataUpdateCoordinator[WattBoxData] = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"WB-800 {host}",
        update_method=_create_update_method(client),
        update_interval=scan_interval,
    )
    
    # Fetch initial data so we have it for entity creation
    try:
        await coordinator.async_refresh()
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to initialize WB-800 at %s: %s", host, exc, exc_info=True)
        return
    
    if not coordinator.data:
        _LOGGER.error("Coordinator data is None after initial refresh")
        return
    
    metrics = coordinator.data.metrics
    outlets = coordinator.data.outlets
    _LOGGER.info("Successfully fetched metrics and %d outlets from %s", len(outlets), host)

    entities: List[SensorEntity] = []
    
    # Add system-level sensors
    _LOGGER.debug("Creating system-level sensors for %s", host)
    entities.append(WattBoxMetricSensor(coordinator, host, "Voltage", "voltage", metrics.voltage, "V"))
    entities.append(WattBoxPowerSensor(coordinator, host, "Power", "total_watts", metrics.total_watts))
    entities.append(WattBoxMetricSensor(coordinator, host, "Current", "total_amps", metrics.total_amps, "A"))
    
    # Add total energy sensor for the PDU
    if metrics.total_watts is not None:
        _LOGGER.debug("Creating total energy sensor (total_watts=%.2f)", metrics.total_watts)
        entities.append(
            WattBoxEnergySensor(
                coordinator,
                host,
                "Energy",
                "total_energy",
                metrics.total_watts,
            )
        )
    else:
        _LOGGER.warning("Total watts is None, skipping total energy sensor creation")

    # Add individual outlet power, current, and energy sensors
    # Create sensors for ALL outlets to avoid "no longer in use" issues
    # Even if initial values are None, sensors can update later
    _LOGGER.debug("Processing %d outlets for sensor creation", len(outlets))
    outlets_with_watts = 0
    outlets_with_amps = 0
    outlets_with_energy = 0
    
    for outlet in outlets:
        outlet_name = outlet.name or f"Outlet {outlet.number}"
        _LOGGER.debug(
            "Outlet %d (%s): watts=%s, amps=%s, is_on=%s, reset_only=%s",
            outlet.number,
            outlet_name,
            outlet.watts,
            outlet.amps,
            outlet.is_on,
            outlet.is_reset_only,
        )
        
        # Always create power and energy sensors for ALL outlets that have watts data
        # Reset-only outlets still report power/energy, so create sensors for them too
        # Only create if we have watts data (not None) or if outlet is not reset_only
        should_create_power_energy = (
            not outlet.is_reset_only or outlet.watts is not None
        )
        
        if should_create_power_energy:
            outlets_with_watts += 1
            try:
                # Use 0.0 as default if watts is None to ensure sensor is created
                initial_watts = outlet.watts if outlet.watts is not None else 0.0
                entities.append(
                    WattBoxOutletPowerSensor(
                        coordinator,
                        host,
                        outlet.number,
                        outlet_name,
                        initial_watts,
                    )
                )
                _LOGGER.debug(
                    "Created power sensor for outlet %d (%s) with initial watts=%s (reset_only=%s)",
                    outlet.number,
                    outlet_name,
                    initial_watts,
                    outlet.is_reset_only,
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.error(
                    "Failed to create power sensor for outlet %d (%s): %s",
                    outlet.number,
                    outlet_name,
                    exc,
                    exc_info=True,
                )
            
            # Add energy sensor for this outlet
            outlets_with_energy += 1
            try:
                # Use 0.0 as default if watts is None to ensure sensor is created
                initial_watts = outlet.watts if outlet.watts is not None else 0.0
                entities.append(
                    WattBoxOutletEnergySensor(
                        coordinator,
                        host,
                        outlet.number,
                        outlet_name,
                        initial_watts,
                    )
                )
                _LOGGER.debug(
                    "Created energy sensor for outlet %d (%s) with initial watts=%s (reset_only=%s)",
                    outlet.number,
                    outlet_name,
                    initial_watts,
                    outlet.is_reset_only,
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.error(
                    "Failed to create energy sensor for outlet %d (%s): %s",
                    outlet.number,
                    outlet_name,
                    exc,
                    exc_info=True,
                )
        else:
            _LOGGER.debug(
                "Skipping power/energy sensors for outlet %d (%s): reset_only=True and watts=None",
                outlet.number,
                outlet_name,
            )
        
        # Always create amps sensor for all outlets (including reset-only)
        # This ensures sensors persist even if initial values are None
        outlets_with_amps += 1
        try:
            # Use 0.0 as default if amps is None to ensure sensor is created
            initial_amps = outlet.amps if outlet.amps is not None else 0.0
            entities.append(
                WattBoxOutletSensor(
                    coordinator,
                    host,
                    outlet.number,
                    outlet_name,
                    "amps",
                    initial_amps,
                    "A",
                )
            )
            _LOGGER.debug(
                "Created amps sensor for outlet %d (%s) with initial amps=%s",
                outlet.number,
                outlet_name,
                initial_amps,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error(
                "Failed to create amps sensor for outlet %d (%s): %s",
                outlet.number,
                outlet_name,
                exc,
                exc_info=True,
            )

    _LOGGER.info(
        "Created %d sensor entities: %d outlets with watts, %d outlets with amps, %d outlets with energy",
        len(entities),
        outlets_with_watts,
        outlets_with_amps,
        outlets_with_energy,
    )
    
    try:
        add_entities(entities, update_before_add=False)  # Coordinator handles updates
        _LOGGER.info("Successfully added %d sensor entities to Home Assistant", len(entities))
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to add entities to Home Assistant: %s", exc, exc_info=True)
        raise


class WattBoxMetricSensor(CoordinatorEntity, SensorEntity):
    """Base sensor for WattBox metrics."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        host: str,
        name: str,
        key: str,
        initial_value: float | None,
        unit: str,
    ) -> None:
        super().__init__(coordinator)
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

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        self._handle_coordinator_update()

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator.data:
            value = getattr(self.coordinator.data.metrics, self._key)
            self._state = value
            self.async_write_ha_state()


class WattBoxPowerSensor(CoordinatorEntity, SensorEntity):
    """Power sensor with proper device class for Energy Dashboard."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        host: str,
        name: str,
        key: str,
        initial_value: float | None,
    ) -> None:
        super().__init__(coordinator)
        self._host = host
        self._name = name
        self._key = key
        self._state = initial_value
        self._attr_unique_id = f"wb800-{host}-metric-{key}"

    @property
    def name(self) -> str:
        return f"WattBox {self._name}"

    @property
    def native_value(self):
        return self._state

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=f"WattBox WB-800 ({self._host})",
            manufacturer="SnapAV",
            model="WattBox WB-800",
        )

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        self._handle_coordinator_update()

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator.data:
            value = getattr(self.coordinator.data.metrics, self._key)
            self._state = value
            self.async_write_ha_state()


class WattBoxEnergySensor(RestoreEntity, CoordinatorEntity, SensorEntity):
    """Energy sensor that integrates power over time for Energy Dashboard."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        host: str,
        name: str,
        key: str,
        initial_power: float | None,
    ) -> None:
        super().__init__(coordinator)
        self._host = host
        self._name = name
        self._key = key
        self._last_power: Optional[float] = initial_power
        self._last_update: Optional[datetime] = None
        self._total_energy: float = 0.0
        self._attr_unique_id = f"wb800-{host}-metric-{key}"

    @property
    def name(self) -> str:
        return f"WattBox {self._name}"

    @property
    def native_value(self) -> float:
        return round(self._total_energy, 3)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=f"WattBox WB-800 ({self._host})",
            manufacturer="SnapAV",
            model="WattBox WB-800",
        )

    async def async_added_to_hass(self) -> None:
        """Restore state when added to Home Assistant."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            if last_state.state not in ("unknown", "unavailable"):
                try:
                    self._total_energy = float(last_state.state)
                    # Restore last update time if available for better integration accuracy
                    if last_state.last_updated:
                        self._last_update = last_state.last_updated
                    # Restore last power from attributes if available
                    if last_state.attributes and "last_power" in last_state.attributes:
                        try:
                            self._last_power = float(last_state.attributes["last_power"])
                        except (ValueError, TypeError):
                            pass
                except (ValueError, TypeError):
                    pass

    @property
    def extra_state_attributes(self) -> dict[str, float | None]:
        """Return additional state attributes."""
        return {
            "last_power": self._last_power,
        }

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self.coordinator.data:
            return
            
        power = self.coordinator.data.metrics.total_watts
        
        if power is None:
            _LOGGER.debug("Total watts is None, skipping energy calculation update")
            return
            
        now = dt_util.utcnow()
        
        # Maximum time gap to prevent incorrect calculations after long outages (24 hours)
        MAX_TIME_GAP_HOURS = 24.0
        
        if self._last_power is not None and self._last_update is not None:
            # Calculate energy: power (W) * time (hours)
            time_diff_seconds = (now - self._last_update).total_seconds()
            time_diff_hours = time_diff_seconds / 3600.0
            
            # Handle edge cases
            if time_diff_hours < 0:
                # Negative time difference (clock adjustment), skip this update
                _LOGGER.debug("Negative time difference detected, skipping energy calculation")
            elif time_diff_hours > MAX_TIME_GAP_HOURS:
                # Large time gap, likely device was offline - use current power only
                _LOGGER.warning(
                    "Large time gap detected (%.2f hours), using current power only",
                    time_diff_hours,
                )
                # Estimate energy using current power for max gap duration
                energy_kwh = (power * MAX_TIME_GAP_HOURS) / 1000.0
                self._total_energy += energy_kwh
            elif time_diff_hours > 0:
                # Normal case: use average power for integration (trapezoidal rule)
                avg_power = (power + self._last_power) / 2.0
                energy_kwh = (avg_power * time_diff_hours) / 1000.0
                self._total_energy += energy_kwh
        
        self._last_power = power
        self._last_update = now
        self.async_write_ha_state()


class WattBoxOutletPowerSensor(CoordinatorEntity, SensorEntity):
    """Power sensor for individual outlet with proper device class."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        host: str,
        outlet_number: int,
        outlet_name: str,
        initial_value: float | None,
    ) -> None:
        super().__init__(coordinator)
        self._host = host
        self._outlet_number = outlet_number
        self._outlet_name = outlet_name
        self._state = initial_value
        # Match switch unique_id format for consistent entity IDs
        self._attr_unique_id = f"wb800-{host}-outlet-{outlet_number}-watts"

    @property
    def name(self) -> str:
        return f"{self._outlet_name} Watts"

    @property
    def native_value(self):
        return self._state

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._attr_available

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=f"WattBox WB-800 ({self._host})",
            manufacturer="SnapAV",
            model="WattBox WB-800",
        )

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        self._handle_coordinator_update()

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self.coordinator.data:
            return
            
        outlets = self.coordinator.data.outlets
        for outlet in outlets:
            if outlet.number == self._outlet_number:
                self._state = outlet.watts if outlet.watts is not None else 0.0
                self.async_write_ha_state()
                return
        
        # Outlet not found - log available outlet numbers for debugging
        available_numbers = [o.number for o in outlets]
        _LOGGER.warning(
            "Outlet %d (%s) not found during power sensor update. Available outlets: %s",
            self._outlet_number,
            self._outlet_name,
            available_numbers,
        )


class WattBoxOutletEnergySensor(RestoreEntity, CoordinatorEntity, SensorEntity):
    """Energy sensor for individual outlet that integrates power over time."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        host: str,
        outlet_number: int,
        outlet_name: str,
        initial_power: float | None,
    ) -> None:
        super().__init__(coordinator)
        self._host = host
        self._outlet_number = outlet_number
        self._outlet_name = outlet_name
        self._last_power: Optional[float] = initial_power
        self._last_update: Optional[datetime] = None
        self._total_energy: float = 0.0
        # Match switch unique_id format for consistent entity IDs
        self._attr_unique_id = f"wb800-{host}-outlet-{outlet_number}-energy"

    @property
    def name(self) -> str:
        return f"{self._outlet_name} Energy"

    @property
    def native_value(self) -> float:
        return round(self._total_energy, 3)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=f"WattBox WB-800 ({self._host})",
            manufacturer="SnapAV",
            model="WattBox WB-800",
        )

    async def async_added_to_hass(self) -> None:
        """Restore state when added to Home Assistant."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            if last_state.state not in ("unknown", "unavailable"):
                try:
                    self._total_energy = float(last_state.state)
                    # Restore last update time if available for better integration accuracy
                    if last_state.last_updated:
                        self._last_update = last_state.last_updated
                    # Restore last power from attributes if available
                    if last_state.attributes and "last_power" in last_state.attributes:
                        try:
                            self._last_power = float(last_state.attributes["last_power"])
                        except (ValueError, TypeError):
                            pass
                except (ValueError, TypeError):
                    pass
        self._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict[str, float | None]:
        """Return additional state attributes."""
        return {
            "last_power": self._last_power,
        }

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self.coordinator.data:
            return
            
        outlets = self.coordinator.data.outlets
        power: Optional[float] = None
        for outlet in outlets:
            if outlet.number == self._outlet_number:
                power = outlet.watts
                break
        
        if power is None:
            _LOGGER.debug(
                "Outlet %d (%s) watts is None, skipping energy calculation",
                self._outlet_number,
                self._outlet_name,
            )
            return
                
        now = dt_util.utcnow()
        
        # Maximum time gap to prevent incorrect calculations after long outages (24 hours)
        MAX_TIME_GAP_HOURS = 24.0
        
        if self._last_power is not None and self._last_update is not None:
            # Calculate energy: power (W) * time (hours)
            time_diff_seconds = (now - self._last_update).total_seconds()
            time_diff_hours = time_diff_seconds / 3600.0
            
            # Handle edge cases
            if time_diff_hours < 0:
                # Negative time difference (clock adjustment), skip this update
                _LOGGER.debug("Negative time difference detected, skipping energy calculation")
            elif time_diff_hours > MAX_TIME_GAP_HOURS:
                # Large time gap, likely device was offline - use current power only
                _LOGGER.warning(
                    "Large time gap detected (%.2f hours), using current power only",
                    time_diff_hours,
                )
                # Estimate energy using current power for max gap duration
                energy_kwh = (power * MAX_TIME_GAP_HOURS) / 1000.0
                self._total_energy += energy_kwh
            elif time_diff_hours > 0:
                # Normal case: use average power for integration (trapezoidal rule)
                avg_power = (power + self._last_power) / 2.0
                energy_kwh = (avg_power * time_diff_hours) / 1000.0
                self._total_energy += energy_kwh
        
        self._last_power = power
        self._last_update = now
        self.async_write_ha_state()


class WattBoxOutletSensor(CoordinatorEntity, SensorEntity):
    """Sensor for individual outlet current readings."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        host: str,
        outlet_number: int,
        outlet_name: str,
        key: str,
        initial_value: float | None,
        unit: str,
    ) -> None:
        super().__init__(coordinator)
        self._host = host
        self._outlet_number = outlet_number
        self._outlet_name = outlet_name
        self._key = key
        self._unit = unit
        self._state = initial_value
        # Match switch unique_id format for consistent entity IDs
        self._attr_unique_id = f"wb800-{host}-outlet-{outlet_number}-{key}"

    @property
    def name(self) -> str:
        return f"{self._outlet_name} {self._key.capitalize()}"

    @property
    def native_value(self):
        return self._state

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self._unit

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._attr_available

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=f"WattBox WB-800 ({self._host})",
            manufacturer="SnapAV",
            model="WattBox WB-800",
        )

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        self._handle_coordinator_update()

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self.coordinator.data:
            return
            
        outlets = self.coordinator.data.outlets
        for outlet in outlets:
            if outlet.number == self._outlet_number:
                value = getattr(outlet, self._key)
                # Handle None values by using 0.0
                self._state = value if value is not None else 0.0
                self.async_write_ha_state()
                return
        
        # Outlet not found - log available outlet numbers for debugging
        available_numbers = [o.number for o in outlets]
        _LOGGER.warning(
            "Outlet %d (%s) not found during %s sensor update. Available outlets: %s",
            self._outlet_number,
            self._outlet_name,
            self._key,
            available_numbers,
        )


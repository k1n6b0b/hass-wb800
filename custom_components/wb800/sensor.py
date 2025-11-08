from __future__ import annotations

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
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

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
        outlets = await client.async_fetch_outlets()
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to initialize WB-800 metrics at %s: %s", host, exc)
        return

    entities: List[SensorEntity] = [
        WattBoxMetricSensor(client, host, "Voltage", "voltage", metrics.voltage, "V"),
        WattBoxPowerSensor(client, host, "Power", "total_watts", metrics.total_watts),
        WattBoxMetricSensor(client, host, "Current", "total_amps", metrics.total_amps, "A"),
    ]
    
    # Add total energy sensor for the PDU
    if metrics.total_watts is not None:
        entities.append(
            WattBoxEnergySensor(
                client,
                host,
                "Energy",
                "total_energy",
                metrics.total_watts,
            )
        )

    # Add individual outlet power, current, and energy sensors
    for outlet in outlets:
        if outlet.watts is not None:
            entities.append(
                WattBoxOutletPowerSensor(
                    client,
                    host,
                    outlet.number,
                    outlet.name or f"Outlet {outlet.number}",
                    outlet.watts,
                )
            )
            # Add energy sensor for this outlet
            entities.append(
                WattBoxOutletEnergySensor(
                    client,
                    host,
                    outlet.number,
                    outlet.name or f"Outlet {outlet.number}",
                    outlet.watts,
                )
            )
        if outlet.amps is not None:
            entities.append(
                WattBoxOutletSensor(
                    client,
                    host,
                    outlet.number,
                    outlet.name or f"Outlet {outlet.number}",
                    "amps",
                    outlet.amps,
                    "A",
                )
            )

    add_entities(entities, update_before_add=True)


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


class WattBoxPowerSensor(SensorEntity):
    """Power sensor with proper device class for Energy Dashboard."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self,
        client: WattBoxClient,
        host: str,
        name: str,
        key: str,
        initial_value: float | None,
    ) -> None:
        self._client = client
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

    async def async_update(self) -> None:
        try:
            metrics: DeviceMetrics = await self._client.async_fetch_metrics()
            value = getattr(metrics, self._key)
            self._state = value
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to update power sensor %s: %s", self._key, exc)


class WattBoxEnergySensor(RestoreEntity, SensorEntity):
    """Energy sensor that integrates power over time for Energy Dashboard."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self,
        client: WattBoxClient,
        host: str,
        name: str,
        key: str,
        initial_power: float | None,
    ) -> None:
        self._client = client
        self._host = host
        self._name = name
        self._key = key
        self._last_power: Optional[float] = None
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
                except (ValueError, TypeError):
                    pass

    async def async_update(self) -> None:
        try:
            metrics: DeviceMetrics = await self._client.async_fetch_metrics()
            power = metrics.total_watts
            
            if power is None:
                return
                
            now = datetime.now()
            
            if self._last_power is not None and self._last_update is not None:
                # Calculate energy: power (W) * time (hours)
                time_diff = (now - self._last_update).total_seconds() / 3600.0
                if time_diff > 0:
                    # Use average power for integration
                    avg_power = (power + self._last_power) / 2.0
                    energy_kwh = (avg_power * time_diff) / 1000.0
                    self._total_energy += energy_kwh
            
            self._last_power = power
            self._last_update = now
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to update energy sensor %s: %s", self._key, exc)


class WattBoxOutletPowerSensor(SensorEntity):
    """Power sensor for individual outlet with proper device class."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self,
        client: WattBoxClient,
        host: str,
        outlet_number: int,
        outlet_name: str,
        initial_value: float | None,
    ) -> None:
        self._client = client
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
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=f"WattBox WB-800 ({self._host})",
            manufacturer="SnapAV",
            model="WattBox WB-800",
        )

    async def async_update(self) -> None:
        try:
            outlets: List[OutletInfo] = await self._client.async_fetch_outlets()
            for outlet in outlets:
                if outlet.number == self._outlet_number:
                    self._state = outlet.watts
                    break
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to update outlet power sensor %s: %s", self._outlet_number, exc)


class WattBoxOutletEnergySensor(RestoreEntity, SensorEntity):
    """Energy sensor for individual outlet that integrates power over time."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self,
        client: WattBoxClient,
        host: str,
        outlet_number: int,
        outlet_name: str,
        initial_power: float | None,
    ) -> None:
        self._client = client
        self._host = host
        self._outlet_number = outlet_number
        self._outlet_name = outlet_name
        self._last_power: Optional[float] = None
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
                except (ValueError, TypeError):
                    pass

    async def async_update(self) -> None:
        try:
            outlets: List[OutletInfo] = await self._client.async_fetch_outlets()
            power: Optional[float] = None
            for outlet in outlets:
                if outlet.number == self._outlet_number:
                    power = outlet.watts
                    break
            
            if power is None:
                return
                
            now = datetime.now()
            
            if self._last_power is not None and self._last_update is not None:
                # Calculate energy: power (W) * time (hours)
                time_diff = (now - self._last_update).total_seconds() / 3600.0
                if time_diff > 0:
                    # Use average power for integration
                    avg_power = (power + self._last_power) / 2.0
                    energy_kwh = (avg_power * time_diff) / 1000.0
                    self._total_energy += energy_kwh
            
            self._last_power = power
            self._last_update = now
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to update outlet energy sensor %s: %s", self._outlet_number, exc)


class WattBoxOutletSensor(SensorEntity):
    """Sensor for individual outlet current readings."""

    def __init__(
        self,
        client: WattBoxClient,
        host: str,
        outlet_number: int,
        outlet_name: str,
        key: str,
        initial_value: float | None,
        unit: str,
    ) -> None:
        self._client = client
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
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=f"WattBox WB-800 ({self._host})",
            manufacturer="SnapAV",
            model="WattBox WB-800",
        )

    async def async_update(self) -> None:
        try:
            outlets: List[OutletInfo] = await self._client.async_fetch_outlets()
            for outlet in outlets:
                if outlet.number == self._outlet_number:
                    value = getattr(outlet, self._key)
                    self._state = value
                    break
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to update outlet sensor %s %s: %s", self._outlet_number, self._key, exc)


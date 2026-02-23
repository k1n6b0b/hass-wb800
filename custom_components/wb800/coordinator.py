from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import List

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import DeviceMetrics, OutletInfo, WattBoxClient

_LOGGER = logging.getLogger(__name__)


@dataclass
class WattBoxData:
    """Current WB800 snapshot."""

    metrics: DeviceMetrics
    outlets: List[OutletInfo]


class WattBoxCoordinator(DataUpdateCoordinator[WattBoxData]):
    """Shared coordinator for WB800 data."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        client: WattBoxClient,
        host_label: str,
        scan_interval_seconds: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"WB-800 {host_label}",
            update_interval=timedelta(seconds=scan_interval_seconds),
        )
        self.client = client
        self.host_label = host_label

    async def _async_update_data(self) -> WattBoxData:
        try:
            html = await self.client.async_fetch_main_html()
            metrics = self.client.parse_metrics_from_html(html)
            outlets = self.client.parse_outlets_from_html(html)

            if metrics.total_watts is None or metrics.total_amps is None:
                watts_sum = sum(o.watts for o in outlets if o.watts is not None)
                amps_sum = sum(o.amps for o in outlets if o.amps is not None)
                if metrics.total_watts is None:
                    metrics.total_watts = round(watts_sum, 2)
                if metrics.total_amps is None:
                    metrics.total_amps = round(amps_sum, 2)

            return WattBoxData(metrics=metrics, outlets=outlets)
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Error fetching WattBox data: {err}") from err

    def get_outlet(self, outlet_number: int) -> OutletInfo | None:
        """Return outlet by number from current data."""
        if not self.data:
            return None
        for outlet in self.data.outlets:
            if outlet.number == outlet_number:
                return outlet
        return None

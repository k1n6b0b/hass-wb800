from __future__ import annotations

from dataclasses import dataclass
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady, SOURCE_IMPORT
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import Event, HomeAssistant, callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import UpdateFailed

from .client import WattBoxClient
from .const import (
    CONF_VERIFY_SSL,
    DATA_CLIENTS,
    DATA_STOP_LISTENER,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    PLATFORMS,
    get_scan_interval_seconds,
    host_label_from_base_url,
    normalize_base_url,
)
from .coordinator import WattBoxCoordinator

_LOGGER = logging.getLogger(__name__)

IMPORT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): cv.boolean,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL_SECONDS): vol.Coerce(int),
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.All(cv.ensure_list, [IMPORT_SCHEMA]),
    },
    extra=vol.ALLOW_EXTRA,
)


@dataclass
class WB800RuntimeData:
    """Runtime objects for one config entry."""

    client: WattBoxClient
    coordinator: WattBoxCoordinator
    host_label: str


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up wb800 domain and optional YAML import block."""
    hass.data.setdefault(DOMAIN, {})

    for yaml_item in config.get(DOMAIN, []):
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data={
                    CONF_HOST: yaml_item[CONF_HOST],
                    CONF_USERNAME: yaml_item[CONF_USERNAME],
                    CONF_PASSWORD: yaml_item[CONF_PASSWORD],
                    CONF_VERIFY_SSL: yaml_item.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                    CONF_SCAN_INTERVAL: int(
                        yaml_item.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS)
                    ),
                },
            )
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up wb800 from a config entry."""
    base_url = normalize_base_url(entry.data[CONF_HOST])
    host_label = host_label_from_base_url(base_url)
    verify_ssl = entry.options.get(
        CONF_VERIFY_SSL, entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    )
    scan_interval = int(
        entry.options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS),
        )
    )

    client = WattBoxClient(
        base_url=base_url,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        verify_ssl=verify_ssl,
    )

    coordinator = WattBoxCoordinator(
        hass,
        client=client,
        host_label=host_label,
        scan_interval_seconds=scan_interval,
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except UpdateFailed as err:
        await client.async_close()
        raise ConfigEntryNotReady(
            f"Unable to connect to WB-800 {host_label}: {err}"
        ) from err

    runtime = WB800RuntimeData(
        client=client,
        coordinator=coordinator,
        host_label=host_label,
    )

    hass.data[DOMAIN][entry.entry_id] = runtime
    entry.runtime_data = runtime

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a wb800 config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    runtime: WB800RuntimeData | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime is not None:
        await runtime.client.async_close()

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload wb800 config entry after options update."""
    await hass.config_entries.async_reload(entry.entry_id)


def get_runtime_data(hass: HomeAssistant, entry: ConfigEntry) -> WB800RuntimeData:
    """Return runtime data for config entry."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    return runtime


def get_or_create_client(
    hass: HomeAssistant,
    *,
    base_url: str,
    username: str,
    password: str,
    verify_ssl: bool,
) -> WattBoxClient:
    """Return a shared client for legacy YAML platform setup."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    clients: dict[tuple[str, str, str, bool], WattBoxClient] = domain_data.setdefault(DATA_CLIENTS, {})
    key = (base_url, username, password, verify_ssl)

    client = clients.get(key)
    if client is None:
        client = WattBoxClient(
            base_url=base_url,
            username=username,
            password=password,
            verify_ssl=verify_ssl,
        )
        clients[key] = client

    if DATA_STOP_LISTENER not in domain_data:

        @callback
        def _on_stop(_event: Event) -> None:
            hass.async_create_task(_async_close_clients(clients))

        domain_data[DATA_STOP_LISTENER] = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP,
            _on_stop,
        )

    return client


async def _async_close_clients(clients: dict[tuple[str, str, str, bool], WattBoxClient]) -> None:
    """Close all open HTTP sessions on HA shutdown."""
    for client in clients.values():
        await client.async_close()

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

from .client import WattBoxClient
from .const import (
    CONF_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
    host_label_from_base_url,
    normalize_base_url,
)

_LOGGER = logging.getLogger(__name__)


class WB800ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for WB800."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = normalize_base_url(user_input[CONF_HOST])
            unique = base_url.lower()

            await self.async_set_unique_id(unique)
            self._abort_if_unique_id_configured()

            try:
                await _async_validate_input(
                    host=user_input[CONF_HOST],
                    username=user_input[CONF_USERNAME],
                    password=user_input[CONF_PASSWORD],
                    verify_ssl=user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                )
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected exception during wb800 config flow")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=host_label_from_base_url(base_url),
                    data={
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_VERIFY_SSL: user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                        CONF_SCAN_INTERVAL: int(
                            user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS)
                        ),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_step_user_schema(user_input),
            errors=errors,
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> FlowResult:
        """Handle import from YAML wb800: block."""
        base_url = normalize_base_url(import_data[CONF_HOST])
        unique = base_url.lower()

        await self.async_set_unique_id(unique)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=host_label_from_base_url(base_url),
            data={
                CONF_HOST: import_data[CONF_HOST],
                CONF_USERNAME: import_data[CONF_USERNAME],
                CONF_PASSWORD: import_data[CONF_PASSWORD],
                CONF_VERIFY_SSL: import_data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                CONF_SCAN_INTERVAL: int(
                    import_data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS)
                ),
            },
        )

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return WB800OptionsFlow(config_entry)


class WB800OptionsFlow(OptionsFlow):
    """Handle wb800 options."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                },
            )

        current_scan = int(
            self._config_entry.options.get(
                CONF_SCAN_INTERVAL,
                self._config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS),
            )
        )
        current_verify = self._config_entry.options.get(
            CONF_VERIFY_SSL,
            self._config_entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        )

        schema = vol.Schema(
            {
                vol.Required(CONF_VERIFY_SSL, default=current_verify): bool,
                vol.Required(CONF_SCAN_INTERVAL, default=current_scan): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL_SECONDS, max=MAX_SCAN_INTERVAL_SECONDS),
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)


async def _async_validate_input(
    *,
    host: str,
    username: str,
    password: str,
    verify_ssl: bool,
) -> None:
    """Validate user credentials and host by a read-only fetch."""
    client = WattBoxClient(
        base_url=normalize_base_url(host),
        username=username,
        password=password,
        verify_ssl=verify_ssl,
    )

    try:
        outlets = await client.async_fetch_outlets()
    except Exception as err:  # noqa: BLE001
        err_text = str(err).lower()
        if "unauthorized" in err_text or "credentials" in err_text:
            raise InvalidAuth from err
        raise CannotConnect from err
    finally:
        await client.async_close()

    if not outlets:
        raise CannotConnect


def _step_user_schema(user_input: dict[str, Any] | None) -> vol.Schema:
    defaults = user_input or {}

    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "admin")): str,
            vol.Required(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")): str,
            vol.Required(
                CONF_VERIFY_SSL,
                default=defaults.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            ): bool,
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS),
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=MIN_SCAN_INTERVAL_SECONDS, max=MAX_SCAN_INTERVAL_SECONDS),
            ),
        }
    )


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate auth is invalid."""

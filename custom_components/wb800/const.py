from __future__ import annotations

from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.helpers.typing import ConfigType

DOMAIN = "wb800"

CONF_VERIFY_SSL = "verify_ssl"

DEFAULT_VERIFY_SSL = True
DEFAULT_SCAN_INTERVAL_SECONDS = 30
MIN_SCAN_INTERVAL_SECONDS = 10
MAX_SCAN_INTERVAL_SECONDS = 3600

DATA_CLIENTS = "clients"
DATA_STOP_LISTENER = "stop_listener"

PLATFORMS = ["sensor", "switch", "button"]


def normalize_base_url(host: str) -> str:
    """Normalize host input to an HTTP(S) base URL."""
    value = host.strip()
    if value.startswith(("http://", "https://")):
        return value.rstrip("/")
    return f"http://{value.rstrip('/')}"


def host_label_from_base_url(base_url: str) -> str:
    """Best-effort host label from a normalized base URL."""
    return base_url.replace("http://", "").replace("https://", "").strip("/")


def get_scan_interval_seconds(config: ConfigType) -> int:
    """Convert YAML scan_interval into integer seconds with sane floor."""
    raw = config.get(CONF_SCAN_INTERVAL)
    if raw is None:
        return DEFAULT_SCAN_INTERVAL_SECONDS

    if hasattr(raw, "total_seconds"):
        return max(MIN_SCAN_INTERVAL_SECONDS, int(raw.total_seconds()))

    try:
        return max(MIN_SCAN_INTERVAL_SECONDS, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_SCAN_INTERVAL_SECONDS

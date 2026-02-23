# WattBox WB-800 for Home Assistant

Custom integration for SnapAV WattBox WB-800 PDU devices.

## Status

- Config entry (UI) setup supported
- Options flow supported (SSL verify + polling interval)
- Legacy YAML platform setup still supported for backward compatibility
- Entity model:
  - Switches: outlet on/off (non-reset-only outlets)
  - Buttons: outlet reset
  - Sensors: system voltage/power/current/energy + per-outlet power/current/energy

## Install

1. Copy this folder to:
   - `/config/custom_components/wb800`
2. Restart Home Assistant.
3. Add integration:
   - **Settings -> Devices & Services -> Add Integration -> WattBox WB-800**

## Recommended Setup (UI)

When adding the integration, provide:

- `host`: hostname or URL of the WB-800
- `username`
- `password`
- `verify_ssl`
- `scan_interval` (seconds)

Notes:

- If `host` has no scheme, `http://` is assumed.
- Recommended scan interval: `30` seconds.
- Allowed scan interval range: `10` to `3600` seconds.

## Optional Legacy YAML

Legacy YAML remains supported. UI config entries are preferred.

```yaml
switch:
  - platform: wb800
    host: wb-800.local
    username: !secret wb800_username
    password: !secret wb800_password
    verify_ssl: false
    scan_interval: 30

button:
  - platform: wb800
    host: wb-800.local
    username: !secret wb800_username
    password: !secret wb800_password
    verify_ssl: false

sensor:
  - platform: wb800
    host: wb-800.local
    username: !secret wb800_username
    password: !secret wb800_password
    verify_ssl: false
    scan_interval: 30
```

You can also import to config entries via top-level domain block:

```yaml
wb800:
  - host: wb-800.local
    username: !secret wb800_username
    password: !secret wb800_password
    verify_ssl: false
    scan_interval: 30
```

## Entities

Device-level sensors:

- `WattBox Voltage` (`V`)
- `WattBox Power` (`W`)
- `WattBox Current` (`A`)
- `WattBox Energy` (`kWh`, `total_increasing`)

Per-outlet entities:

- Switch: `<Outlet Name>`
- Button: `<Outlet Name> Reset`
- Sensors:
  - `<Outlet Name> Power` (`W`)
  - `<Outlet Name> Current` (`A`)
  - `<Outlet Name> Energy` (`kWh`, `total_increasing`)

## Authentication + Compatibility

The client handles WB-800 variants that use:

- HTTP Basic authentication
- HTTP Digest authentication
- Form login flow

## Troubleshooting

Enable debug logs:

```yaml
logger:
  logs:
    custom_components.wb800: debug
```

Read-only connectivity checks:

```sh
nslookup wb-800.local
curl -I http://wb-800.local/main
```

## Security Notes

- Always keep credentials in `secrets.yaml`.
- Prefer HTTPS if your WB-800 firmware/network supports it.
- Do not expose WB-800 management directly to the internet.

## Development

Suggested checks before pushing:

1. Ensure no local artifacts are committed (`__pycache__`, `.pyc`, `._*`).
2. Run Home Assistant config validation.
3. Restart HA and confirm no `custom_components.wb800` errors in logs.
4. Verify entities populate and state updates are stable.

## Publish Checklist

Before publishing this to GitHub, update:

1. `manifest.json` `documentation` URL to your final repo URL.
2. `manifest.json` `codeowners` to your GitHub handle (optional but recommended).

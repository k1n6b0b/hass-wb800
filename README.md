# WattBox WB-800 custom component for Home Assistant

## Install

Copy the `custom_components/wb800` folder into your Home Assistant `config/custom_components` directory.

Restart Home Assistant.

## Configure (YAML)

Add to `configuration.yaml`:

```yaml
switch:
  - platform: wb800
    host: YOUR-WATTBOX-HOST
    username: YOUR-USERNAME
    password: YOUR-PASSWORD
    verify_ssl: false
    scan_interval: 30

sensor:
  - platform: wb800
    host: YOUR-WATTBOX-HOST
    username: YOUR-USERNAME
    password: YOUR-PASSWORD
    verify_ssl: false
    scan_interval: 30
```

Notes:

- Set `verify_ssl: false` if the device uses a self-signed certificate. The integration also supports `http://` base URLs.
- Each outlet will appear as an individual switch using the names from the device.

## Entities

**Switch Platform:**

- One `switch` per outlet, named from the WattBox UI.
- Attributes: `outlet_number`, `reset_only`, `watts`, `amps` (when available).

**Sensor Platform:**

- `WattBox Voltage` - System voltage
- `WattBox Power` - Total power consumption
- `WattBox Current` - Total current draw
- Individual sensors for each outlet: `{Outlet Name} Watts` and `{Outlet Name} Amps` when available

## Services

Turn on/off via standard switch services in Home Assistant.

## Troubleshooting

Ensure the WB-800 is reachable from Home Assistant and that the credentials are correct.

If your unit requires HTTP Basic Auth, it will be detected automatically. If it requires form login, the component will perform it transparently.

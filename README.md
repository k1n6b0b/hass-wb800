# WattBox WB-800 Custom Component for Home Assistant

I couldn’t get the other WattBox component working well, so this is a lightweight alternative designed to provide full control and energy monitoring for the SnapAV / WattBox WB-800 series.

---

## Installation

1. Copy the `custom_components/wb800` folder into your Home Assistant `config/custom_components` directory.
2. Restart Home Assistant.

---

## Configuration (YAML)

Add the following to your `configuration.yaml` file:

~~~yaml
switch:
  - platform: wb800
    host: YOUR-WATTBOX-HOST
    username: YOUR-USERNAME
    password: YOUR-PASSWORD
    verify_ssl: false
    scan_interval: 30

button:
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
~~~

### Notes
- Use `verify_ssl: false` if your WattBox uses a self-signed certificate. HTTP (`http://`) is also supported.
- Each outlet will appear as an individual switch using the names defined in the WattBox UI.
- Energy and power sensors are compatible with Home Assistant’s Energy Dashboard.

---

## Entities

### Switch Platform
- One switch entity and a reset button per outlet.
- Attributes:
  - `outlet_number`
  - `reset_only`
  - `watts`
  - `amps` *(when available)*

### Sensor Platform
- **System-level sensors**
  - Voltage
  - Total instantaneous power
  - Total current

- **Per-outlet sensors**
  - `{Outlet Name} Watts`
  - `{Outlet Name} Amps`
  - `{Outlet Name} Energy` *(compatible with Energy Dashboard)*

---

## Energy Dashboard Integration

Energy sensors provide:
- `device_class: energy`
- `state_class: total_increasing`
- `unit_of_measurement: kWh`

### To configure:
1. Go to **Settings → Dashboards → Energy**.
2. Click **Add Consumption / Individual Device**.
3. Select the **WB-800 energy sensors**.

---

## Services

Standard Home Assistant services apply:
- `switch.turn_on`
- `switch.turn_off`
- `button.press` *(for outlet reset)*

---

## Troubleshooting

- Verify the WattBox is reachable and credentials are correct.
- Both Basic Auth and form-based login are automatically detected.
- Check **Developer Tools → Logs** for connection or parsing errors.

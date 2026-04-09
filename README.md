# Observer Communicating Thermostat

Home Assistant custom integration for the **Observer Communicating Thermostat (TSTAT0201CW)**.

This integration intercepts communication between the thermostat and the Observer cloud API, replacing it with a local HTTP server running inside Home Assistant. This gives you full local control with no cloud dependency.

## How It Works

The thermostat periodically contacts a cloud server to report status and receive configuration. This integration runs a local HTTP server that mimics that cloud API. You point the thermostat at your Home Assistant instance (via your router/DNS) instead of the Observer cloud, and the integration handles everything locally.

## Entities

| Platform | Entity | Description |
|----------|--------|-------------|
| `climate` | Thermostat | Full HVAC control (modes, fan, setpoints, presets) |
| `sensor` | Temperature, Humidity, Operating Mode, Fan Mode, State, Setpoint, and more | 15 diagnostic/status sensors |
| `number` | Humidification Setpoint, Dehumidification Setpoint, Backlight Brightness, Hold Override Timer | Configurable setpoints |
| `switch` | Screen Lockout | Enable/disable physical screen lockout |

## Installation via HACS

1. In HACS, go to **Integrations** → click the three-dot menu → **Custom repositories**
2. Add this repository URL and select **Integration** as the category
3. Click **Download**
4. Restart Home Assistant

## Configuration

After installation, add the integration via **Settings → Devices & Services → Add Integration → Observer Communicating Thermostat**.

You will need:
- A name for the thermostat
- The thermostat's serial number
- A port for the local API server (default: `8080`)

After setup, configure your router or local DNS to redirect the thermostat's cloud API hostname to your Home Assistant IP on the configured port.

## Requirements

- Home Assistant 2024.2.0 or newer
- Observer Communicating Thermostat (TSTAT0201CW)
- Ability to redirect thermostat traffic to your Home Assistant instance (router/DNS)

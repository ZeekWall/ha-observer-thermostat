# Observer Communicating Thermostat for Home Assistant

Local control of the **Observer Communicating Thermostat (TSTAT0201CW)** — also sold under
the Arcoaire/ICP/Carrier-family brands. No cloud, no account.

The integration runs a small HTTP server inside Home Assistant that the thermostat talks
to instead of the manufacturer's cloud. Everything the thermostat reports shows up in HA,
and changes you make in HA are delivered on its next check-in (a few seconds).

Details of the wire protocol are in [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Install

1. HACS → ⋮ → **Custom repositories** → add this repository as an **Integration**.
2. Download it, then restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → Observer Communicating Thermostat.**
   You need a name, the thermostat's serial number, and a port (default `8080`).

## Point the thermostat at Home Assistant

In the thermostat's advanced Wi-Fi / server settings, set the server address to
`<your HA IP>:<port>` (for example `192.168.0.100:8080`). The thermostat then reports to
HA every few seconds.

* After Home Assistant restarts, the thermostat may wait several minutes before retrying.
  Re-saving the address or reconnecting its Wi-Fi makes it reconnect immediately.
* Entities show **unavailable** if the thermostat is silent for 5 minutes.

## Entities

**Climate** — modes Off, Cool, Heat, Heat/Cool (the thermostat's *Auto*), fan Auto/Low,
presets *Schedule* and *Hold*, and a setpoint or low/high range limited to the thermostat's
own installer limits (for example 52–88 °F). Changing a setpoint starts
a hold, exactly like using the thermostat. The `pending_changes` attribute shows anything
still being delivered.

| Platform | Entities |
|---|---|
| Sensor | Temperature, Humidity, Operating Mode, Fan Mode, State, Setpoint, Fan Status, Hold, **Hold Until**, **Next Schedule Change** (time, with the heat/cool setpoints as attributes), **Equipment Stage**, Filter Life Remaining, Outdoor Ambient/Coil Temperature, Indoor CFM, Operating Status, Active Equipment Event (+ time), **Last Fault** (full fault history in its attributes), **Cooling Runtime / Cycles (Lifetime)** (daily lifetime counters from the outdoor unit; use them with a utility meter for compressor runtime per day), Last Communication, Outdoor/Indoor Unit Type, Inducer RPM, Schedule Period |
| Diagnostic sensors (disabled by default) | Deadband, Changeover Setting, Cool/Heat Lockout Setting, Room Temperature Offset, Filter Interval, Indoor/Outdoor Unit Capacity, Indoor Unit Stages, Service Level, Schedule Day — read from the installer configuration the thermostat reports; shown raw |
| Binary sensor | Filter Service Needed, Indoor Unit Lockout |
| Number | Backlight Brightness (%), **Hold Duration**; Humidification / Dehumidification Setpoint (disabled by default; humidification is unavailable when the installer settings say humidity control is off) |
| Switch | Screen Lockout, **Indefinite Hold** |

### Holds

* **Hold Duration** `0` (default): a hold ends 15 minutes before the next schedule period,
  like the thermostat does. Any other value is a number of minutes.
* **Indefinite Hold** (switch): holds started from HA have no end time until you choose
  the *Schedule* preset. *Experimental — this has not yet been verified on a real unit.*
* A hold that was started at the thermostat keeps its own end time when you change the
  setpoint from HA.

## How changes are delivered

Each change from HA is tracked until the thermostat confirms it. If it isn't applied
within 90 seconds it is sent again (up to 3 attempts, then dropped with a log warning).
Changes that cannot be delivered for 3 minutes (thermostat offline) are dropped rather
than applied later. The thermostat's own schedule is never modified.

## Troubleshooting

* **Download diagnostics** from the device page. It contains current state, in-flight
  changes, the latest payload of every endpoint, the config the thermostat last reported,
  and recent requests/responses (serial redacted).
* PIN, lockout code and installer contact details are masked in captures and diagnostics.
* Traffic is also written to `/config/observer_thermostat/captures/capture.jsonl`
  (size-limited and rotated). Turn this off under the integration's **Configure** options.
* Enable debug logging for `custom_components.observer_thermostat` for per-request detail.

## Limitations / not yet verified

* Single zone only. Dealer/installer settings and the schedule are read-only (the schedule
  is preserved but not editable from HA).
* Verified: Cool and Off modes, Auto fan / Low fan, holds with and without an end time,
  schedule resume. **Not yet verified from HA:** Heat and Heat/Cool modes, Med/High fan,
  the Indefinite Hold switch, and the backlight / humidity / lockout writes.
* Weather, history and fault reports are not handled; they have not been observed yet.
* Humidity setpoints are untested and likely do nothing without humidity equipment enabled by the installer; they are disabled by default. The 20–65 % range is a guess.

## Requirements

* Home Assistant 2024.11.0 or newer
* An Observer Communicating Thermostat (TSTAT0201CW) that can be pointed at a custom server
  address

## Development

```
python -m venv venv && venv/bin/pip install aiohttp pytest pytest-asyncio
venv/bin/python -m pytest
```

Tests run the real server against a simulated thermostat modelled on captured traffic;
no Home Assistant installation is needed.

# Observer TSTAT0201CW local API protocol

Reverse-engineered from captured traffic of an Observer Communicating Thermostat
(model `TSTAT0201CW`, firmware `CESR131611-5.02`, sold under the Arcoaire/ICP/Carrier
family) with a 2-stage furnace and variable-speed AC, single zone. This is what the
integration implements; **"Confirmed"** means seen on the wire, **"Unverified"** means
inferred or not yet observed. Serial numbers, PINs and dealer details are intentionally
omitted.

## Transport

* Plain HTTP, no authentication, to the server address configured on the thermostat
  (`Host: <ha-ip>:<port>`). The thermostat is the only client; it initiates everything.
* Requests are `POST` with `Content-Type: application/x-www-form-urlencoded` and a
  single field: `data=<url-encoded XML>`. Root elements carry `version="1.7"`.
* Responses are XML (we answer `version="1.9"`) or empty `200`.
* The thermostat polls roughly every **4 s** (`status`, then `odu_status`), with silent
  gaps of up to ~95 s. After the server has been unreachable it backs off for minutes;
  re-saving the server address / reconnecting Wi-Fi on the thermostat makes it retry.

## How a command reaches the thermostat

1. HA records a *desired* value.
2. The next `POST /status` is answered with `<configHasChanges>on</configHasChanges>`.
3. The thermostat `GET`s `/systems/<serial>/config` and applies it.
4. It POSTs `/notifications` ("System settings updated") listing what it changed.
5. Subsequent `/status` reports show the new values. **Zone setpoints and hold** are
   confirmed from `/status`; **mode and the HA-only config values** (backlight, humidity,
   lockout) are confirmed by the notification, because `/status` does not carry them.

Changes made on the thermostat itself are reported through the same channels, plus a
config echo (below). A command not delivered within 3 minutes is dropped.

## Endpoints (thermostat → server)

| Request | Content | Status |
|---|---|---|
| `GET /Alive?sn=<serial>` | Reply `alive` (text/plain). | Confirmed |
| `GET /time/` | Reply `<time><utc>…Z</utc></time>`. | Confirmed |
| `POST /systems/<serial>/status` | Main state, every ~4 s. Reply carries the ping rates and `configHasChanges`. | Confirmed |
| `GET /systems/<serial>/config` | The config to apply; only requested after `configHasChanges=on`. | Confirmed |
| `POST /systems/<serial>` | **Config echo**: the thermostat's full config (`<system><config>…`). Sent on connect and after *local* (wall-unit) changes; not after pushed changes. | Confirmed |
| `POST /systems/<serial>/notifications` | Acknowledgement with `<change id="…">` entries. | Confirmed |
| `POST …/odu_status`, `…/idu_status` | Outdoor/indoor unit telemetry. | Confirmed |
| `POST …/equipment_events` | Fault/event history list. | Confirmed |
| `POST …/profile`, `…/dealer`, `…/dealer_config`, `…/idu_config`, `…/odu_config` | Identity and installer configuration, sent on connect. Read-only here. | Confirmed |
| `GET …/weather`, `POST …/history`, `…/idu_faults`, `…/odu_faults`, utility events | Advertised by the ping rates below; not yet observed. | Unverified |

Ping rates we advertise in the `/status` reply: weather 14400 s, history 86400 s,
IDU faults 86400 s, ODU faults 86400 s, IDU status 300 s, equipment events 60 s.

## `/status` fields

Flattened `<status>` (zone 1 fields live under `<zones><zone id="1">`):

| Field | Meaning |
|---|---|
| `rt` | Room temperature °F (zone) |
| `rh` | Room humidity % |
| `oat` | Outdoor air temperature °F |
| `mode` | **Resolved** operating mode (`off`, `cool`, `heat`). With the setting `auto` it reports the currently active one, so the *setting* must be read from the config echo. |
| `fan` | Fan setting: `auto`, `low` (continuous; the wall unit calls this change `continuous_fan`). `med`/`high`: unverified. |
| `hold` | `on`/`off` (zone) |
| `htsp`, `clsp` | Heat / cool setpoint °F (zone). While no hold is active these are the schedule's current values. |
| `coolicon`, `heaticon`, `fanicon` | Equipment running (`on`/`off`) |
| `filtrlvl` | Filter level; `0` when the thermostat shows "Service filter". Believed to be percent remaining (unverified). |
| `servicelvl` | Service reminder level (`0` observed) |
| `period` | Current schedule period index (1–4) |
| `day` | Day id, Sunday = 1 |
| `lat`, `damper` | Not populated on this system |

## `odu_status` / `idu_status`

* `odu_status`: `odutype` (e.g. `proteusac`), `opstat` (`off`, `stage 2`, …), `iducfm`,
  `oducoiltmp`, `oat`, `blwrpm`, `mincoolstage`, `maxcoolstage`, `minheatstage`,
  `maxheatstage`. `opstat` appears twice, the second time blank; keep the first value.
* `idu_status`: `idutype` (e.g. `furnace2stg`), `iducfm`, `blwrpm`, `inducerrpm`,
  `lockoutactive`, `lockouttime`.

## `equipment_events`

`<events><event id="N">` with `source` (`idu`/`odu`), `equip`, `code`, `description`,
`localtime` (`  7/18/26 12:07AM`, thermostat-local), `occurrences`, `active`. Event 1 is
the most recent. The integration shows the first event only while `active=on`.

## Config (`/config` response and config echo)

```xml
<config version="1.9">
  <atom:link rel="self" …/> …           <!-- links, ignored by the thermostat -->
  <timestamp>…Z</timestamp>
  <mode>cool</mode>                       <!-- setting: off | cool | heat | auto -->
  <fan>auto</fan>
  <zones>
    <zone id="1">
      <name>Zone 1</name>
      <hold>on</hold>
      <htsp>62</htsp><clsp>68</clsp>      <!-- only present while hold=on -->
      <otmr>22:15</otmr>                  <!-- hold end, HH:MM; empty = indefinite -->
      <program>…</program>                <!-- see Schedule -->
    </zone>
    <zone id="2"/> … <zone id="6"/>       <!-- empty stubs -->
  </zones>
  <timeFormat>12</timeFormat><dst>on</dst><volume>high</volume><soundType>click</soundType>
  <scrLockout>off</scrLockout><scrLockoutCode>…</scrLockoutCode>
  <humSetpoint>45</humSetpoint><dehumSetpoint>45</dehumSetpoint>
  <blight>30</blight>                     <!-- percent -->
</config>
```

The integration builds `/config` by taking the thermostat's own last echo and changing
only the fields HA changed, so the schedule, names and untouched settings are preserved.

### Holds (confirmed)

* A setpoint change **is** a hold: `hold=on` plus `htsp`/`clsp` and `otmr`.
* From the wall unit, changing the temperature offers a hold *until a time*; the time seen
  was 15 minutes before the next schedule period (e.g. 22:15 with a 22:30 period).
  Pressing Hold first and then changing the temperature gives an **indefinite** hold
  (`<otmr/>` empty). This is the origin of the integration's "Hold Duration" (0 = until
  the next period) and "Indefinite Hold" options.
* An empty or already-past `otmr` in a pushed config makes the thermostat drop the hold
  within seconds, taking the setpoint with it. This was the cause of changes "not
  sticking" in earlier versions. Indefinite holds pushed from HA (empty `otmr`) are
  **unverified**.
* While `hold=off` the zone has no `htsp`/`clsp`/`otmr`; setpoints come from the schedule.

### Notification change ids (confirmed)

`op_mode` (mode), `continuous_fan` (fan), `zone_hold` (hold and its setpoints). Ids for
backlight, humidity and lockout changes: unverified; the integration accepts any
`System settings updated` notification following a fetch.

## Schedule (read-only in this integration)

`<program><day id="1">…</day>` for days 1–7 (Sunday = 1), each with periods
`<period id="1"><time>06:30</time><htsp>60</htsp><clsp>64</clsp></period>` — four periods
per day on the observed unit. An empty `<program/>` in a pushed config did **not**
erase the schedule. Writing a program has not been tried.

## Installer configuration (read-only, never written)

`dealer_config` (limits and system options such as `cfgdead`, `cfgchgover`,
`lascoollckout`, `maxhtsp`, `minclsp`, `filterinterval`, `zonesused`), `idu_config`,
`odu_config`, `dealer`, `profile` (`brand`, `model`, `firmware`, stage counts,
capacities, zone sensors). Treated as diagnostics only.

## Capturing traffic

The integration records requests and responses with the serial redacted (diagnostics
download, plus `/config/observer_thermostat/captures/capture.jsonl`). Polling endpoints
(`status`, `odu_status`, `idu_status`, `equipment_events`, `Alive`, `time`) are logged
only when their content changes or every 10 minutes (with a `repeats` counter); all
other requests are always logged.

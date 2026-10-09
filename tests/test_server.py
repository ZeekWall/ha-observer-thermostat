import datetime
import xml.etree.ElementTree as ET

import pytest_asyncio
from observer_thermostat import server as server_mod
from observer_thermostat.capture import CaptureLog
from observer_thermostat.server import (
    ObserverThermostatServer,
    ThermostatData,
    decode_body,
    parse_event_time,
    values_equal,
)
from sim import SERIAL, FakeThermostat

TZ = datetime.timezone(datetime.timedelta(hours=-4))
THURSDAY_2059 = datetime.datetime(2026, 10, 8, 20, 59, tzinfo=TZ)


@pytest_asyncio.fixture
async def env():
    data = ThermostatData(SERIAL, "127.0.0.1:8080", clock=lambda: THURSDAY_2059)
    capture = CaptureLog(SERIAL, maxlen=50)
    srv = ObserverThermostatServer(data, 0, lambda: None, capture=capture)
    await srv.start()
    tstat = FakeThermostat(srv.bound_port)
    yield data, srv, tstat, capture
    await tstat.close()
    await srv.stop()


async def with_echo(env):
    """Poll once and let the thermostat report its config, like after a push."""
    data, _, tstat, _ = env
    await tstat.poll()
    await tstat.post_echo()
    return data, tstat


def zone_of(config: ET.Element) -> ET.Element:
    return next(z for z in config.iterfind("zones/zone") if z.get("id") == "1")


# ── basics ─────────────────────────────────────────────────────────


async def test_first_status_populates_state(env):
    data, _, tstat, _ = env
    assert await tstat.poll() is False
    assert (data.temperature, data.mode, data.heating_setpoint) == (72, "heat", 68)
    assert "zone" not in data.reported  # empty zone stubs are ignored


# ── the reported bug: setpoint changes must stick ──────────────────


async def test_setpoint_change_sticks_with_valid_hold_time(env):
    data, tstat = await with_echo(env)
    tstat.status["mode"] = "cool"
    await tstat.poll()
    data.set_cool_setpoint(68)
    data.set_hold("on")
    assert data.cooling_setpoint == 68  # optimistic

    assert await tstat.poll() is True
    zone = zone_of(tstat.last_config)
    assert zone.findtext("otmr") == "22:15"  # 15 min before the 22:30 period
    assert (tstat.status["hold"], tstat.status["clsp"]) == ("on", "68")

    await tstat.poll()  # reports the applied state
    assert data.desired == {}
    assert (data.hold, data.cooling_setpoint) == ("on", 68)
    assert data.needs_push is False


async def test_works_before_any_config_echo_via_fallback_template(env):
    data, _, tstat, _ = env
    tstat.status["mode"] = "cool"
    await tstat.poll()
    data.set_cool_setpoint(70)
    data.set_hold("on")
    assert await tstat.poll() is True
    # schedule unknown until the first echo: now + 2h fallback, still a future time
    assert tstat.last_config.findtext("zones/zone/otmr") == "22:59"
    assert tstat.status["clsp"] == "70"
    await tstat.poll()
    assert data.desired == {}


async def test_existing_hold_keeps_its_end_time(env):
    data, tstat = await with_echo(env)
    tstat.status.update(mode="cool", hold="on", clsp="68")
    tstat.otmr = "21:30"
    await tstat.poll()
    await tstat.post_echo()
    data.set_cool_setpoint(70)
    assert await tstat.poll() is True
    assert zone_of(tstat.last_config).findtext("otmr") == "21:30"
    assert tstat.status["clsp"] == "70"


async def test_releasing_hold_drops_zone_setpoints(env):
    data, tstat = await with_echo(env)
    tstat.status.update(hold="on", htsp="66")
    tstat.otmr = "22:15"
    await tstat.poll()
    data.set_hold("off")
    assert await tstat.poll() is True
    zone = zone_of(tstat.last_config)
    assert zone.findtext("hold") == "off"
    assert zone.find("htsp") is None and zone.find("otmr") is None


async def test_config_is_the_thermostats_own_not_a_rebuild(env):
    data, tstat = await with_echo(env)
    data.set_fan_mode("low")
    await tstat.poll()
    zone = zone_of(tstat.last_config)
    assert zone.findtext("name") == "Zone 1"
    assert len(list(zone.iterfind("program/day/period"))) == 28  # schedule preserved
    assert tstat.last_config.findtext("blight") == "30"  # real value, not a default
    assert tstat.last_config.find("zones/zone[@id='2']") is not None


async def test_manual_change_on_thermostat_flows_through(env):
    data, _, tstat, _ = env
    await tstat.poll()
    tstat.status["htsp"] = "66"
    await tstat.poll()
    assert data.heating_setpoint == 66 and data.needs_push is False


async def test_setting_current_value_is_noop(env):
    data, _, tstat, _ = env
    await tstat.poll()
    data.set_heat_setpoint(68.0)
    assert data.desired == {} and data.needs_push is False


# ── retries and confirmation ───────────────────────────────────────


async def test_unacknowledged_change_is_retried_then_dropped(env):
    data, tstat = await with_echo(env)
    tstat.reject = {"fan"}
    data.set_fan_mode("low")

    pushes = 0
    for _ in range(20):
        if await tstat.poll():
            pushes += 1
        pending = data.desired.get("fan")
        if pending is None:
            break
        assert data.fan_mode == "low"  # optimistic while retrying
        if pending.sent_at is not None:  # pretend the grace window has passed
            pending.sent_at -= datetime.timedelta(seconds=server_mod.CONFIRM_GRACE_SECONDS + 1)

    assert pushes == server_mod.MAX_PUSH_ATTEMPTS
    assert "fan" not in data.desired
    assert data.fan_mode == "auto"


async def test_stale_status_after_push_does_not_undo_change(env):
    data, tstat = await with_echo(env)
    data.set_fan_mode("low")
    tstat.reject = {"fan"}  # not applied yet
    await tstat.poll()
    await tstat.poll()
    assert data.fan_mode == "low" and "fan" in data.desired  # still pending


# ── settings confirmed through the config echo ─────────────────────


async def test_backlight_confirmed_by_echo(env):
    data, tstat = await with_echo(env)
    assert data.blight == 30  # thermostat's real value
    data.set_blight(50)
    assert data.blight == 50
    assert await tstat.poll() is True
    assert tstat.last_config.findtext("blight") == "50"
    assert data.desired == {}  # echo showed 50
    assert data.blight == 50
    assert await tstat.poll() is False


async def test_lockout_and_humidity_setters(env):
    data, tstat = await with_echo(env)
    data.set_scr_lockout(True)
    data.set_hum_setpoint(40)
    await tstat.poll()
    assert (data.scr_lockout, data.hum_setpoint) == (True, 40)
    assert tstat.top["scrLockout"] == "on" and tstat.top["humSetpoint"] == "40"


# ── hold timing ────────────────────────────────────────────────────


def test_hold_until_variants():
    d = ThermostatData(SERIAL, "h", clock=lambda: THURSDAY_2059)
    d.set_echo(FakeThermostat.config_echo_xml(type("S", (), {
        "status": {"hold": "off", "mode": "cool", "fan": "auto", "htsp": "60", "clsp": "64"},
        "top": {}, "otmr": ""})()))
    assert d.hold_until() == "22:15"
    late = THURSDAY_2059.replace(hour=22, minute=20)  # inside the 15 min lead
    assert d.hold_until(late) == "06:15"  # tomorrow's first period
    d.set_otmr(90)
    assert d.hold_until() == "22:29"
    d.set_otmr(0)
    bare = ThermostatData(SERIAL, "h", clock=lambda: THURSDAY_2059)  # no schedule known
    assert bare.hold_until() == "22:59"


# ── other endpoints and robustness ─────────────────────────────────


async def test_odu_idu_status_feed_sensors_and_duplicate_blank_tags(env):
    data, _, tstat, _ = env
    body = ("data=%3Codu_status%3E%3Copstat%3Eoff%3C%2Fopstat%3E%3Copstat%2F%3E"
            "%3Coducoiltmp%3E86%3C%2Foducoiltmp%3E%3Ciducfm%3E1200%3C%2Fiducfm%3E%3C%2Fodu_status%3E")
    assert await tstat.post("odu_status", body) == 200
    assert (data.opstat, data.outdoor_coil_temp, data.indoor_cfm) == ("off", 86, 1200)


async def test_equipment_event_time_is_parsed(env):
    data, _, tstat, _ = env
    xml = ("<equipment_events><events><event id='1'><source>idu</source><description>Oops</description>"
           "<localtime>  6/01/26  3:47PM</localtime><active>on</active></event></events></equipment_events>")
    await tstat.post("equipment_events", "data=" + xml)
    assert data.latest_equip_description == "Oops"
    assert data.latest_equip_time == datetime.datetime(2026, 6, 1, 15, 47, tzinfo=TZ)


def test_parse_event_time():
    assert parse_event_time("  7/18/26 12:07AM", None) == datetime.datetime(2026, 7, 18, 0, 7)
    assert parse_event_time("garbage", None) is None


async def test_notifications_and_unknown_payloads_are_kept_and_capture_redacts(env):
    data, _, tstat, capture = env
    assert await tstat.post("notifications", "<notifications><x>1</x></notifications>") == 200
    assert "notifications" in data.raw_last
    assert "<SERIAL>" in capture.entries()[-1]["path"]
    assert SERIAL not in capture.entries()[-1]["path"]


async def test_short_and_malformed_bodies_do_not_crash(env):
    _, _, tstat, _ = env
    assert await tstat.post("status", "") == 200
    assert await tstat.post("status", "data=%3Cstatus%3E") == 200
    assert await tstat.post("idu_faults", "data=<a/>") == 200
    assert await tstat.post("", "not xml") == 200  # config echo with garbage


async def test_alive_time_and_dealer_config(env):
    _, _, tstat, _ = env
    async with tstat.session.get(f"{tstat.base}/Alive?sn={SERIAL}") as r:
        assert await r.text() == "alive"
    async with tstat.session.get(f"{tstat.base}/time/") as r:
        assert "<utc>" in await r.text()
    async with tstat.session.get(f"{tstat.base}/systems/{SERIAL}/dealer_config") as r:
        assert r.status == 200 and "<config" not in await r.text()


# ── persistence, capture, helpers ──────────────────────────────────


async def test_store_roundtrip_keeps_echo_and_last_known(env):
    data, tstat = await with_echo(env)
    data.last_known["htsp"] = "70"
    data.set_otmr(60)
    fresh = ThermostatData(SERIAL, "h")
    fresh.load_store(data.to_store())
    assert (fresh.otmr, fresh.last_known["htsp"], fresh.blight) == (60, "70", 30)
    assert fresh.program()[5] == [390, 570, 1110, 1350]


async def test_last_mode_remembered(env):
    data, _, tstat, _ = env
    await tstat.poll()
    assert data.last_on_mode == "heat"
    data.set_mode("off")
    assert data.last_on_mode == "heat"


def test_capture_ring_is_bounded():
    c = CaptureLog("S", maxlen=3)
    for i in range(10):
        c.add(method="GET", path=f"/{i}", query="", remote=None, headers={}, req_body="", status=200, resp_body="")
    assert [e["path"] for e in c.entries()] == ["/7", "/8", "/9"]


def test_decode_body():
    assert decode_body("data=%3Ca%3Ex+y%3C%2Fa%3E") == "<a>x y</a>"
    assert decode_body("<a>data=1</a>") == "<a>data=1</a>"
    assert decode_body("data=%3Cdata%3E") == "<data>"


def test_values_equal():
    assert values_equal("75", "75.0")
    assert not values_equal("75", "76")
    assert not values_equal(None, "75")
    assert values_equal("heat", "heat")

import datetime

import pytest
import pytest_asyncio
from observer_thermostat import server as server_mod
from observer_thermostat.capture import CaptureLog
from observer_thermostat.server import (
    ObserverThermostatServer,
    ThermostatData,
    decode_body,
    values_equal,
)
from sim import SERIAL, FakeThermostat


@pytest_asyncio.fixture
async def env():
    data = ThermostatData(serial=SERIAL, api_address="127.0.0.1:8080")
    capture = CaptureLog(SERIAL, maxlen=50)
    updates = []
    srv = ObserverThermostatServer(data, 0, lambda: updates.append(1), capture=capture)
    await srv.start()
    tstat = FakeThermostat(srv.bound_port)
    yield data, srv, tstat, capture
    await tstat.close()
    await srv.stop()


async def test_first_status_populates_state(env):
    data, _, tstat, _ = env
    assert await tstat.poll() is False
    assert data.temperature == 72
    assert data.mode == "heat"
    assert data.heating_setpoint == 68


async def test_change_survives_stale_status_report(env):
    """The reported bug: a stale /status after /config must not undo the change."""
    data, _, tstat, _ = env
    await tstat.poll()

    data.set_heat_setpoint(72)
    assert data.heating_setpoint == 72  # optimistic

    tstat.apply_late = True  # firmware reports old value once more before adopting
    assert await tstat.poll() is True  # fetched config, stages it
    assert tstat.last_config["htsp"] == "72"

    assert data.heating_setpoint == 72
    assert "htsp" in data.desired  # still pending, not cleared by /config

    await tstat.poll()  # late poll: stale 68 then adopts 72
    await tstat.poll()  # now reports 72
    assert data.heating_setpoint == 72
    assert data.desired == {}
    assert data.needs_push is False


async def test_only_changed_fields_override_thermostat_values(env):
    data, _, tstat, _ = env
    await tstat.poll()
    tstat.state["clsp"] = "77"  # changed on the wall since our last report
    data.set_heat_setpoint(70)
    await tstat.poll()  # reports clsp=77, then fetches config
    assert tstat.last_config["clsp"] == "77"
    assert tstat.last_config["htsp"] == "70"


async def test_manual_change_on_thermostat_flows_through(env):
    data, _, tstat, _ = env
    await tstat.poll()
    tstat.state["htsp"] = "66"
    await tstat.poll()
    assert data.heating_setpoint == 66
    assert data.needs_push is False


async def test_setting_current_value_is_noop(env):
    data, _, tstat, _ = env
    await tstat.poll()
    data.set_heat_setpoint(68.0)
    assert data.desired == {}
    assert data.needs_push is False


async def test_unacknowledged_change_is_retried_then_dropped(env):
    data, _, tstat, _ = env
    await tstat.poll()
    tstat.reject = {"htsp"}
    data.set_heat_setpoint(72)

    pushes = 0
    for _ in range(20):
        if await tstat.poll():  # server announced changes; sim fetched config
            pushes += 1
        pending = data.desired.get("htsp")
        if pending is None:
            break
        assert data.heating_setpoint == 72  # optimistic value held while retrying
        if pending.sent_at is not None:  # pretend the grace window has passed
            pending.sent_at -= datetime.timedelta(seconds=server_mod.CONFIRM_GRACE_SECONDS + 1)

    assert pushes == server_mod.MAX_PUSH_ATTEMPTS
    assert "htsp" not in data.desired
    assert data.heating_setpoint == 68  # back to what the thermostat really has


async def test_local_settings_are_pushed_once(env):
    data, _, tstat, _ = env
    await tstat.poll()
    data.set_blight(4)
    assert data.needs_push
    assert await tstat.poll() is True
    assert tstat.last_config["blight"] == "4"
    assert await tstat.poll() is False


async def test_config_uses_persisted_values_not_defaults(env):
    data, _, tstat, _ = env
    data.load_store({"last_known": {"htsp": "66", "clsp": "79", "mode": "cool"}, "blight": 3})
    await tstat.fetch_config()
    assert tstat.last_config["htsp"] == "66"
    assert tstat.last_config["mode"] == "cool"
    assert tstat.last_config["blight"] == "3"


async def test_store_roundtrip():
    a = ThermostatData("S", "h")
    a.set_hum_setpoint(50)
    a.set_scr_lockout(True)
    a.last_known["htsp"] = "70"
    b = ThermostatData("S", "h")
    b.load_store(a.to_store())
    assert (b.hum_setpoint, b.scr_lockout, b.last_known["htsp"]) == (50, True, "70")


async def test_turn_on_remembers_last_mode(env):
    data, _, tstat, _ = env
    await tstat.poll()
    assert data.last_on_mode == "heat"
    data.set_mode("off")
    assert data.last_on_mode == "heat"


async def test_unknown_endpoint_payloads_are_kept_and_capture_redacts(env):
    data, _, tstat, capture = env
    status = await tstat.post("odu_status", "data=%3Codu_status%3E%3Cfoo%3E1%3C%2Ffoo%3E%3C%2Fodu_status%3E")
    assert status == 200
    assert data.raw_last["odu_status"] == {"foo": "1"}
    entry = capture.entries()[-1]
    assert SERIAL not in entry["path"]
    assert "<SERIAL>" in entry["path"]


async def test_short_and_malformed_bodies_do_not_crash(env):
    data, _, tstat, _ = env
    assert await tstat.post("status", "") == 200
    assert await tstat.post("status", "data=%3Cstatus%3E") == 200
    assert await tstat.post("idu_faults", "data=<a/>") == 200


async def test_alive_time_and_dealer_config(env):
    _, srv, tstat, _ = env
    async with tstat.session.get(f"{tstat.base}/Alive?sn={SERIAL}") as r:
        assert await r.text() == "alive"
    async with tstat.session.get(f"{tstat.base}/time/") as r:
        assert "<utc>" in await r.text()
    async with tstat.session.get(f"{tstat.base}/systems/{SERIAL}/dealer_config") as r:
        assert r.status == 200 and "<config" not in await r.text()


def test_capture_ring_is_bounded():
    c = CaptureLog("S", maxlen=3)
    for i in range(10):
        c.add(method="GET", path=f"/{i}", query="", remote=None, headers={}, req_body="", status=200, resp_body="")
    assert [e["path"] for e in c.entries()] == ["/7", "/8", "/9"]


def test_decode_body():
    assert decode_body("data=%3Ca%3Ex+y%3C%2Fa%3E") == "<a>x y</a>"
    assert decode_body("<a>data=1</a>") == "<a>data=1</a>"
    # old lstrip("data=") would have mangled a payload starting with these chars
    assert decode_body("data=%3Cdata%3E") == "<data>"


def test_values_equal():
    assert values_equal("75", "75.0")
    assert not values_equal("75", "76")
    assert not values_equal(None, "75")
    assert values_equal("heat", "heat")

"""Constants for the Observer Thermostat integration."""

DOMAIN = "observer_thermostat"

CONF_THERMOSTAT_NAME = "thermostat_name"
CONF_THERMOSTAT_SERIAL = "thermostat_serial"
CONF_SERVER_PORT = "server_port"

DEFAULT_PORT = 8080
DEFAULT_NAME = "Thermostat"

MODE_OFF = "off"
MODE_COOL = "cool"
MODE_HEAT = "heat"
MODE_AUTO = "auto"

FAN_AUTO = "auto"
FAN_LOW = "low"
FAN_MED = "med"
FAN_HIGH = "high"

FAN_MODES = [FAN_AUTO, FAN_LOW, FAN_MED, FAN_HIGH]

PRESET_HOLD = "Hold"
PRESET_SCHEDULE = "Schedule"

MIN_TEMP = 55
MAX_TEMP = 85

DEFAULT_HUM_SETPOINT = 45
DEFAULT_DEHUM_SETPOINT = 45
DEFAULT_BLIGHT = 10
DEFAULT_OTMR = 0  # 0 = permanent hold, >0 = minutes before reverting to schedule

KEY_RT = "rt"
KEY_RH = "rh"
KEY_MODE = "mode"
KEY_FAN = "fan"
KEY_COOLICON = "coolicon"
KEY_HEATICON = "heaticon"
KEY_FANICON = "fanicon"
KEY_HOLD = "hold"
KEY_FILTRLVL = "filtrlvl"
KEY_CLSP = "clsp"
KEY_HTSP = "htsp"
KEY_OPSTAT = "opstat"
KEY_IDUCFM = "iducfm"
KEY_OAT = "oat"
KEY_ODUCOILTMP = "oducoiltmp"

MONITORED_KEYS = [
    KEY_RT, KEY_RH, KEY_MODE, KEY_FAN, KEY_COOLICON, KEY_HEATICON,
    KEY_FANICON, KEY_HOLD, KEY_FILTRLVL, KEY_CLSP, KEY_HTSP, KEY_OPSTAT,
    KEY_IDUCFM, KEY_OAT, KEY_ODUCOILTMP,
]

CHANGES_PENDING_TIMEOUT_SECONDS = 300  # 5 minutes

SIGNAL_THERMOSTAT_UPDATE = f"{DOMAIN}_update"

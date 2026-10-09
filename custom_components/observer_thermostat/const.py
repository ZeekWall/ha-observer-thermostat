"""Constants for the Observer Thermostat integration."""

DOMAIN = "observer_thermostat"

CONF_THERMOSTAT_NAME = "thermostat_name"
CONF_THERMOSTAT_SERIAL = "thermostat_serial"
CONF_SERVER_PORT = "server_port"
CONF_CAPTURE_TO_FILE = "capture_to_file"

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
DEFAULT_BLIGHT = 30  # percent; only shown until the thermostat reports its own
DEFAULT_OTMR = 0  # hold length in minutes; 0 = until the next schedule period

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

# A change we pushed must show up in the thermostat's /status within this long,
# otherwise it is re-pushed (up to MAX_PUSH_ATTEMPTS times, then dropped).
CONFIRM_GRACE_SECONDS = 90
MAX_PUSH_ATTEMPTS = 3

# Fields we can set and expect the thermostat to echo back in /status.
CONTROL_KEYS = ("mode", "fan", "hold", "htsp", "clsp")

# Top-level /config tags HA can change; the thermostat confirms them by echoing
# its config (POST /systems/<serial>), not in /status.
LOCAL_KEYS = ("blight", "humSetpoint", "dehumSetpoint", "scrLockout")

# A wall-unit hold ends 15 minutes before the next schedule period (observed:
# hold at 20:59 -> otmr 22:15 with a 22:30 period).
HOLD_END_LEAD_MINUTES = 15
HOLD_FALLBACK_MINUTES = 120

CAPTURE_RING_SIZE = 500
CAPTURE_FILE_MAX_BYTES = 2 * 1024 * 1024
CAPTURE_FILE_BACKUPS = 5
CAPTURE_DIR_NAME = "observer_thermostat/captures"

STORAGE_VERSION = 1
STORAGE_SAVE_DELAY = 30

SIGNAL_THERMOSTAT_UPDATE = f"{DOMAIN}_update"

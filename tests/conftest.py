"""Load the integration's HA-independent modules without importing Home Assistant.

``custom_components/observer_thermostat/__init__.py`` imports homeassistant, so
we register the directory as a bare package instead of executing it.
"""

import sys
import types
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "observer_thermostat"

pkg = types.ModuleType("observer_thermostat")
pkg.__path__ = [str(PKG_DIR)]
sys.modules.setdefault("observer_thermostat", pkg)
sys.path.insert(0, str(Path(__file__).resolve().parent))

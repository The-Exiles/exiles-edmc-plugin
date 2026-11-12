"""EDMC_Exiles: The Exiles' own plugin for EDMC, beside BGS Tally. These are EDMC's entry points; everything else
lives in exiles/ (core, sender, status, state, pairing, words, ui). See README.md at the repo root.

There is no plugin_prefs: the plugin asks EDMC for no tab in its Settings dialog (0.2.6). Two settings pages were one
too many, and a box a pilot can type in is a way to break the plugin. The one settings page is the Exiles window's,
which the strip's word opens."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

PLUGIN_DIR = Path(__file__).resolve().parent
PLUGIN_NAME = PLUGIN_DIR.name     # EDMC_Exiles: the folder name is the import name
VERSION = (PLUGIN_DIR / "version.txt").read_text(encoding="utf-8").strip() if (PLUGIN_DIR / "version.txt").exists() else "0.0.0"

# EDMC's own modules; the replay harness puts stubs of them on sys.path
try:
    from config import config   # type: ignore
except Exception:   # pragma: no cover
    config = None
try:
    from config import appversion   # type: ignore
except Exception:   # pragma: no cover
    appversion = None
try:
    from config import user_agent as _edmc_user_agent   # type: ignore
except Exception:   # pragma: no cover
    _edmc_user_agent = None
try:
    from EDMCLogging import get_plugin_logger   # type: ignore
    logger = get_plugin_logger(PLUGIN_NAME)
except Exception:   # pragma: no cover
    logger = logging.getLogger(PLUGIN_NAME)
try:
    from monitor import monitor   # type: ignore
    _game_running = lambda: bool(monitor.game_running())   # noqa: E731
except Exception:   # pragma: no cover
    _game_running = lambda: True   # noqa: E731
try:
    from timeout_session import new_session   # type: ignore
except Exception:   # pragma: no cover
    new_session = None

if str(PLUGIN_DIR.parent) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR.parent))

from EDMC_Exiles.exiles.core import ExilesPlugin, Settings   # noqa: E402
from EDMC_Exiles.exiles.net import make_http   # noqa: E402

this = SimpleNamespace(core=None, strip=None)


def _edmc_version() -> str:
    try:
        return str(appversion()) if callable(appversion) else str(appversion or "")
    except Exception:
        return ""


def _user_agent() -> str:
    base = ""
    try:
        base = str(getattr(config, "user_agent", "") or _edmc_user_agent or "")
    except Exception:
        base = ""
    return f"{base} {PLUGIN_NAME}/{VERSION}".strip()


def plugin_start3(plugin_dir: str) -> str:
    session = new_session() if callable(new_session) else None
    this.core = ExilesPlugin(plugin_dir=Path(plugin_dir), settings=Settings(config), http=make_http(_user_agent(), session),
                             game_running=_game_running, log=logger, version=VERSION, edmc_version=_edmc_version())
    this.core.start()
    # The whole package is loaded now, never piece by piece later. EDMC runs for days, and a newer version copied over
    # it would otherwise be imported half and half the first time a part of it is needed: new code on old words.
    try:
        from EDMC_Exiles.exiles import ui   # noqa: F401
    except Exception as e:   # no tkinter (a headless replay): the windows are simply never opened
        logger.info(f"{PLUGIN_NAME}: the interface is not loaded ({e.__class__.__name__})")
    logger.info(f"{PLUGIN_NAME} {VERSION} started, {'paired' if this.core.paired else 'not paired yet'}")
    return PLUGIN_NAME


def plugin_stop() -> None:
    if this.core:
        this.core.stop()


def plugin_app(parent):
    from EDMC_Exiles.exiles import ui
    this.strip = ui.Strip(parent, this.core)
    return this.strip.frame


def prefs_changed(cmdr: str, is_beta: bool) -> None:
    """EDMC's Settings dialog was closed with OK. Nothing of ours is in it; its theme may have changed."""
    if this.strip:
        this.strip.settings_changed()


def journal_entry(cmdr, is_beta, system, station, entry, state):
    if this.core:
        this.core.journal_entry(cmdr, is_beta, system, station, entry, state)


def dashboard_entry(cmdr, is_beta, entry):
    if this.core:
        this.core.dashboard_entry(cmdr, is_beta, entry)

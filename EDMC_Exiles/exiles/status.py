"""The Status file: which entries are worth sending, and what the flags mean in words. EDMC calls dashboard_entry
about once a second in flight; EXDB wants the moments that matter (a flag, the legal state, the body, the
destination or the cockpit focus changed, or the fuel crossed a whole tonne) and otherwise one entry every
STATUS_EVERY seconds while the game runs. It also says where the pilot is on a planet, which the journal never does:
the kinds in STAMPED go out with that position (where_from_status)."""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional

STATUS_EVERY = 30.0
WATCHED = ("Flags", "Flags2", "LegalState", "BodyName", "GuiFocus")

# Where the Rhino was (0.1.2), so that a pilot's mining spots place themselves on the site: the journal never says,
# but the Status file carries Latitude, Longitude, Altitude and Heading whenever the pilot is near a surface. A
# journal entry of a kind in STAMPED goes out with the latest Status position under WHERE_KEY when that Status entry
# is at most WHERE_FRESH seconds old (the game writes the file about once a second while it runs). EXDB sets the key
# aside when it tells entries apart, so BGS Tally's copy of the same line is still the same entry, and folds it into
# whichever copy arrived first.
STAMPED = ("MiningRefined",)
WHERE_KEY = "ExilesWhere"
WHERE_FRESH = 60.0

# the Status file's Flags and Flags2, as EDMC's edmc_data.py names them
FLAGS = {
    "docked": 1 << 0, "landed": 1 << 1, "shields_up": 1 << 3, "supercruise": 1 << 4, "hardpoints": 1 << 6,
    "in_wing": 1 << 7, "cargo_scoop": 1 << 9, "silent_running": 1 << 10, "scooping_fuel": 1 << 11,
    "mass_locked": 1 << 16, "fsd_charging": 1 << 17, "fsd_cooldown": 1 << 18, "low_fuel": 1 << 19,
    "overheating": 1 << 20, "has_lat_long": 1 << 21, "in_danger": 1 << 22, "being_interdicted": 1 << 23,
    "in_main_ship": 1 << 24, "in_fighter": 1 << 25, "in_srv": 1 << 26, "analysis_mode": 1 << 27,
    "night_vision": 1 << 28, "fsd_jump": 1 << 30,
}
FLAGS2 = {
    "on_foot": 1 << 0, "in_taxi": 1 << 1, "in_multicrew": 1 << 2, "on_foot_in_station": 1 << 3,
    "on_foot_on_planet": 1 << 4, "low_oxygen": 1 << 6, "low_health": 1 << 7, "glide_mode": 1 << 12,
    "on_foot_in_hangar": 1 << 13, "on_foot_social": 1 << 14, "on_foot_exterior": 1 << 15,
}


def decode_flags(flags: Optional[int], flags2: Optional[int]) -> dict:
    f, f2 = int(flags or 0), int(flags2 or 0)
    out = {name: bool(f & bit) for name, bit in FLAGS.items()}
    out.update({name: bool(f2 & bit) for name, bit in FLAGS2.items()})
    return out


def doing_from_status(d: dict) -> str:
    """One plain phrase for where the pilot is."""
    if d.get("on_foot"):
        return "on foot"
    if d.get("in_taxi"):
        return "in a taxi"
    if d.get("docked"):
        return "docked"
    if d.get("landed"):
        return "landed"
    if d.get("in_srv"):
        return "in an SRV"
    if d.get("in_fighter"):
        return "in a fighter"
    if d.get("fsd_jump"):
        return "jumping"
    if d.get("supercruise"):
        return "in supercruise"
    return "flying"


def fuel_tonnes(entry: dict) -> Optional[int]:
    fuel = entry.get("Fuel")
    if isinstance(fuel, dict) and isinstance(fuel.get("FuelMain"), (int, float)):
        return int(fuel["FuelMain"])
    return None


def where_from_status(entry: Optional[dict]) -> Optional[dict]:
    """The position a Status entry carries, as EXDB wants it under WHERE_KEY; None away from any surface."""
    if not isinstance(entry, dict):
        return None
    lat, lon = entry.get("Latitude"), entry.get("Longitude")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)) or isinstance(lat, bool) or isinstance(lon, bool):
        return None
    out = {"Latitude": lat, "Longitude": lon}
    for k in ("Altitude", "Heading", "BodyName"):
        if entry.get(k) is not None:
            out[k] = entry[k]
    return out


def status_key(entry: dict) -> tuple:
    """What a change in means a Status entry is worth sending."""
    dest = entry.get("Destination")
    return tuple(entry.get(k) for k in WATCHED) + (
        json.dumps(dest, sort_keys=True) if isinstance(dest, dict) else None, fuel_tonnes(entry))


class StatusThrottle:
    def __init__(self, clock: Callable[[], float] = time.monotonic, every: float = STATUS_EVERY):
        self._clock = clock
        self._every = every
        self._last_key: Optional[tuple] = None
        self._last_sent: Optional[float] = None

    def worth(self, entry: dict) -> bool:
        """True when this entry should go: something watched changed, or STATUS_EVERY has passed. Records the send."""
        key = status_key(entry)
        now = self._clock()
        if key != self._last_key or self._last_sent is None or now - self._last_sent >= self._every:
            self._last_key = key
            self._last_sent = now
            return True
        return False

    def reset(self) -> None:
        self._last_key = None
        self._last_sent = None


def status_words(entry: dict) -> str:
    """The strip's one line about the moment: where the pilot is and what the cockpit says."""
    d = decode_flags(entry.get("Flags"), entry.get("Flags2"))
    bits = [doing_from_status(d)]
    if d.get("being_interdicted"):
        bits.append("being interdicted")
    if d.get("low_fuel"):
        bits.append("low fuel")
    if d.get("in_danger"):
        bits.append("in danger")
    legal = entry.get("LegalState")
    if isinstance(legal, str) and legal not in ("Clean", "Allied", ""):
        bits.append(legal.lower())
    t = fuel_tonnes(entry)
    if t is not None:
        bits.append(f"{t} t fuel")
    return ", ".join(bits)

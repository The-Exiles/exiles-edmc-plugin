"""EDMC's commander state, by kind. EDMC hands journal_entry a `state` dict it keeps current (cargo, materials, the
ship locker, the loadout, ranks, credits, Powerplay, the plotted route, the suit, the game build). The tracker sends a
kind when its content changes (a hash of it), on StartUp, and once an hour as a keepalive, so EXDB always holds the
latest of each without a flood. Friends and Statistics are left out: the friends list is private, and the Statistics
entry itself is already in the journal stream."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable, Optional

KEEPALIVE = 3600.0


def _pick(state: dict, *keys: str) -> dict:
    return {k: state.get(k) for k in keys if state.get(k) is not None}


KINDS: dict = {
    "cargo": lambda s: _pick(s, "Cargo", "CargoJSON"),
    "materials": lambda s: _pick(s, "Raw", "Manufactured", "Encoded"),
    "locker": lambda s: _pick(s, "Component", "Item", "Consumable", "Data", "BackPack", "ShipLockerJSON"),
    "loadout": lambda s: _pick(s, "Modules", "ModuleInfo"),
    "ship": lambda s: _pick(s, "ShipID", "ShipIdent", "ShipName", "ShipType", "HullValue", "ModulesValue", "Rebuy"),
    "ranks": lambda s: _pick(s, "Rank", "Progress", "Reputation", "Engineers"),
    "credits": lambda s: _pick(s, "Credits", "Loan"),
    "powerplay": lambda s: _pick(s, "Powerplay"),
    "navroute": lambda s: _pick(s, "NavRoute"),
    "suit": lambda s: _pick(s, "SuitCurrent", "SuitLoadoutCurrent", "OnFoot", "Taxi", "Dropship"),
    "game": lambda s: _pick(s, "GameVersion", "GameBuild", "GameLanguage", "Odyssey", "Horizons", "FID"),
}


def jsonable(value: Any) -> Any:
    """EDMC keeps sets and tuples in its state; JSON wants lists."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(jsonable(v) for v in value) if all(isinstance(v, (str, int, float)) for v in value) else [jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def state_hash(data: Any) -> str:
    return hashlib.sha1(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


class StateTracker:
    def __init__(self, clock: Callable[[], float] = time.monotonic, keepalive: float = KEEPALIVE):
        self._clock = clock
        self._keepalive = keepalive
        self._hashes: dict = {}
        self._sent_at: dict = {}

    def changed(self, state: dict, force: bool = False) -> list:
        """The kinds worth sending now, as (kind, data) pairs, and marks them sent."""
        out = []
        now = self._clock()
        for kind, pick in KINDS.items():
            try:
                data = jsonable(pick(state))
            except Exception:
                continue
            if not data:
                continue
            h = state_hash(data)
            due = force or self._hashes.get(kind) != h or now - self._sent_at.get(kind, -self._keepalive) >= self._keepalive
            if due:
                self._hashes[kind] = h
                self._sent_at[kind] = now
                out.append((kind, data))
        return out

    def reset(self) -> None:
        self._hashes.clear()
        self._sent_at.clear()

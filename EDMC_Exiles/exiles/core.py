"""The plugin's core: EDMC's hooks come in here and everything else hangs off it. No tkinter in this file; the UI
reads the core and drains `ui_queue` on the main thread. Network, EDMC's config and the game-running question are
injected, so the replay harness and the tests run the same code without EDMC."""
from __future__ import annotations

import base64
import hashlib
import logging
import queue
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from . import updater
from .pairing import Pairing
from .sender import Sender
from .state import StateTracker
from .status import STAMPED, WHERE_FRESH, WHERE_KEY, StatusThrottle, status_words, where_from_status

DEFAULT_SITE = "https://ops.theexiles.gg"
DEFAULT_EXDB = "https://api.exdb.theexiles.gg"
HEARTBEAT_EVERY = 60.0
HEARTBEAT_IDLE_EVERY = 300.0      # with the game closed: EDMC is open and that is all there is to say
FEED_EVERY_OPEN = 60.0
FEED_EVERY_CLOSED = 300.0
UPDATE_EVERY = 86400.0
# A hand-in shows in the pilot's tick on the site within the minute (EXDB builds a plugin pilot's tick at once since
# 2026-11-11), so with the window open the feed is read again this many seconds after one, and once more to be sure,
# rather than at the next round.
HAND_INS = frozenset({"RedeemVoucher", "MissionCompleted", "MarketSell", "SellExplorationData", "MultiSellExplorationData",
                      "SellOrganicData", "SearchAndRescue"})
FEED_SOON = (25.0, 70.0)
# "ShutDown" is EDMC's own: it makes one when the game's process goes, and one when the game drops to its main menu
# (monitor.py, on the Music cue "MainMenu"). Either way the pilot is out of the game until the next LoadGame.
SHUTDOWN_EVENTS = ("Shutdown", "ShutDown")
FORCE_STATE_ON = ("StartUp", "LoadGame")
INSTALL_ID = re.compile(r"^[a-z0-9]{8,32}$")
# What never leaves this PC: the pilot's chat, both ways, the friends list, and the music cues. The squad's database
# keeps none of them, so sending them only put private words on the wire; they were about half of all the lines a
# day of play writes.
NEVER_SENT = frozenset({"ReceiveText", "SendText", "Friends", "Music"})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Settings:
    """The plugin's settings in EDMC's config, every key prefixed exiles_. Booleans are kept as "1" and "0" strings so
    one getter serves every EDMC version. Without a config object (tests) it keeps them in memory."""
    PREFIX = "exiles_"
    DEFAULTS = {"site_url": DEFAULT_SITE, "exdb_url": DEFAULT_EXDB, "exdb_key": "", "site_token": "", "member": "",
                "update_checked": "0", "update_latest": "", "install": "", "orders_seen": "", "window": ""}

    def __init__(self, cfg: Any = None):
        self._cfg = cfg
        self._mem: dict = {}

    @property
    def in_memory(self) -> bool:
        """True without EDMC's config behind them: the tests and the drawing tools."""
        return self._cfg is None

    def get(self, key: str, default: Optional[str] = None) -> str:
        fallback = self.DEFAULTS.get(key, "") if default is None else default
        if self._cfg is None:
            return self._mem.get(key, fallback)
        try:
            value = self._cfg.get_str(self.PREFIX + key)
        except Exception:
            value = None
        return value if value else fallback

    def get_bool(self, key: str) -> bool:
        return self.get(key) in ("1", "true", "True")

    def set(self, key: str, value: Any) -> None:
        text = ("1" if value else "0") if isinstance(value, bool) else str(value if value is not None else "")
        if self._cfg is None:
            self._mem[key] = text
            return
        try:
            self._cfg.set(self.PREFIX + key, text)
        except Exception:
            pass


class ExilesPlugin:
    def __init__(self, *, plugin_dir: Path, settings: Settings, http: Callable[..., tuple], game_running: Callable[[], bool],
                 log: logging.Logger, version: str, edmc_version: str = "", clock: Callable[[], float] = time.monotonic,
                 download: Optional[Callable[..., tuple]] = None):
        """http(method, url, body=None, headers=None, timeout=20) -> (status_code, parsed_json_or_None).
        download(url, limit, timeout=60, headers=None) -> (status_code, bytes or None), for a new version's zip (0.6.0)."""
        self.plugin_dir = Path(plugin_dir)
        self.settings = settings
        self.http = http
        self.download = download
        self.game_running = game_running
        self.log = log
        self.version = version
        self.edmc_version = edmc_version
        self._clock = clock
        self.ui_queue: "queue.Queue[tuple]" = queue.Queue()
        self.sender = Sender(post=self._post_exdb, spool_dir=self.plugin_dir / "spool", log=log, notify=self._from_worker, clock=clock)
        self.sender.paused = not self.paired
        self.throttle = StatusThrottle(clock=clock)
        self.tracker = StateTracker(clock=clock)
        self.pairing: Optional[Pairing] = None
        self.feed: Optional[dict] = None
        self.feed_error: Optional[str] = None
        self.feed_refused = False                     # the site turned this plugin's token away: only a pairing mends it
        self.feed_reads = 0                           # how many times the site was asked, answered or not
        self._feed_now = False                        # Refresh was pressed, the window opened or a pairing was made
        self._feed_soon: list = []                    # clock times at which the feed is read again, after a hand-in
        self._feed_read_at: Optional[float] = None    # the clock when the site was last asked
        self.unpairing: Optional[int] = None          # the key of the paired PC being taken off the list, while it is asked
        self.unpair_error: Optional[str] = None       # why the last try to take one off failed
        self.update: Optional[dict] = None
        self.cmdr: Optional[str] = None
        self.system: Optional[str] = None
        self.station: Optional[str] = None
        self.docked: Optional[bool] = None
        self.on_foot: Optional[bool] = None
        self.body: Optional[str] = None
        self.game: dict = {}
        self.last_status: Optional[dict] = None
        self._status_at: Optional[float] = None       # the clock when last_status arrived
        self.entries_seen = 0
        # Whether the pilot is in a game session: True from LoadGame (or EDMC's StartUp), False from a shutdown entry,
        # which EDMC also makes when the game drops to its main menu. None until one of them is seen.
        self.in_session: Optional[bool] = None
        self._install: Optional[str] = None
        self.window_open = False
        self._stop = threading.Event()
        self._feed_wake = threading.Event()
        self._beat_thread: Optional[threading.Thread] = None
        self._beat_at: Optional[float] = None         # the clock at the last heartbeat queued
        self._beat_running: Optional[bool] = None     # what it said of the game
        self._feed_thread: Optional[threading.Thread] = None

    # ----- lifecycle ------------------------------------------------------------------------------------------------
    @property
    def install_id(self) -> str:
        """This install's own id, made once and kept in EDMC's config (0.2.3). The site mints a key for each install,
        so a pilot with EDMC on two PCs pairs both and neither pairing ends the other's. It names no one and no PC:
        sixteen random characters."""
        if self._install is None:
            kept = self.settings.get("install")
            if not INSTALL_ID.match(kept or ""):
                kept = secrets.token_hex(8)
                self.settings.set("install", kept)
            self._install = kept
        return self._install

    @property
    def paired(self) -> bool:
        return bool(self.settings.get("exdb_key")) and bool(self.settings.get("site_token"))

    @property
    def site_url(self) -> str:
        """The ops site. In EDMC it is the one address this plugin was built for, whatever EDMC's config holds (0.2.5:
        EDMC's Settings tab had a box for it, and an address a pilot can change is a way to break the plugin). Only
        settings kept in memory, which the tests use, name another."""
        return (self.settings.get("site_url") if self.settings.in_memory else DEFAULT_SITE).rstrip("/")

    @property
    def exdb_url(self) -> str:
        return self.settings.get("exdb_url").rstrip("/")

    def start(self) -> None:
        self.sender.start()
        self._stop.clear()
        self._beat_thread = threading.Thread(target=self._beat_loop, name="EDMC_Exiles heartbeat", daemon=True)
        self._beat_thread.start()
        self._feed_thread = threading.Thread(target=self._feed_loop, name="EDMC_Exiles feed", daemon=True)
        self._feed_thread.start()

    def stop(self) -> None:
        """EDMC is closing: a last word to EXDB (the game may still run, so only `closing`), then the sender winds up."""
        self._stop.set()
        self._feed_wake.set()
        if self.pairing:
            self.pairing.cancel()
        if self.paired:
            try:
                self._post_exdb("/plugin/heartbeat", self.heartbeat_body(closing=True), timeout=3)
            except Exception as e:
                self.log.info(f"closing heartbeat not sent: {e.__class__.__name__}")
        self.sender.stop()

    # ----- EDMC's hooks ---------------------------------------------------------------------------------------------
    def journal_entry(self, cmdr: Optional[str], is_beta: bool, system: Optional[str], station: Optional[str], entry: dict,
                      state: dict) -> None:
        if is_beta or not isinstance(entry, dict):
            return
        if cmdr:
            self.cmdr = cmdr
            self.sender.cmdr = cmdr
        self.system = state.get("SystemName") or system or self.system
        self.station = state.get("StationName") or station
        if "IsDocked" in state:
            self.docked = bool(state.get("IsDocked"))
        if "OnFoot" in state:
            self.on_foot = bool(state.get("OnFoot"))
        self.body = state.get("Body") or self.body
        for k, sk in (("version", "GameVersion"), ("build", "GameBuild"), ("odyssey", "Odyssey")):
            if state.get(sk) is not None:
                self.game[k] = state.get(sk)
        name = entry.get("event")
        self.entries_seen += 1
        # Nothing is gathered before the pilot pairs (2026-11-10): an install never paired held every line in memory
        # and wrote the session to disk at each close. What was played before the pairing reaches EXDB by Frontier.
        # A paired plugin sends everything (0.2.2: it had three switches, and what the squad sees is only whole when
        # every paired pilot sends the same). A switch an earlier version left off is not read.
        if self.paired and name not in NEVER_SENT:
            self.sender.add_event(self.stamped(entry), self.cmdr)
        if self.paired:
            for kind, data in self.tracker.changed(state, force=name in FORCE_STATE_ON):
                self.sender.add("/plugin/state", {"at": entry.get("timestamp") or utc_now_iso(), "kind": kind, "data": data},
                                replace_key=f"state:{kind}")
        if name in SHUTDOWN_EVENTS:
            self.in_session = False
            self.send_heartbeat(running=False)
            self.throttle.reset()
        elif name in FORCE_STATE_ON:
            self.in_session = True
            self.send_heartbeat(running=True)
        if name in HAND_INS and self.paired and self.window_open:
            self.feed_soon()
        self._from_worker("entry", name)

    def stamped(self, entry: dict) -> dict:
        """The entry as the game wrote it, plus where the pilot was (WHERE_KEY) for the kinds EXDB places by position
        (status.STAMPED): the latest Status position when it is at most WHERE_FRESH seconds old. A copy: EDMC hands the
        same dict to every plugin."""
        event = dict(entry)
        if entry.get("event") in STAMPED and self._status_at is not None and self._clock() - self._status_at <= WHERE_FRESH:
            where = where_from_status(self.last_status)
            if where:
                event[WHERE_KEY] = where
        return event

    def dashboard_entry(self, cmdr: Optional[str], is_beta: bool, entry: dict) -> None:
        if is_beta or not isinstance(entry, dict):
            return
        self.last_status, self._status_at = entry, self._clock()
        if self.paired and self.throttle.worth(entry):
            self.sender.add("/plugin/status", {"at": entry.get("timestamp") or utc_now_iso(), "status": dict(entry)}, replace_key="status")
        self._from_worker("status", status_words(entry))

    # ----- the heartbeat --------------------------------------------------------------------------------------------
    def in_game(self) -> bool:
        """Whether the pilot is in the game: its process is there, and they have not left for its main menu. A game left
        sitting at the menu is not a pilot in the game (0.2.3: until then the next beat said "running" again a minute
        after the menu, and the site showed the pilot in the game for as long as the PC stayed on)."""
        try:
            running = bool(self.game_running())
        except Exception:
            running = False
        return running and self.in_session is not False

    def heartbeat_body(self, running: Optional[bool] = None, closing: bool = False) -> dict:
        is_running = self.in_game() if running is None else bool(running)
        body = {"at": utc_now_iso(), "gameRunning": is_running, "cmdr": self.cmdr, "system": self.system, "station": self.station,
                "docked": self.docked, "onFoot": self.on_foot, "body": self.body,
                "plugin": {"version": self.version}, "edmc": {"version": self.edmc_version},
                "game": {"version": self.game.get("version"), "build": self.game.get("build"), "odyssey": self.game.get("odyssey")}}
        if closing:
            body["closing"] = True
        return body

    def send_heartbeat(self, running: Optional[bool] = None) -> None:
        if self.paired:
            body = self.heartbeat_body(running)
            self._beat_at, self._beat_running = self._clock(), bool(body.get("gameRunning"))
            self.sender.add("/plugin/heartbeat", body, replace_key="heartbeat")

    def beat_due(self, running: bool) -> bool:
        """A beat a minute while the game runs. With the game closed one every HEARTBEAT_IDLE_EVERY is enough (EXDB
        reads a pilot as out the moment a beat says the game is closed), and one at once when that changes."""
        if self._beat_at is None or running or running != self._beat_running:
            return True
        return self._clock() - self._beat_at >= HEARTBEAT_IDLE_EVERY

    def _beat_loop(self) -> None:
        while not self._stop.wait(HEARTBEAT_EVERY):
            try:
                running = self.in_game()
                if self.beat_due(running):
                    self.send_heartbeat(running)
            except Exception as e:
                self.log.warning(f"heartbeat: {e.__class__.__name__}: {e}")

    # ----- pairing --------------------------------------------------------------------------------------------------
    def begin_pairing(self) -> None:
        if self.pairing and self.pairing.state == "waiting":
            return
        def ask(method: str, url: str, body: Optional[dict] = None, headers: Optional[dict] = None) -> tuple:
            return self.http(method, url, body, headers={**self._ua_headers(), **(headers or {})})

        self.pairing = Pairing(http=ask, site_url=self.site_url, log=self.log, notify=self._pairing_event, install=self.install_id)
        self.pairing.start()

    def cancel_pairing(self) -> None:
        if self.pairing:
            self.pairing.cancel()

    def _pairing_event(self, kind: str, detail: Any) -> None:
        if kind == "paired" and isinstance(detail, dict):
            self.settings.set("exdb_key", detail.get("exdbKey") or "")
            self.settings.set("site_token", detail.get("siteToken") or "")
            if detail.get("exdbBaseUrl"):
                self.settings.set("exdb_url", str(detail["exdbBaseUrl"]).rstrip("/"))
            member = detail.get("member") if isinstance(detail.get("member"), dict) else {}
            self.settings.set("member", member.get("name") or "")
            self.sender.unauthorized = False
            self.sender.paused = False
            self.feed_refused, self.feed_error = False, None
            self.log.info("paired with the ops site")
            self.send_heartbeat()
            self.refresh_feed_now()
            self._from_worker("paired", member.get("name"))
            return
        self._from_worker(f"pairing_{kind}", detail)

    # ----- taking a paired PC off the list ----------------------------------------------------------------------------
    def unpair(self, key: int) -> None:
        """Take one of the pilot's paired PCs off their list (0.3.0): the site ends that PC's key and forgets its token.
        `key` is the handle the feed gave for it (the pcs list). Asked on a worker thread; the UI hears "unpaired" (with
        True when it was this PC) or "unpair_failed" (with why)."""
        if self.unpairing is not None or not self.paired:
            return
        self.unpairing, self.unpair_error = int(key), None
        threading.Thread(target=self._unpair, args=(int(key),), name="EDMC_Exiles unpair", daemon=True).start()

    def _unpair(self, key: int) -> None:
        try:
            status, answer = self.http("POST", f"{self.site_url}/api/plugin/unpair", {"key": key}, headers=self._site_headers())
        except Exception as e:
            self._unpair_failed(f"the site did not answer ({e.__class__.__name__})")
            return
        if status == 200 and isinstance(answer, dict) and answer.get("ok"):
            mine = bool(answer.get("self"))
            if mine:
                self.forget_pairing()
            self.unpairing = None
            self.log.info("a paired PC was taken off the list" + (": this one, which is no longer paired" if mine else ""))
            self._from_worker("unpaired", mine)
            self.refresh_feed_now()
            return
        said = answer.get("error") if isinstance(answer, dict) else None
        self._unpair_failed(str(said).rstrip(".") if said else f"the site answered HTTP {status}")

    def _unpair_failed(self, why: str) -> None:
        self.unpairing, self.unpair_error = None, why
        self.log.info(f"taking a paired PC off the list failed: {why}")
        self._from_worker("unpair_failed", why)

    def forget_pairing(self) -> None:
        """This PC is no longer paired: its key and its token leave EDMC's config, and nothing more is gathered or sent
        until the pilot pairs again. The install's own id stays, so a new pairing is this PC's again."""
        for name in ("exdb_key", "site_token", "member"):
            self.settings.set(name, "")
        self.sender.paused = True
        self.sender.unauthorized = False
        self.feed, self.feed_error, self.feed_refused = None, None, False

    # ----- the orders, read or new -----------------------------------------------------------------------------------
    def orders_mark(self) -> str:
        """What tells one set of orders from the next: their words. Empty while there are none."""
        orders = self.feed.get("orders") if isinstance(self.feed, dict) else None
        words = (orders.get("md") or orders.get("text")) if isinstance(orders, dict) else None
        return hashlib.sha1(str(words).encode("utf-8")).hexdigest()[:16] if words else ""

    @property
    def orders_new(self) -> bool:
        """The orders have changed since the pilot last had them open (0.3.0: the strip says so)."""
        mark = self.orders_mark()
        return bool(mark) and mark != self.settings.get("orders_seen")

    def orders_read(self) -> None:
        """The pilot has the orders in front of them: the strip's mark goes until the orders change again."""
        mark = self.orders_mark()
        if mark and mark != self.settings.get("orders_seen"):
            self.settings.set("orders_seen", mark)
            self._from_worker("orders", None)

    # ----- the site's feed ------------------------------------------------------------------------------------------
    def refresh_feed_now(self) -> None:
        self._feed_now = True
        self._feed_wake.set()

    def feed_soon(self) -> None:
        """Read the feed again shortly (FEED_SOON): the pilot just handed something in and the window is open."""
        now = self._clock()
        self._feed_soon = [now + s for s in FEED_SOON]
        self._feed_wake.set()

    def _feed_round(self) -> float:
        return FEED_EVERY_OPEN if self.window_open else FEED_EVERY_CLOSED

    def _feed_wait(self) -> float:
        """How long the feed loop sleeps: what is left of the round, or less when a read was asked for soon."""
        now = self._clock()
        wait = self._feed_round()
        if self._feed_read_at is not None:
            wait -= now - self._feed_read_at
        soon = list(self._feed_soon)
        if soon:
            wait = min(wait, min(soon) - now)
        return max(0.5, wait)

    def _feed_due(self) -> bool:
        """Whether the site is asked now: a read was asked for at once, one asked for soon has come due, or the round
        is over. What it answers for is cleared."""
        now = self._clock()
        asked, self._feed_now = self._feed_now, False
        soon = list(self._feed_soon)
        due = [t for t in soon if t <= now]
        if due:
            self._feed_soon = [t for t in soon if t > now]
        over = self._feed_read_at is None or now - self._feed_read_at >= self._feed_round() - 0.5
        return asked or bool(due) or over

    def _feed_loop(self) -> None:
        self._read_feed()
        self._check_update()
        while not self._stop.is_set():
            self._feed_wake.wait(self._feed_wait())
            self._feed_wake.clear()
            if self._stop.is_set():
                return
            if not self._feed_due():
                continue
            self._read_feed()
            self._check_update()

    def _read_feed(self) -> None:
        self._feed_read_at = self._clock()
        if not self.paired:
            return
        try:
            status, answer = self.http("GET", f"{self.site_url}/api/plugin/feed", None, headers=self._site_headers())
        except Exception as e:
            self.feed_error = f"the site did not answer ({e.__class__.__name__})"
            self.feed_reads += 1
            self._from_worker("feed", None)
            return
        if status == 200 and isinstance(answer, dict):
            self.feed = answer
            self.feed_error = None
            self.feed_refused = False
        elif status in (401, 403):
            self.feed_error = "the site no longer knows this plugin. Pair again."
            self.feed_refused = True
        else:
            self.feed_error = f"the site answered HTTP {status}"
        self.feed_reads += 1
        self._from_worker("feed", None)

    # ----- updates --------------------------------------------------------------------------------------------------
    def _check_update(self) -> None:
        try:
            last = float(self.settings.get("update_checked") or 0)
        except ValueError:
            last = 0.0
        if time.time() - last < UPDATE_EVERY and self.update is not None:
            return
        try:
            status, answer = self.http("GET", f"{self.site_url}/plugin/version.json", None, headers=self._ua_headers())
        except Exception:
            return
        if status == 200 and isinstance(answer, dict) and answer.get("version"):
            self.settings.set("update_checked", str(int(time.time())))
            latest = str(answer["version"])
            self.update = {"latest": latest, "url": answer.get("downloadUrl") or answer.get("url"), "notes": answer.get("notes"),
                           "newer": version_tuple(latest) > version_tuple(self.version), "installed": False, "trouble": None}
            if self.update["newer"]:
                self._put_update_in_place(answer)
            self._from_worker("update", self.update)

    def _put_update_in_place(self, answer: dict) -> None:
        """The newer version's zip fetched, checked (updater.py says how) and written over this plugin, to start the next
        time EDMC starts (0.6.0). Whatever stops it is kept in update["trouble"] for Copy details, and the Settings page
        keeps its button for an update by hand."""
        u = self.update
        try:
            on_disk = (self.plugin_dir / updater.LAST).read_text(encoding="utf-8").strip()
        except OSError:
            on_disk = None
        if on_disk == u["latest"]:
            u["installed"] = True          # put in place earlier, waiting for EDMC to start again
            return
        url, signature = answer.get("downloadUrl"), answer.get("signature")
        if not isinstance(url, str) or not isinstance(signature, str) or self.download is None:
            u["trouble"] = "the site names no signed zip"
            return
        if not (url.startswith("https://") or (self.settings.in_memory and url.startswith("http://127.0.0.1"))):
            u["trouble"] = "the zip's address is not https"
            return
        # the site hands its zips only to a paired plugin, by its token; the token never goes to any other address
        headers = self._site_headers() if url.startswith(self.site_url + "/") else {}
        try:
            status, data = self.download(url, limit=updater.MOST_BYTES, timeout=60, headers=headers)
        except Exception as e:
            u["trouble"] = f"the download did not answer ({e.__class__.__name__})"
            return
        if status != 200 or not data:
            u["trouble"] = f"the download answered HTTP {status}" if status != 200 else "the download was empty or too big"
            return
        want = str(answer.get("sha256") or "").lower()
        if want and hashlib.sha256(data).hexdigest() != want:
            u["trouble"] = "the zip is not the one the site named"
            return
        try:
            sig = base64.b64decode(signature, validate=True)
        except ValueError:
            sig = b""
        try:
            if not updater.signed(data, sig):
                raise ValueError("the zip is not signed with the squad's release key")
            updater.put_in_place(self.plugin_dir, updater.unpack(data, u["latest"]))
        except Exception as e:
            u["trouble"] = str(e) if isinstance(e, ValueError) else f"it could not be written ({e.__class__.__name__})"
            self.log.warning(f"version {u['latest']} not put in place: {u['trouble']}")
            return
        u["installed"] = True
        self.log.info(f"version {u['latest']} put in place; it starts the next time EDMC starts")

    # ----- network --------------------------------------------------------------------------------------------------
    def _ua_headers(self) -> dict:
        return {"X-Exiles-Plugin": self.version}

    def _site_headers(self) -> dict:
        token = self.settings.get("site_token")
        return {**self._ua_headers(), **({"Authorization": f"Bearer {token}"} if token else {})}

    def _post_exdb(self, path: str, body: dict, timeout: float = 20) -> tuple:
        key = self.settings.get("exdb_key")
        if not key:
            raise RuntimeError("not paired")
        return self.http("POST", self.exdb_url + path, body, headers={**self._ua_headers(), "apikey": key}, timeout=timeout)

    # ----- for the UI (what it says is in words.py) -------------------------------------------------------------------
    def _from_worker(self, kind: str, detail: Any) -> None:
        self.ui_queue.put((kind, detail))


def version_tuple(text: str) -> tuple:
    out = []
    for part in str(text).strip().split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def show_code(code: str) -> str:
    code = str(code).replace("-", "")
    return f"{code[:3]}-{code[3:]}" if len(code) == 6 else code

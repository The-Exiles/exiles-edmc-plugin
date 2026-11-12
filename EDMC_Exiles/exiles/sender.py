"""The sender: everything EDMC_Exiles sends to EXDB goes through one worker thread here, so EDMC's main thread never
waits on the network. Journal entries are batched (up to EVENTS_BATCH_MAX, every EVENTS_FLUSH_EVERY seconds or once
EVENTS_FLUSH_AT wait), one commander a batch; status, state and heartbeats are posted as they come, the newest of a
kind replacing an older one still waiting. What cannot be sent is spooled to disk under spool/ and retried oldest
first with a growing wait. A batch the server refuses for good (REFUSED) is halved until the one entry it will not
take is found; that entry is set aside under spool/refused/ on this PC and the rest goes through, so one bad line
can never hold the feed. The key is never written to a log line."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

EVENTS_BATCH_MAX = 200
EVENTS_FLUSH_AT = 50
EVENTS_FLUSH_EVERY = 10.0
RETRY_WAITS = (10, 30, 60, 120, 300)      # seconds between tries after a failure, then every 300
SPOOL_MAX_FILES = 2000                     # about a week of busy play; beyond it the oldest batch is dropped
REFUSED = (400, 413, 422)                  # the server will never take this body as it stands: no use trying it again
REFUSED_KEEP = 50                          # refused entries kept on this PC for a look; beyond it the oldest goes
TICK = 1.0


class Sender:
    """One worker thread, one requests session. `post` is injected (a callable taking path and body, returning
    (status_code, parsed_json_or_None)) so the replay harness can run the sender without a network."""

    def __init__(self, post: Callable[[str, dict], tuple], spool_dir: Path, log: logging.Logger,
                 notify: Optional[Callable[[str, Any], None]] = None, clock: Callable[[], float] = time.monotonic):
        self._post = post
        self._spool_dir = Path(spool_dir)
        self._log = log
        self._notify = notify or (lambda kind, detail: None)
        self._clock = clock
        self._events: list = []               # (commander, entry): a batch never mixes two commanders
        self._items: deque = deque()          # (path, body, replace_key)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_flush = clock()
        self._retry_at = 0.0
        self._fails = 0
        self.cmdr: Optional[str] = None
        self.paused = False                   # no key yet: everything waits in memory and on disk
        self.counts = {"events": 0, "batches": 0, "status": 0, "state": 0, "heartbeats": 0, "spooled": 0, "errors": 0,
                       "skipped": 0, "refused": 0}
        self.last_error: Optional[str] = None
        self.last_sent_at: Optional[float] = None   # time.time() of the last 200
        self.unauthorized = False

    # ----- the main thread's side -------------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="EDMC_Exiles sender", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 4.0) -> None:
        """EDMC is closing: one quick try at what is waiting, the rest to the spool, then the thread ends."""
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)
        with self._lock:
            left = self._events
            self._events = []
        run: list = []
        for cmdr, entry in left:           # one file a commander, in the order the lines came
            if run and run[0][0] != cmdr:
                self._spool(run[0][0], [e for _, e in run])
                run = []
            run.append((cmdr, entry))
        if run:
            self._spool(run[0][0], [e for _, e in run])

    def add_event(self, entry: dict, cmdr: Optional[str] = None) -> None:
        """A journal entry, with the commander it belongs to (the one flying when it was written)."""
        with self._lock:
            self._events.append((cmdr or self.cmdr, entry))
            many = len(self._events) >= EVENTS_FLUSH_AT
        if many:
            self._wake.set()

    def add(self, path: str, body: dict, replace_key: Optional[str] = None) -> None:
        """A status, state or heartbeat post. With a replace_key, an older waiting item with the same key is dropped:
        only the newest status or heartbeat is worth sending."""
        with self._lock:
            if replace_key:
                self._items = deque(i for i in self._items if i[2] != replace_key)
            self._items.append((path, body, replace_key))
        self._wake.set()

    def waiting(self) -> int:
        with self._lock:
            return len(self._events) + len(self._items)

    def spooled(self) -> int:
        try:
            return sum(1 for _ in self._spool_dir.glob("*.json"))
        except OSError:
            return 0

    # ----- the worker ---------------------------------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(TICK)
            self._wake.clear()
            try:
                self._work()
            except Exception as e:   # the worker must never die
                self._log.warning(f"sender: {e.__class__.__name__}: {e}")
        try:
            self._work(final=True)
        except Exception as e:
            self._log.warning(f"sender (closing): {e.__class__.__name__}: {e}")

    def _work(self, final: bool = False) -> None:
        if self.paused:
            return
        now = self._clock()
        if now < self._retry_at and not final:
            return
        # journal entries first, oldest first: the spool before the buffer
        if not self._retry_spool():
            return
        while True:
            with self._lock:
                due = self._events and (final or len(self._events) >= EVENTS_FLUSH_AT or now - self._last_flush >= EVENTS_FLUSH_EVERY)
                if not due:
                    break
                cmdr = self._events[0][0]
                n = 0
                while n < min(len(self._events), EVENTS_BATCH_MAX) and self._events[n][0] == cmdr:
                    n += 1
                batch = [e for _, e in self._events[:n]]
                del self._events[:n]
                rest = bool(self._events)   # another commander's lines, or more than one batch holds: they go now too
                if not rest:
                    self._last_flush = now
            if not self._deliver(cmdr, batch):
                self._spool(cmdr, batch)
                return
            if not rest:
                break
        # then the rest, in order
        while True:
            with self._lock:
                if not self._items:
                    break
                path, body, key = self._items.popleft()
            ok, status, answer = self._try(path, body)
            if ok:
                self._count(path)
            elif status in (400, 404, 413, 422):
                self._log.warning(f"sender: {path} refused with {status}, dropped")
            elif key in ("status", "heartbeat"):
                pass   # a moment that has passed: the next one says more
            else:
                with self._lock:
                    self._items.appendleft((path, body, key))
                return

    def _count(self, path: str) -> None:
        if path.endswith("/status"):
            self.counts["status"] += 1
        elif path.endswith("/state"):
            self.counts["state"] += 1
        elif path.endswith("/heartbeat"):
            self.counts["heartbeats"] += 1

    def _deliver(self, cmdr: Optional[str], batch: list) -> bool:
        """Send one commander's batch. True when it is off our hands: taken, or refused for good with the entry the
        server will not take set aside. False when it could not be sent now and must wait (the caller spools it; an
        entry sent twice is no harm, EXDB skips what it has)."""
        ok, status, answer = self._try("/plugin/events", {"cmdr": cmdr, "events": batch})
        if ok:
            self.counts["events"] += len(batch)
            self.counts["batches"] += 1
            if isinstance(answer, dict):
                self.counts["skipped"] += int(answer.get("skipped") or 0)
            self._notify("sent", len(batch))
            return True
        if status in REFUSED:
            if len(batch) <= 1:
                self._set_aside(cmdr, batch[0] if batch else {}, status, answer)
                return True
            half = len(batch) // 2
            return self._deliver(cmdr, batch[:half]) and self._deliver(cmdr, batch[half:])
        return False

    def _set_aside(self, cmdr: Optional[str], entry: dict, status: int, answer: Any) -> None:
        """One entry the server refuses for good: kept on this PC under spool/refused/ with the server's words, and
        the feed moves on."""
        self.counts["refused"] += 1
        detail = answer.get("detail") if isinstance(answer, dict) else None
        self._log.warning(f"sender: the server refused one {entry.get('event')} entry (HTTP {status}"
                          + (f": {str(detail)[:120]}" if detail else "") + "); set aside in spool/refused")
        try:
            folder = self._spool_dir / "refused"
            folder.mkdir(parents=True, exist_ok=True)
            files = sorted(folder.glob("*.json"))
            while len(files) >= REFUSED_KEEP:
                files.pop(0).unlink(missing_ok=True)
            name = f"{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}-{uuid.uuid4().hex[:8]}.json"
            (folder / name).write_text(json.dumps({"cmdr": cmdr, "status": status, "answer": answer, "entry": entry}, default=str),
                                       encoding="utf-8")
        except OSError as e:
            self._log.warning(f"sender: could not keep the refused entry: {e}")
        self._notify("refused", entry.get("event"))

    def _try(self, path: str, body: dict) -> tuple:
        """One attempt. Returns (ok, status_code_or_None, answer). Sets the retry wait on any failure."""
        try:
            status, answer = self._post(path, body)
        except Exception as e:
            self._failed(f"{e.__class__.__name__}: {str(e)[:120]}")
            return False, None, None
        if 200 <= status < 300:
            self._fails = 0
            self._retry_at = 0.0
            self.last_error = None
            self.last_sent_at = time.time()
            if self.unauthorized:
                self.unauthorized = False
                self._notify("authorized", None)
            return True, status, answer
        if status == 401 or status == 403:
            self.unauthorized = True
            self._failed(f"HTTP {status}: the key is not accepted")
            self._notify("unauthorized", status)
            self._retry_at = self._clock() + RETRY_WAITS[-1]
            return False, status, answer
        if status == 429:
            self._failed("HTTP 429: asked to slow down")
            return False, status, answer
        detail = answer.get("detail") if isinstance(answer, dict) else None
        if status in REFUSED:
            # the server is there and said no to this body: no wait before the next try, the caller decides what goes
            self.counts["errors"] += 1
            self.last_error = f"HTTP {status}" + (f": {str(detail)[:120]}" if detail else "")
            return False, status, answer
        self._failed(f"HTTP {status}" + (f": {str(detail)[:120]}" if detail else ""))
        return False, status, answer

    def _failed(self, why: str) -> None:
        self.counts["errors"] += 1
        self.last_error = why
        wait = RETRY_WAITS[min(self._fails, len(RETRY_WAITS) - 1)]
        self._fails += 1
        self._retry_at = self._clock() + wait
        self._log.info(f"sender: {why}; next try in {wait} s")
        self._notify("error", why)

    # ----- the spool ----------------------------------------------------------------------------------------------
    def _spool(self, cmdr: Optional[str], batch: list) -> None:
        try:
            self._spool_dir.mkdir(parents=True, exist_ok=True)
            files = sorted(self._spool_dir.glob("*.json"))
            while len(files) >= SPOOL_MAX_FILES:
                files.pop(0).unlink(missing_ok=True)
                self._log.warning("sender: the spool is full, the oldest batch was dropped")
            name = f"{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}-{uuid.uuid4().hex[:8]}.json"
            tmp = self._spool_dir / (name + ".tmp")
            tmp.write_text(json.dumps({"cmdr": cmdr, "events": batch}), encoding="utf-8")
            tmp.replace(self._spool_dir / name)
            self.counts["spooled"] += len(batch)
            self._notify("spooled", len(batch))
        except OSError as e:
            self._log.warning(f"sender: could not spool {len(batch)} entries: {e}")

    def _retry_spool(self) -> bool:
        """Send spooled batches oldest first; stop at the first failure. True when the spool is empty (or was)."""
        try:
            files = sorted(self._spool_dir.glob("*.json"))
        except OSError:
            return True
        for f in files:
            try:
                saved = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                f.unlink(missing_ok=True)
                continue
            body = {"cmdr": saved.get("cmdr") or self.cmdr, "events": saved.get("events") or []}
            if not body["events"]:
                f.unlink(missing_ok=True)
                continue
            if not self._deliver(body["cmdr"], body["events"]):
                return False
            f.unlink(missing_ok=True)
        return True

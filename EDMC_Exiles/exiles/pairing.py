"""Pairing by code, so nobody copies a key: the plugin asks the ops site for a code, the pilot types it on Cmdr >
Settings while signed in, and the plugin polls until the site hands back the EXDB key and the site token. One worker
thread; results reach the plugin through `notify(kind, detail)` on that thread (the UI marshals to the main thread).
Kinds: code {code, expiresAt}, paired {exdbKey, exdbBaseUrl, siteToken, member}, expired, failed <reason>.

The plugin names its install when it asks for the code (0.2.3), so the site mints a key for this install alone: a pilot
with EDMC on two PCs pairs both, and pairing one never ends the other's.

A pairing is collected only by the plugin that asked for it (0.5.1). The code stands on the pilot's screen, where a
stream, a screenshot or a shared screen shows it to others, so the code alone must not be enough to fetch the key. The
plugin makes a secret for each pairing, sends it when it asks for the code and again with every ask after it
(SECRET_HEADER), and the site hands the key to nobody who comes without it. The secret is never shown, kept or
logged: it lives as long as the ask does."""
from __future__ import annotations

import logging
import secrets
import threading
import time
from typing import Any, Callable, Optional

POLL_EVERY = 5.0
WAIT_AT_MOST = 600.0
SECRET_HEADER = "X-Exiles-Pair"


class Pairing:
    def __init__(self, http: Callable[..., tuple], site_url: str, log: logging.Logger, notify: Callable[[str, Any], None],
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 install: Optional[str] = None):
        """http(method, url, body=None, headers=None) -> (status_code, parsed_json_or_None)."""
        self._http = http
        self._install = install
        self._site = site_url.rstrip("/")
        self._log = log
        self._notify = notify
        self._clock = clock
        self._sleep = sleep
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.code: Optional[str] = None
        self.expires_at: Optional[str] = None
        self.state = "idle"          # idle, waiting, paired, expired, failed

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.state = "waiting"
        self.code = None
        self._thread = threading.Thread(target=self._run, name="EDMC_Exiles pairing", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._stop.set()
        if self.state == "waiting":
            self.state = "idle"

    def _run(self) -> None:
        secret = secrets.token_urlsafe(32)      # this pairing's own, 43 characters; a new one every time
        asked = {"secret": secret}
        if self._install:
            asked["install"] = self._install
        try:
            status, answer = self._http("POST", f"{self._site}/api/plugin/pair/start", asked)
        except Exception as e:
            self._fail(f"the site did not answer ({e.__class__.__name__})")
            return
        if status != 200 or not isinstance(answer, dict) or not answer.get("code"):
            self._fail(f"the site would not give a code (HTTP {status})")
            return
        self.code = str(answer["code"])
        self.expires_at = answer.get("expiresAt")
        every = float(answer.get("pollEvery") or POLL_EVERY)
        self._notify("code", {"code": self.code, "expiresAt": self.expires_at})
        started = self._clock()
        while not self._stop.is_set() and self._clock() - started < WAIT_AT_MOST:
            self._sleep(every)
            if self._stop.is_set():
                return
            try:
                status, answer = self._http("GET", f"{self._site}/api/plugin/pair/{self.code}", None, {SECRET_HEADER: secret})
            except Exception as e:
                self._log.info(f"pairing: poll failed ({e.__class__.__name__}), trying again")
                continue
            if status == 202:
                continue
            if status == 200 and isinstance(answer, dict) and answer.get("exdbKey") and answer.get("siteToken"):
                self.state = "paired"
                self._notify("paired", answer)
                return
            if status == 410:
                self.state = "expired"
                self._notify("expired", None)
                return
            self._log.info(f"pairing: unexpected answer HTTP {status}, trying again")
        if not self._stop.is_set():
            self.state = "expired"
            self._notify("expired", None)

    def _fail(self, why: str) -> None:
        self.state = "failed"
        self._log.info(f"pairing: {why}")
        self._notify("failed", why)

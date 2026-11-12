"""The one network primitive the core uses: http(method, url, body=None, headers=None, timeout=20) returning
(status_code, parsed_json_or_None). Built on EDMC's requests session when EDMC provides one (timeout_session), else
a plain requests session. Nothing here logs headers."""
from __future__ import annotations

from typing import Any, Callable, Optional


def make_http(user_agent: str, session: Any = None) -> Callable[..., tuple]:
    if session is None:
        import requests
        session = requests.Session()

    def http(method: str, url: str, body: Optional[dict] = None, headers: Optional[dict] = None, timeout: float = 20) -> tuple:
        sent = {"User-Agent": user_agent, **(headers or {})}
        r = session.request(method.upper(), url, json=body if body is not None else None, headers=sent, timeout=timeout)
        try:
            data = r.json()
        except ValueError:
            data = None
        return r.status_code, data

    return http

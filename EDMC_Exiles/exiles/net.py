"""The network primitives the core uses: http(method, url, body=None, headers=None, timeout=20) returning
(status_code, parsed_json_or_None), and download(url, limit, timeout=60, headers=None) returning (status_code, bytes or
None) for a new version's zip. Built on EDMC's requests session when EDMC provides one (timeout_session), else a plain requests
session. Nothing here logs headers."""
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


def make_download(user_agent: str, session: Any = None) -> Callable[..., tuple]:
    """A file of at most `limit` bytes, or None in its place when it is bigger (0.6.0). Redirects are followed, and
    requests leaves an Authorization header behind on a redirect to another host."""
    if session is None:
        import requests
        session = requests.Session()

    def download(url: str, limit: int, timeout: float = 60, headers: Optional[dict] = None) -> tuple:
        sent = {"User-Agent": user_agent, **(headers or {})}
        r = session.get(url, headers=sent, timeout=timeout, stream=True, allow_redirects=True)
        try:
            if r.status_code != 200:
                return r.status_code, None
            out = bytearray()
            for chunk in r.iter_content(65536):
                out += chunk
                if len(out) > limit:
                    return r.status_code, None
            return r.status_code, bytes(out)
        finally:
            r.close()

    return download

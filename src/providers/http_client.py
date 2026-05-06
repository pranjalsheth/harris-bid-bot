from __future__ import annotations

import time
from typing import Any

import requests

DEFAULT_TIMEOUT = 30
USER_AGENT = "HarrisBidBot/1.0 (real estate investment dashboard; contact owner)"


class ProviderError(RuntimeError):
    pass


def get_json(url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT, retries: int = 2) -> Any:
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, headers=hdrs, timeout=timeout)
            if resp.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise ProviderError(f"GET {url} failed: {exc}") from exc
    raise ProviderError(f"GET {url} failed: {last_exc}")


def post_json(url: str, *, params: dict[str, Any] | None = None, json_body: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT, retries: int = 2) -> Any:
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(url, params=params, json=json_body, headers=hdrs, timeout=timeout)
            if resp.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise ProviderError(f"POST {url} failed: {exc}") from exc
    raise ProviderError(f"POST {url} failed: {last_exc}")

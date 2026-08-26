"""Shared HTTP with retries. Stdlib only."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


class HttpError(RuntimeError):
    pass


def _request(url: str, headers: dict[str, str], body: dict[str, Any], timeout: int, retries: int) -> bytes:
    data = json.dumps(body).encode("utf-8")
    headers = {"content-type": "application/json", **headers}
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            last = HttpError(f"HTTP {e.code} from {url}: {detail}")
            if e.code not in (408, 409, 429, 500, 502, 503, 504):
                raise last from e
        except (urllib.error.URLError, TimeoutError) as e:
            last = HttpError(f"network error calling {url}: {e}")
        if attempt < retries:
            time.sleep(2 ** attempt)
    raise last  # type: ignore[misc]


def post_json(url: str, headers: dict[str, str], body: dict[str, Any], timeout: int, retries: int) -> dict[str, Any]:
    return json.loads(_request(url, headers, body, timeout, retries).decode("utf-8"))


def post_bytes(url: str, headers: dict[str, str], body: dict[str, Any], timeout: int, retries: int) -> bytes:
    """For endpoints that hand back audio rather than JSON."""
    return _request(url, headers, body, timeout, retries)

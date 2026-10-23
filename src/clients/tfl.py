"""Shared HTTP client for the TfL Unified API.

Every collector, the Streamlit app, the MCP server and the Kafka producers
call TfL through this module, so retries, backoff, timeouts and secret
handling are implemented and tested once.

Example:
    client = TflClient.from_environment()
    response = client.get_json(
        "/Line/Mode/tube,dlr/Status", {"detail": "false"}
    )
    lines = response.data
"""

import gzip
import http.client
import json
import logging
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("tfl.client")

DEFAULT_BASE_URL = "https://api.tfl.gov.uk"
USER_AGENT = "Mozilla/5.0 TfLTransportDataProject/1.0"

# Temporary failures: the same request may succeed if repeated.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Network-level failures that are also temporary. HTTPError is handled
# separately because its status code decides whether to retry.
RETRYABLE_EXCEPTIONS = (
    urllib.error.URLError,
    TimeoutError,
    ConnectionError,
    http.client.HTTPException,
)


class TflError(Exception):
    """Base class for TfL client errors. Messages never contain the API key."""


class TflHttpError(TflError):
    """TfL answered with a non-success HTTP status."""

    def __init__(self, status, path, attempts):
        self.status = status
        self.path = path
        self.attempts = attempts
        super().__init__(
            f"TfL returned HTTP {status} for {path} "
            f"after {attempts} attempt(s)"
        )


class TflRequestError(TflError):
    """The request failed at network level on every attempt."""

    def __init__(self, error_type, path, attempts):
        self.error_type = error_type
        self.path = path
        self.attempts = attempts
        super().__init__(
            f"Request to {path} failed ({error_type}) "
            f"after {attempts} attempt(s)"
        )


class TflResponseError(TflError):
    """TfL answered 200 but the body was not valid JSON."""


@dataclass(frozen=True)
class TflResponse:
    """A successful response plus the metadata collectors need.

    `params` excludes the API key, so a TflResponse is always safe to log
    or store (for example in a coverage record).
    """

    path: str
    params: dict
    status: int
    data: object
    fetched_at_utc: str
    latency_ms: int
    attempts: int
    size_bytes: int
    cache_max_age: int | None = None
    headers: dict = field(default_factory=dict, repr=False)


class TflClient:
    def __init__(
        self,
        api_key,
        base_url=DEFAULT_BASE_URL,
        timeout=30,
        max_attempts=4,
        backoff_base=1.0,
        backoff_max=30.0,
        opener=urllib.request.urlopen,
        sleep=time.sleep,
        rng=random.random,
    ):
        """
        opener, sleep and rng are injectable so tests can simulate failures
        and backoff without a network or real waiting.
        """
        if not api_key:
            raise ValueError("A TfL API key is required.")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")

        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self._opener = opener
        self._sleep = sleep
        self._rng = rng

    def __repr__(self):
        # Never include the key, even by accident in a debugger or log.
        return f"TflClient(base_url={self.base_url!r}, api_key=<hidden>)"

    @classmethod
    def from_environment(cls, **kwargs):
        """Build a client using the project's standard key lookup."""
        from src.clients.secrets import get_tfl_api_key

        base_url = os.getenv("TFL_API_BASE_URL", DEFAULT_BASE_URL)
        return cls(get_tfl_api_key(), base_url=base_url, **kwargs)

    def get_json(self, path, params=None):
        """GET a TfL path and return a TflResponse with parsed JSON.

        `path` must start with "/" and have its segments already
        URL-encoded where needed (e.g. "/Line/tube,dlr/Status").
        Raises TflHttpError, TflRequestError or TflResponseError.
        """
        if not path.startswith("/"):
            raise ValueError("path must start with '/'")

        params = dict(params or {})
        if "app_key" in params:
            raise ValueError("Pass the API key to TflClient, not in params.")

        request = urllib.request.Request(
            self._url(path, params),
            # gzip: the network-wide bus feed is 84 MB plain but a fraction of that compressed,
            # and arrives in about a minute instead of many (measured 2026-10-23).
            headers={"Accept": "application/json", "Accept-Encoding": "gzip",
                     "User-Agent": USER_AGENT},
            method="GET",
        )

        started = time.monotonic()
        fetched_at = datetime.now(timezone.utc).isoformat()

        for attempt in range(1, self.max_attempts + 1):
            retry_after = None

            try:
                # Read the body inside the try: TfL can reset the
                # connection mid-download, not only when connecting.
                with self._opener(request, timeout=self.timeout) as response:
                    body = response.read()
                    status = response.status
                    headers = dict(response.headers.items())
                if {k.lower(): v for k, v in headers.items()}.get("content-encoding", "").lower() == "gzip":
                    body = gzip.decompress(body)

                return self._parse(
                    path, params, status, body, headers,
                    fetched_at, started, attempt,
                )

            except urllib.error.HTTPError as exc:
                if exc.code not in RETRYABLE_STATUS:
                    raise TflHttpError(exc.code, path, attempt) from None
                if attempt == self.max_attempts:
                    raise TflHttpError(exc.code, path, attempt) from None
                retry_after = _parse_retry_after(exc.headers)
                reason = f"HTTP {exc.code}"

            except RETRYABLE_EXCEPTIONS as exc:
                if attempt == self.max_attempts:
                    raise TflRequestError(
                        type(exc).__name__, path, attempt
                    ) from None
                reason = type(exc).__name__

            delay = self._backoff_delay(attempt, retry_after)
            logger.warning(
                "TfL request %s failed (%s), attempt %d/%d; retrying in %.1f s",
                path, reason, attempt, self.max_attempts, delay,
            )
            self._sleep(delay)

        raise AssertionError("unreachable")  # pragma: no cover

    def _url(self, path, params):
        query = urllib.parse.urlencode({**params, "app_key": self._api_key})
        return f"{self.base_url}{path}?{query}"

    def _backoff_delay(self, attempt, retry_after):
        """Exponential backoff with jitter, capped at backoff_max.

        attempt 1 -> up to 1 s, 2 -> up to 2 s, 3 -> up to 4 s, ...
        "Equal jitter": half the delay is fixed, half random, so retries
        from many clients spread out but never retry instantly.
        A server-provided Retry-After takes precedence.
        """
        if retry_after is not None:
            return min(retry_after, self.backoff_max)

        delay = min(self.backoff_max, self.backoff_base * 2 ** (attempt - 1))
        return delay / 2 + self._rng() * delay / 2

    def _parse(self, path, params, status, body, headers,
               fetched_at, started, attempts):
        try:
            data = json.loads(body)
        except ValueError:
            raise TflResponseError(
                f"TfL returned invalid JSON for {path} (HTTP {status})"
            ) from None

        return TflResponse(
            path=path,
            params=params,
            status=status,
            data=data,
            fetched_at_utc=fetched_at,
            latency_ms=round((time.monotonic() - started) * 1000),
            attempts=attempts,
            size_bytes=len(body),
            cache_max_age=_parse_max_age(headers),
            headers=headers,
        )


def _parse_retry_after(headers):
    """Seconds from a Retry-After header, or None (HTTP-date form ignored)."""
    value = (headers or {}).get("Retry-After")
    if value and str(value).strip().isdigit():
        return float(value)
    return None


def _parse_max_age(headers):
    lowered = {k.lower(): v for k, v in (headers or {}).items()}
    match = re.search(r"max-age=(\d+)", lowered.get("cache-control", ""))
    return int(match.group(1)) if match else None

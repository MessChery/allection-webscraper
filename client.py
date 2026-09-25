"""
client.py
---------
Reusable async HTTP client for the Allection web scraping microservice.

Features
--------
- Single persistent curl_cffi.requests.AsyncSession (connection pool reuse,
  Chrome TLS / JA3 / HTTP2 fingerprint impersonation)
- Per-domain rate limiting  (≥ 2 s between requests to the same host)
- Exponential backoff + random jitter retry (max 3 retries) on
  network timeouts and 5xx HTTP errors
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.errors import RequestsError
from curl_cffi.requests.exceptions import HTTPError, Timeout

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IMPERSONATE_TARGET: str = "chrome120"

# Realistic desktop Chrome headers — supplemented by curl_cffi's built-in
# browser impersonation headers to pass enterprise anti-bot checks.
BROWSER_HEADERS: dict[str, str] = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
}

RATE_LIMIT_SECONDS: float = 2.0   # minimum gap between requests per domain
MAX_RETRIES: int = 3
BACKOFF_BASE: float = 1.5         # seconds — exponential base
BACKOFF_MAX: float = 30.0         # seconds — cap on any single delay
JITTER_RANGE: float = 0.5         # ± seconds of random jitter added to delay
REQUEST_TIMEOUT: float = 15.0     # seconds before curl_cffi raises Timeout


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------

def with_retries(
    max_retries: int = MAX_RETRIES,
    backoff_base: float = BACKOFF_BASE,
    backoff_max: float = BACKOFF_MAX,
    jitter_range: float = JITTER_RANGE,
) -> Callable:
    """
    Decorator factory that wraps an async method with exponential backoff
    retry logic.

    Retries are triggered by:
    - ``Timeout``                  — any flavour of network timeout
    - ``HTTPError`` / ``RequestsError`` — when the response status is 5xx
      or a timeout curl error code (28) occurs

    Parameters
    ----------
    max_retries:  Maximum number of retry attempts (default: 3).
    backoff_base: Initial delay in seconds, doubled on each attempt.
    backoff_max:  Upper ceiling on the computed delay.
    jitter_range: A random value in [-jitter_range, +jitter_range] is added
                  to each delay to prevent retry thundering-herd.
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Exception | None = None

            for attempt in range(max_retries + 1):  # attempt 0 is the first try
                try:
                    return await fn(*args, **kwargs)

                except Timeout as exc:
                    last_exception = exc
                    logger.warning(
                        "Timeout on attempt %d/%d: %s",
                        attempt + 1,
                        max_retries + 1,
                        exc,
                    )

                except (HTTPError, RequestsError) as exc:
                    response = getattr(exc, "response", None)
                    status_code = getattr(response, "status_code", None)
                    curl_code = getattr(exc, "code", None)

                    if status_code is not None:
                        if status_code < 500:
                            # 4xx errors are not retryable
                            raise
                        last_exception = exc
                        logger.warning(
                            "HTTP %s on attempt %d/%d: %s",
                            status_code,
                            attempt + 1,
                            max_retries + 1,
                            exc,
                        )
                    elif curl_code == 28:  # CURLE_OPERATION_TIMEDOUT
                        last_exception = exc
                        logger.warning(
                            "Curl timeout on attempt %d/%d: %s",
                            attempt + 1,
                            max_retries + 1,
                            exc,
                        )
                    else:
                        # Non-timeout request error (e.g. DNS failure) — do not retry
                        raise

                if attempt < max_retries:
                    raw_delay = backoff_base * (2 ** attempt)
                    jitter = random.uniform(-jitter_range, jitter_range)
                    delay = min(raw_delay + jitter, backoff_max)
                    delay = max(delay, 0.0)  # guard against negative jitter
                    logger.info(
                        "Retrying in %.2f s (attempt %d/%d) …",
                        delay,
                        attempt + 2,
                        max_retries + 1,
                    )
                    await asyncio.sleep(delay)

            # All attempts exhausted
            raise last_exception  # type: ignore[misc]

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# ScraperClient
# ---------------------------------------------------------------------------

class ScraperClient:
    """
    Async HTTP client for Allection scraping operations using curl_cffi
    for Chrome TLS fingerprint impersonation.

    Lifecycle
    ---------
    Use as an async context manager to guarantee the underlying
    ``AsyncSession`` (and its connection pool) is opened and closed
    cleanly::

        async with ScraperClient() as client:
            text = await client.get("https://example.com/products")

    Alternatively, call ``await client.aclose()`` manually if you manage
    the lifecycle yourself.

    Rate Limiting
    -------------
    A per-domain timestamp is stored in ``_domain_last_called``. Before each
    request the client sleeps for however many seconds remain of the
    ``RATE_LIMIT_SECONDS`` window, ensuring polite crawling.
    """

    def __init__(
        self,
        rate_limit_seconds: float = RATE_LIMIT_SECONDS,
        timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        self._rate_limit_seconds = rate_limit_seconds
        self._timeout = timeout
        self._domain_last_called: dict[str, float] = {}

        self._client = AsyncSession(
            impersonate=IMPERSONATE_TARGET,
            headers=BROWSER_HEADERS,
            timeout=self._timeout,
            allow_redirects=True,
        )

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "ScraperClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP session and release all connections."""
        await self._client.close()

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _extract_domain(self, url: str) -> str:
        """Return the netloc (host + optional port) from *url*."""
        return urlparse(url).netloc

    async def _enforce_rate_limit(self, domain: str) -> None:
        """
        Sleep until at least ``rate_limit_seconds`` have elapsed since the
        last request to *domain*.  Updates the timestamp immediately after
        waking so concurrent coroutines targeting the same domain will queue
        correctly.
        """
        now = time.monotonic()
        last = self._domain_last_called.get(domain, 0.0)
        elapsed = now - last
        wait = self._rate_limit_seconds - elapsed

        if wait > 0:
            logger.debug("Rate-limiting %s — sleeping %.2f s", domain, wait)
            await asyncio.sleep(wait)

        # Stamp *after* sleep so the next caller measures from now
        self._domain_last_called[domain] = time.monotonic()

    # ------------------------------------------------------------------
    # Core request method
    # ------------------------------------------------------------------

    @with_retries()
    async def get(
        self,
        url: str,
        *,
        as_json: bool = False,
        **kwargs: Any,
    ) -> str | Any:
        """
        Perform a GET request to *url* with rate limiting and retry logic.

        Parameters
        ----------
        url:
            The fully-qualified URL to fetch.
        as_json:
            When ``True``, parse and return the response body as a Python
            object (dict / list). When ``False`` (default), return the raw
            response text.
        **kwargs:
            Any additional keyword arguments are forwarded verbatim to
            ``AsyncSession.get`` (e.g. ``params``, ``headers``).

        Returns
        -------
        str
            Response body as a string (default, ``as_json=False``).
        Any
            Parsed JSON object (when ``as_json=True``).

        Raises
        ------
        curl_cffi.requests.errors.RequestsError
            Re-raised after all retry attempts are exhausted (for timeouts
            or 5xx errors) or immediately for 4xx / non-timeout errors.
        """
        domain = self._extract_domain(url)
        await self._enforce_rate_limit(domain)

        logger.debug("GET %s", url)
        response = await self._client.get(url, **kwargs)
        response.raise_for_status()

        if as_json:
            return response.json()

        return response.text

"""
strategies/enterprise_api.py
-----------------------------
Enterprise internal XHR/Ajax API scraping strategy for the Allection
microservice — proof-of-concept implementation targeting FNAC.pt.

Context
-------
Enterprise retailers like FNAC load a static HTML shell and populate product
listings via background JSON API calls (typically to an Algolia search
cluster). This strategy simulates that frontend XHR request directly,
bypassing all HTML parsing entirely.

How to port this to a new enterprise site
-----------------------------------------
1. Open the target site in Chrome / Firefox.
2. Open DevTools → Network tab → filter by "Fetch/XHR".
3. Type a search query on the site and find the API call that returns
   product JSON (look for endpoints containing "search", "query", "algolia",
   "elastic", etc.).
4. Right-click the request → "Copy as cURL" to capture the exact URL,
   method, headers, and body.
5. Replace every value flagged with a  # ← NETWORK TAB  comment below.

Phase 4 of the Allection scraping architecture.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from base_scraper import BaseScraperStrategy
from client import ScraperClient
from models import ScrapedItem

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mock response shape  (Algolia-style — for reference, not enforced at runtime)
# ---------------------------------------------------------------------------
#
# A typical Algolia response looks like:
#
# {
#   "hits": [
#     {
#       "objectID":    str,          # internal product ID
#       "name":        str,          # product title
#       "url":         str,          # relative or absolute product URL
#       "price": {
#         "EUR": { "default": float }  # nested price object
#       },
#       "brand":       str,
#       "category":    str,
#       ...
#     },
#     ...
#   ],
#   "nbHits":         int,           # total number of matching products
#   "page":           int,
#   "nbPages":        int,
#   "hitsPerPage":    int,
# }
#
# Some enterprise sites return { "results": [...] } instead of { "hits": [...] }.
# Both paths are handled by _extract_hits() below.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# FNAC.pt endpoint constants
# ---------------------------------------------------------------------------

_DOMAIN = "fnac.pt"
_BASE_URL = f"https://www.{_DOMAIN}"
_DEFAULT_CURRENCY = "EUR"               # FNAC.pt trades exclusively in EUR

# ── ① Endpoint URL ────────────────────────────────────────────────────────
# ← NETWORK TAB: Replace with the real XHR URL from the Network tab.
#   Example (Algolia): "https://<app-id>-dsn.algolia.net/1/indexes/*/queries"
_API_ENDPOINT: str = "https://mock-algolia-endpoint.algolia.net/1/indexes/*/queries"

# ── ② Results per page ────────────────────────────────────────────────────
_HITS_PER_PAGE: int = 20            # ← NETWORK TAB: match the real page size

# ---------------------------------------------------------------------------
# Request headers template
# ---------------------------------------------------------------------------
#
# ← NETWORK TAB: Copy the exact request headers from the "Headers" panel of
#   the relevant XHR call.  API keys rotate; always verify before deploying.
#
_API_HEADERS: dict[str, str] = {
    # ← NETWORK TAB: Real Algolia application ID (X-Algolia-Application-Id)
    "X-Algolia-Application-Id": "REPLACE_WITH_REAL_APP_ID",
    # ← NETWORK TAB: Real Algolia API key (X-Algolia-API-Key)
    "X-Algolia-API-Key": "REPLACE_WITH_REAL_API_KEY",
    "Content-Type": "application/json",
    "Accept": "application/json",
    # ← NETWORK TAB: Some sites require an Origin / Referer to pass CORS
    "Origin": _BASE_URL,
    "Referer": f"{_BASE_URL}/",
}

# ---------------------------------------------------------------------------
# Request body template
# ---------------------------------------------------------------------------
#
# ← NETWORK TAB: Copy the exact POST body from the "Payload" / "Request Body"
#   panel.  The `query` key and `hitsPerPage` are the only dynamic values;
#   all other keys should be reproduced verbatim from the captured request.
#
def _build_payload(query: str) -> dict[str, Any]:
    """
    Build the Algolia-style POST body for a product search.

    Parameters
    ----------
    query:
        The user-supplied search term.

    Returns
    -------
    dict
        A JSON-serialisable request body ready to be sent as the POST payload.

    Developer notes
    ---------------
    - ``indexName`` ← NETWORK TAB: replace with the real index name
      (e.g. ``"prod_FNAC_PT_products"``).
    - ``params`` ← NETWORK TAB: reproduce the real query-string params
      blob verbatim; the only substitution is ``query`` and ``hitsPerPage``.
    """
    return {
        "requests": [
            {
                # ← NETWORK TAB: Replace with the real Algolia index name
                "indexName": "REPLACE_WITH_REAL_INDEX_NAME",
                "params": (
                    f"query={query}"
                    f"&hitsPerPage={_HITS_PER_PAGE}"
                    # ← NETWORK TAB: Append any additional params found in
                    #   the real request (filters, facets, analytics flags …)
                    # e.g. "&filters=available%3Dtrue&analytics=false"
                ),
            }
        ]
    }


class FnacPtScraper(BaseScraperStrategy):
    """
    Concrete scraping strategy that simulates FNAC.pt's internal Algolia
    XHR search request to retrieve structured product data.

    This is a **proof-of-concept template**.  The endpoint URL, API keys,
    index name, and payload parameters are intentionally left as placeholder
    strings.  Follow the "← NETWORK TAB" comments throughout this file to
    swap them with the real values captured from a browser Network tab
    inspection.

    Parameters
    ----------
    client:
        An active :class:`ScraperClient` instance.  The caller manages its
        lifecycle.

    Example
    -------
    ::

        async with ScraperClient() as client:
            scraper = FnacPtScraper(client)
            results = await scraper.search("sony headphones")
            for item in results:
                print(item.item_title, item.price, item.currency)

    Porting to another enterprise site
    -----------------------------------
    1. Subclass :class:`FnacPtScraper` or copy this file.
    2. Replace ``_DOMAIN``, ``_API_ENDPOINT``, ``_API_HEADERS``, and
       ``_build_payload`` with values from the target site's Network tab.
    3. Update ``_extract_hits`` and ``_parse_hit`` to match the response
       schema of the new API.
    """

    def __init__(self, client: ScraperClient) -> None:
        # Domain is hardcoded — this class is FNAC.pt-specific
        self.domain: str = _DOMAIN
        self._base_url: str = _BASE_URL
        self._client: ScraperClient = client

    # ------------------------------------------------------------------
    # BaseScraperStrategy implementation
    # ------------------------------------------------------------------

    async def search(self, query: str) -> list[ScrapedItem]:
        """
        Search FNAC.pt for products matching *query* via its internal API.

        Sends a POST request (simulated with the current ScraperClient GET
        — see NOTE below) to the Algolia search endpoint and maps the
        response ``hits`` to a list of :class:`ScrapedItem` instances.

        Malformed or incomplete hit entries are **skipped** with a warning
        rather than crashing the entire call.

        NOTE on POST support
        --------------------
        :class:`ScraperClient` currently exposes only a ``.get()`` method.
        Real Algolia/enterprise endpoints require a POST with a JSON body.
        Two upgrade paths:

        Option A — Add a ``.post()`` method to ScraperClient (recommended).
        Option B — Call ``client._client.post(...)`` directly (interim hack).

        This implementation uses a **mock coroutine** in place of the real
        network call so the strategy can be imported, instantiated, and
        tested without live credentials.  Replace the ``# ← MOCK`` block
        with a real ``await self._client.post(...)`` once ScraperClient
        gains a ``.post()`` method.

        Parameters
        ----------
        query:
            Product search term (e.g. ``"sony headphones"``).

        Returns
        -------
        list[ScrapedItem]
            Validated items; may be empty if no results or all hits were
            malformed.

        Raises
        ------
        httpx.HTTPStatusError
            Re-raised from ScraperClient for 4xx / unrecoverable 5xx.
        httpx.RequestError
            Re-raised for network-level failures.
        """
        logger.info("FnacPtScraper searching '%s' on %s", query, self.domain)

        payload = _build_payload(query)

        # ── Network call ──────────────────────────────────────────────────
        #
        # ← REAL IMPLEMENTATION (uncomment once ScraperClient has .post()):
        #
        #     data: Any = await self._client.post(
        #         _API_ENDPOINT,
        #         json=payload,
        #         headers=_API_HEADERS,
        #         as_json=True,
        #     )
        #
        # ← MOCK  (remove when switching to the real call above) ──────────
        data: Any = await self._mock_api_call(query)
        # ─────────────────────────────────────────────────────────────────

        raw_hits = self._extract_hits(data)
        logger.debug(
            "FnacPtScraper received %d raw hit(s) from %s",
            len(raw_hits),
            self.domain,
        )

        items: list[ScrapedItem] = []
        for raw in raw_hits:
            item = self._parse_hit(raw)
            if item is not None:
                items.append(item)

        logger.info(
            "FnacPtScraper returning %d valid item(s) from %s",
            len(items),
            self.domain,
        )
        return items

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_hits(data: Any) -> list[dict[str, Any]]:
        """
        Safely extract the list of raw product hits from the API response.

        Handles two common enterprise response shapes:

        1. Algolia multi-index:  ``data["results"][0]["hits"]``
        2. Flat single-index:    ``data["hits"]``

        Returns an empty list for any unexpected envelope.
        """
        try:
            # Algolia multi-index style (most common for enterprise sites)
            return data["results"][0]["hits"]
        except (KeyError, IndexError, TypeError):
            pass

        try:
            # Flat single-index fallback
            return data["hits"]
        except (KeyError, TypeError):
            pass

        logger.warning(
            "Unexpected enterprise API response shape — could not locate "
            "hits under data['results'][0]['hits'] or data['hits']. "
            "Returning empty list. Raw keys: %s",
            list(data.keys()) if isinstance(data, dict) else type(data).__name__,
        )
        return []

    def _parse_hit(self, raw: dict[str, Any]) -> ScrapedItem | None:
        """
        Map a single raw Algolia hit dict to a :class:`ScrapedItem`.

        Returns ``None`` (with a warning) for any entry that is missing
        required fields or fails Pydantic validation, so one bad hit never
        kills the entire result set.

        Expected hit schema (Algolia / FNAC.pt)
        ----------------------------------------
        ::

            {
                "name":  str,
                "url":   str,   # ← NETWORK TAB: key may differ (e.g. "slug")
                "price": {
                    "EUR": { "default": float }
                    # ← NETWORK TAB: currency key and nesting may differ
                },
                "currency_code": str   # optional flat alternative to nested price
            }

        Parameters
        ----------
        raw:
            A single element from the ``hits`` list in the API response.

        Returns
        -------
        ScrapedItem | None
            A fully validated model instance, or ``None`` on failure.
        """
        try:
            # ── Title ──────────────────────────────────────────────────
            # ← NETWORK TAB: key may be "name", "title", "productName", etc.
            title: str = raw["name"]

            # ── Price ──────────────────────────────────────────────────
            # Algolia typically nests price as: price.{CURRENCY}.default (float)
            # ← NETWORK TAB: Adjust path to match the real response structure.
            price_block: dict[str, Any] = raw["price"]
            currency: str = next(iter(price_block))   # e.g. "EUR"
            price: float = float(price_block[currency]["default"])

            # ── URL ────────────────────────────────────────────────────
            # ← NETWORK TAB: key may be "url", "slug", "productUrl", etc.
            raw_url: str = raw["url"]
            direct_buy_url = (
                raw_url
                if raw_url.startswith("http")
                else f"{self._base_url}{raw_url}"
            )

            return ScrapedItem(
                source_website=self.domain,
                item_title=title,
                price=price,
                currency=currency,
                direct_buy_url=direct_buy_url,
            )

        except (KeyError, IndexError, TypeError, StopIteration, ValueError) as exc:
            logger.warning(
                "Skipping malformed enterprise API hit on %s — %s: %s. "
                "Raw data: %r",
                self.domain,
                type(exc).__name__,
                exc,
                raw,
            )
            return None

    # ------------------------------------------------------------------
    # Mock network call  (remove once ScraperClient gains .post())
    # ------------------------------------------------------------------

    async def _mock_api_call(self, query: str) -> dict[str, Any]:
        """
        Return a hard-coded Algolia-shaped response for testing purposes.

        This coroutine exists **only** to make the strategy importable and
        testable without live credentials.  It faithfully replicates the
        response envelope a real Algolia call would return.

        ← Remove this method and replace the call site in ``search()``
          with a real ``await self._client.post(...)`` once the credentials
          are sourced from the Network tab and ScraperClient is extended.
        """
        logger.debug(
            "FnacPtScraper using MOCK response for query '%s' "
            "(replace _mock_api_call with a real POST when ready)",
            query,
        )
        # Simulate a tiny network delay so the coroutine is genuinely async
        await asyncio.sleep(0)

        return {
            "results": [
                {
                    "hits": [
                        {
                            "objectID": "mock-001",
                            "name": f"Sony WH-1000XM5 Headphones [{query}]",
                            "url": "/product/sony-wh1000xm5",
                            "price": {"EUR": {"default": 279.99}},
                        },
                        {
                            "objectID": "mock-002",
                            "name": f"Bose QuietComfort 45 [{query}]",
                            "url": "/product/bose-qc45",
                            "price": {"EUR": {"default": 249.00}},
                        },
                        # Intentionally malformed — missing "name" — to prove
                        # graceful skip behaviour
                        {
                            "objectID": "mock-003",
                            "url": "/product/broken-item",
                            "price": {"EUR": {"default": 0.0}},
                        },
                    ],
                    "nbHits": 2,
                    "page": 0,
                    "nbPages": 1,
                    "hitsPerPage": _HITS_PER_PAGE,
                }
            ]
        }

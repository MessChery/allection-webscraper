"""
strategies/shopify.py
---------------------
Shopify "hidden" JSON API scraping strategy for the Allection microservice.

Instead of fragile HTML parsing, this strategy exploits Shopify's built-in
search-suggestion endpoint which returns clean, structured product data:

    GET https://{domain}/search/suggest.json
            ?q={query}&resources[type]=product

Phase 3 of the Allection scraping architecture.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote_plus

from base_scraper import BaseScraperStrategy
from client import ScraperClient
from models import ScrapedItem

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shopify JSON response shape (for reference — not enforced at runtime)
# ---------------------------------------------------------------------------
#
# {
#   "resources": {
#     "results": {
#       "products": [
#         {
#           "title":             str,
#           "url":               str,   # relative path, e.g. "/products/slug"
#           "price":             str,   # e.g. "12.99" — always a string
#           "currency_code":     str,   # ISO 4217, e.g. "EUR"
#           "featured_image": { "url": str },
#           ...
#         },
#         ...
#       ]
#     }
#   }
# }
#
# NOTE: `price` is a numeric string without a currency symbol.
# NOTE: `currency_code` availability depends on the store theme/config;
#       we fall back to "USD" when absent to keep ScrapedItem valid.
# ---------------------------------------------------------------------------

_SUGGEST_PATH = "/search/suggest.json"
_DEFAULT_CURRENCY = "USD"


class ShopifyScraper(BaseScraperStrategy):
    """
    Concrete scraping strategy that queries the Shopify search-suggestion
    JSON endpoint to retrieve structured product data.

    Parameters
    ----------
    domain:
        The bare hostname of the Shopify store (e.g. ``"loja-vinil.pt"``).
        Do **not** include the scheme; it is added automatically.
    client:
        An active :class:`ScraperClient` instance.  The caller is responsible
        for managing the client's lifecycle (opening and closing it).

    Example
    -------
    ::

        async with ScraperClient() as client:
            scraper = ShopifyScraper("loja-vinil.pt", client)
            results = await scraper.search("toca discos")
            for item in results:
                print(item.item_title, item.price, item.currency)
    """

    def __init__(self, domain: str, client: ScraperClient) -> None:
        self._domain = domain.rstrip("/")
        self._client = client
        self._base_url = f"https://{self._domain}"

    # ------------------------------------------------------------------
    # BaseScraperStrategy implementation
    # ------------------------------------------------------------------

    async def search(self, query: str) -> list[ScrapedItem]:
        """
        Search the Shopify store for products matching *query*.

        Calls ``GET /search/suggest.json?q=<query>&resources[type]=product``
        and maps the response to a list of :class:`ScrapedItem` instances.

        Malformed or incomplete product entries are **skipped** with a
        warning rather than crashing the entire call, so a partially
        structured response still yields as many valid items as possible.

        Parameters
        ----------
        query:
            The product search term (e.g. ``"vinyl record player"``).

        Returns
        -------
        list[ScrapedItem]
            Validated product items; may be empty if the store returned
            no results or all entries were malformed.

        Raises
        ------
        httpx.HTTPStatusError
            Re-raised from :class:`ScraperClient` if the endpoint returns
            a 4xx or an unrecoverable 5xx after all retries.
        httpx.RequestError
            Re-raised from :class:`ScraperClient` for network-level failures.
        """
        url = self._build_url(query)
        logger.info("ShopifyScraper searching '%s' on %s", query, self._domain)

        data: Any = await self._client.get(url, as_json=True)

        raw_products = self._extract_products(data)
        logger.debug(
            "ShopifyScraper received %d raw product(s) from %s",
            len(raw_products),
            self._domain,
        )

        items: list[ScrapedItem] = []
        for raw in raw_products:
            item = self._parse_product(raw)
            if item is not None:
                items.append(item)

        logger.info(
            "ShopifyScraper returning %d valid item(s) from %s",
            len(items),
            self._domain,
        )
        return items

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_url(self, query: str) -> str:
        """
        Construct the fully-qualified Shopify suggest endpoint URL.

        The query is percent-encoded so that spaces and special characters
        are handled correctly.
        """
        encoded_query = quote_plus(query)
        return (
            f"{self._base_url}{_SUGGEST_PATH}"
            f"?q={encoded_query}&resources[type]=product"
        )

    @staticmethod
    def _extract_products(data: Any) -> list[dict[str, Any]]:
        """
        Safely navigate the Shopify JSON envelope and return the raw list
        of product dicts.

        Returns an empty list if the expected keys are absent rather than
        raising, so callers always receive a list.
        """
        try:
            return data["resources"]["results"]["products"]
        except (KeyError, TypeError):
            logger.warning(
                "Unexpected Shopify response shape — could not locate "
                "data['resources']['results']['products']. "
                "Returning empty list."
            )
            return []

    def _parse_product(self, raw: dict[str, Any]) -> ScrapedItem | None:
        """
        Map a single raw Shopify product dict to a :class:`ScrapedItem`.

        Returns ``None`` and logs a warning for any entry that is missing
        required fields or contains values that fail Pydantic validation
        (e.g. a non-numeric price string).

        Parameters
        ----------
        raw:
            A single element from the ``products`` list in the Shopify
            suggest response.

        Returns
        -------
        ScrapedItem | None
            A fully validated model instance, or ``None`` on failure.
        """
        try:
            title: str = raw["title"]

            # `price` is returned as a plain numeric string (e.g. "29.99")
            raw_price: str = raw["price"]
            price = float(raw_price)

            # `currency_code` may be absent on older or custom themes
            currency: str = raw.get("currency_code") or _DEFAULT_CURRENCY

            # `url` is a relative path — make it absolute
            relative_url: str = raw["url"]
            direct_buy_url = (
                relative_url
                if relative_url.startswith("http")
                else f"{self._base_url}{relative_url}"
            )

            return ScrapedItem(
                source_website=self._domain,
                item_title=title,
                price=price,
                currency=currency,
                direct_buy_url=direct_buy_url,
            )

        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "Skipping malformed Shopify product entry on %s — %s: %s. "
                "Raw data: %r",
                self._domain,
                type(exc).__name__,
                exc,
                raw,
            )
            return None

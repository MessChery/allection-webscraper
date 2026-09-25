"""
strategies/enterprise_api.py
-----------------------------
FNAC.pt HTML scraping strategy for the Allection microservice.

Network Analysis Finding
------------------------
FNAC.pt uses ASP.NET Server-Side Rendering — search results are delivered
as raw HTML via ResultList.aspx rather than a clean JSON payload. This
strategy parses the response DOM using BeautifulSoup + lxml.

Search endpoint:
    GET https://www.fnac.pt/SearchResult/ResultList.aspx
            ?Search={query}&sft=1&sa=0

How to update the CSS selectors
---------------------------------
1. Open https://www.fnac.pt/SearchResult/ResultList.aspx?Search=headphones
   in Chrome / Firefox.
2. Open DevTools → Elements (Inspector) tab.
3. Find the repeating product card container — right-click → "Copy selector".
4. Replace the selector strings marked  # ← CSS SELECTOR  below.

Phase 4 (revised) of the Allection scraping architecture.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag

from base_scraper import BaseScraperStrategy
from client import ScraperClient
from models import ScrapedItem

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DOMAIN = "fnac.pt"
_BASE_URL = f"https://www.{_DOMAIN}"
_DEFAULT_CURRENCY = "EUR"   # FNAC.pt trades exclusively in EUR

# Search URL template
# sft=1  → search in all categories
# sa=0   → sort by relevance
_SEARCH_URL = (
    f"{_BASE_URL}/SearchResult/ResultList.aspx"
    "?Search={query}&sft=1&sa=0"
)

# ---------------------------------------------------------------------------
# CSS selector constants
# ---------------------------------------------------------------------------
# All selectors below are educated placeholders derived from common FNAC.pt
# DOM patterns.  Replace each one after inspecting the live page in DevTools.
# Every constant is annotated with  # ← CSS SELECTOR  for quick discovery.

# Outer container — one per product card on the results page
_SEL_PRODUCT_CARD = ".Article-item"                  # ← CSS SELECTOR
#   alt candidates: "article.product-item", ".product-list .item"

# Product title — the human-readable name of the product
_SEL_TITLE = ".Article-title"                       # ← CSS SELECTOR
#   alt candidates: ".product-title", "h3.title", "[itemprop='name']"

# Price — the displayed sale price (may include currency symbol)
_SEL_PRICE = ".userPrice"                           # ← CSS SELECTOR
#   alt candidates: ".Article-price", "[itemprop='price']", ".price-value"

# Canonical product URL — the <a> that wraps the product title.
# The href is safely nested inside the title anchor, bypassing JS-obfuscated links.
_SEL_URL = "a.Article-title"                       # ← CSS SELECTOR (confirmed via live DOM)
#   alt candidates: "a.product-link", "a[href*='/p/']"


class FnacPtScraper(BaseScraperStrategy):
    """
    Concrete scraping strategy for FNAC.pt using HTML (SSR) parsing.

    Sends a GET request to FNAC.pt's search results page and extracts
    product cards from the HTML response using BeautifulSoup + lxml.

    Parameters
    ----------
    client:
        An active :class:`ScraperClient` instance.  The caller manages
        its lifecycle.

    Example
    -------
    ::

        async with ScraperClient() as client:
            scraper = FnacPtScraper(client)
            results = await scraper.search("sony headphones")
            for item in results:
                print(item.item_title, item.price, item.currency)
    """

    def __init__(self, client: ScraperClient) -> None:
        self.domain: str = _DOMAIN
        self._base_url: str = _BASE_URL
        self._client: ScraperClient = client

    # ------------------------------------------------------------------
    # BaseScraperStrategy implementation
    # ------------------------------------------------------------------

    async def search(self, query: str) -> list[ScrapedItem]:
        """
        Search FNAC.pt for products matching *query* via HTML parsing.

        Fetches the SSR search results page and parses product cards from
        the DOM.  Individual card parse failures are isolated — one broken
        card never prevents the rest from being returned.

        Parameters
        ----------
        query:
            Product search term (e.g. ``"sony headphones"``).

        Returns
        -------
        list[ScrapedItem]
            Validated items; may be empty if no results or all cards were
            unparseable.

        Raises
        ------
        httpx.HTTPStatusError
            Re-raised from ScraperClient for 4xx / unrecoverable 5xx.
        httpx.RequestError
            Re-raised for network-level failures.
        """
        url = self._build_url(query)
        logger.info("FnacPtScraper searching '%s' on %s", query, self.domain)

        # Warm up session to acquire initial cookies from the homepage
        warmup_url = f"{self._base_url}/"
        logger.debug("FnacPtScraper warming up session via GET %s", warmup_url)
        await self._client.get(
            warmup_url,
            as_json=False,
            headers={
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-User": "?1",
            },
        )

        logger.debug("FnacPtScraper GET %s", url)

        # as_json=False → returns raw HTML text
        html: str = await self._client.get(
            url,
            as_json=False,
            headers={
                "Referer": f"{self._base_url}/",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-User": "?1",
            },
        )
        print(f"DEBUG: Fetched HTML length: {len(html)}")

        cards = self._extract_cards(html)
        logger.debug(
            "FnacPtScraper found %d product card(s) on %s",
            len(cards),
            self.domain,
        )

        items: list[ScrapedItem] = []
        for card in cards:
            item = self._parse_card(card)
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

    def _build_url(self, query: str) -> str:
        """Build the fully-qualified FNAC.pt search URL for *query*."""
        return _SEARCH_URL.format(query=quote_plus(query))

    def _extract_cards(self, html: str) -> list[Tag]:
        """
        Parse *html* with BeautifulSoup and return all product card tags.

        Uses the ``lxml`` parser for speed and lenient error recovery on
        malformed HTML (common on large retail sites).

        Returns an empty list if the selector finds nothing, so the caller
        always receives a list.

        Developer note
        --------------
        If this method consistently returns ``[]`` on a live page, the
        selector ``_SEL_PRODUCT_CARD`` needs updating.  To diagnose:

        1. Save the raw HTML to a file:
           ``with open("debug.html", "w") as f: f.write(html)``
        2. Open it in a browser and inspect the product card elements.
        3. Update ``_SEL_PRODUCT_CARD`` accordingly.
        """
        soup = BeautifulSoup(html, "lxml")
        cards = soup.select(_SEL_PRODUCT_CARD)   # ← CSS SELECTOR applied here
        print(f"DEBUG: Found {len(cards)} cards using {_SEL_PRODUCT_CARD}")

        if not cards:
            logger.warning(
                "FnacPtScraper: no product cards matched selector %r on %s. "
                "The page structure may have changed — update _SEL_PRODUCT_CARD.",
                _SEL_PRODUCT_CARD,
                self.domain,
            )

        return cards

    def _parse_card(self, card: Tag) -> ScrapedItem | None:
        """
        Extract product data from a single BeautifulSoup ``Tag`` and map
        it to a :class:`ScrapedItem`.

        Returns ``None`` (with a warning) if any required field is missing
        or fails validation, so one broken card never kills the entire batch.

        CSS selectors used
        ------------------
        - Title  : ``_SEL_TITLE``   # ← CSS SELECTOR
        - Price  : ``_SEL_PRICE``   # ← CSS SELECTOR
        - URL    : ``_SEL_URL``     # ← CSS SELECTOR

        Parameters
        ----------
        card:
            A single ``<li>`` / ``<article>`` tag representing one product.

        Returns
        -------
        ScrapedItem | None
        """
        try:
            # ── Title ──────────────────────────────────────────────────
            title_tag = card.select_one(_SEL_TITLE)  # ← CSS SELECTOR
            if title_tag is None:
                raise KeyError(f"Title selector {_SEL_TITLE!r} matched nothing")
            title: str = title_tag.get_text(strip=True)
            if not title:
                raise ValueError("Extracted title is empty")

            # ── Price ──────────────────────────────────────────────────
            price_tag = card.select_one(_SEL_PRICE)  # ← CSS SELECTOR
            if price_tag is None:
                raise KeyError(f"Price selector {_SEL_PRICE!r} matched nothing")

            price = self._parse_price(price_tag.get_text(strip=True))

            # ── URL ────────────────────────────────────────────────────
            # Primary: dedicated link wrapper selector
            url_tag = card.select_one(_SEL_URL)       # ← CSS SELECTOR
            # Fallback: any <a href> inside the card
            if url_tag is None:
                url_tag = card.find("a", href=True)

            if url_tag is None:
                raise KeyError("No <a href> found inside product card")

            raw_href: str = url_tag.get("href", "")    # type: ignore[arg-type]
            direct_buy_url = (
                raw_href
                if raw_href.startswith("http")
                else f"{self._base_url}{raw_href}"
            )

            return ScrapedItem(
                source_website=self.domain,
                item_title=title,
                price=price,
                currency=_DEFAULT_CURRENCY,
                direct_buy_url=direct_buy_url,
            )

        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            print(f"DEBUG: Card parse failed: {exc}")
            logger.warning(
                "FnacPtScraper: skipping malformed product card on %s "
                "— %s: %s",
                self.domain,
                type(exc).__name__,
                exc,
            )
            return None

    @staticmethod
    def _parse_price(raw: str) -> float:
        """
        Normalise a raw price string scraped from the DOM and return a float.

        Handles common European formatting patterns found on FNAC.pt:

        - Currency symbols  : "€ 109,99"  → 109.99
        - Non-breaking spaces: "109\xa099" → 109.99
        - Comma decimals     : "109,99"    → 109.99
        - Dot thousands      : "1.099,99"  → 1099.99

        Parameters
        ----------
        raw:
            The raw text content of the price element (e.g. ``"€ 109,99"``).

        Returns
        -------
        float

        Raises
        ------
        ValueError
            If the cleaned string cannot be converted to a float.
        """
        # Strip currency symbols, whitespace, non-breaking spaces
        cleaned = (
            raw
            .replace("€", "")
            .replace("\xa0", "")   # non-breaking space
            .replace("\u202f", "") # narrow no-break space
            .strip()
        )

        # European format: thousands separator = ".", decimal separator = ","
        # e.g. "1.099,99" → "1099.99"
        if "," in cleaned and "." in cleaned:
            # Both present → dot is thousands, comma is decimal
            cleaned = cleaned.replace(".", "").replace(",", ".")
        elif "," in cleaned:
            # Only comma → it's the decimal separator
            cleaned = cleaned.replace(",", ".")

        return float(cleaned)

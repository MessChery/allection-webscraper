"""
strategies/enterprise_api.py
-----------------------------
FNAC.pt headless-browser + HTML scraping strategy for the Allection
microservice.

Network & WAF Analysis Finding
------------------------------
FNAC.pt uses ASP.NET Server-Side Rendering protected by DataDome's JavaScript
challenge WAF.  Pure HTTP clients cannot execute the JS challenge needed to
acquire the ``datadome`` clearance cookie.  This strategy launches a headless
Chromium instance via Playwright to execute the page JS, waits for the DOM to
settle, extracts the rendered HTML, and parses product cards using
BeautifulSoup + lxml.

Search endpoint:
    GET https://www.fnac.pt/SearchResult/ResultList.aspx
            ?Search={query}&sft=1&sa=0

Phase 4 (Playwright revision) of the Allection scraping architecture.
"""

from __future__ import annotations

import logging
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

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

_DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)
_DESKTOP_VIEWPORT = {"width": 1366, "height": 768}

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

# Outer container — one per product card on the results page
_SEL_PRODUCT_CARD = ".Article-item"                  # ← CSS SELECTOR

# Product title — the human-readable name of the product
_SEL_TITLE = ".Article-title"                       # ← CSS SELECTOR

# Price — the displayed sale price (may include currency symbol)
_SEL_PRICE = ".userPrice"                           # ← CSS SELECTOR

# Canonical product URL — the <a> that wraps the product title.
# The href is safely nested inside the title anchor, bypassing JS-obfuscated links.
_SEL_URL = "a.Article-title"                        # ← CSS SELECTOR (confirmed via live DOM)


class FnacPtScraper(BaseScraperStrategy):
    """
    Concrete scraping strategy for FNAC.pt using Playwright (headless Chromium)
    + BeautifulSoup HTML parsing to bypass DataDome JS challenges.

    Parameters
    ----------
    client:
        An active :class:`ScraperClient` instance (kept in the constructor
        signature for uniform strategy initialization).
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
        Search FNAC.pt for products matching *query* using headless Chromium.

        Launches a headless Chromium browser via Playwright, creates a
        realistic desktop browser context, navigates to the search URL,
        waits for the network to settle or for ``.Article-item`` cards to
        render, extracts ``page.content()``, closes the browser, and parses
        the rendered HTML via :meth:`_parse_html`.
        """
        url = self._build_url(query)
        logger.info("FnacPtScraper searching '%s' on %s via Playwright", query, self.domain)
        logger.debug("FnacPtScraper navigating to %s", url)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    viewport=_DESKTOP_VIEWPORT,
                    user_agent=_DESKTOP_USER_AGENT,
                    locale="pt-PT",
                )
                page = await context.new_page()

                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

                try:
                    await page.wait_for_selector(_SEL_PRODUCT_CARD, timeout=15_000)
                except PlaywrightTimeoutError:
                    logger.debug(
                        "Timed out waiting for %r; falling back to networkidle.",
                        _SEL_PRODUCT_CARD,
                    )
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10_000)
                    except PlaywrightTimeoutError:
                        pass

                html: str = await page.content()
            finally:
                await browser.close()

        print(f"DEBUG: Fetched HTML length: {len(html)}")
        return self._parse_html(html)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_url(self, query: str) -> str:
        """Build the fully-qualified FNAC.pt search URL for *query*."""
        return _SEARCH_URL.format(query=quote_plus(query))

    def _parse_html(self, html: str) -> list[ScrapedItem]:
        """
        Extract product cards from *html* and map valid entries to
        :class:`ScrapedItem` instances.
        """
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

    def _extract_cards(self, html: str) -> list[Tag]:
        """
        Parse *html* with BeautifulSoup and return all product card tags.
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
            url_tag = card.select_one(_SEL_URL)       # ← CSS SELECTOR
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
        """
        cleaned = (
            raw
            .replace("€", "")
            .replace("\xa0", "")   # non-breaking space
            .replace("\u202f", "") # narrow no-break space
            .strip()
        )

        if "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        elif "," in cleaned:
            cleaned = cleaned.replace(",", ".")

        return float(cleaned)

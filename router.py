"""
router.py
---------
Strategy Router & Manager for the Allection web scraping microservice.

The ScraperManager is the single entry-point for all scraping activity.
It owns the ScraperClient lifecycle, maintains a registry of active
strategy instances, and fans out every search query to all strategies
concurrently — isolating individual failures so one broken backend never
prevents results from the others.

Phase 5 of the Allection scraping architecture.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from base_scraper import BaseScraperStrategy
from client import ScraperClient
from models import ScrapedItem
from strategies.enterprise_api import FnacPtScraper
from strategies.shopify import ShopifyScraper

# Root-level logger for this module
logger = logging.getLogger(__name__)


class ScraperManager:
    """
    Central orchestrator for all Allection scraping strategies.

    Responsibilities
    ----------------
    - Owns and manages the lifecycle of a single :class:`ScraperClient`
      (shared connection pool across all strategies).
    - Maintains a registry of :class:`BaseScraperStrategy` instances,
      each targeting a different e-commerce backend.
    - Executes every registered strategy concurrently via
      ``asyncio.gather``, isolating individual failures so a single
      broken backend cannot prevent results from the others.
    - Flattens per-strategy result lists into one unified
      ``list[ScrapedItem]`` for the caller.

    Lifecycle
    ---------
    Use as an async context manager for automatic cleanup::

        async with ScraperManager() as manager:
            results = await manager.search_all("Nike Air Force 1")

    Alternatively, call ``await manager.close()`` manually.

    Extending the registry
    ----------------------
    To add a new strategy, append an instance to ``self._registry``
    inside ``__init__``::

        self._registry.append(ShopifyScraper("new-store.com", self._client))
    """

    def __init__(self) -> None:
        # ── Shared HTTP client (one connection pool for all strategies) ──
        self._client = ScraperClient()

        # ── Strategy registry ────────────────────────────────────────────
        # Each entry is a fully-initialised BaseScraperStrategy instance.
        # Add or remove strategies here as the microservice grows.
        self._registry: list[BaseScraperStrategy] = [
            # Shopify "hidden" JSON API — independent vinyl store demo
            ShopifyScraper("loja-vinil.pt", self._client),

            # FNAC.pt enterprise Algolia XHR API
            FnacPtScraper(self._client),
        ]

        logger.info(
            "ScraperManager initialised with %d strategy/strategies: %s",
            len(self._registry),
            [type(s).__name__ for s in self._registry],
        )

    # ------------------------------------------------------------------
    # Async context-manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "ScraperManager":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search_all(self, query: str) -> list[ScrapedItem]:
        """
        Execute *query* against every registered strategy concurrently
        and return a single aggregated list of results.

        Each strategy runs as an independent ``asyncio.Task``.  If a
        strategy raises any exception (network error, parsing failure,
        etc.) it is caught, logged at ERROR level, and skipped — the
        remaining strategies' results are still returned.

        Parameters
        ----------
        query:
            The product search term to send to all backends.

        Returns
        -------
        list[ScrapedItem]
            Flattened, aggregated results from every strategy that
            completed successfully.  May be empty if every strategy
            failed or returned no results.
        """
        logger.info(
            "search_all: dispatching '%s' to %d strategy/strategies",
            query,
            len(self._registry),
        )

        # Build one coroutine per registered strategy
        tasks = [scraper.search(query) for scraper in self._registry]

        # Fan out — return_exceptions=True prevents one failure from
        # cancelling the other coroutines
        raw_results: list[list[ScrapedItem] | BaseException] = (
            await asyncio.gather(*tasks, return_exceptions=True)
        )

        aggregated: list[ScrapedItem] = []

        for scraper, outcome in zip(self._registry, raw_results):
            scraper_name = type(scraper).__name__

            if isinstance(outcome, BaseException):
                # Log the failure but keep processing the remaining results
                logger.error(
                    "search_all: %s raised %s — skipping. Detail: %s",
                    scraper_name,
                    type(outcome).__name__,
                    outcome,
                    exc_info=outcome,  # attaches the traceback to the log record
                )
                continue

            logger.info(
                "search_all: %s returned %d item(s)",
                scraper_name,
                len(outcome),
            )
            aggregated.extend(outcome)

        logger.info(
            "search_all: returning %d total item(s) for query '%s'",
            len(aggregated),
            query,
        )
        return aggregated

    async def close(self) -> None:
        """
        Cleanly shut down the shared :class:`ScraperClient`.

        Releases all open HTTP connections.  Always call this (or use the
        async context manager) when the manager is no longer needed.
        """
        logger.info("ScraperManager shutting down ScraperClient …")
        await self._client.aclose()
        logger.info("ScraperManager closed.")


# ---------------------------------------------------------------------------
# Manual smoke-test
# ---------------------------------------------------------------------------

async def _demo() -> None:
    """
    Demonstrate the full aggregation pipeline across all registered backends.

    Uses the mock backends wired in __init__ — no live credentials needed.
    This block is intentionally verbose so each stage of the pipeline is
    clearly visible in the terminal output.
    """
    _QUERY = "Nike Air Force 1 Retro Premium Cacao Wow"

    print("\n" + "=" * 60)
    print("  Allection ScraperManager — aggregation demo")
    print("=" * 60)
    print(f"  Query : {_QUERY!r}")
    print("=" * 60 + "\n")

    async with ScraperManager() as manager:
        results = await manager.search_all(_QUERY)

    if not results:
        print("No results returned (all backends returned empty or failed).")
        return

    print(f"\n{'─'*60}")
    print(f"  {len(results)} item(s) aggregated across all backends")
    print(f"{'─'*60}")

    for idx, item in enumerate(results, start=1):
        print(
            f"  [{idx:02d}] {item.source_website:<22}"
            f"  {item.currency} {item.price:>8.2f}"
            f"  {item.item_title}"
        )
        print(f"        {item.direct_buy_url}")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    # Configure a human-readable log format so the concurrency flow is
    # clearly visible when running this file directly.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    asyncio.run(_demo())

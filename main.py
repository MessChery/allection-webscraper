"""
main.py
-------
FastAPI gateway for the Allection web scraping microservice.

Exposes a single REST endpoint that fans out search queries to all
registered scraping strategies concurrently via ScraperManager and
returns a unified list of ScrapedItems.

Phase 6 of the Allection scraping architecture.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from models import ScrapedItem
from router import ScraperManager

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — manager is created once on startup, closed cleanly on shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Manage the ScraperManager lifecycle tied to the FastAPI app.

    - On startup : instantiates ScraperManager (which opens the shared
      ScraperClient / httpx connection pool) and stores it in app.state.
    - On shutdown: calls manager.close() to drain the connection pool and
      release all resources cleanly before the process exits.
    """
    logger.info("Allection scraper starting up …")
    manager = ScraperManager()
    app.state.manager = manager

    yield  # application runs here

    logger.info("Allection scraper shutting down …")
    await manager.close()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Allection Scraper API",
    description=(
        "Concurrent web scraping microservice for the Allection platform. "
        "Aggregates product listings from multiple e-commerce backends "
        "and returns unified, structured pricing data."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get(
    "/api/v1/search",
    response_model=list[ScrapedItem],
    summary="Search all backends",
    description=(
        "Dispatches the search query to every registered scraping strategy "
        "concurrently. Individual backend failures are isolated — the "
        "endpoint always returns the results from whichever strategies "
        "succeeded, rather than raising a 500 for a partial failure."
    ),
    tags=["Search"],
)
async def search(
    q: str = Query(
        ...,
        min_length=1,
        max_length=200,
        description="Product search term (e.g. 'Nike Air Force 1 Retro').",
        example="Nike Air Force 1 Retro Premium",
    ),
) -> list[ScrapedItem]:
    """
    Search all registered e-commerce backends for *q* and return
    aggregated results.

    Parameters
    ----------
    q:
        The product search query string (1–200 characters).

    Returns
    -------
    list[ScrapedItem]
        Unified list of matched products across all backends.
        Returns an empty list when no results are found — never raises
        a 404 for zero results, only for genuine service errors.

    Raises
    ------
    422 Unprocessable Entity
        When *q* is absent, empty, or exceeds 200 characters.
    500 Internal Server Error
        When ScraperManager itself is unavailable (should not occur
        under normal operation — individual backend failures are
        silently swallowed and logged by the manager).
    """
    manager: ScraperManager = app.state.manager

    logger.info("GET /api/v1/search  q=%r", q)

    try:
        results = await manager.search_all(q)
    except Exception as exc:
        logger.exception("Unhandled error in search_all for query %r: %s", q, exc)
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred while executing the search.",
        ) from exc

    logger.info("GET /api/v1/search  q=%r → %d result(s)", q, len(results))
    return results


@app.get("/health", tags=["Ops"], summary="Health check")
async def health() -> JSONResponse:
    """Lightweight liveness probe for Docker / load-balancer health checks."""
    return JSONResponse({"status": "ok", "service": "allection-scraper"})

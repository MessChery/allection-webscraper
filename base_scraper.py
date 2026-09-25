"""
base_scraper.py
---------------
Abstract base class defining the scraper strategy interface for the
Allection web scraping microservice.

All concrete scrapers must inherit from BaseScraperStrategy and implement
the `search` coroutine.
"""

from abc import ABC, abstractmethod

from models import ScrapedItem


class BaseScraperStrategy(ABC):
    """
    Abstract strategy that every site-specific scraper must implement.

    Design follows the Strategy Pattern — the calling service depends only
    on this interface, making individual scrapers fully interchangeable
    without changing client code.

    Usage
    -----
    Subclass this class and implement `search`:

        class EbayScraper(BaseScraperStrategy):
            async def search(self, query: str) -> list[ScrapedItem]:
                ...

    Then use it from an async context:

        scraper: BaseScraperStrategy = EbayScraper()
        results = await scraper.search("vintage camera")
    """

    @abstractmethod
    async def search(self, query: str) -> list[ScrapedItem]:
        """
        Search the target website for items matching *query* and return
        the results as a list of ScrapedItem instances.

        Parameters
        ----------
        query : str
            The search term or product name to look up.

        Returns
        -------
        list[ScrapedItem]
            A (possibly empty) list of scraped product items. The list
            must never contain None entries.

        Raises
        ------
        NotImplementedError
            Raised automatically by the ABC machinery if a subclass
            fails to override this method.
        """
        ...  # pragma: no cover

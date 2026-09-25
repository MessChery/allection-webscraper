# strategies/__init__.py
# Exposes all concrete scraper strategies as a flat namespace.

from strategies.enterprise_api import FnacPtScraper
from strategies.shopify import ShopifyScraper

__all__ = ["FnacPtScraper", "ShopifyScraper"]

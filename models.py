"""
models.py
---------
Pydantic data models for the Allection web scraping microservice.
"""

from pydantic import BaseModel, HttpUrl, field_validator


class ScrapedItem(BaseModel):
    """
    Represents a single product item scraped from a source website.

    Attributes:
        source_website  (str):   The hostname or name of the website the item was scraped from.
        item_title      (str):   The display title / name of the product.
        price           (float): The listed price of the product.
        currency        (str):   ISO 4217 currency code (e.g. "USD", "EUR", "GBP").
        direct_buy_url  (str):   The direct URL to the product's purchase page.
    """

    source_website: str
    item_title: str
    price: float
    currency: str
    direct_buy_url: str

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, value: float) -> float:
        """Ensure the price is a non-negative number."""
        if value < 0:
            raise ValueError(f"price must be non-negative, got {value}")
        return value

    @field_validator("currency")
    @classmethod
    def currency_must_be_valid_iso(cls, value: str) -> str:
        """Normalize and loosely validate the ISO 4217 currency code."""
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError(
                f"currency must be a 3-letter ISO 4217 code (e.g. 'USD'), got '{value}'"
            )
        return normalized

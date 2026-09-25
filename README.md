# Allection Webscraper

> Async web scraping microservice powering the [Allection](https://allection.app) platform — aggregates product listings and pricing data from multiple e-commerce backends into a single unified REST API.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    FastAPI Gateway                       │
│              GET /api/v1/search?q=…                     │
└───────────────────────┬─────────────────────────────────┘
                        │
              ScraperManager (router.py)
              asyncio.gather — concurrent fan-out
                        │
          ┌─────────────┴──────────────┐
          │                            │
  ShopifyScraper               FnacPtScraper
  (Shopify JSON API)           (Enterprise XHR API)
          │                            │
          └─────────────┬──────────────┘
                        │
                  ScraperClient
             httpx · rate limiting · retry
```

**Strategy Pattern** — every backend implements `BaseScraperStrategy`. Adding a new store = one new file, one new line in `ScraperManager`.

---

## Features

- ⚡ **Fully async** — `httpx.AsyncClient` + `asyncio.gather` for concurrent scraping
- 🛡️ **Resilient** — exponential backoff retry (3×) with random jitter on timeouts & 5xx errors
- 🤝 **Polite** — per-domain rate limiting (≥ 2 s between requests to the same host)
- 🔌 **Extensible** — Strategy Pattern makes adding new backends trivial
- 🐳 **Production-ready** — Dockerised, non-root user, health check, resource limits
- ✅ **Type-safe** — Pydantic v2 models with strict field validation throughout

---

## Project Structure

```
allection-webscraper/
├── main.py                # FastAPI app — lifespan, /api/v1/search, /health
├── router.py              # ScraperManager — orchestrates all strategies
├── client.py              # ScraperClient — httpx wrapper, rate limit, retry
├── base_scraper.py        # BaseScraperStrategy ABC
├── models.py              # ScrapedItem Pydantic model
├── strategies/
│   ├── shopify.py         # Shopify hidden JSON API strategy
│   └── enterprise_api.py  # Enterprise XHR/Algolia API strategy (FNAC.pt)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .dockerignore
```

---

## Quickstart

### Local (with virtualenv)

```bash
git clone https://github.com/MessChery/allection-webscraper.git
cd allection-webscraper

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

uvicorn main:app --reload --port 8000
```

### Docker Compose

```bash
docker compose up -d --build
```

The API will be available at `http://localhost:8000`.

---

## API Reference

### `GET /api/v1/search`

Search all registered backends concurrently for a product query.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `q` | `string` | ✅ | Search term (1–200 characters) |

**Example request:**
```bash
curl "http://localhost:8000/api/v1/search?q=Nike+Air+Force+1"
```

**Example response** `200 OK`:
```json
[
  {
    "source_website": "fnac.pt",
    "item_title": "Nike Air Force 1 '07",
    "price": 109.99,
    "currency": "EUR",
    "direct_buy_url": "https://www.fnac.pt/product/nike-air-force-1"
  }
]
```

| Status | Meaning |
|---|---|
| `200` | Results returned (may be an empty array — no 404 on zero results) |
| `422` | `q` missing, empty, or exceeds 200 characters |
| `500` | Unexpected internal error |

---

### `GET /health`

Liveness probe for Docker / load-balancer health checks.

```bash
curl http://localhost:8000/health
# {"status": "ok", "service": "allection-scraper"}
```

---

### Interactive Docs

| URL | Interface |
|---|---|
| `http://localhost:8000/docs` | Swagger UI |
| `http://localhost:8000/redoc` | ReDoc |

---

## Data Model

```python
class ScrapedItem(BaseModel):
    source_website: str    # hostname of the scraped store
    item_title:     str    # product display name
    price:          float  # listed price (non-negative)
    currency:       str    # ISO 4217 code, e.g. "EUR" (normalised to uppercase)
    direct_buy_url: str    # direct link to the product page
```

---

## Adding a New Scraping Strategy

1. **Create** `strategies/my_store.py` and subclass `BaseScraperStrategy`:

```python
from base_scraper import BaseScraperStrategy
from client import ScraperClient
from models import ScrapedItem

class MyStoreScraper(BaseScraperStrategy):
    def __init__(self, client: ScraperClient) -> None:
        self._client = client
        self.domain = "mystore.com"

    async def search(self, query: str) -> list[ScrapedItem]:
        data = await self._client.get(
            f"https://mystore.com/api/search?q={query}",
            as_json=True,
        )
        # … parse and return list[ScrapedItem]
```

2. **Register** it in `router.py`:

```python
from strategies.my_store import MyStoreScraper

# inside ScraperManager.__init__:
self._registry.append(MyStoreScraper(self._client))
```

That's it — `search_all()` will fan it out automatically.

---

## Configuration

All tuneable constants live at the top of [`client.py`](client.py):

| Constant | Default | Description |
|---|---|---|
| `RATE_LIMIT_SECONDS` | `2.0` | Minimum seconds between requests to the same domain |
| `MAX_RETRIES` | `3` | Maximum retry attempts on timeout / 5xx |
| `BACKOFF_BASE` | `1.5` | Exponential backoff base delay (seconds) |
| `BACKOFF_MAX` | `30.0` | Maximum single retry delay (seconds) |
| `JITTER_RANGE` | `0.5` | ± random jitter added to each backoff delay |
| `REQUEST_TIMEOUT` | `15.0` | Per-request timeout (seconds) |

---

## Deployment

The service is designed to run on an Ubuntu VM with Docker.

```bash
# Pull latest and rebuild
git pull && docker compose up -d --build

# Stream logs
docker compose logs -f allection-scraper

# Stop
docker compose down
```

The container will automatically restart on crashes and VM reboots (`restart: always`).

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.14 |
| Web framework | FastAPI 0.115 |
| HTTP client | httpx 0.28 (async) |
| Data validation | Pydantic v2 |
| ASGI server | Uvicorn + uvloop |
| Containerisation | Docker / Docker Compose |

---

## Roadmap

- [ ] Add `.post()` to `ScraperClient` to activate real enterprise API calls
- [ ] Wire real Network Tab values for FNAC.pt Algolia endpoint
- [ ] Unit & integration test suite (`pytest` + `pytest-asyncio` + `respx`)
- [ ] Result deduplication and price-ranked sorting
- [ ] Currency normalisation to a single base currency
- [ ] CI/CD pipeline via GitHub Actions

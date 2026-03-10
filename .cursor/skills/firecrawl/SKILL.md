---
name: firecrawl
description: Uses Firecrawl to scrape web pages to clean markdown, search and scrape top results, crawl entire websites, or map a domain. Use when the user needs to scrape a URL, crawl a site, search the web and get page content, or discover/map URLs on a domain.
---

# Firecrawl

## When to Use

- Scrape a single page to clean markdown for LLMs or processing
- Search the web and scrape the top results (query → markdown)
- Crawl an entire website with limits and timeout
- Map a domain to discover/index URLs (search, sitemap options)

## Setup

API key: set `FIRECRAWL_API_KEY` in `.env` (or environment). Get a key at [firecrawl.dev](https://firecrawl.dev).

Project helper (recommended): use `firecrawl_tools.scrape_url`, `search_and_scrape`, `crawl_site`, `map_domain` — they read the key from env and return errors if unset.

Direct SDK (firecrawl-py v4):

```python
import os
from firecrawl import FirecrawlApp

app = FirecrawlApp(api_key=os.getenv("FIRECRAWL_API_KEY"))
```

## Scrape One URL (v4: `scrape`)

Returns a Document (markdown, metadata).

```python
doc = app.scrape("https://example.com", only_main_content=True)
# doc has markdown/content and metadata
```

Or use project helper: `firecrawl_tools.scrape_url(url)`.

## Search and Scrape Top Results (v4: `search`)

```python
result = app.search("what is Firecrawl?", limit=5)
# result is SearchData (scraped content for top results)
```

Or: `firecrawl_tools.search_and_scrape(query, limit=5)`.

## Crawl a Website (v4: `crawl`)

Starts crawl and waits until done or timeout. Returns CrawlJob (status, data).

```python
job = app.crawl("https://example.com", limit=100, timeout=300)
# job.status, job.data
```

Or: `firecrawl_tools.crawl_site(start_url, limit=100, timeout=300)`.

To start without waiting, use `app.start_crawl(url, limit=...)` then `app.get_crawl_status(job_id)` to poll.

## Map a Domain (v4: `map`)

Discover URLs on a domain (optional search query, sitemap, limit).

```python
map_result = app.map("https://example.com", search="pricing", limit=50)
```

Or: `firecrawl_tools.map_domain(url, search=..., limit=...)`.

## CLI (Optional)

User can run locally:

```bash
npx -y firecrawl-cli@latest init --all --browser
```

After that, the CLI can scrape/crawl from the command line; the agent can suggest CLI commands when appropriate.

## Notes

- Prefer reading `FIRECRAWL_API_KEY` from environment; do not hardcode keys.
- For LLM extraction with a schema, use `app.extract` (v4) or see Firecrawl docs.

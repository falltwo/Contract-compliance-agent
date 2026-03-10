"""Firecrawl 工具：單頁擷取、搜尋並擷取、整站爬取、網域對應。API key 請設於 .env 的 FIRECRAWL_API_KEY。"""
from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv


def get_firecrawl_app():
    """取得 FirecrawlApp 實例（firecrawl-py v4）；若未設定 API key 則回傳 None。"""
    load_dotenv()
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        return None
    from firecrawl import FirecrawlApp
    return FirecrawlApp(api_key=api_key)


def scrape_url(url: str, *, only_main_content: bool = True) -> str | dict[str, Any] | Any:
    """
    擷取單一 URL 為乾淨 markdown（v4 API：scrape）。
    若未設定 FIRECRAWL_API_KEY 則回傳錯誤訊息字串。
    """
    app = get_firecrawl_app()
    if app is None:
        return "未設定 FIRECRAWL_API_KEY，請在 .env 加入（可至 https://firecrawl.dev 取得）。"
    try:
        doc = app.scrape(url, only_main_content=only_main_content)
        return doc.model_dump() if hasattr(doc, "model_dump") else doc
    except Exception as e:
        return f"Firecrawl 擷取失敗：{e!s}"


def search_and_scrape(query: str, *, limit: int | None = None) -> str | Any:
    """
    搜尋網路並擷取前幾筆結果的 markdown（v4 API：search）。
    若未設定 FIRECRAWL_API_KEY 則回傳錯誤訊息字串。
    """
    app = get_firecrawl_app()
    if app is None:
        return "未設定 FIRECRAWL_API_KEY，請在 .env 加入（可至 https://firecrawl.dev 取得）。"
    try:
        result = app.search(query, limit=limit)
        return result.model_dump() if hasattr(result, "model_dump") else result
    except Exception as e:
        return f"Firecrawl 搜尋/擷取失敗：{e!s}"


def crawl_site(
    start_url: str,
    *,
    limit: int = 100,
    timeout: int = 300,
) -> str | dict[str, Any] | Any:
    """
    爬取整站（v4 API：crawl，會等待完成或逾時）。
    若未設定 FIRECRAWL_API_KEY 則回傳錯誤訊息字串。
    """
    app = get_firecrawl_app()
    if app is None:
        return "未設定 FIRECRAWL_API_KEY，請在 .env 加入（可至 https://firecrawl.dev 取得）。"
    try:
        job = app.crawl(start_url, limit=limit, timeout=timeout)
        return job.model_dump() if hasattr(job, "model_dump") else job
    except Exception as e:
        return f"Firecrawl 爬取失敗：{e!s}"


def map_domain(
    url: str,
    *,
    search: str | None = None,
    limit: int | None = None,
) -> str | dict[str, Any] | Any:
    """
    對網域做 URL 對應/探索（v4 API：map）。
    若未設定 FIRECRAWL_API_KEY 則回傳錯誤訊息字串。
    """
    app = get_firecrawl_app()
    if app is None:
        return "未設定 FIRECRAWL_API_KEY，請在 .env 加入（可至 https://firecrawl.dev 取得）。"
    try:
        result = app.map(url, search=search, limit=limit)
        return result.model_dump() if hasattr(result, "model_dump") else result
    except Exception as e:
        return f"Firecrawl 對應失敗：{e!s}"

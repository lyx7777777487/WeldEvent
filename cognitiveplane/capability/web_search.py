"""Web search provider — Tavily REST API + DuckDuckGo Lite fallback.

No extra pip dependencies — uses urllib only.
- Tavily: requires TAVILY_API_KEY env var (free tier: 1000 searches/month)
- DuckDuckGo Lite: zero-config fallback, no API key needed
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class WebSearchResult:
    title: str
    url: str
    snippet: str


@dataclass
class WebSearchResponse:
    results: list[WebSearchResult] = field(default_factory=list)
    error: str | None = None


class WebSearchProvider(ABC):
    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> WebSearchResponse: ...


class TavilySearchProvider(WebSearchProvider):
    """Tavily search — purpose-built for AI agents, returns clean JSON."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("TAVILY_API_KEY", "")

    async def search(self, query: str, max_results: int = 5) -> WebSearchResponse:
        if not self._api_key:
            return WebSearchResponse(error="TAVILY_API_KEY not configured")

        url = "https://api.tavily.com/search"
        payload = json.dumps({
            "api_key": self._api_key,
            "query": query,
            "max_results": max_results,
            "include_answer": False,
            "search_depth": "basic",
        }).encode()

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            results = [
                WebSearchResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("content", ""),
                )
                for r in data.get("results", [])
            ]
            return WebSearchResponse(results=results)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode()[:200]
            except Exception:
                pass
            return WebSearchResponse(error=f"HTTP {exc.code}: {body}")
        except Exception as exc:
            return WebSearchResponse(error=str(exc)[:200])


class DuckDuckGoSearchProvider(WebSearchProvider):
    """DuckDuckGo Lite search — zero-config, no API key needed.

    Uses POST to lite.duckduckgo.com. Parses HTML for result links
    and snippets. Less structured than Tavily but works out-of-box.
    """

    _USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0"
    )

    async def search(self, query: str, max_results: int = 5) -> WebSearchResponse:
        url = "https://lite.duckduckgo.com/lite/"
        data = urllib.parse.urlencode({"q": query, "kl": "wt-wt"}).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "User-Agent": self._USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            return WebSearchResponse(error=str(exc)[:200])

        results: list[WebSearchResult] = []
        # DDG Lite: result links are <a> tags with external hrefs
        # Snippets are in subsequent <td> elements
        anchors = re.findall(
            r'<a[^>]*class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            re.DOTALL,
        )
        if not anchors:
            # Fallback: find all external links (non-DDG, non-fragment)
            anchors = re.findall(
                r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                html,
                re.DOTALL,
            )
            # Filter out DDG internal links
            anchors = [
                (href, text) for href, text in anchors
                if "duckduckgo.com" not in href
            ]

        # Extract snippets from <td class="result-snippet">
        snippet_matches = re.findall(
            r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
            html,
            re.DOTALL,
        )

        for i, (href, title_html) in enumerate(anchors[:max_results]):
            title = re.sub(r"<[^>]+>", "", title_html).strip()
            snippet = ""
            if i < len(snippet_matches):
                snippet = re.sub(r"<[^>]+>", "", snippet_matches[i]).strip()
            results.append(WebSearchResult(title=title, url=href, snippet=snippet))

        return WebSearchResponse(results=results)


class AutoWebSearchProvider(WebSearchProvider):
    """Auto-selects best available search backend.

    1. Tavily if TAVILY_API_KEY is set
    2. DuckDuckGo as zero-config fallback
    """

    def __init__(self) -> None:
        tavily_key = os.getenv("TAVILY_API_KEY", "")
        if tavily_key:
            self._provider: WebSearchProvider = TavilySearchProvider(tavily_key)
        else:
            self._provider = DuckDuckGoSearchProvider()

    async def search(self, query: str, max_results: int = 5) -> WebSearchResponse:
        return await self._provider.search(query, max_results)


class StubWebSearchProvider(WebSearchProvider):
    """Returns empty results — used when web search is disabled."""

    async def search(self, query: str, max_results: int = 5) -> WebSearchResponse:
        return WebSearchResponse(error="Web search not available (no API key configured)")

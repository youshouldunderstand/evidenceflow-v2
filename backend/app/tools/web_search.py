"""搜索 Provider 抽象、Google 官方实现与确定性 Fake。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.demo_data import DEMO_SOURCE_TITLE, DEMO_SOURCE_URL
from app.reliability.exceptions import NetworkError, RateLimitError, SearchError
from app.schemas.common import NonEmptyStr, SourceType


GOOGLE_CUSTOM_SEARCH_ENDPOINT = (
    "https://customsearch.googleapis.com/customsearch/v1"
)
TAVILY_SEARCH_ENDPOINT = "https://api.tavily.com/search"
_OFFICIAL_DOCUMENTATION_HOSTS = frozenset(
    {
        "docs.python.org",
        "git-scm.com",
        "peps.python.org",
        "www.git-scm.com",
        "sqlite.org",
        "www.sqlite.org",
    }
)


def classify_source_url(url: str) -> SourceType:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    path = parsed.path.casefold().rstrip("/")
    is_cpython_repository = hostname == "github.com" and (
        path == "/python/cpython"
        or path.startswith("/python/cpython/blob/")
        or path.startswith("/python/cpython/tree/")
    )
    if hostname in _OFFICIAL_DOCUMENTATION_HOSTS or is_cpython_repository:
        return SourceType.OFFICIAL
    return SourceType.BLOG


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: NonEmptyStr
    url: NonEmptyStr
    source_type: SourceType = SourceType.BLOG


class SearchProvider(Protocol):
    async def search(self, query: str) -> list[SearchResult]: ...


class _GoogleSearchItem(BaseModel):
    """Google 响应中当前业务真正使用的最小字段集合。"""

    model_config = ConfigDict(extra="ignore")

    title: NonEmptyStr
    link: NonEmptyStr


class _GoogleSearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[_GoogleSearchItem] = Field(default_factory=list)


class _TavilySearchItem(BaseModel):
    """Tavily 搜索结果中证据发现阶段需要的最小字段。"""

    model_config = ConfigDict(extra="ignore")

    title: NonEmptyStr
    url: NonEmptyStr


class _TavilySearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: list[_TavilySearchItem] = Field(default_factory=list)


class GoogleSearchProvider:
    """Google Custom Search JSON API 的最小异步客户端。

    搜索摘要不会进入 Evidence；本类只提供候选 URL，网页正文仍由 WebReader
    独立读取并保存快照。
    """

    def __init__(
        self,
        *,
        api_key: str,
        search_engine_id: str,
        result_count: int = 5,
        language: str | None = None,
        safe_search: bool = True,
        timeout_seconds: float = 15,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise SearchError("GOOGLE_SEARCH_API_KEY 不能为空")
        if not search_engine_id:
            raise SearchError("GOOGLE_SEARCH_ENGINE_ID 不能为空")
        if not 1 <= result_count <= 10:
            raise ValueError("Google 单次搜索结果数必须在 1 到 10 之间")
        self._api_key = api_key
        self._search_engine_id = search_engine_id
        self._result_count = result_count
        self._language = language
        self._safe_search = safe_search
        self._timeout = httpx.Timeout(timeout_seconds)
        self._transport = transport

    async def search(self, query: str) -> list[SearchResult]:
        params: dict[str, str | int] = {
            "key": self._api_key,
            "cx": self._search_engine_id,
            "q": query,
            "num": self._result_count,
            "safe": "active" if self._safe_search else "off",
        }
        if self._language:
            params["lr"] = self._language

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    GOOGLE_CUSTOM_SEARCH_ENDPOINT,
                    params=params,
                )
        except httpx.TimeoutException as exc:
            raise NetworkError("Google Search 请求超时") from exc
        except httpx.RequestError as exc:
            raise NetworkError("Google Search 网络请求失败") from exc

        if response.status_code == 429:
            raise RateLimitError("Google Search 返回 HTTP 429")
        if response.status_code >= 500:
            raise NetworkError(
                f"Google Search 暂时不可用（HTTP {response.status_code}）"
            )
        if response.status_code >= 400:
            raise SearchError(
                f"Google Search 拒绝请求（HTTP {response.status_code}）"
            )

        try:
            payload = _GoogleSearchResponse.model_validate_json(response.content)
        except ValidationError as exc:
            raise SearchError("Google Search 响应结构无效") from exc

        results: list[SearchResult] = []
        seen_urls: set[str] = set()
        for item in payload.items:
            parsed = urlsplit(item.link)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            if item.link in seen_urls:
                continue
            seen_urls.add(item.link)
            # Provider 不返回权威性等级；只提升维护的官方文档域名。
            results.append(
                SearchResult(
                    title=item.title,
                    url=item.link,
                    source_type=classify_source_url(item.link),
                )
            )
        return results


class TavilySearchProvider:
    """Tavily Search API 的最小异步客户端。

    Tavily 返回的 answer、content 和 raw_content 都不直接进入 Evidence。这里只取
    标题与 URL，之后仍由独立 WebReader 抓取网页并保存可复现快照。
    """

    def __init__(
        self,
        *,
        api_key: str,
        max_results: int = 5,
        search_depth: str = "basic",
        timeout_seconds: float = 15,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise SearchError("TAVILY_API_KEY 不能为空")
        if not 1 <= max_results <= 20:
            raise ValueError("Tavily 单次搜索结果数必须在 1 到 20 之间")
        if search_depth not in {"basic", "fast", "ultra-fast", "advanced"}:
            raise ValueError("Tavily search_depth 配置无效")
        self._api_key = api_key
        self._max_results = max_results
        self._search_depth = search_depth
        self._timeout = httpx.Timeout(timeout_seconds)
        self._transport = transport

    async def search(self, query: str) -> list[SearchResult]:
        # 显式关闭模型答案、原始正文和图片，既节省额度，也避免绕过证据快照链。
        request_body = {
            "query": query,
            "search_depth": self._search_depth,
            "max_results": self._max_results,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    TAVILY_SEARCH_ENDPOINT,
                    headers=headers,
                    json=request_body,
                )
        except httpx.TimeoutException as exc:
            raise NetworkError("Tavily Search 请求超时") from exc
        except httpx.RequestError as exc:
            raise NetworkError("Tavily Search 网络请求失败") from exc

        if response.status_code == 429:
            raise RateLimitError("Tavily Search 返回 HTTP 429")
        if response.status_code >= 500:
            raise NetworkError(
                f"Tavily Search 暂时不可用（HTTP {response.status_code}）"
            )
        if response.status_code >= 400:
            raise SearchError(
                f"Tavily Search 拒绝请求（HTTP {response.status_code}）"
            )

        try:
            payload = _TavilySearchResponse.model_validate_json(response.content)
        except ValidationError as exc:
            raise SearchError("Tavily Search 响应结构无效") from exc

        results: list[SearchResult] = []
        seen_urls: set[str] = set()
        for item in payload.results:
            parsed = urlsplit(item.url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            if item.url in seen_urls:
                continue
            seen_urls.add(item.url)
            # 相关度不等于权威性；只提升维护的官方文档域名。
            results.append(
                SearchResult(
                    title=item.title,
                    url=item.url,
                    source_type=classify_source_url(item.url),
                )
            )
        return results


class FakeSearchProvider:
    """供测试和显式标记演示使用的确定性 Provider。"""

    def __init__(
        self,
        results_by_query: Mapping[str, Sequence[SearchResult]] | None = None,
        default_results: Sequence[SearchResult] | None = None,
    ) -> None:
        self._results_by_query = {
            key: list(value) for key, value in (results_by_query or {}).items()
        }
        self._default_results = list(
            default_results
            or [
                SearchResult(
                    title=DEMO_SOURCE_TITLE,
                    url=DEMO_SOURCE_URL,
                    source_type=SourceType.OFFICIAL,
                )
            ]
        )

    async def search(self, query: str) -> list[SearchResult]:
        return list(self._results_by_query.get(query, self._default_results))

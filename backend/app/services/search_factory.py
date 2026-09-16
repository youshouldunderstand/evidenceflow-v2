"""根据显式配置构建成对的搜索与网页读取实现。"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.tools.web_reader import FakeWebReader, HttpWebReader, WebReader
from app.tools.web_search import (
    FakeSearchProvider,
    GoogleSearchProvider,
    SearchProvider,
    TavilySearchProvider,
)


@dataclass(frozen=True)
class SearchStack:
    provider: SearchProvider
    web_reader: WebReader
    notice: str


def build_search_stack(settings: Settings) -> SearchStack:
    """构建匹配的 SearchProvider 与 WebReader，禁止真假来源混用。"""

    if settings.search_provider == "fake":
        return SearchStack(
            provider=FakeSearchProvider(),
            web_reader=FakeWebReader(),
            notice=(
                "Fake Search/Web providers; sources are deterministic demo data, "
                "not internet research."
            ),
        )

    if settings.search_provider == "tavily":
        if not settings.tavily_api_key:
            raise ValueError(
                "SEARCH_PROVIDER=tavily requires TAVILY_API_KEY"
            )
        return SearchStack(
            provider=TavilySearchProvider(
                api_key=settings.tavily_api_key,
                max_results=settings.tavily_search_max_results,
                search_depth=settings.tavily_search_depth,
            ),
            web_reader=HttpWebReader(),
            notice=(
                "Tavily Search API with live WebReader; search results are "
                "candidate URLs and evidence comes only from fetched snapshots."
            ),
        )

    if not settings.google_search_api_key:
        raise ValueError(
            "SEARCH_PROVIDER=google requires GOOGLE_SEARCH_API_KEY"
        )
    if not settings.google_search_engine_id:
        raise ValueError(
            "SEARCH_PROVIDER=google requires GOOGLE_SEARCH_ENGINE_ID"
        )
    return SearchStack(
        provider=GoogleSearchProvider(
            api_key=settings.google_search_api_key,
            search_engine_id=settings.google_search_engine_id,
            result_count=settings.google_search_num_results,
            language=settings.google_search_language,
        ),
        web_reader=HttpWebReader(),
        notice=(
            "Google Custom Search JSON API with live WebReader; search results "
            "are candidate URLs and evidence comes only from fetched snapshots."
        ),
    )

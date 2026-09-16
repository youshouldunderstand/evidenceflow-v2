"""安全网页读取抽象与 HTTP 实现。"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.demo_data import DEMO_PAGE_HTML, DEMO_SOURCE_TITLE, DEMO_SOURCE_URL
from app.reliability.exceptions import FetchError, NetworkError, RateLimitError
from app.schemas.common import NonEmptyStr


ALLOWED_CONTENT_TYPES = {
    "text/html",
    "text/plain",
    "application/xhtml+xml",
}


class PageContent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: NonEmptyStr
    url: NonEmptyStr
    raw_content: str
    extracted_text: str
    retrieved_at: AwareDatetime
    content_type: str = "text/plain"
    extraction_version: str = "readable-v2"


class WebReader(Protocol):
    async def read(self, url: str) -> PageContent: ...


def validate_public_url(url: str) -> None:
    """拒绝非 HTTP、localhost、私网和特殊网络目标。"""

    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise FetchError("Only http:// and https:// URLs are allowed")
    if not parsed.hostname:
        raise FetchError("URL must include a hostname")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise FetchError("localhost URLs are not allowed")

    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, None)}
    except socket.gaierror as exc:
        raise NetworkError(f"Could not resolve host: {hostname}") from exc

    if not addresses:
        raise NetworkError(f"Host resolved to no addresses: {hostname}")

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise FetchError(f"Private or special network address is not allowed: {ip}")


def extract_readable_text(raw_content: str, content_type: str) -> tuple[str, str]:
    """移除非正文和已删除内容，避免将废弃结论作为当前证据。"""

    if content_type == "text/plain":
        return "Untitled text source", raw_content

    soup = BeautifulSoup(raw_content, "html.parser")
    # Raw HTML remains in the source snapshot for auditing historical content.
    # Plain text loses strikethrough semantics, so withdrawn claims must not
    # enter the current-evidence text used by extraction and quote validation.
    for element in soup(["script", "style", "noscript", "template", "s", "del", "strike"]):
        element.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else "Untitled source"
    return title, soup.get_text("\n", strip=True)


class HttpWebReader:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10,
        max_response_bytes: int = 2_000_000,
        max_redirects: int = 3,
        user_agent: str = "EvidenceFlow/0.1",
        transport: httpx.AsyncBaseTransport | None = None,
        url_validator: Callable[[str], None] = validate_public_url,
    ) -> None:
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max_redirects
        self._user_agent = user_agent
        self._transport = transport
        self._validate_url = url_validator

    async def read(self, url: str) -> PageContent:
        current_url = url
        async with httpx.AsyncClient(
            timeout=self._timeout,
            headers={"User-Agent": self._user_agent},
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            for redirect_count in range(self._max_redirects + 1):
                self._validate_url(current_url)
                try:
                    async with client.stream("GET", current_url) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                raise FetchError("Redirect response did not include Location")
                            if redirect_count >= self._max_redirects:
                                raise FetchError("Maximum redirect count exceeded")
                            current_url = urljoin(current_url, location)
                            continue
                        if response.status_code == 429:
                            raise RateLimitError("Web source returned HTTP 429")
                        if response.status_code == 404:
                            raise FetchError("Web source returned HTTP 404")
                        if response.status_code >= 400:
                            raise FetchError(
                                f"Web source returned HTTP {response.status_code}"
                            )

                        content_type = response.headers.get("content-type", "")
                        media_type = content_type.split(";", 1)[0].strip().lower()
                        if media_type not in ALLOWED_CONTENT_TYPES:
                            raise FetchError(f"Unsupported Content-Type: {media_type}")

                        chunks: list[bytes] = []
                        total_size = 0
                        async for chunk in response.aiter_bytes():
                            total_size += len(chunk)
                            if total_size > self._max_response_bytes:
                                raise FetchError("Web response exceeded maximum size")
                            chunks.append(chunk)

                        encoding = response.encoding or "utf-8"
                        raw_content = b"".join(chunks).decode(
                            encoding, errors="replace"
                        )
                        title, extracted_text = extract_readable_text(
                            raw_content, media_type
                        )
                        if not extracted_text.strip():
                            raise FetchError("Web source did not contain readable text")
                        return PageContent(
                            title=title,
                            url=str(response.url),
                            raw_content=raw_content,
                            extracted_text=extracted_text,
                            retrieved_at=datetime.now(timezone.utc),
                            content_type=media_type,
                            extraction_version="readable-v2",
                        )
                except httpx.TimeoutException as exc:
                    raise NetworkError(f"Timed out while reading {current_url}") from exc
                except httpx.NetworkError as exc:
                    raise NetworkError(f"Network error while reading {current_url}") from exc

        raise FetchError("Web reader ended without a result")


class FakeWebReader:
    """读取 FakeSearchProvider 所给 URL 的确定性测试实现。"""

    def __init__(self, pages: Mapping[str, PageContent] | None = None) -> None:
        default_page = PageContent(
            title=DEMO_SOURCE_TITLE,
            url=DEMO_SOURCE_URL,
            raw_content=DEMO_PAGE_HTML,
            extracted_text=extract_readable_text(DEMO_PAGE_HTML, "text/html")[1],
            retrieved_at=datetime.now(timezone.utc),
        )
        self._pages = dict(pages or {DEMO_SOURCE_URL: default_page})

    async def read(self, url: str) -> PageContent:
        try:
            return self._pages[url]
        except KeyError as exc:
            raise FetchError(f"Fake reader has no page for URL: {url}") from exc

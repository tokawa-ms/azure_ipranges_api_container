"""Secure discovery and download of the Azure Service Tags document."""

from __future__ import annotations

import asyncio
import html
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from azure_ipranges_api.config import Settings


_FILENAME_PATTERN = re.compile(r"ServiceTags_Public_[0-9]{8}\.json")
_EMBEDDED_LINK_PATTERN = re.compile(
    r"""(?P<url>
        https://[^\s"'<>]+ServiceTags_Public_[0-9]{8}\.json(?:\?[^\s"'<>]*)?
        |
        //[^\s"'<>]+ServiceTags_Public_[0-9]{8}\.json(?:\?[^\s"'<>]*)?
        |
        /[^\s"'<>]*ServiceTags_Public_[0-9]{8}\.json(?:\?[^\s"'<>]*)?
        |
        [A-Za-z0-9._~-][^\s"'<>]*ServiceTags_Public_[0-9]{8}\.json
        (?:\?[^\s"'<>]*)?
    )""",
    re.VERBOSE | re.IGNORECASE,
)
_DOWNLOAD_HOSTS = frozenset({"download.microsoft.com"})
_RETRYABLE_STATUS_CODES = frozenset({429})


class DownloaderErrorReason(StrEnum):
    """Stable, body-free categories for downloader failures."""

    PAGE_REQUEST_FAILED = "page_request_failed"
    PAGE_HTTP_ERROR = "page_http_error"
    DOWNLOAD_LINK_NOT_FOUND = "download_link_not_found"
    DOWNLOAD_LINK_INVALID = "download_link_invalid"
    DOWNLOAD_REQUEST_FAILED = "download_request_failed"
    DOWNLOAD_HTTP_ERROR = "download_http_error"
    REDIRECT_LIMIT_EXCEEDED = "redirect_limit_exceeded"


class DownloaderError(RuntimeError):
    """A safe downloader failure that never includes an upstream body."""

    def __init__(
        self,
        reason: DownloaderErrorReason,
        *,
        status_code: int | None = None,
    ) -> None:
        self.reason = reason
        self.status_code = status_code
        message = reason.value
        if status_code is not None:
            message = f"{message} (HTTP {status_code})"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class DownloadedServiceTags:
    """The unparsed bytes downloaded from Microsoft's distribution endpoint."""

    source_url: str
    filename: str
    content: bytes


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del tag
        self.values.extend(value for _, value in attrs if value)

    handle_startendtag = handle_starttag


SleepCallable = Callable[[float], Awaitable[None]]


class ServiceTagsDownloader:
    """Discover and securely download the current public Service Tags JSON."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: SleepCallable = asyncio.sleep,
        max_redirects: int = 5,
    ) -> None:
        if max_redirects < 0:
            raise ValueError("max_redirects must be non-negative")
        self._settings = settings
        self._client = client
        self._sleep = sleep
        self._max_redirects = max_redirects
        self._timeout = httpx.Timeout(settings.http_timeout_seconds)

    async def download(self) -> DownloadedServiceTags:
        """Fetch the Microsoft page, discover its JSON link, and download bytes."""

        if self._client is not None:
            return await self._download_with_client(self._client)

        async with httpx.AsyncClient(follow_redirects=False) as client:
            return await self._download_with_client(client)

    async def _download_with_client(
        self, client: httpx.AsyncClient
    ) -> DownloadedServiceTags:
        page_response = await self._get_following_redirects(
            client,
            self._settings.download_page_url,
            request_reason=DownloaderErrorReason.PAGE_REQUEST_FAILED,
            status_reason=DownloaderErrorReason.PAGE_HTTP_ERROR,
            validate_url=self._validate_https_url,
        )
        page_url = str(page_response.url)
        try:
            download_url = self._discover_download_url(
                page_response.text, page_url
            )
        finally:
            await page_response.aclose()

        response = await self._get_following_redirects(
            client,
            download_url,
            request_reason=DownloaderErrorReason.DOWNLOAD_REQUEST_FAILED,
            status_reason=DownloaderErrorReason.DOWNLOAD_HTTP_ERROR,
            validate_url=self._validate_download_url,
        )
        try:
            final_url, filename = self._validate_download_url(str(response.url))
            return DownloadedServiceTags(
                source_url=final_url,
                filename=filename,
                content=response.content,
            )
        finally:
            await response.aclose()

    def _discover_download_url(self, page_html: str, page_url: str) -> str:
        collector = _LinkCollector()
        collector.feed(page_html)

        normalized_html = self._normalize_embedded_content(page_html)
        candidates = list(collector.values)
        candidates.extend(
            match.group("url")
            for match in _EMBEDDED_LINK_PATTERN.finditer(normalized_html)
        )

        saw_named_candidate = False
        for candidate in candidates:
            normalized = self._normalize_embedded_content(candidate).strip(
                " \t\r\n'\"(),;"
            )
            if not _FILENAME_PATTERN.search(normalized):
                continue
            saw_named_candidate = True
            resolved = urljoin(page_url, normalized)
            try:
                validated, _ = self._validate_download_url(resolved)
            except ValueError:
                continue
            return validated

        reason = (
            DownloaderErrorReason.DOWNLOAD_LINK_INVALID
            if saw_named_candidate
            else DownloaderErrorReason.DOWNLOAD_LINK_NOT_FOUND
        )
        raise DownloaderError(reason)

    async def _get_following_redirects(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        request_reason: DownloaderErrorReason,
        status_reason: DownloaderErrorReason,
        validate_url: Callable[[str], object],
    ) -> httpx.Response:
        current_url = url
        for redirect_count in range(self._max_redirects + 1):
            try:
                validate_url(current_url)
            except (TypeError, ValueError) as exc:
                raise DownloaderError(
                    DownloaderErrorReason.DOWNLOAD_LINK_INVALID
                ) from exc

            response = await self._request_with_retries(
                client,
                current_url,
                request_reason=request_reason,
                status_reason=status_reason,
            )
            if not response.is_redirect:
                if 300 <= response.status_code < 400:
                    status_code = response.status_code
                    await response.aclose()
                    raise DownloaderError(status_reason, status_code=status_code)
                try:
                    validate_url(str(response.url))
                except (TypeError, ValueError) as exc:
                    await response.aclose()
                    raise DownloaderError(
                        DownloaderErrorReason.DOWNLOAD_LINK_INVALID
                    ) from exc
                return response

            location = response.headers.get("location")
            await response.aclose()
            if not location:
                raise DownloaderError(status_reason, status_code=response.status_code)
            if redirect_count == self._max_redirects:
                raise DownloaderError(
                    DownloaderErrorReason.REDIRECT_LIMIT_EXCEEDED
                )
            current_url = urljoin(str(response.url), location)

        raise DownloaderError(DownloaderErrorReason.REDIRECT_LIMIT_EXCEEDED)

    async def _request_with_retries(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        request_reason: DownloaderErrorReason,
        status_reason: DownloaderErrorReason,
    ) -> httpx.Response:
        retries = self._settings.http_retry_count
        for attempt in range(retries + 1):
            try:
                response = await client.get(
                    url,
                    timeout=self._timeout,
                    follow_redirects=False,
                )
            except httpx.TransportError as exc:
                if attempt == retries:
                    raise DownloaderError(request_reason) from exc
                await self._sleep(self._backoff_seconds(attempt))
                continue

            if response.status_code < 400:
                return response

            retryable = (
                response.status_code in _RETRYABLE_STATUS_CODES
                or response.status_code >= 500
            )
            status_code = response.status_code
            await response.aclose()
            if not retryable or attempt == retries:
                raise DownloaderError(status_reason, status_code=status_code)
            await self._sleep(self._backoff_seconds(attempt))

        raise DownloaderError(request_reason)

    @staticmethod
    def _backoff_seconds(attempt: int) -> float:
        return min(0.5 * (2**attempt), 8.0)

    @staticmethod
    def _normalize_embedded_content(value: str) -> str:
        normalized = html.unescape(value)
        replacements = {
            r"\/": "/",
            r"\u002f": "/",
            r"\u002F": "/",
            r"\u003a": ":",
            r"\u003A": ":",
            r"\u0026": "&",
        }
        for escaped, replacement in replacements.items():
            normalized = normalized.replace(escaped, replacement)
        return normalized

    @staticmethod
    def _validate_https_url(url: str) -> str:
        parsed = urlsplit(url)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("URL must be an HTTPS URL without credentials")
        return url

    @classmethod
    def _validate_download_url(cls, url: str) -> tuple[str, str]:
        cls._validate_https_url(url)
        parsed = urlsplit(url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("invalid URL port") from exc
        if parsed.hostname.lower() not in _DOWNLOAD_HOSTS or port not in (None, 443):
            raise ValueError("download URL host is not allowed")
        if parsed.fragment:
            raise ValueError("download URL must not contain a fragment")

        filename = parsed.path.rsplit("/", 1)[-1]
        if _FILENAME_PATTERN.fullmatch(filename) is None:
            raise ValueError("download filename is invalid")
        return url, filename

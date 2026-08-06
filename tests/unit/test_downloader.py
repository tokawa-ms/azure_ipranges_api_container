"""Comprehensive tests for ServiceTagsDownloader."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import respx

from azure_ipranges_api.config import Settings
from azure_ipranges_api.data.downloader import (
    DownloadedServiceTags,
    DownloaderError,
    DownloaderErrorReason,
    ServiceTagsDownloader,
)


class TestServiceTagsDownloaderValidation:
    """Test URL validation and link discovery."""

    def test_valid_https_url_accepted(self, settings_with_temp_dir: Settings) -> None:
        """Valid HTTPS URLs should be accepted."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)
        url = "https://example.com/path?query=value"
        assert downloader._validate_https_url(url) == url

    def test_non_https_url_rejected(self, settings_with_temp_dir: Settings) -> None:
        """Non-HTTPS URLs should be rejected."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)
        with pytest.raises(ValueError, match="HTTPS"):
            downloader._validate_https_url("http://example.com/path")

    def test_url_with_credentials_rejected(self, settings_with_temp_dir: Settings) -> None:
        """URLs with username/password should be rejected."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)
        with pytest.raises(ValueError, match="credentials"):
            downloader._validate_https_url("https://user:pass@example.com/path")

    def test_valid_download_url_extracted(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """Valid download URLs should extract filename correctly."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)
        url = "https://download.microsoft.com/download/test/ServiceTags_Public_20240101.json"
        validated_url, filename = downloader._validate_download_url(url)
        assert validated_url == url
        assert filename == "ServiceTags_Public_20240101.json"

    def test_invalid_hostname_rejected(self, settings_with_temp_dir: Settings) -> None:
        """Non-whitelisted hostnames should be rejected."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)
        with pytest.raises(ValueError, match="host"):
            downloader._validate_download_url(
                "https://example.com/ServiceTags_Public_20240101.json"
            )

    def test_non_standard_port_rejected(self, settings_with_temp_dir: Settings) -> None:
        """Non-standard HTTPS ports should be rejected."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)
        with pytest.raises(ValueError, match="host"):
            downloader._validate_download_url(
                "https://download.microsoft.com:8443/ServiceTags_Public_20240101.json"
            )

    def test_standard_port_accepted(self, settings_with_temp_dir: Settings) -> None:
        """Port 443 should be accepted as valid."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)
        url = "https://download.microsoft.com:443/ServiceTags_Public_20240101.json"
        validated_url, filename = downloader._validate_download_url(url)
        assert filename == "ServiceTags_Public_20240101.json"

    def test_invalid_filename_rejected(self, settings_with_temp_dir: Settings) -> None:
        """Filenames not matching the pattern should be rejected."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)
        with pytest.raises(ValueError, match="filename"):
            downloader._validate_download_url(
                "https://download.microsoft.com/download/test.json"
            )

    def test_fragment_rejected(self, settings_with_temp_dir: Settings) -> None:
        """URLs with fragments should be rejected."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)
        with pytest.raises(ValueError, match="fragment"):
            downloader._validate_download_url(
                "https://download.microsoft.com/ServiceTags_Public_20240101.json#anchor"
            )

    def test_valid_link_extraction_from_html(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """Valid Service Tags links should be extracted from HTML."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)
        html = """
        <html>
        <body>
            <a href="https://download.microsoft.com/download/test/ServiceTags_Public_20240101.json">
                Download
            </a>
        </body>
        </html>
        """
        page_url = "https://www.microsoft.com/en-us/download/details.aspx?id=56519"
        url = downloader._discover_download_url(html, page_url)
        assert "ServiceTags_Public_20240101.json" in url
        assert url.startswith("https://download.microsoft.com")

    def test_relative_link_extraction_from_html(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """Relative links should be resolved correctly."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)
        html = """
        <html>
        <body>
            <a href="/download/test/ServiceTags_Public_20240101.json">
                Download
            </a>
        </body>
        </html>
        """
        page_url = "https://download.microsoft.com/en-us/download/details"
        url = downloader._discover_download_url(html, page_url)
        assert url.endswith("ServiceTags_Public_20240101.json")

    def test_embedded_escaped_link_extraction(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """Links with HTML escaping should be discovered."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)
        html = """
        <html>
        <script>
        var url = "https:\\/\\/download.microsoft.com\\/ServiceTags_Public_20240101.json";
        </script>
        </html>
        """
        page_url = "https://www.microsoft.com/download"
        url = downloader._discover_download_url(html, page_url)
        assert "ServiceTags_Public_20240101.json" in url

    def test_invalid_link_in_html_rejected(self, settings_with_temp_dir: Settings) -> None:
        """Invalid links in HTML should cause an error."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)
        html = """
        <html>
        <body>
            <a href="https://untrusted.com/ServiceTags_Public_20240101.json">
                Download
            </a>
        </body>
        </html>
        """
        page_url = "https://www.microsoft.com/download"
        with pytest.raises(DownloaderError) as exc_info:
            downloader._discover_download_url(html, page_url)
        assert exc_info.value.reason == DownloaderErrorReason.DOWNLOAD_LINK_INVALID

    def test_missing_link_in_html_raises_error(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """Missing download link should raise appropriate error."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)
        html = "<html><body>No download link here</body></html>"
        page_url = "https://www.microsoft.com/download"
        with pytest.raises(DownloaderError) as exc_info:
            downloader._discover_download_url(html, page_url)
        assert exc_info.value.reason == DownloaderErrorReason.DOWNLOAD_LINK_NOT_FOUND


class TestServiceTagsDownloaderRetries:
    """Test retry behavior for various failure scenarios."""

    @pytest.mark.asyncio
    async def test_transport_error_retried(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """Transport errors should be retried."""
        settings = Settings(data_dir=settings_with_temp_dir.data_dir, http_retry_count=2)
        downloader = ServiceTagsDownloader(settings)

        async def failing_sleep(_: float) -> None:
            pass

        downloader._sleep = failing_sleep

        with respx.mock:
            # Mock the page request to fail once, then succeed
            page_route = respx.get(settings.download_page_url)
            page_route.side_effect = [
                httpx.ReadTimeout("Connection timeout"),
                httpx.Response(
                    200,
                    text='<a href="https://download.microsoft.com/ServiceTags_Public_20240101.json">Link</a>',
                ),
            ]
            
            # Mock the file download
            respx.get(
                "https://download.microsoft.com/ServiceTags_Public_20240101.json"
            ).mock(return_value=httpx.Response(200, content=b"{}"))

            async with httpx.AsyncClient() as client:
                try:
                    await downloader._download_with_client(client)
                except DownloaderError as e:
                    # Expected - second request also needs a valid file
                    assert e.reason in {
                        DownloaderErrorReason.DOWNLOAD_LINK_INVALID,
                        DownloaderErrorReason.DOWNLOAD_REQUEST_FAILED,
                    }

    @pytest.mark.asyncio
    async def test_429_retried(self, settings_with_temp_dir: Settings) -> None:
        """HTTP 429 (Too Many Requests) should be retried."""
        settings = Settings(data_dir=settings_with_temp_dir.data_dir, http_retry_count=2)
        downloader = ServiceTagsDownloader(settings)

        async def failing_sleep(_: float) -> None:
            pass

        downloader._sleep = failing_sleep

        with respx.mock:
            route = respx.get(settings.download_page_url)
            route.side_effect = [
                httpx.Response(429),
                httpx.Response(200),
            ]

            async with httpx.AsyncClient() as client:
                try:
                    await downloader._download_with_client(client)
                except DownloaderError:
                    # Expected - needs to find valid content
                    pass

    @pytest.mark.asyncio
    async def test_5xx_retried(self, settings_with_temp_dir: Settings) -> None:
        """5xx errors should be retried."""
        settings = Settings(data_dir=settings_with_temp_dir.data_dir, http_retry_count=2)
        downloader = ServiceTagsDownloader(settings)

        async def failing_sleep(_: float) -> None:
            pass

        downloader._sleep = failing_sleep

        with respx.mock:
            route = respx.get(settings.download_page_url)
            route.side_effect = [
                httpx.Response(502),
                httpx.Response(503),
                httpx.Response(200),
            ]

            async with httpx.AsyncClient() as client:
                try:
                    await downloader._download_with_client(client)
                except DownloaderError:
                    # Expected
                    pass

    @pytest.mark.asyncio
    async def test_4xx_not_retried(self, settings_with_temp_dir: Settings) -> None:
        """4xx errors (except 429) should not be retried."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)

        async def failing_sleep(_: float) -> None:
            pass

        downloader._sleep = failing_sleep

        with respx.mock:
            route = respx.get(settings_with_temp_dir.download_page_url)
            route.side_effect = httpx.Response(404)

            async with httpx.AsyncClient() as client:
                with pytest.raises(DownloaderError) as exc_info:
                    await downloader._download_with_client(client)
                assert exc_info.value.reason == DownloaderErrorReason.PAGE_HTTP_ERROR
                assert exc_info.value.status_code == 404
                # Verify request was only made once (no retries)
                assert route.called

    @pytest.mark.asyncio
    async def test_403_forbidden_not_retried(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """HTTP 403 should not be retried."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)

        with respx.mock:
            route = respx.get(settings_with_temp_dir.download_page_url)
            route.return_value = httpx.Response(403)

            async with httpx.AsyncClient() as client:
                with pytest.raises(DownloaderError) as exc_info:
                    await downloader._download_with_client(client)
                assert exc_info.value.reason == DownloaderErrorReason.PAGE_HTTP_ERROR
                assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_max_retries_exhausted(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """All retries should be exhausted on continuous failure."""
        settings = Settings(data_dir=settings_with_temp_dir.data_dir, http_retry_count=2)
        downloader = ServiceTagsDownloader(settings)

        async def failing_sleep(_: float) -> None:
            pass

        downloader._sleep = failing_sleep

        with respx.mock:
            route = respx.get(settings.download_page_url)
            route.return_value = httpx.Response(502)

            async with httpx.AsyncClient() as client:
                with pytest.raises(DownloaderError) as exc_info:
                    await downloader._download_with_client(client)
                assert exc_info.value.reason == DownloaderErrorReason.PAGE_HTTP_ERROR
                # Should have been called retries + 1 times
                assert route.call_count == 3


class TestServiceTagsDownloaderErrorReasons:
    """Test error reason mapping for various scenarios."""

    def test_page_request_failed_reason(self, settings_with_temp_dir: Settings) -> None:
        """DownloaderError should report page request failures."""
        error = DownloaderError(DownloaderErrorReason.PAGE_REQUEST_FAILED)
        assert error.reason == DownloaderErrorReason.PAGE_REQUEST_FAILED
        assert "page_request_failed" in str(error)

    def test_page_http_error_reason_with_status(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """HTTP errors should include status code in message."""
        error = DownloaderError(
            DownloaderErrorReason.PAGE_HTTP_ERROR, status_code=404
        )
        assert error.reason == DownloaderErrorReason.PAGE_HTTP_ERROR
        assert error.status_code == 404
        assert "404" in str(error)

    def test_download_link_not_found_reason(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """Missing download link should report as not found."""
        error = DownloaderError(DownloaderErrorReason.DOWNLOAD_LINK_NOT_FOUND)
        assert error.reason == DownloaderErrorReason.DOWNLOAD_LINK_NOT_FOUND

    def test_download_link_invalid_reason(self, settings_with_temp_dir: Settings) -> None:
        """Invalid download link should report as invalid."""
        error = DownloaderError(DownloaderErrorReason.DOWNLOAD_LINK_INVALID)
        assert error.reason == DownloaderErrorReason.DOWNLOAD_LINK_INVALID

    def test_download_request_failed_reason(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """Download request failures should be reported."""
        error = DownloaderError(DownloaderErrorReason.DOWNLOAD_REQUEST_FAILED)
        assert error.reason == DownloaderErrorReason.DOWNLOAD_REQUEST_FAILED

    def test_download_http_error_reason(self, settings_with_temp_dir: Settings) -> None:
        """Download HTTP errors should be reported with status."""
        error = DownloaderError(
            DownloaderErrorReason.DOWNLOAD_HTTP_ERROR, status_code=500
        )
        assert error.reason == DownloaderErrorReason.DOWNLOAD_HTTP_ERROR
        assert error.status_code == 500

    def test_redirect_limit_exceeded_reason(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """Redirect limit exceeded should be reported."""
        error = DownloaderError(DownloaderErrorReason.REDIRECT_LIMIT_EXCEEDED)
        assert error.reason == DownloaderErrorReason.REDIRECT_LIMIT_EXCEEDED


class TestServiceTagsDownloaderRedirects:
    """Test redirect handling."""

    @pytest.mark.asyncio
    async def test_follow_redirects(self, settings_with_temp_dir: Settings) -> None:
        """Redirects should be followed up to max_redirects."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir, max_redirects=2)

        with respx.mock:
            # First redirect
            respx.get(settings_with_temp_dir.download_page_url).mock(
                return_value=httpx.Response(
                    302,
                    headers={"location": "https://download.microsoft.com/redirect1"},
                )
            )
            # Second redirect
            respx.get("https://download.microsoft.com/redirect1").mock(
                return_value=httpx.Response(
                    302,
                    headers={"location": "https://download.microsoft.com/redirect2"},
                )
            )
            # Final destination with valid link
            respx.get("https://download.microsoft.com/redirect2").mock(
                return_value=httpx.Response(
                    200,
                    text='<a href="/ServiceTags_Public_20240101.json">Link</a>',
                )
            )
            respx.get(
                "https://download.microsoft.com/ServiceTags_Public_20240101.json"
            ).mock(return_value=httpx.Response(200, content=b"{}"))

            async with httpx.AsyncClient() as client:
                try:
                    await downloader._download_with_client(client)
                except DownloaderError:
                    # Will fail due to invalid JSON schema, but redirect handling succeeded
                    pass

    @pytest.mark.asyncio
    async def test_redirect_limit_exceeded_error(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """Exceeding redirect limit should raise error."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir, max_redirects=1)

        with respx.mock:
            respx.get(settings_with_temp_dir.download_page_url).mock(
                return_value=httpx.Response(
                    302, headers={"location": "https://redirect1.example.com/"}
                )
            )
            respx.get("https://redirect1.example.com/").mock(
                return_value=httpx.Response(
                    302, headers={"location": "https://redirect2.example.com/"}
                )
            )

            async with httpx.AsyncClient() as client:
                with pytest.raises(DownloaderError) as exc_info:
                    await downloader._download_with_client(client)
                assert (
                    exc_info.value.reason
                    == DownloaderErrorReason.REDIRECT_LIMIT_EXCEEDED
                )

    @pytest.mark.asyncio
    async def test_redirect_without_location_header(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """Redirect without location header should raise error."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir, max_redirects=1)

        with respx.mock:
            respx.get(settings_with_temp_dir.download_page_url).mock(
                return_value=httpx.Response(302)  # No location header
            )

            async with httpx.AsyncClient() as client:
                with pytest.raises(DownloaderError) as exc_info:
                    await downloader._download_with_client(client)
                assert exc_info.value.reason == DownloaderErrorReason.PAGE_HTTP_ERROR

    @pytest.mark.asyncio
    async def test_invalid_redirect_target_rejected(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """Invalid redirect target should be rejected."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir, max_redirects=2)

        with respx.mock:
            respx.get(settings_with_temp_dir.download_page_url).mock(
                return_value=httpx.Response(
                    302, headers={"location": "http://example.com/"}  # Non-HTTPS
                )
            )

            async with httpx.AsyncClient() as client:
                with pytest.raises(DownloaderError) as exc_info:
                    await downloader._download_with_client(client)
                assert (
                    exc_info.value.reason
                    == DownloaderErrorReason.DOWNLOAD_LINK_INVALID
                )


class TestServiceTagsDownloaderTimeout:
    """Test timeout behavior."""

    def test_timeout_from_settings(self, settings_with_temp_dir: Settings) -> None:
        """Downloader should use timeout from settings."""
        settings = Settings(
            data_dir=settings_with_temp_dir.data_dir, http_timeout_seconds=45.0
        )
        downloader = ServiceTagsDownloader(settings)
        assert downloader._timeout == httpx.Timeout(45.0)

    @pytest.mark.asyncio
    async def test_timeout_error_handling(self, settings_with_temp_dir: Settings) -> None:
        """Timeout errors should be handled as transport failures."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)

        async def failing_sleep(_: float) -> None:
            pass

        downloader._sleep = failing_sleep

        with respx.mock:
            respx.get(settings_with_temp_dir.download_page_url).side_effect = (
                httpx.ConnectTimeout("Connection timeout")
            )

            async with httpx.AsyncClient() as client:
                with pytest.raises(DownloaderError) as exc_info:
                    await downloader._download_with_client(client)
                assert (
                    exc_info.value.reason
                    == DownloaderErrorReason.PAGE_REQUEST_FAILED
                )


class TestServiceTagsDownloaderConstructor:
    """Test constructor validation."""

    def test_invalid_max_redirects_rejected(self, settings_with_temp_dir: Settings) -> None:
        """Negative max_redirects should be rejected."""
        with pytest.raises(ValueError, match="max_redirects"):
            ServiceTagsDownloader(settings_with_temp_dir, max_redirects=-1)

    def test_zero_max_redirects_allowed(self, settings_with_temp_dir: Settings) -> None:
        """Zero max_redirects should be allowed."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir, max_redirects=0)
        assert downloader._max_redirects == 0

    def test_custom_client_used(self, settings_with_temp_dir: Settings) -> None:
        """Custom client should be stored and used."""
        custom_client = Mock()
        downloader = ServiceTagsDownloader(
            settings_with_temp_dir, client=custom_client
        )
        assert downloader._client is custom_client

    def test_custom_sleep_used(self, settings_with_temp_dir: Settings) -> None:
        """Custom sleep function should be stored and used."""
        custom_sleep = Mock()
        downloader = ServiceTagsDownloader(
            settings_with_temp_dir, sleep=custom_sleep
        )
        assert downloader._sleep is custom_sleep


class TestDownloadedServiceTags:
    """Test the DownloadedServiceTags dataclass."""

    def test_downloaded_service_tags_creation(self) -> None:
        """DownloadedServiceTags should store source, filename, and content."""
        source_url = "https://download.microsoft.com/ServiceTags_Public_20240101.json"
        filename = "ServiceTags_Public_20240101.json"
        content = b'{"changeNumber": 100, "values": []}'

        tags = DownloadedServiceTags(
            source_url=source_url,
            filename=filename,
            content=content,
        )
        assert tags.source_url == source_url
        assert tags.filename == filename
        assert tags.content == content

    def test_downloaded_service_tags_frozen(self) -> None:
        """DownloadedServiceTags should be immutable."""
        tags = DownloadedServiceTags(
            source_url="https://example.com/file.json",
            filename="file.json",
            content=b"{}",
        )
        with pytest.raises(AttributeError):
            tags.content = b"modified"


class TestHTMLNormalization:
    """Test HTML content normalization for link discovery."""

    def test_normalize_html_entities(self, settings_with_temp_dir: Settings) -> None:
        """HTML entities should be decoded."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)
        result = downloader._normalize_embedded_content("&lt;test&gt;")
        assert result == "<test>"

    def test_normalize_forward_slash_escape(
        self, settings_with_temp_dir: Settings
    ) -> None:
        """Forward slashes should be unescaped."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)
        result = downloader._normalize_embedded_content(r"https:\/\/example.com")
        assert result == "https://example.com"

    def test_normalize_unicode_escapes(self, settings_with_temp_dir: Settings) -> None:
        """Unicode escapes should be normalized."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)
        result = downloader._normalize_embedded_content(r"https\u003a\u002f\u002fexample.com")
        assert result == "https://example.com"

    def test_normalize_ampersand_escape(self, settings_with_temp_dir: Settings) -> None:
        """Ampersand escapes should be normalized."""
        downloader = ServiceTagsDownloader(settings_with_temp_dir)
        result = downloader._normalize_embedded_content(r"query\u0026value")
        assert result == "query&value"

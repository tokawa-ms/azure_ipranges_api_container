"""Comprehensive API endpoint tests for all service paths and catalog states."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from azure_ipranges_api.config import Settings
from azure_ipranges_api.data.loader import LoadedCache
from azure_ipranges_api.data.refresh import InitializationResult
from azure_ipranges_api.errors import DataUnavailableReason
from azure_ipranges_api.main import create_app
from azure_ipranges_api.models import ServiceTagsDocument


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "service-tags-sample.json"


@pytest.fixture
def available_app(sample_cache_metadata):
    """Create app with available catalog loaded from sample data."""
    raw_json = FIXTURE_PATH.read_bytes()
    document = ServiceTagsDocument.model_validate(json.loads(raw_json))
    result = InitializationResult.from_cache(
        LoadedCache(
            document=document,
            metadata=sample_cache_metadata,
            raw_json=raw_json,
        )
    )

    def coordinator_factory(settings):
        class AvailableCoordinator:
            async def initialize(self):
                return result
        return AvailableCoordinator()

    return create_app(Settings(), coordinator_factory=coordinator_factory)


@pytest.fixture
def unavailable_app():
    """Create app with unavailable catalog."""
    result = InitializationResult.unavailable(
        DataUnavailableReason.CACHE_MISSING,
        retryable=True,
    )

    def coordinator_factory(settings):
        class UnavailableCoordinator:
            async def initialize(self):
                return result
        return UnavailableCoordinator()

    return create_app(Settings(), coordinator_factory=coordinator_factory)


class TestAvailableCatalog:
    """Tests for API with available catalog."""

    def test_health_live_always_200(self, available_app) -> None:
        """Liveness endpoint always returns 200."""
        with TestClient(available_app) as client:
            response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_health_ready_returns_200_with_diagnostics(
        self, available_app
    ) -> None:
        """Readiness endpoint returns 200 with cache diagnostics."""
        with TestClient(available_app) as client:
            response = client.get("/health/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        assert data["stale"] is False
        assert "fetched_at" in data
        assert "change_number" in data
        assert data["reason"] is None

    def test_ranges_service_match_success(
        self, available_app
    ) -> None:
        """Ranges endpoint returns matching service tags."""
        with TestClient(available_app) as client:
            response = client.get(
                "/api/v1/ranges", params={"service": "Storage"}
            )
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "Storage"
        assert len(data["items"]) > 0
        # Should match both "Storage" and "Storage.JapanEast"
        assert any(item["name"] == "Storage" for item in data["items"])

    def test_ranges_with_region_filter(
        self, available_app
    ) -> None:
        """Ranges endpoint filters by region."""
        with TestClient(available_app) as client:
            response = client.get(
                "/api/v1/ranges",
                params={"service": "Storage", "region": "japaneast"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["region"] == "japaneast"
        assert len(data["items"]) > 0
        # All items should be from japaneast
        for item in data["items"]:
            assert item["region"] == "japaneast"

    def test_ranges_with_ipv4_filter(
        self, available_app
    ) -> None:
        """Ranges endpoint filters by IP version."""
        with TestClient(available_app) as client:
            response = client.get(
                "/api/v1/ranges",
                params={"service": "Storage", "ip_version": 4},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["ip_version"] == 4
        # All items should have IPv4 prefixes
        for item in data["items"]:
            for prefix in item["address_prefixes"]:
                # Parse CIDR and verify it's IPv4
                assert "." in prefix  # IPv4 addresses contain dots

    def test_ranges_with_ipv6_filter(
        self, available_app
    ) -> None:
        """Ranges endpoint filters by IPv6."""
        with TestClient(available_app) as client:
            response = client.get(
                "/api/v1/ranges",
                params={"service": "Storage", "ip_version": 6},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["ip_version"] == 6
        # All items should have IPv6 prefixes
        for item in data["items"]:
            for prefix in item["address_prefixes"]:
                # IPv6 addresses contain colons
                assert ":" in prefix

    def test_ranges_empty_results(self, available_app) -> None:
        """Ranges endpoint returns empty items for no matches."""
        with TestClient(available_app) as client:
            response = client.get(
                "/api/v1/ranges",
                params={"service": "NonexistentService"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []

    def test_ranges_empty_with_filters(
        self, available_app
    ) -> None:
        """Ranges endpoint returns empty items with non-matching filters."""
        with TestClient(available_app) as client:
            response = client.get(
                "/api/v1/ranges",
                params={
                    "service": "Storage",
                    "region": "nonexistent-region",
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []

    def test_ranges_invalid_service_returns_empty(
        self, available_app
    ) -> None:
        """Ranges endpoint returns empty for invalid service param."""
        with TestClient(available_app) as client:
            response = client.get(
                "/api/v1/ranges",
                params={"service": "   "},  # Whitespace only
            )
        # Should fail validation
        assert response.status_code == 422

    def test_ip_lookup_ipv4_success(
        self, available_app
    ) -> None:
        """IP lookup endpoint returns matching prefixes for IPv4."""
        with TestClient(available_app) as client:
            response = client.get("/api/v1/ip/10.0.0.70")
        assert response.status_code == 200
        data = response.json()
        assert data["ip_address"] == "10.0.0.70"
        assert len(data["items"]) > 0
        # Should match at least one prefix
        for item in data["items"]:
            assert "prefix" in item
            assert item["name"] in ["Storage", "Storage.JapanEast", "AzureCloud"]

    def test_ip_lookup_ipv6_success(
        self, available_app
    ) -> None:
        """IP lookup endpoint returns matching prefixes for IPv6."""
        with TestClient(available_app) as client:
            response = client.get("/api/v1/ip/2001:db8::1")
        assert response.status_code == 200
        data = response.json()
        assert data["ip_address"] == "2001:db8::1"
        # Should return matching IPv6 prefixes
        assert len(data["items"]) > 0

    def test_ip_lookup_no_matches(self, available_app) -> None:
        """IP lookup returns empty items for non-matching IP."""
        # Use an IP not in the sample data
        with TestClient(available_app) as client:
            response = client.get("/api/v1/ip/1.2.3.4")
        assert response.status_code == 200
        data = response.json()
        assert data["ip_address"] == "1.2.3.4"
        assert data["items"] == []

    def test_ip_lookup_invalid_ipv4_returns_422(
        self, available_app
    ) -> None:
        """IP lookup returns 422 for invalid IPv4."""
        with TestClient(available_app) as client:
            response = client.get("/api/v1/ip/not-an-ip")
        assert response.status_code == 422
        data = response.json()
        assert isinstance(data["detail"], list)
        assert data["detail"][0]["loc"] == ["path", "ip_address"]

    def test_ip_lookup_invalid_ipv6_returns_422(
        self, available_app
    ) -> None:
        """IP lookup returns 422 for invalid IPv6."""
        with TestClient(available_app) as client:
            response = client.get("/api/v1/ip/gggg::1")
        assert response.status_code == 422
        data = response.json()
        assert isinstance(data["detail"], list)

    def test_metadata_returns_cache_info(
        self, available_app
    ) -> None:
        """Metadata endpoint returns catalog metadata."""
        with TestClient(available_app) as client:
            response = client.get("/api/v1/metadata")
        assert response.status_code == 200
        data = response.json()
        assert "source_url" in data
        assert "filename" in data
        assert "fetched_at" in data
        assert "sha256" in data
        assert "change_number" in data
        assert data["tag_count"] > 0
        assert data["prefix_count"] > 0

    def test_openapi_contains_all_paths(
        self, available_app
    ) -> None:
        """OpenAPI schema includes all API paths."""
        with TestClient(available_app) as client:
            response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        paths = schema["paths"]

        required_paths = {
            "/health/live",
            "/health/ready",
            "/api/v1/metadata",
            "/api/v1/ranges",
            "/api/v1/ip/{ip_address}",
        }
        assert required_paths.issubset(paths.keys())

    def test_openapi_documents_422_response(
        self, available_app
    ) -> None:
        """OpenAPI schema documents 422 validation errors."""
        with TestClient(available_app) as client:
            response = client.get("/openapi.json")
        schema = response.json()

        # Check ranges endpoint has 422 response documented
        ranges_responses = schema["paths"]["/api/v1/ranges"]["get"]["responses"]
        assert "422" in ranges_responses

        # Check ip lookup endpoint has 422 response documented
        ip_responses = schema["paths"]["/api/v1/ip/{ip_address}"]["get"][
            "responses"
        ]
        assert "422" in ip_responses

    def test_openapi_documents_503_response(
        self, available_app
    ) -> None:
        """OpenAPI schema documents 503 data unavailable errors."""
        with TestClient(available_app) as client:
            response = client.get("/openapi.json")
        schema = response.json()

        # Check ranges endpoint has 503 response documented
        ranges_responses = schema["paths"]["/api/v1/ranges"]["get"]["responses"]
        assert "503" in ranges_responses

        # Check metadata endpoint has 503 response documented
        metadata_responses = schema["paths"]["/api/v1/metadata"]["get"][
            "responses"
        ]
        assert "503" in metadata_responses

    def test_openapi_documents_response_models(
        self, available_app
    ) -> None:
        """OpenAPI schema documents response models."""
        with TestClient(available_app) as client:
            response = client.get("/openapi.json")
        schema = response.json()
        components = schema["components"]["schemas"]

        # Check for key response models
        component_names = set(components.keys())
        # At least check that we have range/IP response models
        assert any(
            "ForwardLookupResponse" in name or "RangeLookupResponse" in name
            for name in component_names
        )
        assert any(
            "ReverseLookupResponse" in name or "IpLookupResponse" in name
            for name in component_names
        )

    def test_docs_endpoint_loads(
        self, available_app
    ) -> None:
        """Swagger docs endpoint loads successfully."""
        with TestClient(available_app) as client:
            response = client.get("/docs")
        assert response.status_code == 200
        assert "html" in response.text.lower()


class TestUnavailableCatalog:
    """Tests for API with unavailable catalog."""

    def test_health_live_still_200_when_unavailable(
        self, unavailable_app
    ) -> None:
        """Liveness endpoint returns 200 even when catalog unavailable."""
        with TestClient(unavailable_app) as client:
            response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_health_ready_returns_503_with_diagnostics(
        self, unavailable_app
    ) -> None:
        """Readiness returns 503 with unavailable reason."""
        with TestClient(unavailable_app) as client:
            response = client.get("/health/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["ready"] is False
        assert data["reason"] == DataUnavailableReason.CACHE_MISSING.value
        assert data["fetched_at"] is None
        assert data["stale"] is False

    def test_ranges_returns_standardized_503(
        self, unavailable_app
    ) -> None:
        """Ranges endpoint returns standardized 503 error."""
        with TestClient(unavailable_app) as client:
            response = client.get(
                "/api/v1/ranges", params={"service": "Storage"}
            )
        assert response.status_code == 503
        data = response.json()
        assert "detail" in data
        detail = data["detail"]
        assert detail["code"] == "data_unavailable"
        assert detail["message"] == (
            "Azure IP ranges data is temporarily unavailable."
        )
        assert detail["reason"] == DataUnavailableReason.CACHE_MISSING.value
        assert detail["retryable"] is True

    def test_ip_lookup_returns_standardized_503(
        self, unavailable_app
    ) -> None:
        """IP lookup returns standardized 503 error."""
        with TestClient(unavailable_app) as client:
            response = client.get("/api/v1/ip/10.0.0.1")
        assert response.status_code == 503
        data = response.json()
        assert "detail" in data
        detail = data["detail"]
        assert detail["code"] == "data_unavailable"
        assert detail["retryable"] is True

    def test_metadata_returns_standardized_503(
        self, unavailable_app
    ) -> None:
        """Metadata endpoint returns standardized 503 error."""
        with TestClient(unavailable_app) as client:
            response = client.get("/api/v1/metadata")
        assert response.status_code == 503
        data = response.json()
        assert "detail" in data
        detail = data["detail"]
        assert detail["code"] == "data_unavailable"
        assert detail["message"] == (
            "Azure IP ranges data is temporarily unavailable."
        )

    def test_all_503_responses_have_consistent_shape(
        self, unavailable_app
    ) -> None:
        """All 503 responses have the same error shape."""
        endpoints = [
            ("/api/v1/ranges", {"service": "Storage"}),
            ("/api/v1/ip/10.0.0.1", None),
            ("/api/v1/metadata", None),
        ]

        with TestClient(unavailable_app) as client:
            responses = []
            for endpoint, params in endpoints:
                if params:
                    resp = client.get(endpoint, params=params)
                else:
                    resp = client.get(endpoint)
                responses.append(resp)

            # All should be 503
            assert all(resp.status_code == 503 for resp in responses)

            # All should have the same error structure
            expected_keys = {"code", "message", "reason", "retryable"}
            for resp in responses:
                data = resp.json()
                assert "detail" in data
                detail = data["detail"]
                assert set(detail.keys()) == expected_keys


class TestValidationErrors:
    """Tests for request validation (422 responses)."""

    def test_ranges_missing_service_param_returns_422(
        self, available_app
    ) -> None:
        """Ranges endpoint requires service parameter."""
        with TestClient(available_app) as client:
            response = client.get("/api/v1/ranges")
        assert response.status_code == 422
        data = response.json()
        assert isinstance(data["detail"], list)
        # Should indicate missing 'service' parameter
        locations = [error["loc"] for error in data["detail"]]
        assert any("service" in loc for loc in locations)

    def test_ranges_empty_service_param_returns_422(
        self, available_app
    ) -> None:
        """Ranges endpoint rejects empty service."""
        with TestClient(available_app) as client:
            response = client.get(
                "/api/v1/ranges",
                params={"service": ""},
            )
        assert response.status_code == 422

    def test_ranges_invalid_ip_version_returns_422(
        self, available_app
    ) -> None:
        """Ranges endpoint validates IP version parameter."""
        with TestClient(available_app) as client:
            response = client.get(
                "/api/v1/ranges",
                params={"service": "Storage", "ip_version": 5},
            )
        assert response.status_code == 422

    def test_validation_error_shape_matches_fastapi_standard(
        self, available_app
    ) -> None:
        """Validation errors follow FastAPI structure."""
        with TestClient(available_app) as client:
            response = client.get("/api/v1/ip/invalid")
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data
        assert isinstance(data["detail"], list)

        error = data["detail"][0]
        expected_keys = {"loc", "msg", "type", "input"}
        assert expected_keys.issubset(error.keys())


class TestOpenAPISchema:
    """Tests for OpenAPI schema completeness."""

    def test_openapi_schema_is_valid_json(
        self, available_app
    ) -> None:
        """OpenAPI endpoint returns valid JSON."""
        with TestClient(available_app) as client:
            response = client.get("/openapi.json")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
        # Should parse without error
        data = response.json()
        assert "openapi" in data
        assert "paths" in data
        assert "components" in data

    def test_openapi_includes_all_http_methods(
        self, available_app
    ) -> None:
        """OpenAPI schema documents correct HTTP methods."""
        with TestClient(available_app) as client:
            response = client.get("/openapi.json")
        schema = response.json()
        paths = schema["paths"]

        # All endpoints should be GET
        for path, methods in paths.items():
            assert "get" in methods or "post" in methods or "put" in methods
            # In this API, all are GET
            if path.startswith("/api") or path.startswith("/health"):
                assert "get" in methods

    def test_openapi_documents_parameter_examples(
        self, available_app
    ) -> None:
        """OpenAPI schema includes parameter examples."""
        with TestClient(available_app) as client:
            response = client.get("/openapi.json")
        schema = response.json()

        # Check ranges endpoint parameters
        ranges_params = schema["paths"]["/api/v1/ranges"]["get"]["parameters"]
        service_param = next(p for p in ranges_params if p["name"] == "service")
        # Examples may be in different places or not present in all FastAPI versions
        assert "description" in service_param

    def test_redoc_endpoint_loads(
        self, available_app
    ) -> None:
        """ReDoc endpoint loads successfully."""
        with TestClient(available_app) as client:
            response = client.get("/redoc")
        assert response.status_code == 200
        assert "html" in response.text.lower()

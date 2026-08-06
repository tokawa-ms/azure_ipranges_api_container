from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from azure_ipranges_api.api.health import router
from azure_ipranges_api.errors import DataUnavailableError, DataUnavailableReason
from azure_ipranges_api.models import MetadataResponse, ReadinessResponse


class StubCatalogService:
    def __init__(
        self,
        readiness: ReadinessResponse,
        metadata: MetadataResponse | DataUnavailableError,
    ) -> None:
        self._readiness = readiness
        self._metadata = metadata

    def readiness(self) -> ReadinessResponse:
        return self._readiness

    def metadata(self) -> MetadataResponse:
        if isinstance(self._metadata, DataUnavailableError):
            raise self._metadata
        return self._metadata


def _client(catalog_service: StubCatalogService) -> TestClient:
    app = FastAPI()
    app.state.catalog_service = catalog_service
    app.include_router(router)
    return TestClient(app)


def _metadata() -> MetadataResponse:
    return MetadataResponse(
        source_url="https://example.test/service-tags.json",
        filename="service-tags.json",
        fetched_at=datetime(2026, 8, 6, 10, 0, tzinfo=timezone.utc),
        sha256="a" * 64,
        change_number=42,
        tag_count=3,
        prefix_count=6,
    )


def test_liveness_is_always_ok() -> None:
    catalog = StubCatalogService(
        ReadinessResponse(ready=False, reason="unavailable", stale=False),
        DataUnavailableError(DataUnavailableReason.CACHE_MISSING),
    )

    response = _client(catalog).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_returns_typed_success_with_cache_diagnostics() -> None:
    metadata = _metadata()
    catalog = StubCatalogService(
        ReadinessResponse(
            ready=True,
            fetched_at=metadata.fetched_at,
            change_number=metadata.change_number,
            stale=False,
        ),
        metadata,
    )

    response = _client(catalog).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "ready": True,
        "reason": None,
        "fetched_at": "2026-08-06T10:00:00Z",
        "change_number": 42,
        "stale": False,
    }


def test_readiness_returns_diagnostic_503_without_raising() -> None:
    catalog = StubCatalogService(
        ReadinessResponse(
            ready=False,
            reason=DataUnavailableReason.CACHE_EXPIRED.value,
            stale=True,
        ),
        DataUnavailableError(DataUnavailableReason.CACHE_EXPIRED),
    )
    client = _client(catalog)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "ready": False,
        "reason": DataUnavailableReason.CACHE_EXPIRED.value,
        "fetched_at": None,
        "change_number": None,
        "stale": True,
    }
    readiness_responses = client.app.openapi()["paths"]["/health/ready"]["get"][
        "responses"
    ]
    assert "503" in readiness_responses
    assert (
        readiness_responses["503"]["content"]["application/json"]["schema"][
            "$ref"
        ]
        == "#/components/schemas/ReadinessResponse"
    )


def test_metadata_returns_typed_cache_metadata() -> None:
    metadata = _metadata()
    catalog = StubCatalogService(
        ReadinessResponse(
            ready=True,
            fetched_at=metadata.fetched_at,
            change_number=metadata.change_number,
            stale=False,
        ),
        metadata,
    )

    response = _client(catalog).get("/api/v1/metadata")

    assert response.status_code == 200
    assert response.json()["filename"] == "service-tags.json"
    assert response.json()["change_number"] == 42


def test_metadata_allows_data_unavailable_error_to_reach_app_handler() -> None:
    error = DataUnavailableError(DataUnavailableReason.CACHE_MISSING)
    catalog = StubCatalogService(
        ReadinessResponse(
            ready=False,
            reason=DataUnavailableReason.CACHE_MISSING.value,
            stale=False,
        ),
        error,
    )

    with pytest.raises(DataUnavailableError) as raised:
        _client(catalog).get("/api/v1/metadata")

    assert raised.value is error

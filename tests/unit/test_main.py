from __future__ import annotations

import logging

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from azure_ipranges_api.config import Settings
from azure_ipranges_api.data.refresh import InitializationResult
from azure_ipranges_api.errors import (
    DataUnavailableError,
    DataUnavailableReason,
)
from azure_ipranges_api.main import (
    APP_DESCRIPTION,
    APP_TITLE,
    create_app,
    register_router,
)
from azure_ipranges_api.services import CatalogService


@pytest.mark.asyncio
async def test_lifespan_initializes_once_and_registers_isolated_state(
    tmp_path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    result = InitializationResult.unavailable(
        DataUnavailableReason.CACHE_MISSING
    )

    class Coordinator:
        calls = 0

        async def initialize(self) -> InitializationResult:
            self.calls += 1
            return result

    coordinator = Coordinator()
    app = create_app(
        settings,
        coordinator_factory=lambda actual: coordinator,
    )

    assert app.title == APP_TITLE
    assert app.description == APP_DESCRIPTION
    assert app.version

    async with app.router.lifespan_context(app):
        assert coordinator.calls == 1
        assert app.state.settings is settings
        assert isinstance(app.state.catalog_service, CatalogService)
        assert (
            app.state.catalog_service.readiness().reason
            == DataUnavailableReason.CACHE_MISSING.value
        )

    other_app = create_app(
        Settings(data_dir=tmp_path / "other"),
        coordinator_factory=lambda actual: Coordinator(),
    )
    assert other_app.state is not app.state


@pytest.mark.asyncio
async def test_lifespan_converts_unexpected_failure_to_unavailable_catalog(
    tmp_path,
    caplog,
) -> None:
    class FailingCoordinator:
        calls = 0

        async def initialize(self) -> InitializationResult:
            self.calls += 1
            raise RuntimeError("sensitive upstream detail")

    coordinator = FailingCoordinator()
    app = create_app(
        Settings(data_dir=tmp_path),
        coordinator_factory=lambda actual: coordinator,
    )

    with caplog.at_level(logging.ERROR):
        async with app.router.lifespan_context(app):
            readiness = app.state.catalog_service.readiness()

    assert coordinator.calls == 1
    assert not readiness.ready
    assert (
        readiness.reason
        == DataUnavailableReason.INITIALIZATION_FAILED.value
    )
    assert "Application data initialization failed" in caplog.text


def test_router_registration_is_explicit_and_import_safe(tmp_path) -> None:
    router = APIRouter()

    @router.get("/probe")
    def probe() -> dict[str, bool]:
        return {"ok": True}

    app = create_app(
        Settings(data_dir=tmp_path),
        router_registrar=lambda application: register_router(
            application, router
        ),
    )

    assert "/probe" in app.openapi()["paths"]


def test_default_routers_are_registered(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path))

    assert set(app.openapi()["paths"]) >= {
        "/health/live",
        "/health/ready",
        "/api/v1/metadata",
        "/api/v1/ranges",
        "/api/v1/ip/{ip_address}",
    }


def test_data_unavailable_has_same_standardized_shape_across_endpoints(
    tmp_path,
) -> None:
    error = DataUnavailableError(
        DataUnavailableReason.CACHE_MISSING,
        retryable=False,
    )

    class UnavailableCatalog:
        def forward_lookup(self, *args, **kwargs):
            raise error

        def reverse_lookup(self, *args, **kwargs):
            raise error

        def metadata(self):
            raise error

    app = create_app(
        Settings(data_dir=tmp_path),
        catalog_factory=lambda result: UnavailableCatalog(),
    )
    expected = {
        "detail": {
            "code": "data_unavailable",
            "message": "Azure IP ranges data is temporarily unavailable.",
            "reason": DataUnavailableReason.CACHE_MISSING.value,
            "retryable": False,
        }
    }

    with TestClient(app) as client:
        responses = [
            client.get("/api/v1/ranges", params={"service": "Storage"}),
            client.get("/api/v1/ip/20.60.1.10"),
            client.get("/api/v1/metadata"),
        ]

    assert all(response.status_code == 503 for response in responses)
    assert all(response.json() == expected for response in responses)


def test_validation_and_invalid_ip_use_standard_422_shape(tmp_path) -> None:
    class Catalog:
        def reverse_lookup(self, ip_address: str):
            from azure_ipranges_api.search.reverse import (
                InvalidIpAddressError,
            )

            raise InvalidIpAddressError("IP address must be valid")

    app = create_app(
        Settings(data_dir=tmp_path),
        catalog_factory=lambda result: Catalog(),
    )

    with TestClient(app) as client:
        missing_service = client.get("/api/v1/ranges")
        invalid_ip = client.get("/api/v1/ip/not-an-ip")

    assert missing_service.status_code == 422
    assert invalid_ip.status_code == 422
    assert isinstance(missing_service.json()["detail"], list)
    assert invalid_ip.json()["detail"][0]["loc"] == [
        "path",
        "ip_address",
    ]


def test_unexpected_endpoint_exceptions_are_not_swallowed(tmp_path) -> None:
    class BrokenCatalog:
        def metadata(self):
            raise RuntimeError("unexpected")

    app = create_app(
        Settings(data_dir=tmp_path),
        catalog_factory=lambda result: BrokenCatalog(),
    )

    with TestClient(app) as client:
        with pytest.raises(RuntimeError, match="unexpected"):
            client.get("/api/v1/metadata")

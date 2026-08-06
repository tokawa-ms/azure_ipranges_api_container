"""FastAPI application factory and lifecycle wiring."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Protocol

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from azure_ipranges_api.config import Settings
from azure_ipranges_api.data.refresh import (
    InitializationResult,
    RefreshCoordinator,
)
from azure_ipranges_api.errors import (
    DataUnavailableError,
    DataUnavailableReason,
    data_unavailable_response,
)
from azure_ipranges_api.logging import configure_logging
from azure_ipranges_api.services import CatalogService


logger = logging.getLogger(__name__)

APP_TITLE = "Azure IP Ranges API"
APP_DESCRIPTION = "Lookup API for Microsoft Azure Service Tags and IP ranges."


class RefreshCoordinatorProtocol(Protocol):
    async def initialize(self) -> InitializationResult: ...


CoordinatorFactory = Callable[[Settings], RefreshCoordinatorProtocol]
CatalogFactory = Callable[[InitializationResult], CatalogService]
RouterRegistrar = Callable[[FastAPI], None]


def _package_version() -> str:
    try:
        return version("azure-ipranges-api")
    except PackageNotFoundError:
        return "0.1.0"


def register_router(app: FastAPI, router: APIRouter) -> None:
    """Register a router after both the application and router are importable."""

    app.include_router(router)


def register_api_routers(app: FastAPI) -> None:
    """Register all API routers without making them application dependencies."""

    from azure_ipranges_api.api.health import router as health_router
    from azure_ipranges_api.api.ranges import router as ranges_router

    register_router(app, health_router)
    register_router(app, ranges_router)


def create_app(
    settings: Settings | None = None,
    *,
    coordinator_factory: CoordinatorFactory = RefreshCoordinator,
    catalog_factory: CatalogFactory = CatalogService,
    router_registrar: RouterRegistrar | None = None,
) -> FastAPI:
    """Create an isolated application whose catalog is initialized at startup."""

    app_settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(app_settings.log_level)

        try:
            coordinator = coordinator_factory(app_settings)
            result = await coordinator.initialize()
            if not isinstance(result, InitializationResult):
                raise TypeError(
                    "RefreshCoordinator.initialize() returned an invalid result"
                )
        except Exception:
            logger.exception("Application data initialization failed")
            result = InitializationResult.unavailable(
                DataUnavailableReason.INITIALIZATION_FAILED,
                retryable=False,
            )

        try:
            catalog = catalog_factory(result)
        except Exception:
            logger.exception("Catalog service construction failed")
            catalog = CatalogService(
                InitializationResult.unavailable(
                    DataUnavailableReason.INITIALIZATION_FAILED,
                    retryable=False,
                )
            )

        app.state.settings = app_settings
        app.state.catalog_service = catalog
        yield

    application = FastAPI(
        title=APP_TITLE,
        version=_package_version(),
        description=APP_DESCRIPTION,
        lifespan=lifespan,
    )

    @application.exception_handler(DataUnavailableError)
    async def handle_data_unavailable(
        request: Request,
        error: DataUnavailableError,
    ) -> JSONResponse:
        del request
        return data_unavailable_response(error)

    registrar = (
        router_registrar
        if router_registrar is not None
        else register_api_routers
    )
    registrar(application)
    return application


__all__ = [
    "APP_DESCRIPTION",
    "APP_TITLE",
    "create_app",
    "register_api_routers",
    "register_router",
]

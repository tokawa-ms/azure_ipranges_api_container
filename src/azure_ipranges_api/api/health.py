"""Health and catalog metadata API endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from azure_ipranges_api.models import (
    ErrorResponse,
    LivenessResponse,
    MetadataResponse,
    ReadinessResponse,
)
from azure_ipranges_api.services import CatalogService


router = APIRouter()


def get_catalog_service(request: Request) -> CatalogService:
    """Return the application-scoped catalog service."""

    return request.app.state.catalog_service


CatalogDependency = Annotated[CatalogService, Depends(get_catalog_service)]


@router.get(
    "/health/live",
    response_model=LivenessResponse,
    summary="Check process liveness",
)
def get_liveness() -> LivenessResponse:
    """Report that the API process is alive."""

    return LivenessResponse()


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessResponse,
            "description": "The catalog is unavailable.",
        }
    },
    summary="Check catalog readiness",
)
def get_readiness(catalog_service: CatalogDependency) -> ReadinessResponse | JSONResponse:
    """Report whether validated catalog data is ready to serve."""

    readiness = catalog_service.readiness()
    if readiness.ready:
        return readiness

    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=readiness.model_dump(mode="json"),
    )


@router.get(
    "/api/v1/metadata",
    response_model=MetadataResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "No validated Azure IP ranges catalog is available.",
        }
    },
    summary="Get catalog metadata",
)
def get_metadata(catalog_service: CatalogDependency) -> MetadataResponse:
    """Return metadata for the currently loaded catalog."""

    return catalog_service.metadata()


__all__ = ["get_catalog_service", "router"]

"""Lookup endpoints for Azure Service Tag ranges."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, BeforeValidator, ConfigDict

from azure_ipranges_api.models import (
    ErrorResponse,
    ForwardLookupResponse,
    ReverseLookupResponse,
)
from azure_ipranges_api.search.reverse import InvalidIpAddressError
from azure_ipranges_api.services import CatalogService


class ValidationErrorDetail(BaseModel):
    """One FastAPI/Pydantic validation error."""

    model_config = ConfigDict(extra="allow")

    loc: list[str | int]
    msg: str
    type: str
    input: Any | None = None


class ValidationErrorResponse(BaseModel):
    """FastAPI validation error envelope."""

    detail: list[ValidationErrorDetail]


def get_catalog_service(request: Request) -> CatalogService:
    """Return the immutable catalog initialized during application startup."""

    catalog: CatalogService = request.app.state.catalog_service
    return catalog


CatalogDependency = Annotated[CatalogService, Depends(get_catalog_service)]
IpVersionQuery = Annotated[Literal[4, 6], BeforeValidator(int)]

router = APIRouter(prefix="/api/v1", tags=["ranges"])

_VALIDATION_RESPONSE = {
    "model": ValidationErrorResponse,
    "description": "The request contains an invalid query or path parameter.",
    "content": {
        "application/json": {
            "example": {
                "detail": [
                    {
                        "type": "value_error",
                        "loc": ["path", "ip_address"],
                        "msg": (
                            "Value error, IP address must be a valid IPv4 "
                            "or IPv6 address"
                        ),
                        "input": "not-an-ip",
                    }
                ]
            }
        }
    },
}

_UNAVAILABLE_RESPONSE = {
    "model": ErrorResponse,
    "description": "No validated Azure IP ranges catalog is available.",
    "content": {
        "application/json": {
            "example": {
                "detail": {
                    "code": "data_unavailable",
                    "message": (
                        "Azure IP ranges data is temporarily unavailable."
                    ),
                    "reason": "No validated Azure IP ranges data is available.",
                    "retryable": True,
                }
            }
        }
    },
}


@router.get(
    "/ranges",
    response_model=ForwardLookupResponse,
    summary="Look up ranges by service",
    description=(
        "Returns Service Tag records whose full tag name or system service "
        "matches `service`. Results can be filtered by region and IP version; "
        "a lookup with no matches returns an empty `items` array."
    ),
    responses={
        200: {
            "description": "Matching Service Tag ranges.",
            "content": {
                "application/json": {
                    "example": {
                        "service": "Storage",
                        "region": "japaneast",
                        "ip_version": 4,
                        "items": [
                            {
                                "name": "Storage.JapanEast",
                                "id": "Storage.JapanEast",
                                "change_number": 1,
                                "region": "japaneast",
                                "platform": "Azure",
                                "system_service": "Storage",
                                "address_prefixes": ["20.60.0.0/16"],
                            }
                        ],
                    }
                }
            },
        },
        422: _VALIDATION_RESPONSE,
        503: _UNAVAILABLE_RESPONSE,
    },
)
def get_ranges(
    catalog: CatalogDependency,
    service: Annotated[
        str,
        Query(
            min_length=1,
            pattern=r".*\S.*",
            description="Full Service Tag name or system service.",
            examples=["Storage"],
        ),
    ],
    region: Annotated[
        str | None,
        Query(
            description="Optional case-insensitive Azure region filter.",
            examples=["japaneast"],
        ),
    ] = None,
    ip_version: Annotated[
        IpVersionQuery | None,
        Query(description="Optional IP version filter.", examples=[4]),
    ] = None,
) -> ForwardLookupResponse:
    """Return ranges matching a service and optional filters."""

    return catalog.forward_lookup(
        service,
        region=region,
        ip_version=ip_version,
    )


@router.get(
    "/ip/{ip_address}",
    response_model=ReverseLookupResponse,
    summary="Look up an IP address",
    description=(
        "Returns every indexed Service Tag prefix containing the supplied IPv4 "
        "or IPv6 address. A valid address with no matches returns an empty "
        "`items` array."
    ),
    responses={
        200: {
            "description": "Service Tag prefixes containing the IP address.",
            "content": {
                "application/json": {
                    "example": {
                        "ip_address": "20.60.1.10",
                        "items": [
                            {
                                "name": "Storage.JapanEast",
                                "id": "Storage.JapanEast",
                                "change_number": 1,
                                "region": "japaneast",
                                "platform": "Azure",
                                "system_service": "Storage",
                                "prefix": "20.60.0.0/16",
                            }
                        ],
                    }
                }
            },
        },
        422: _VALIDATION_RESPONSE,
        503: _UNAVAILABLE_RESPONSE,
    },
)
def get_ip_ranges(
    ip_address: str,
    catalog: CatalogDependency,
) -> ReverseLookupResponse:
    """Return all Service Tag prefixes containing an IP address."""

    try:
        return catalog.reverse_lookup(ip_address)
    except InvalidIpAddressError as error:
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("path", "ip_address"),
                    "msg": f"Value error, {error}",
                    "input": ip_address,
                }
            ]
        ) from None


__all__ = ["get_catalog_service", "router"]

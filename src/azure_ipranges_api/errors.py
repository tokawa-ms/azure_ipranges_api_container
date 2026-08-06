"""Safe, standardized API errors."""

from enum import StrEnum
from typing import Final, TypedDict

from fastapi import status
from fastapi.responses import JSONResponse


DATA_UNAVAILABLE_CODE: Final = "data_unavailable"
DATA_UNAVAILABLE_MESSAGE: Final = (
    "Azure IP ranges data is temporarily unavailable."
)


class DataUnavailableReason(StrEnum):
    """Reasons safe to expose to API clients."""

    CACHE_MISSING = "No validated Azure IP ranges data is available."
    CACHE_EXPIRED = (
        "The cached Azure IP ranges data has expired and could not be refreshed."
    )
    CACHE_INVALID = (
        "The cached Azure IP ranges data is invalid and could not be refreshed."
    )
    REFRESH_FAILED = "Azure IP ranges data could not be refreshed."
    INITIALIZATION_FAILED = "Azure IP ranges data could not be initialized."


class ErrorResponseContent(TypedDict):
    code: str
    message: str
    reason: str
    retryable: bool


class DataUnavailableError(RuntimeError):
    """Raised when no validated Azure IP ranges data can be served."""

    code: Final = DATA_UNAVAILABLE_CODE
    message: Final = DATA_UNAVAILABLE_MESSAGE

    def __init__(
        self,
        reason: DataUnavailableReason,
        *,
        retryable: bool = True,
    ) -> None:
        if not isinstance(reason, DataUnavailableReason):
            raise TypeError("reason must be a DataUnavailableReason")
        if not isinstance(retryable, bool):
            raise TypeError("retryable must be a bool")

        self.reason = reason
        self.retryable = retryable
        super().__init__(self.message)


def data_unavailable_content(
    error: DataUnavailableError,
) -> ErrorResponseContent:
    """Build client-safe content without exception or upstream details."""

    return {
        "code": error.code,
        "message": error.message,
        "reason": error.reason.value,
        "retryable": error.retryable,
    }


def data_unavailable_response(error: DataUnavailableError) -> JSONResponse:
    """Build the standardized HTTP 503 response."""

    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": data_unavailable_content(error)},
    )

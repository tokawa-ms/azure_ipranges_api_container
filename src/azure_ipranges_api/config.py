from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,
        env_prefix="",
        case_sensitive=True,
        populate_by_name=True,
        extra="ignore",
    )

    data_dir: Path = Field(
        default=Path("/data"),
        validation_alias="DATA_DIR",
    )
    cache_ttl_seconds: Annotated[int, Field(gt=0)] = Field(
        default=604800,
        validation_alias="CACHE_TTL_SECONDS",
    )
    download_page_url: Annotated[str, Field(min_length=1)] = Field(
        default="https://www.microsoft.com/en-us/download/details.aspx?id=56519",
        validation_alias="DOWNLOAD_PAGE_URL",
    )
    http_timeout_seconds: Annotated[float, Field(gt=0)] = Field(
        default=30,
        validation_alias="HTTP_TIMEOUT_SECONDS",
    )
    http_retry_count: Annotated[int, Field(ge=0)] = Field(
        default=3,
        validation_alias="HTTP_RETRY_COUNT",
    )
    log_level: LogLevel = Field(
        default="INFO",
        validation_alias="LOG_LEVEL",
    )

    @field_validator("data_dir")
    @classmethod
    def resolve_data_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("download_page_url")
    @classmethod
    def validate_download_page_url(cls, value: str) -> str:
        from pydantic import AnyHttpUrl, TypeAdapter

        url = TypeAdapter(AnyHttpUrl).validate_python(value)
        if url.scheme != "https":
            raise ValueError("DOWNLOAD_PAGE_URL must use HTTPS")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        normalized = value.strip().upper()
        aliases = {"WARN": "WARNING", "FATAL": "CRITICAL"}
        return aliases.get(normalized, normalized)

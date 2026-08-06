from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from azure_ipranges_api.data.loader import LoadedCache
from azure_ipranges_api.data.refresh import InitializationResult
from azure_ipranges_api.errors import DataUnavailableError, DataUnavailableReason
from azure_ipranges_api.models import ServiceTagsDocument
from azure_ipranges_api.services import CatalogService


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "service-tags-sample.json"


def _available_result(sample_cache_metadata) -> InitializationResult:
    raw_json = FIXTURE_PATH.read_bytes()
    document = ServiceTagsDocument.model_validate(json.loads(raw_json))
    return InitializationResult.from_cache(
        LoadedCache(
            document=document,
            metadata=sample_cache_metadata,
            raw_json=raw_json,
        )
    )


def test_available_catalog_builds_one_read_only_index_and_serves_lookups(
    sample_cache_metadata,
) -> None:
    catalog = CatalogService(_available_result(sample_cache_metadata))

    forward = catalog.forward_lookup("Storage", ip_version=4)
    reverse = catalog.reverse_lookup("10.0.0.70")
    metadata = catalog.metadata()
    readiness = catalog.readiness()

    assert [item.name for item in forward.items] == [
        "Storage",
        "Storage.JapanEast",
    ]
    assert [item.prefix for item in reverse.items] == [
        "10.0.0.64/26",
        "10.0.0.0/25",
        "10.0.0.0/24",
    ]
    assert metadata.model_dump() == sample_cache_metadata.model_dump()
    assert readiness.ready
    assert readiness.fetched_at == sample_cache_metadata.fetched_at
    assert readiness.change_number == sample_cache_metadata.change_number
    assert not readiness.stale

    metadata.filename = "changed.json"
    assert catalog.metadata().filename == sample_cache_metadata.filename
    with pytest.raises(FrozenInstanceError):
        catalog._index = None


@pytest.mark.parametrize(
    ("reason", "retryable", "stale"),
    [
        (DataUnavailableReason.CACHE_MISSING, True, False),
        (DataUnavailableReason.CACHE_EXPIRED, True, True),
        (DataUnavailableReason.INITIALIZATION_FAILED, False, False),
    ],
)
def test_unavailable_catalog_exposes_readiness_but_rejects_data_access(
    reason: DataUnavailableReason,
    retryable: bool,
    stale: bool,
) -> None:
    catalog = CatalogService(
        InitializationResult.unavailable(reason, retryable=retryable)
    )

    readiness = catalog.readiness()
    assert not readiness.ready
    assert readiness.reason == reason.value
    assert readiness.stale is stale
    assert readiness.fetched_at is None
    assert readiness.change_number is None

    for operation in (
        lambda: catalog.forward_lookup("Storage"),
        lambda: catalog.reverse_lookup("10.0.0.1"),
        catalog.metadata,
    ):
        with pytest.raises(DataUnavailableError) as raised:
            operation()
        assert raised.value.reason is reason
        assert raised.value.retryable is retryable
        assert raised.value.message == (
            "Azure IP ranges data is temporarily unavailable."
        )

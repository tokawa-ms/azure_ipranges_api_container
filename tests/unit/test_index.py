"""Comprehensive tests for the ServiceTagIndex module."""

from __future__ import annotations

import json
from ipaddress import IPv4Network, IPv6Network
from pathlib import Path
from unittest.mock import Mock

import pytest

from azure_ipranges_api.models import ServiceTagsDocument
from azure_ipranges_api.search.index import (
    InvalidAddressPrefixError,
    ServiceTagIndex,
    ServiceTagRecord,
    PrefixRecord,
)
from azure_ipranges_api.search.forward import lookup_forward
from azure_ipranges_api.search.reverse import reverse_lookup, InvalidIpAddressError


@pytest.fixture
def sample_document(sample_service_tags_json: bytes) -> ServiceTagsDocument:
    """Load and parse the sample service tags document."""
    data = json.loads(sample_service_tags_json)
    return ServiceTagsDocument(**data)


@pytest.fixture
def sample_index(sample_document: ServiceTagsDocument) -> ServiceTagIndex:
    """Build a ServiceTagIndex from the sample document."""
    return ServiceTagIndex(sample_document)


class TestIndexConstruction:
    """Test index construction from valid documents."""

    def test_index_construction_succeeds_with_valid_document(
        self, sample_document: ServiceTagsDocument
    ) -> None:
        """Index construction should succeed with a valid document."""
        index = ServiceTagIndex(sample_document)
        assert index is not None
        assert index.document_change_number == 100

    def test_index_construction_parses_ipv4_and_ipv6_prefixes(
        self, sample_document: ServiceTagsDocument
    ) -> None:
        """Index should parse both IPv4 and IPv6 address prefixes."""
        index = ServiceTagIndex(sample_document)
        # Check that reverse index contains entries for both IPv4 and IPv6
        assert any(key[0] == 4 for key in index.reverse_index.keys())
        assert any(key[0] == 6 for key in index.reverse_index.keys())

    def test_index_deduplicates_duplicate_prefixes(self) -> None:
        """Index should deduplicate identical prefixes within a tag."""
        document_dict = {
            "changeNumber": 1,
            "values": [
                {
                    "name": "TestDuplicates",
                    "id": "TestDuplicates",
                    "properties": {
                        "changeNumber": 1,
                        "region": "",
                        "platform": "Azure",
                        "systemService": "",
                        "addressPrefixes": [
                            "10.0.0.0/24",
                            "10.0.0.0/24",  # duplicate
                            "10.0.0.0/24",  # another duplicate
                        ],
                    },
                }
            ],
        }
        document = ServiceTagsDocument(**document_dict)
        index = ServiceTagIndex(document)
        # Should have only one record for this tag
        records = index.full_name_index.get("testduplicates", ())
        assert len(records) == 1
        # Should have only one prefix in the record
        assert len(records[0].address_prefixes) == 1

    def test_index_construction_with_empty_system_service(self) -> None:
        """Index should handle tags with empty system service."""
        document_dict = {
            "changeNumber": 1,
            "values": [
                {
                    "name": "AzureCloud",
                    "id": "AzureCloud",
                    "properties": {
                        "changeNumber": 1,
                        "region": "",
                        "platform": "Azure",
                        "systemService": "",
                        "addressPrefixes": ["10.0.0.0/24"],
                    },
                }
            ],
        }
        document = ServiceTagsDocument(**document_dict)
        index = ServiceTagIndex(document)
        # Should not be in system_service_index
        assert "azurecloud" not in index.system_service_index
        # Should be in by_name
        assert "azurecloud" in index.full_name_index

    def test_index_construction_raises_on_invalid_prefix_pydantic(self) -> None:
        """Pydantic validation should reject invalid CIDR during document parsing."""
        from pydantic import ValidationError
        document_dict = {
            "changeNumber": 1,
            "values": [
                {
                    "name": "BadPrefix",
                    "id": "BadPrefix",
                    "properties": {
                        "changeNumber": 1,
                        "region": "",
                        "platform": "Azure",
                        "systemService": "",
                        "addressPrefixes": ["invalid-cidr"],
                    },
                }
            ],
        }
        with pytest.raises(ValidationError):
            ServiceTagsDocument(**document_dict)

    def test_index_construction_raises_invalid_address_prefix_error(self) -> None:
        """Index constructor should raise InvalidAddressPrefixError for invalid prefixes."""
        # Create a mock document object with an invalid prefix to bypass Pydantic validation
        mock_doc = Mock(spec=ServiceTagsDocument)
        mock_doc.change_number = 1
        
        mock_value = Mock()
        mock_value.name = "TestTag"
        mock_value.id = "TestTag"
        mock_value.properties = Mock()
        mock_value.properties.change_number = 1
        mock_value.properties.region = ""
        mock_value.properties.platform = "Azure"
        mock_value.properties.system_service = ""
        mock_value.properties.address_prefixes = ["not-a-valid-cidr"]
        
        mock_doc.values = [mock_value]
        
        with pytest.raises(InvalidAddressPrefixError) as exc_info:
            ServiceTagIndex(mock_doc)
        assert exc_info.value.tag_name == "TestTag"
        assert exc_info.value.prefix == "not-a-valid-cidr"

    def test_index_construction_raises_on_non_strict_prefix(self) -> None:
        """Pydantic validation should reject non-strict CIDR (host bits set)."""
        from pydantic import ValidationError
        document_dict = {
            "changeNumber": 1,
            "values": [
                {
                    "name": "NonStrictCIDR",
                    "id": "NonStrictCIDR",
                    "properties": {
                        "changeNumber": 1,
                        "region": "",
                        "platform": "Azure",
                        "systemService": "",
                        "addressPrefixes": ["10.0.0.5/24"],  # host bits set
                    },
                }
            ],
        }
        with pytest.raises(ValidationError):
            ServiceTagsDocument(**document_dict)


class TestImmutableAccessors:
    """Test immutable data accessors."""

    def test_full_name_index_is_immutable(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """full_name_index property should return immutable mapping."""
        index_view = sample_index.full_name_index
        # Accessing the same property should return the same object
        assert index_view is sample_index.by_name
        # Should not be directly modifiable
        with pytest.raises(TypeError):
            index_view["newkey"] = ()  # type: ignore

    def test_system_service_index_is_immutable(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """system_service_index property should return immutable mapping."""
        index_view = sample_index.system_service_index
        assert index_view is sample_index.by_system_service
        with pytest.raises(TypeError):
            index_view["newkey"] = ()  # type: ignore

    def test_reverse_index_is_immutable(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """reverse_index property should return immutable mapping."""
        index_view = sample_index.reverse_index
        assert index_view is sample_index.reverse
        with pytest.raises(TypeError):
            index_view[(4, 24, 0)] = ()  # type: ignore

    def test_index_object_is_frozen(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """ServiceTagIndex should be frozen (immutable)."""
        with pytest.raises(AttributeError):
            sample_index.document_change_number = 999  # type: ignore


class TestCaseInsensitiveExactMatching:
    """Test case-insensitive exact service name and system service matching."""

    def test_forward_lookup_by_exact_name_case_insensitive(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup should match service name case-insensitively."""
        # Try different case variations
        result1 = lookup_forward(sample_index, "Storage")
        result2 = lookup_forward(sample_index, "storage")
        result3 = lookup_forward(sample_index, "STORAGE")
        result4 = lookup_forward(sample_index, "StOrAgE")

        # All should match the same items
        assert len(result1.items) == len(result2.items) == len(result3.items) == len(result4.items)
        assert len(result1.items) == 2  # Storage and Storage.JapanEast

    def test_forward_lookup_by_exact_system_service_case_insensitive(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup should match system_service case-insensitively."""
        result1 = lookup_forward(sample_index, "Storage")
        result2 = lookup_forward(sample_index, "storage")

        # Both should find the storage service tag
        assert len(result1.items) > 0
        assert len(result2.items) > 0

    def test_forward_lookup_no_partial_match(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup should not match partial service names."""
        result = lookup_forward(sample_index, "Stor")
        assert len(result.items) == 0

    def test_forward_lookup_finds_both_name_and_system_service_matches(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup should find tags matching by either name or systemService."""
        result = lookup_forward(sample_index, "Storage")
        # Should find Storage (by name) and Storage.JapanEast (by name) both with systemService=Storage
        assert len(result.items) >= 2


class TestRegionFiltering:
    """Test optional exact region filtering."""

    def test_forward_lookup_with_region_filter(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup with region should filter results."""
        # Without region
        result_no_region = lookup_forward(sample_index, "Storage")
        # With region
        result_with_region = lookup_forward(sample_index, "Storage", region="japaneast")

        # Region-filtered should have fewer items
        assert len(result_with_region.items) < len(result_no_region.items)
        # All items should have japaneast region
        assert all(item.region == "japaneast" for item in result_with_region.items)

    def test_forward_lookup_with_region_case_insensitive(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup should match region case-insensitively."""
        result1 = lookup_forward(sample_index, "Storage", region="japaneast")
        result2 = lookup_forward(sample_index, "Storage", region="JapanEast")
        result3 = lookup_forward(sample_index, "Storage", region="JAPANEAST")

        assert len(result1.items) == len(result2.items) == len(result3.items)

    def test_forward_lookup_with_nonexistent_region(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup with non-existent region should return empty results."""
        result = lookup_forward(sample_index, "Storage", region="nonexistent")
        assert len(result.items) == 0


class TestIpVersionFiltering:
    """Test IPv4/IPv6 filtering."""

    def test_forward_lookup_ipv4_only(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup should filter to IPv4 prefixes only."""
        result = lookup_forward(sample_index, "Storage", ip_version=4)
        # All prefixes should be IPv4
        for item in result.items:
            for prefix in item.address_prefixes:
                assert "." in prefix  # IPv4 address format
                assert ":" not in prefix  # Not IPv6

    def test_forward_lookup_ipv6_only(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup should filter to IPv6 prefixes only."""
        result = lookup_forward(sample_index, "Storage", ip_version=6)
        # All prefixes should be IPv6
        for item in result.items:
            for prefix in item.address_prefixes:
                assert ":" in prefix  # IPv6 address format

    def test_forward_lookup_invalid_ip_version(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup should raise ValueError for invalid ip_version."""
        with pytest.raises(ValueError):
            lookup_forward(sample_index, "Storage", ip_version=3)  # type: ignore

        with pytest.raises(ValueError):
            lookup_forward(sample_index, "Storage", ip_version=True)  # type: ignore

        with pytest.raises(ValueError):
            lookup_forward(sample_index, "Storage", ip_version="4")  # type: ignore

    def test_forward_lookup_empty_results_when_no_matching_ip_version(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup should return empty results if tag has no matching IP version."""
        # AzureCloud only has IPv4 (10.0.0.64/26, 192.0.2.0/24)
        result = lookup_forward(sample_index, "AzureCloud", ip_version=6)
        assert len(result.items) == 0


class TestReverseLookup:
    """Test reverse IP address lookup."""

    def test_reverse_lookup_ipv4_address(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Reverse lookup should find IPv4 addresses in prefixes."""
        # 10.0.0.100 should match 10.0.0.0/24 and 10.0.0.0/25
        result = reverse_lookup(sample_index, "10.0.0.100")
        assert len(result) > 0
        # Should all be Storage-related tags
        assert any("Storage" in item.name for item in result)

    def test_reverse_lookup_ipv6_address(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Reverse lookup should find IPv6 addresses in prefixes."""
        # 2001:db8::1 should match 2001:db8::/48 and 2001:db8::/49
        result = reverse_lookup(sample_index, "2001:db8::1")
        assert len(result) > 0
        assert any("Storage" in item.name for item in result)

    def test_reverse_lookup_invalid_ip_address(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Reverse lookup should raise InvalidIpAddressError for invalid IPs."""
        with pytest.raises(InvalidIpAddressError):
            reverse_lookup(sample_index, "invalid-ip")

        with pytest.raises(InvalidIpAddressError):
            reverse_lookup(sample_index, "999.999.999.999")

        with pytest.raises(InvalidIpAddressError):
            reverse_lookup(sample_index, "gggg::1")

    def test_reverse_lookup_no_match(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Reverse lookup should return empty results if IP not in any prefix."""
        # 192.168.1.1 should not match any sample prefixes
        result = reverse_lookup(sample_index, "192.168.1.1")
        assert len(result) == 0

    def test_reverse_lookup_address_at_network_boundary(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Reverse lookup should find addresses at network boundaries."""
        # First address in 10.0.0.0/24
        result1 = reverse_lookup(sample_index, "10.0.0.0")
        assert len(result1) > 0
        # Last address in 10.0.0.0/24
        result2 = reverse_lookup(sample_index, "10.0.0.255")
        assert len(result2) > 0


class TestLongestPrefixFirstOrdering:
    """Test longest-prefix-first ordering in reverse lookups."""

    def test_reverse_lookup_longest_prefix_first(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Reverse lookup results should be ordered longest-prefix-first."""
        # 10.0.0.100 matches both 10.0.0.0/24 and 10.0.0.0/25
        result = reverse_lookup(sample_index, "10.0.0.100")

        if len(result) > 1:
            # Find records for the overlapping prefixes
            prefixes_with_records = [item.prefix for item in result]
            # Longer prefixes should appear before shorter ones in results
            # Check ordering within same service tag
            current_prefix_len = None
            for item in result:
                prefix_len = int(item.prefix.split("/")[1])
                if current_prefix_len is not None:
                    # Should not go from longer to shorter
                    if prefix_len != current_prefix_len:
                        assert prefix_len <= current_prefix_len
                current_prefix_len = prefix_len

    def test_reverse_lookup_prefix_order_with_multiple_tags(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Reverse lookup should order by prefix length first, then by tag properties."""
        result = reverse_lookup(sample_index, "10.0.0.100")
        # Verify that the ordering is deterministic
        result2 = reverse_lookup(sample_index, "10.0.0.100")
        assert [item.prefix for item in result] == [item.prefix for item in result2]


class TestDeterministicTieOrdering:
    """Test deterministic ordering of results when multiple tags match."""

    def test_forward_lookup_deterministic_ordering(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup results should be deterministically ordered."""
        result1 = lookup_forward(sample_index, "Storage")
        result2 = lookup_forward(sample_index, "Storage")

        # Extract key info from items
        keys1 = [(item.name, item.id) for item in result1.items]
        keys2 = [(item.name, item.id) for item in result2.items]

        assert keys1 == keys2

    def test_reverse_lookup_deterministic_ordering(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Reverse lookup results should be deterministically ordered."""
        result1 = reverse_lookup(sample_index, "10.0.0.100")
        result2 = reverse_lookup(sample_index, "10.0.0.100")

        keys1 = [(item.name, item.id, item.prefix) for item in result1]
        keys2 = [(item.name, item.id, item.prefix) for item in result2]

        assert keys1 == keys2


class TestDeduplication:
    """Test deduplication of results."""

    def test_reverse_lookup_deduplicates_results(self) -> None:
        """Reverse lookup should not return duplicate tag/prefix pairs."""
        document_dict = {
            "changeNumber": 1,
            "values": [
                {
                    "name": "Storage",
                    "id": "Storage",
                    "properties": {
                        "changeNumber": 1,
                        "region": "",
                        "platform": "Azure",
                        "systemService": "Storage",
                        "addressPrefixes": ["10.0.0.0/24"],
                    },
                },
                {
                    "name": "Storage",
                    "id": "Storage",
                    "properties": {
                        "changeNumber": 1,
                        "region": "all",
                        "platform": "Azure",
                        "systemService": "Storage",
                        "addressPrefixes": ["10.0.0.0/24"],
                    },
                },
            ],
        }
        document = ServiceTagsDocument(**document_dict)
        index = ServiceTagIndex(document)

        result = reverse_lookup(index, "10.0.0.100")
        # Should have 2 records (different regions)
        assert len(result) == 2


class TestEmptyResults:
    """Test handling of empty result sets."""

    def test_forward_lookup_no_service_match(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup should return empty items for non-existent service."""
        result = lookup_forward(sample_index, "NonExistentService")
        assert result.service == "NonExistentService"
        assert len(result.items) == 0

    def test_forward_lookup_service_with_wrong_region(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup should return empty items when region doesn't match."""
        result = lookup_forward(sample_index, "Storage", region="uswest")
        assert len(result.items) == 0

    def test_forward_lookup_service_with_wrong_ip_version(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup should return empty items when ip_version matches no prefixes."""
        # AzureCloud has no IPv6 prefixes
        result = lookup_forward(sample_index, "AzureCloud", ip_version=6)
        assert len(result.items) == 0

    def test_reverse_lookup_address_not_in_ranges(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Reverse lookup should return empty list for addresses not in any prefix."""
        result = reverse_lookup(sample_index, "172.16.0.1")
        assert len(result) == 0


class TestInvalidServiceName:
    """Test validation of service name input."""

    def test_forward_lookup_empty_service_name(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup should reject empty service name."""
        with pytest.raises(ValueError):
            lookup_forward(sample_index, "")

    def test_forward_lookup_whitespace_only_service_name(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup should reject whitespace-only service name."""
        with pytest.raises(ValueError):
            lookup_forward(sample_index, "   ")

    def test_forward_lookup_non_string_service_name(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup should reject non-string service name."""
        with pytest.raises(ValueError):
            lookup_forward(sample_index, None)  # type: ignore

        with pytest.raises(ValueError):
            lookup_forward(sample_index, 123)  # type: ignore


class TestRecordTypes:
    """Test the record types and their properties."""

    def test_service_tag_record_is_frozen(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """ServiceTagRecord should be immutable."""
        records = sample_index.full_name_index.get("storage", ())
        if records:
            record = records[0]
            with pytest.raises(AttributeError):
                record.name = "NewName"  # type: ignore

    def test_prefix_record_is_frozen(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """PrefixRecord should be immutable."""
        reverse_keys = list(sample_index.reverse_index.keys())
        if reverse_keys:
            records = sample_index.reverse_index[reverse_keys[0]]
            if records:
                record = records[0]
                with pytest.raises(AttributeError):
                    record.name = "NewName"  # type: ignore


class TestPrefixSorting:
    """Test that prefixes are sorted correctly within records."""

    def test_address_prefixes_sorted_in_records(self) -> None:
        """Address prefixes in ServiceTagRecord should be sorted."""
        document_dict = {
            "changeNumber": 1,
            "values": [
                {
                    "name": "TestSort",
                    "id": "TestSort",
                    "properties": {
                        "changeNumber": 1,
                        "region": "",
                        "platform": "Azure",
                        "systemService": "",
                        "addressPrefixes": [
                            "192.0.2.0/24",
                            "10.0.0.0/24",
                            "2001:db8::/48",
                        ],
                    },
                }
            ],
        }
        document = ServiceTagsDocument(**document_dict)
        index = ServiceTagIndex(document)

        records = index.full_name_index.get("testsort", ())
        assert len(records) == 1
        record = records[0]
        
        # Prefixes should be sorted by version, then network address, then prefix length
        prefixes = record.address_prefixes
        assert prefixes[0].version == 4  # IPv4 comes before IPv6
        assert prefixes[1].version == 4
        assert prefixes[2].version == 6


class TestWithComplexDocument:
    """Test index behavior with more complex documents."""

    def test_index_with_multiple_regions_and_systems(self) -> None:
        """Index should handle multiple regions and system services correctly."""
        document_dict = {
            "changeNumber": 1,
            "values": [
                {
                    "name": "Service.Region1",
                    "id": "Service.Region1",
                    "properties": {
                        "changeNumber": 1,
                        "region": "region1",
                        "platform": "Azure",
                        "systemService": "Service",
                        "addressPrefixes": ["10.0.0.0/24"],
                    },
                },
                {
                    "name": "Service.Region2",
                    "id": "Service.Region2",
                    "properties": {
                        "changeNumber": 1,
                        "region": "region2",
                        "platform": "Azure",
                        "systemService": "Service",
                        "addressPrefixes": ["10.0.1.0/24"],
                    },
                },
                {
                    "name": "OtherService.Region1",
                    "id": "OtherService.Region1",
                    "properties": {
                        "changeNumber": 1,
                        "region": "region1",
                        "platform": "Azure",
                        "systemService": "OtherService",
                        "addressPrefixes": ["10.1.0.0/24"],
                    },
                },
            ],
        }
        document = ServiceTagsDocument(**document_dict)
        index = ServiceTagIndex(document)

        # Test filtering by system service
        result = lookup_forward(index, "Service")
        assert len(result.items) == 2

        # Test filtering by system service and region
        result = lookup_forward(index, "Service", region="region1")
        assert len(result.items) == 1
        assert result.items[0].name == "Service.Region1"

        # Test reverse lookup
        result = reverse_lookup(index, "10.0.0.100")
        assert len(result) == 1
        assert result[0].name == "Service.Region1"


class TestIndexAccessorConsistency:
    """Test that index accessors return consistent results."""

    def test_multiple_accesses_return_same_objects(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Multiple accesses to index properties should return the same objects."""
        accessor1 = sample_index.full_name_index
        accessor2 = sample_index.full_name_index
        assert accessor1 is accessor2

        accessor1 = sample_index.system_service_index
        accessor2 = sample_index.system_service_index
        assert accessor1 is accessor2

        accessor1 = sample_index.reverse_index
        accessor2 = sample_index.reverse_index
        assert accessor1 is accessor2


class TestRegressionAndEdgeCases:
    """Regression tests and edge case handling."""

    def test_index_with_single_prefix(self) -> None:
        """Index should handle tags with a single prefix."""
        document_dict = {
            "changeNumber": 1,
            "values": [
                {
                    "name": "SinglePrefix",
                    "id": "SinglePrefix",
                    "properties": {
                        "changeNumber": 1,
                        "region": "",
                        "platform": "Azure",
                        "systemService": "",
                        "addressPrefixes": ["10.0.0.0/32"],
                    },
                }
            ],
        }
        document = ServiceTagsDocument(**document_dict)
        index = ServiceTagIndex(document)

        result = reverse_lookup(index, "10.0.0.0")
        assert len(result) == 1
        assert result[0].prefix == "10.0.0.0/32"

    def test_index_with_host_only_ipv6_prefix(self) -> None:
        """Index should handle /128 IPv6 prefixes."""
        document_dict = {
            "changeNumber": 1,
            "values": [
                {
                    "name": "IPv6Host",
                    "id": "IPv6Host",
                    "properties": {
                        "changeNumber": 1,
                        "region": "",
                        "platform": "Azure",
                        "systemService": "",
                        "addressPrefixes": ["2001:db8::1/128"],
                    },
                }
            ],
        }
        document = ServiceTagsDocument(**document_dict)
        index = ServiceTagIndex(document)

        result = reverse_lookup(index, "2001:db8::1")
        assert len(result) == 1

    def test_forward_lookup_with_leading_trailing_whitespace(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup should strip leading/trailing whitespace from service name."""
        result = lookup_forward(sample_index, "  Storage  ")
        assert result.service == "Storage"
        assert len(result.items) > 0

    def test_region_none_does_not_filter(
        self, sample_index: ServiceTagIndex
    ) -> None:
        """Forward lookup with region=None should not filter by region."""
        result_without_region = lookup_forward(sample_index, "Storage", region=None)
        result_explicit_none = lookup_forward(sample_index, "Storage", region=None)

        assert len(result_without_region.items) == len(result_explicit_none.items)

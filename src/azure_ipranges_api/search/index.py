"""Immutable indexes built from a validated Service Tags document."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Network, IPv6Network, ip_network
from types import MappingProxyType
from typing import Mapping, TypeAlias

from azure_ipranges_api.models import IpVersion, ServiceTagsDocument


Network: TypeAlias = IPv4Network | IPv6Network
PrefixKey: TypeAlias = tuple[IpVersion, int, int]
TagMap: TypeAlias = Mapping[str, tuple["ServiceTagRecord", ...]]
RegionTagMap: TypeAlias = Mapping[str, Mapping[str, tuple["ServiceTagRecord", ...]]]
ReverseMap: TypeAlias = Mapping[PrefixKey, tuple["PrefixRecord", ...]]


class InvalidAddressPrefixError(ValueError):
    """Raised when index construction encounters an invalid CIDR prefix."""

    def __init__(self, tag_name: str, prefix: object) -> None:
        self.tag_name = tag_name
        self.prefix = prefix
        super().__init__(
            f"invalid address prefix {prefix!r} for service tag {tag_name!r}"
        )


@dataclass(frozen=True, slots=True)
class ServiceTagRecord:
    """Immutable service-tag data retained by the forward indexes."""

    name: str
    id: str
    change_number: int
    region: str
    platform: str
    system_service: str
    address_prefixes: tuple[Network, ...]


@dataclass(frozen=True, slots=True)
class PrefixRecord:
    """Immutable tag and parsed-prefix data retained by the reverse index."""

    name: str
    id: str
    change_number: int
    region: str
    platform: str
    system_service: str
    prefix: Network


def _tag_sort_key(record: ServiceTagRecord) -> tuple[object, ...]:
    return (
        record.name.casefold(),
        record.name,
        record.region.casefold(),
        record.region,
        record.id.casefold(),
        record.id,
        record.system_service.casefold(),
        record.system_service,
        record.platform.casefold(),
        record.platform,
        record.change_number,
        tuple(
            (prefix.version, prefix.prefixlen, int(prefix.network_address))
            for prefix in record.address_prefixes
        ),
    )


def _prefix_sort_key(record: PrefixRecord) -> tuple[object, ...]:
    return (
        record.prefix.version,
        record.prefix.prefixlen,
        int(record.prefix.network_address),
        record.name.casefold(),
        record.name,
        record.region.casefold(),
        record.region,
        record.id.casefold(),
        record.id,
        record.system_service.casefold(),
        record.system_service,
        record.platform.casefold(),
        record.platform,
        record.change_number,
    )


def _freeze_tag_map(
    source: dict[str, set[ServiceTagRecord]],
) -> TagMap:
    return MappingProxyType(
        {
            key: tuple(sorted(source[key], key=_tag_sort_key))
            for key in sorted(source)
        }
    )


def _freeze_region_map(
    source: dict[str, dict[str, set[ServiceTagRecord]]],
) -> RegionTagMap:
    return MappingProxyType(
        {
            key: MappingProxyType(
                {
                    region: tuple(
                        sorted(source[key][region], key=_tag_sort_key)
                    )
                    for region in sorted(source[key])
                }
            )
            for key in sorted(source)
        }
    )


@dataclass(frozen=True, slots=True, init=False)
class ServiceTagIndex:
    """Read-only forward and reverse indexes for one Service Tags document."""

    document_change_number: int
    by_name: TagMap
    by_system_service: TagMap
    by_name_region: RegionTagMap
    by_system_service_region: RegionTagMap
    reverse: ReverseMap
    prefix_lengths_by_version: Mapping[IpVersion, tuple[int, ...]]

    def __init__(self, document: ServiceTagsDocument) -> None:
        by_name: dict[str, set[ServiceTagRecord]] = {}
        by_system_service: dict[str, set[ServiceTagRecord]] = {}
        by_name_region: dict[str, dict[str, set[ServiceTagRecord]]] = {}
        by_system_service_region: dict[
            str, dict[str, set[ServiceTagRecord]]
        ] = {}
        reverse: dict[PrefixKey, set[PrefixRecord]] = {}
        prefix_lengths: dict[IpVersion, set[int]] = {4: set(), 6: set()}

        for value in document.values:
            parsed_prefixes: list[Network] = []
            seen_prefixes: set[Network] = set()
            for prefix_text in value.properties.address_prefixes:
                try:
                    prefix = ip_network(prefix_text, strict=True)
                except (TypeError, ValueError) as exc:
                    raise InvalidAddressPrefixError(
                        value.name, prefix_text
                    ) from exc
                if prefix in seen_prefixes:
                    continue
                seen_prefixes.add(prefix)
                parsed_prefixes.append(prefix)
            parsed_prefixes.sort(
                key=lambda prefix: (
                    prefix.version,
                    int(prefix.network_address),
                    prefix.prefixlen,
                )
            )

            tag_record = ServiceTagRecord(
                name=value.name,
                id=value.id,
                change_number=value.properties.change_number,
                region=value.properties.region,
                platform=value.properties.platform,
                system_service=value.properties.system_service,
                address_prefixes=tuple(parsed_prefixes),
            )
            name_key = value.name.casefold()
            region_key = value.properties.region.casefold()

            by_name.setdefault(name_key, set()).add(tag_record)
            by_name_region.setdefault(name_key, {}).setdefault(
                region_key, set()
            ).add(tag_record)

            system_service = value.properties.system_service
            if system_service:
                service_key = system_service.casefold()
                by_system_service.setdefault(service_key, set()).add(tag_record)
                by_system_service_region.setdefault(service_key, {}).setdefault(
                    region_key, set()
                ).add(tag_record)

            for prefix in parsed_prefixes:
                version: IpVersion = 4 if prefix.version == 4 else 6
                key = (
                    version,
                    prefix.prefixlen,
                    int(prefix.network_address),
                )
                reverse.setdefault(key, set()).add(
                    PrefixRecord(
                        name=value.name,
                        id=value.id,
                        change_number=value.properties.change_number,
                        region=value.properties.region,
                        platform=value.properties.platform,
                        system_service=value.properties.system_service,
                        prefix=prefix,
                    )
                )
                prefix_lengths[version].add(prefix.prefixlen)

        frozen_reverse = MappingProxyType(
            {
                key: tuple(sorted(reverse[key], key=_prefix_sort_key))
                for key in sorted(reverse)
            }
        )
        frozen_prefix_lengths = MappingProxyType(
            {
                version: tuple(sorted(prefix_lengths[version], reverse=True))
                for version in (4, 6)
            }
        )

        object.__setattr__(
            self, "document_change_number", document.change_number
        )
        object.__setattr__(self, "by_name", _freeze_tag_map(by_name))
        object.__setattr__(
            self,
            "by_system_service",
            _freeze_tag_map(by_system_service),
        )
        object.__setattr__(
            self, "by_name_region", _freeze_region_map(by_name_region)
        )
        object.__setattr__(
            self,
            "by_system_service_region",
            _freeze_region_map(by_system_service_region),
        )
        object.__setattr__(self, "reverse", frozen_reverse)
        object.__setattr__(
            self, "prefix_lengths_by_version", frozen_prefix_lengths
        )

    @property
    def full_name_index(self) -> TagMap:
        """Forward records keyed by normalized full tag name."""

        return self.by_name

    @property
    def system_service_index(self) -> TagMap:
        """Forward records keyed by normalized non-empty system service."""

        return self.by_system_service

    @property
    def reverse_index(self) -> ReverseMap:
        """Prefix records keyed by version, prefix length, and network integer."""

        return self.reverse

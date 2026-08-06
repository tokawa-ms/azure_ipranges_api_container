"""Reverse IP-address lookup against a Service Tag index."""

from __future__ import annotations

from ipaddress import IPv4Address, ip_address

from azure_ipranges_api.models import IpLookupItem, IpVersion
from azure_ipranges_api.search.index import PrefixRecord, ServiceTagIndex


class InvalidIpAddressError(ValueError):
    """Raised when a reverse lookup receives an invalid IP address."""

    def __init__(self, value: object) -> None:
        self.value = value
        super().__init__("IP address must be a valid IPv4 or IPv6 address")


def _record_sort_key(record: PrefixRecord) -> tuple[object, ...]:
    return (
        -record.prefix.prefixlen,
        record.name.casefold(),
        record.name,
        str(record.prefix),
        record.id.casefold(),
        record.id,
        record.region.casefold(),
        record.region,
        record.system_service.casefold(),
        record.system_service,
        record.platform.casefold(),
        record.platform,
        record.change_number,
    )


def reverse_lookup(
    index: ServiceTagIndex,
    value: str,
) -> list[IpLookupItem]:
    """Return every indexed prefix containing an IPv4 or IPv6 address."""

    try:
        address = ip_address(value)
    except (TypeError, ValueError) as exc:
        raise InvalidIpAddressError(value) from exc

    version: IpVersion = 4 if isinstance(address, IPv4Address) else 6
    width = 32 if version == 4 else 128
    address_integer = int(address)
    matches: set[PrefixRecord] = set()

    for prefix_length in index.prefix_lengths_by_version[version]:
        host_bits = width - prefix_length
        network_integer = (address_integer >> host_bits) << host_bits
        matches.update(
            index.reverse_index.get(
                (version, prefix_length, network_integer),
                (),
            )
        )

    return [
        IpLookupItem(
            name=record.name,
            id=record.id,
            change_number=record.change_number,
            region=record.region,
            platform=record.platform,
            system_service=record.system_service,
            prefix=str(record.prefix),
        )
        for record in sorted(matches, key=_record_sort_key)
    ]

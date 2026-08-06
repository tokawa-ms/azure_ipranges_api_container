"""Search indexes and lookup operations for Azure Service Tags."""

from azure_ipranges_api.search.index import (
    InvalidAddressPrefixError,
    PrefixKey,
    PrefixRecord,
    ServiceTagIndex,
    ServiceTagRecord,
)
from azure_ipranges_api.search.reverse import (
    InvalidIpAddressError,
    reverse_lookup,
)

__all__ = [
    "InvalidAddressPrefixError",
    "InvalidIpAddressError",
    "PrefixKey",
    "PrefixRecord",
    "ServiceTagIndex",
    "ServiceTagRecord",
    "reverse_lookup",
]

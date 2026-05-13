"""Regression tests for audit-B10 — stricter mask + host validation in
write_tools.

Pre-fix, _validate_ipv4 accepted any 32-bit value as a mask, including
0.0.0.0 and non-contiguous "wildcard" masks. Both are rejected by IOS at
apply time and almost certainly a user error in an interface-IP context.
The new _validate_subnet_mask + _validate_interface_ip_and_mask catch
these before the snapshot+approval gate even runs.
"""

from __future__ import annotations

import pytest

from backend.cli_agent.write_tools import (
    _validate_interface_ip_and_mask,
    _validate_subnet_mask,
)

# ---------------------------------------------------------------------------
# _validate_subnet_mask — contiguous-mask check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mask",
    [
        "255.0.0.0",  # /8
        "255.255.0.0",  # /16
        "255.255.255.0",  # /24
        "255.255.255.252",  # /30
        "255.255.255.254",  # /31
        "255.255.255.255",  # /32
        "128.0.0.0",  # /1
    ],
)
def test_validate_subnet_mask_accepts_contiguous(mask):
    _validate_subnet_mask(mask)  # must not raise


@pytest.mark.parametrize(
    "mask",
    [
        "0.0.0.0",  # all-zeros — explicitly rejected
        "255.0.255.0",  # non-contiguous
        "255.255.0.255",  # non-contiguous
        "10.0.0.1",  # arbitrary IPv4 that isn't a mask
        "0.255.255.255",  # leading zero, trailing ones — bytes reversed
    ],
)
def test_validate_subnet_mask_rejects_bad_mask(mask):
    with pytest.raises(ValueError):
        _validate_subnet_mask(mask)


def test_validate_subnet_mask_rejects_malformed_ipv4():
    with pytest.raises(ValueError):
        _validate_subnet_mask("999.999.999.999")
    with pytest.raises(ValueError):
        _validate_subnet_mask("not-an-ip")


# ---------------------------------------------------------------------------
# _validate_interface_ip_and_mask — combined check (host address sanity)
# ---------------------------------------------------------------------------


def test_validate_interface_ip_and_mask_accepts_normal_host():
    _validate_interface_ip_and_mask("10.0.0.1", "255.255.255.0")


def test_validate_interface_ip_and_mask_rejects_network_address():
    """10.0.0.0/24 → 10.0.0.0 is the network, not a host."""
    with pytest.raises(ValueError, match="network"):
        _validate_interface_ip_and_mask("10.0.0.0", "255.255.255.0")


def test_validate_interface_ip_and_mask_rejects_broadcast_address():
    """10.0.0.0/24 → 10.0.0.255 is the broadcast, not a host."""
    with pytest.raises(ValueError, match="broadcast"):
        _validate_interface_ip_and_mask("10.0.0.255", "255.255.255.0")


def test_validate_interface_ip_and_mask_rejects_wildcard_ip():
    with pytest.raises(ValueError, match="wildcard|broadcast"):
        _validate_interface_ip_and_mask("0.0.0.0", "255.255.255.0")


def test_validate_interface_ip_and_mask_rejects_limited_broadcast_ip():
    with pytest.raises(ValueError, match="wildcard|broadcast"):
        _validate_interface_ip_and_mask("255.255.255.255", "255.255.255.0")


def test_validate_interface_ip_and_mask_rejects_non_contiguous_mask():
    with pytest.raises(ValueError, match="contiguous|mask"):
        _validate_interface_ip_and_mask("10.0.0.1", "255.0.255.0")


def test_validate_interface_ip_and_mask_allows_slash_31_endpoints():
    """A /31 point-to-point link has no network/broadcast address — both
    addresses are valid hosts. RFC 3021. Cisco supports it on routed
    interfaces, so the validator must not reject it."""
    # 10.0.0.0/31 → both 10.0.0.0 and 10.0.0.1 are host addresses
    _validate_interface_ip_and_mask("10.0.0.0", "255.255.255.254")
    _validate_interface_ip_and_mask("10.0.0.1", "255.255.255.254")


def test_validate_interface_ip_and_mask_allows_slash_32_loopback():
    """A /32 is the address itself — common on loopback interfaces."""
    _validate_interface_ip_and_mask("1.1.1.1", "255.255.255.255")

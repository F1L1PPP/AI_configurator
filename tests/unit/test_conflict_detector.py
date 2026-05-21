"""Unit tests for backend.orchestration.conflict_detector.find_existing_block.

12 test scenarios covering anchor selection, block extraction,
exact-match logic, and edge cases.
"""

from __future__ import annotations

from backend.orchestration.conflict_detector import find_existing_block

# ---------------------------------------------------------------------------
# Test 1 — trivial lines are skipped; real anchor found
# ---------------------------------------------------------------------------


def test_anchor_skips_trivial_lines() -> None:
    commands = ["", "exit", "end", "configure terminal", "vlan 30", " name OFFICE"]
    running_config = "vlan 30\n name OLD\n"
    result = find_existing_block(commands, running_config)
    assert result is not None
    assert result["anchor"] == "vlan 30"


# ---------------------------------------------------------------------------
# Test 2 — `no X` lines are skipped; first valid anchor is taken
# ---------------------------------------------------------------------------


def test_anchor_skips_no_prefix() -> None:
    commands = ["no router ospf 1", "router ospf 1", " network 10.0.0.0 0.0.0.255 area 0"]
    running_config = "router ospf 1\n network 10.0.0.0 0.0.0.255 area 0\n"
    result = find_existing_block(commands, running_config)
    assert result is not None
    assert result["anchor"] == "router ospf 1"


# ---------------------------------------------------------------------------
# Test 3 — physical interfaces are skipped → None
# ---------------------------------------------------------------------------


def test_physical_interface_returns_none() -> None:
    # GigabitEthernet
    cmds_gi = ["interface GigabitEthernet0/0/1", " ip address 1.1.1.1 255.255.255.0"]
    running_gi = "interface GigabitEthernet0/0/1\n description WAN\n no shutdown\n"
    assert find_existing_block(cmds_gi, running_gi) is None

    # FastEthernet
    cmds_fa = ["interface FastEthernet0", " duplex full"]
    running_fa = "interface FastEthernet0\n description LAN\n"
    assert find_existing_block(cmds_fa, running_fa) is None

    # TenGigabitEthernet
    cmds_te = ["interface TenGigabitEthernet1/0/1", " shutdown"]
    running_te = "interface TenGigabitEthernet1/0/1\n description UPLINK\n"
    assert find_existing_block(cmds_te, running_te) is None


# ---------------------------------------------------------------------------
# Test 4 — SVI block extracted in full
# ---------------------------------------------------------------------------


def test_vlan_interface_finds_block() -> None:
    commands = ["interface Vlan30", " ip address 10.30.0.1 255.255.255.0", " no shutdown"]
    running_config = (
        "!\n"
        "interface Vlan30\n"
        " ip address 10.30.0.254 255.255.255.0\n"
        " no shutdown\n"
        "!\n"
        "interface Vlan40\n"
    )
    result = find_existing_block(commands, running_config)
    assert result is not None
    assert result["anchor"] == "interface Vlan30"
    assert "ip address 10.30.0.254 255.255.255.0" in result["block"]
    assert "no shutdown" in result["block"]


# ---------------------------------------------------------------------------
# Test 5 — router ospf block with multiple network statements
# ---------------------------------------------------------------------------


def test_router_ospf_finds_block() -> None:
    commands = [
        "router ospf 1",
        " network 10.0.0.0 0.0.0.255 area 0",
        " network 192.168.1.0 0.0.0.255 area 1",
    ]
    running_config = (
        "!\n"
        "router ospf 1\n"
        " passive-interface default\n"
        " network 10.0.0.0 0.0.0.255 area 0\n"
        " network 192.168.1.0 0.0.0.255 area 1\n"
        "!\n"
        "ip route 0.0.0.0 0.0.0.0 10.0.0.1\n"
    )
    result = find_existing_block(commands, running_config)
    assert result is not None
    assert result["anchor"] == "router ospf 1"
    assert "passive-interface default" in result["block"]
    assert "network 10.0.0.0 0.0.0.255 area 0" in result["block"]
    assert "network 192.168.1.0 0.0.0.255 area 1" in result["block"]


# ---------------------------------------------------------------------------
# Test 6 — router bgp block; exit-address-family stays inside block
# ---------------------------------------------------------------------------


def test_router_bgp_finds_block() -> None:
    commands = ["router bgp 65000", " neighbor 10.0.0.2 remote-as 65001"]
    running_config = (
        "router bgp 65000\n"
        " bgp router-id 10.0.0.1\n"
        " neighbor 10.0.0.2 remote-as 65001\n"
        " !\n"
        " address-family ipv4\n"
        "  network 10.0.0.0 mask 255.255.255.0\n"
        " exit-address-family\n"
        "!\n"
        "hostname R1\n"
    )
    result = find_existing_block(commands, running_config)
    assert result is not None
    assert result["anchor"] == "router bgp 65000"
    # exit-address-family must be inside the returned block
    assert "exit-address-family" in result["block"]
    # Stops before 'hostname R1'
    assert "hostname R1" not in result["block"]


# ---------------------------------------------------------------------------
# Test 7 — route-map block
# ---------------------------------------------------------------------------


def test_route_map_finds_block() -> None:
    commands = [
        "route-map MARK_HQ permit 10",
        " match ip address prefix-list HQ_PREFIXES",
        " set local-preference 200",
    ]
    running_config = (
        "!\n"
        "route-map MARK_HQ permit 10\n"
        " match ip address prefix-list HQ_PREFIXES\n"
        " set local-preference 150\n"
        "!\n"
    )
    result = find_existing_block(commands, running_config)
    assert result is not None
    assert result["anchor"] == "route-map MARK_HQ permit 10"
    assert "set local-preference 150" in result["block"]


# ---------------------------------------------------------------------------
# Test 8 — global single-line: hostname
# ---------------------------------------------------------------------------


def test_global_hostname_finds_line() -> None:
    commands = ["hostname c1111-lab"]
    running_config = "!\nhostname c1111-lab\n!\nip domain name lab.local\n"
    result = find_existing_block(commands, running_config)
    assert result is not None
    assert result["block"] == "hostname c1111-lab"
    assert result["is_exact_match"] is True


# ---------------------------------------------------------------------------
# Test 9 — stanza with no indented body
# ---------------------------------------------------------------------------


def test_stanza_with_no_body() -> None:
    commands = ["router rip"]
    running_config = "router rip\n!\nip route 0.0.0.0 0.0.0.0 10.0.0.1\n"
    result = find_existing_block(commands, running_config)
    assert result is not None
    assert result["anchor"] == "router rip"
    # Block should be just the anchor line
    assert result["block"].strip() == "router rip"
    assert result["is_exact_match"] is True


# ---------------------------------------------------------------------------
# Test 10 — anchor not present in running-config → None
# ---------------------------------------------------------------------------


def test_anchor_not_in_running_config() -> None:
    commands = ["vlan 99", " name NEW"]
    running_config = "vlan 10\n name MGMT\n"
    assert find_existing_block(commands, running_config) is None


# ---------------------------------------------------------------------------
# Test 11 — empty running-config → None
# ---------------------------------------------------------------------------


def test_empty_running_config() -> None:
    commands = ["vlan 30", " name OFFICE"]
    assert find_existing_block(commands, "") is None


# ---------------------------------------------------------------------------
# Test 12 — is_exact_match logic (exact vs conflict)
# ---------------------------------------------------------------------------


def test_is_exact_match_logic() -> None:
    # Exact match
    commands = ["vlan 30", " name OFFICE"]
    running_exact = "vlan 30\n name OFFICE\n!\n"
    result_exact = find_existing_block(commands, running_exact)
    assert result_exact is not None
    assert result_exact["is_exact_match"] is True

    # Conflict — different name
    running_conflict = "vlan 30\n name OLD_NAME\n!\n"
    result_conflict = find_existing_block(commands, running_conflict)
    assert result_conflict is not None
    assert result_conflict["is_exact_match"] is False


# ---------------------------------------------------------------------------
# Bonus: anchor case-insensitive match (IOS shows lowercase in running-config)
# ---------------------------------------------------------------------------


def test_anchor_case_insensitive_match() -> None:
    commands = ["Vlan 30"]
    running_config = "vlan 30\n name X\n"
    result = find_existing_block(commands, running_config)
    assert result is not None
    assert result["anchor"] == "Vlan 30"

"""Unit tests for backend/webui_agent/atlas/fingerprint.py.

All tests use plain dicts — no Playwright, no router connection.

Slug contract (documented here for auditor verification):

    slugify("C1111-4P")     -> "c1111-4p"
    slugify("17.6.3a")      -> "17-6-3a"
    slugify("")             -> ""
    slugify("---")          -> ""

    device_fingerprint({"HARDWARE": ["C1111-4P"], "VERSION": "17.6.3a"})
        -> "c1111-4p__17-6-3a"

    route_slug("#/ospf")                    -> "ospf"
    route_slug("/webui/#/dhcp")             -> "dhcp"
    route_slug("https://r/webui/#/dhcp")    -> "dhcp"
    route_slug("#/dhcp/")                   -> "dhcp"
    route_slug("#/dhcp?pool=main")          -> "dhcp"
    route_slug("/general")                  -> "general"
    route_slug("")                          -> "root"
"""

from __future__ import annotations

from backend.webui_agent.atlas.fingerprint import (
    device_fingerprint,
    route_slug,
    slugify,
)

# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_lowercase(self):
        assert slugify("C1111-4P") == "c1111-4p"

    def test_dots_replaced(self):
        assert slugify("17.6.3a") == "17-6-3a"

    def test_empty_string(self):
        assert slugify("") == ""

    def test_all_separators(self):
        assert slugify("---") == ""

    def test_spaces_replaced(self):
        assert slugify("Cisco Router") == "cisco-router"

    def test_multiple_separators_collapsed(self):
        assert slugify("a--b..c") == "a-b-c"

    def test_leading_trailing_separators_stripped(self):
        assert slugify("-hello-") == "hello"

    def test_alphanumeric_only_unchanged(self):
        assert slugify("c1111") == "c1111"


# ---------------------------------------------------------------------------
# route_slug
# ---------------------------------------------------------------------------


class TestRouteSlug:
    def test_hash_ospf(self):
        assert route_slug("#/ospf") == "ospf"

    def test_path_and_hash_dhcp(self):
        assert route_slug("/webui/#/dhcp") == "dhcp"

    def test_full_url_hash(self):
        assert route_slug("https://r/webui/#/dhcp") == "dhcp"

    def test_trailing_slash_stripped(self):
        assert route_slug("#/dhcp/") == "dhcp"

    def test_query_string_stripped(self):
        assert route_slug("#/dhcp?pool=main") == "dhcp"

    def test_no_hash_plain_path(self):
        assert route_slug("/general") == "general"

    def test_empty_string_returns_root(self):
        assert route_slug("") == "root"

    def test_just_slash_returns_root(self):
        # "/" has no meaningful tail — falls to root.
        result = route_slug("/")
        assert result != ""  # must be non-empty
        # Acceptable values: "root" or the slug of "/" which is empty -> "root"
        assert result == "root"

    def test_result_is_always_nonempty(self):
        cases = [
            "#/ospf",
            "/webui/#/dhcp",
            "https://192.168.1.1/webui/#/general",
            "#/vlan/",
            "#/interface?id=Gi0%2F0",
        ]
        for route in cases:
            assert route_slug(route) != "", f"route_slug({route!r}) returned empty string"


# ---------------------------------------------------------------------------
# device_fingerprint
# ---------------------------------------------------------------------------


class TestDeviceFingerprint:
    def test_hardware_list_and_version(self):
        info = {"HARDWARE": ["C1111-4P"], "VERSION": "17.6.3a"}
        assert device_fingerprint(info) == "c1111-4p__17-6-3a"

    def test_hardware_string(self):
        info = {"HARDWARE": "C1111-4P", "VERSION": "17.6.3a"}
        assert device_fingerprint(info) == "c1111-4p__17-6-3a"

    def test_pid_fallback(self):
        info = {"PID": "C1111-8P", "VERSION": "17.3.5"}
        assert device_fingerprint(info) == "c1111-8p__17-3-5"

    def test_model_key_fallback(self):
        info = {"MODEL": "ISR4331", "version": "16.12.4"}
        assert device_fingerprint(info) == "isr4331__16-12-4"

    def test_lowercase_model_key(self):
        info = {"model": "ASR1001-X", "VERSION": "16.9.1"}
        assert device_fingerprint(info) == "asr1001-x__16-9-1"

    def test_lowercase_hardware_key(self):
        info = {"hardware": "C921-4P", "VERSION": "15.9.3M5"}
        assert device_fingerprint(info) == "c921-4p__15-9-3m5"

    def test_lowercase_version_key(self):
        info = {"HARDWARE": ["C1111-4P"], "version": "17.6.3a"}
        assert device_fingerprint(info) == "c1111-4p__17-6-3a"

    def test_os_version_key(self):
        info = {"HARDWARE": ["C1111-4P"], "os_version": "17.6.3a"}
        assert device_fingerprint(info) == "c1111-4p__17-6-3a"

    def test_running_image_fallback(self):
        info = {
            "HARDWARE": ["C1111-4P"],
            "RUNNING_IMAGE": "flash:c1111-universalk9.17.06.03a.SPA.bin",
        }
        result = device_fingerprint(info)
        assert result.startswith("c1111-4p__")
        # Version slug derived from image path must be non-empty and non-"unknown".
        version_part = result.split("__")[1]
        assert version_part != "unknown"
        assert version_part != ""

    def test_missing_model_returns_unknown(self):
        result = device_fingerprint({"VERSION": "17.6.3a"})
        assert result == "unknown__17-6-3a"

    def test_missing_version_returns_unknown(self):
        result = device_fingerprint({"HARDWARE": ["C1111-4P"]})
        assert result == "c1111-4p__unknown"

    def test_empty_dict_returns_unknown_unknown(self):
        assert device_fingerprint({}) == "unknown__unknown"

    def test_none_returns_unknown_unknown(self):
        assert device_fingerprint(None) == "unknown__unknown"

    def test_hardware_empty_list_falls_through_to_pid(self):
        info = {"HARDWARE": [], "PID": "C1111-4P", "VERSION": "17.6.3a"}
        assert device_fingerprint(info) == "c1111-4p__17-6-3a"

    def test_result_is_always_non_empty(self):
        # Pathological input — must always return a non-empty string.
        assert device_fingerprint(None) != ""
        assert device_fingerprint({}) != ""
        assert device_fingerprint({"unknown_key": "irrelevant"}) != ""

    def test_version_priority_version_before_os_version(self):
        # VERSION wins over os_version.
        info = {
            "HARDWARE": ["C1111-4P"],
            "VERSION": "17.6.3a",
            "os_version": "16.0.0",
        }
        assert device_fingerprint(info) == "c1111-4p__17-6-3a"

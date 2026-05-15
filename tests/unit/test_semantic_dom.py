"""Unit tests for backend/webui_agent/semantic_dom.py.

The walker is exercised with MagicMock Page + Locator stubs — no real
Chromium. The project convention (see tests/unit/test_webui_login.py
and tests/unit/test_webui_hostname_page.py) is to stub Locator methods
via .return_value / .side_effect rather than launch Playwright.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.webui_agent.semantic_dom import describe_page

pytestmark = pytest.mark.webui


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_locator(
    *,
    tag: str = "BUTTON",
    attrs: dict[str, str] | None = None,
    text: str = "",
    visible: bool = True,
    enabled: bool = True,
    bbox: dict[str, float] | None = None,
    input_value: str = "",
) -> MagicMock:
    """Return a MagicMock that quacks like a Playwright Locator.

    `attrs` keys are checked by get_attribute (e.g. "role", "aria-label",
    "placeholder", "type", "required"). Missing keys return None.
    `input_value` is returned by ``loc.input_value()`` — used by the value
    field for textbox/combobox roles.
    """
    attrs = attrs or {}
    loc = MagicMock()
    loc.is_visible.return_value = visible
    loc.is_enabled.return_value = enabled
    loc.bounding_box.return_value = bbox or {
        "x": 100.0,
        "y": 100.0,
        "width": 100.0,
        "height": 30.0,
    }
    loc.inner_text.return_value = text
    loc.input_value.return_value = input_value
    loc.get_attribute.side_effect = lambda name, **kw: attrs.get(name)
    loc.evaluate.return_value = tag
    return loc


def _make_page(
    *,
    url: str = "https://lab/",
    title: str = "Test",
    locators: list[MagicMock] | None = None,
    viewport: dict[str, int] | None = None,
) -> MagicMock:
    """Return a MagicMock that quacks like a Playwright Page."""
    page = MagicMock()
    page.url = url
    page.title.return_value = title
    page.viewport_size = viewport or {"width": 1400, "height": 900}
    union = MagicMock()
    union.all.return_value = locators or []
    page.locator.return_value = union
    return page


# ---------------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------------


def test_view_includes_url_and_title():
    page = _make_page(
        url="https://lab/webui/#/general",
        title="General",
        locators=[_make_locator(tag="BUTTON", text="Add")],
    )

    view, _ = describe_page(page)

    assert view["url"] == "https://lab/webui/#/general"
    assert view["title"] == "General"


def test_empty_page_returns_empty_arrays():
    page = _make_page(locators=[])

    view, locator_map = describe_page(page)

    assert view["elements"] == []
    assert view["modals"] == []
    assert view["errors"] == []
    assert locator_map == {}


def test_view_id_present_and_eight_hex_chars():
    page = _make_page(locators=[_make_locator(tag="BUTTON", text="Add")])

    view, _ = describe_page(page)

    view_id = view["view_id"]
    assert isinstance(view_id, str)
    assert len(view_id) == 8
    # 8-hex from uuid4().hex[:8] — accept any lowercase hex digit.
    assert all(c in "0123456789abcdef" for c in view_id)


def test_view_id_differs_between_calls():
    # Two describes of the same page must produce different view_ids so the
    # webui_act gate in Phase 4 can detect a stale planner reference.
    page = _make_page(locators=[_make_locator(tag="BUTTON", text="Add")])

    view1, _ = describe_page(page)
    view2, _ = describe_page(page)

    assert view1["view_id"] != view2["view_id"]


# ---------------------------------------------------------------------------
# Role classification
# ---------------------------------------------------------------------------


def test_button_classified_by_tag():
    btn = _make_locator(tag="BUTTON", text="Add")
    page = _make_page(locators=[btn])

    view, locator_map = describe_page(page)

    assert len(view["elements"]) == 1
    el = view["elements"][0]
    assert el["eid"] == "e_001"
    assert el["role"] == "button"
    assert el["name"] == "Add"
    assert el["enabled"] is True
    # bbox rounded to int (sub-pixel precision is dropped).
    assert el["bbox"] == [100, 100, 100, 30]
    # `visible` is intentionally NOT emitted — visibility is implicit
    # since hidden elements are filtered out before reaching the output.
    assert "visible" not in el
    assert locator_map["e_001"] is btn


def test_input_text_classified_as_textbox():
    inp = _make_locator(
        tag="INPUT",
        attrs={"type": "text", "placeholder": "Process ID"},
    )
    page = _make_page(locators=[inp])

    view, _ = describe_page(page)

    assert len(view["elements"]) == 1
    el = view["elements"][0]
    assert el["role"] == "textbox"
    assert el["name"] == "Process ID"


def test_select_classified_as_combobox():
    sel = _make_locator(
        tag="SELECT",
        attrs={"aria-label": "Router"},
        text="OSPF",
    )
    page = _make_page(locators=[sel])

    view, _ = describe_page(page)

    el = view["elements"][0]
    assert el["role"] == "combobox"
    assert el["name"] == "Router"


def test_explicit_role_attribute_wins_over_tag():
    div = _make_locator(
        tag="DIV",
        attrs={"role": "button"},
        text="Apply",
    )
    page = _make_page(locators=[div])

    view, _ = describe_page(page)

    assert view["elements"][0]["role"] == "button"


# ---------------------------------------------------------------------------
# Modals / errors split
# ---------------------------------------------------------------------------


def test_dialog_routes_to_modals_with_m_prefix_eid():
    dlg = _make_locator(
        tag="DIV",
        attrs={"role": "dialog"},
        text="Confirm change",
    )
    page = _make_page(locators=[dlg])

    view, locator_map = describe_page(page)

    assert view["elements"] == []
    assert len(view["modals"]) == 1
    assert view["modals"][0]["role"] == "dialog"
    assert view["modals"][0]["eid"] == "m_001"
    assert locator_map["m_001"] is dlg


def test_alertdialog_also_routes_to_modals():
    dlg = _make_locator(
        tag="DIV",
        attrs={"role": "alertdialog"},
        text="Unsaved changes",
    )
    page = _make_page(locators=[dlg])

    view, _ = describe_page(page)

    assert view["elements"] == []
    assert len(view["modals"]) == 1
    assert view["modals"][0]["role"] == "alertdialog"


def test_alert_routes_to_errors_without_eid():
    alert = _make_locator(
        tag="DIV",
        attrs={"role": "alert"},
        text="VLAN already exists",
    )
    page = _make_page(locators=[alert])

    view, locator_map = describe_page(page)

    assert view["elements"] == []
    assert view["modals"] == []
    assert view["errors"] == [{"name": "VLAN already exists"}]
    # Alerts are informational, not addressable — no eid is emitted.
    assert locator_map == {}


# ---------------------------------------------------------------------------
# Visibility + enabled scoring
# ---------------------------------------------------------------------------


def test_hidden_element_dropped_from_all_buckets():
    hidden = _make_locator(tag="BUTTON", text="Cancel", visible=False)
    page = _make_page(locators=[hidden])

    view, locator_map = describe_page(page)

    assert view["elements"] == []
    assert view["modals"] == []
    assert view["errors"] == []
    assert locator_map == {}


def test_enabled_element_outranks_disabled_at_same_position():
    enabled_btn = _make_locator(
        tag="BUTTON",
        text="Save",
        enabled=True,
        bbox={"x": 100.0, "y": 400.0, "width": 100.0, "height": 30.0},
    )
    disabled_btn = _make_locator(
        tag="BUTTON",
        text="Cancel",
        enabled=False,
        bbox={"x": 200.0, "y": 400.0, "width": 100.0, "height": 30.0},
    )
    # Input order intentionally reversed — enabled must still come first.
    page = _make_page(locators=[disabled_btn, enabled_btn])

    view, _ = describe_page(page)

    assert len(view["elements"]) == 2
    assert view["elements"][0]["name"] == "Save"
    assert view["elements"][0]["enabled"] is True
    assert view["elements"][1]["name"] == "Cancel"
    assert view["elements"][1]["enabled"] is False


# ---------------------------------------------------------------------------
# Cap
# ---------------------------------------------------------------------------


def test_thirty_element_cap_with_monotonic_eids():
    # 40 buttons; centrality varies by y. Top-30 by score get emitted.
    locators = [
        _make_locator(
            tag="BUTTON",
            text=f"Btn{i}",
            bbox={
                "x": 100.0,
                "y": 100.0 + i * 10.0,
                "width": 100.0,
                "height": 30.0,
            },
        )
        for i in range(40)
    ]
    page = _make_page(locators=locators)

    view, locator_map = describe_page(page)

    assert len(view["elements"]) == 30
    assert len(locator_map) == 30
    # eids are assigned in score-sorted order, so the sequence is monotonic.
    eids = [el["eid"] for el in view["elements"]]
    assert eids == [f"e_{i:03d}" for i in range(1, 31)]


def test_max_elements_override_respected():
    locators = [_make_locator(tag="BUTTON", text=f"Btn{i}") for i in range(5)]
    page = _make_page(locators=locators)

    view, _ = describe_page(page, max_elements=2)

    assert len(view["elements"]) == 2


# ---------------------------------------------------------------------------
# Name resolution fallback chain
# ---------------------------------------------------------------------------


def test_aria_label_beats_inner_text():
    btn = _make_locator(
        tag="BUTTON",
        attrs={"aria-label": "Save changes"},
        text="Save",
    )
    page = _make_page(locators=[btn])

    view, _ = describe_page(page)

    assert view["elements"][0]["name"] == "Save changes"


def test_placeholder_fallback_for_unlabelled_input():
    inp = _make_locator(
        tag="INPUT",
        attrs={"type": "text", "placeholder": "Search VLANs"},
        text="",
    )
    page = _make_page(locators=[inp])

    view, _ = describe_page(page)

    assert view["elements"][0]["name"] == "Search VLANs"


def test_name_truncated_at_fifty_chars():
    long = "x" * 200
    btn = _make_locator(
        tag="BUTTON",
        attrs={"aria-label": long},
    )
    page = _make_page(locators=[btn])

    view, _ = describe_page(page)

    name = view["elements"][0]["name"]
    assert len(name) == 50
    assert name == "x" * 50


def test_aria_labelledby_resolves_referenced_text():
    # Element points to another node via aria-labelledby. The walker should
    # look up that node by id and return its inner_text.
    btn = _make_locator(
        tag="BUTTON",
        attrs={"aria-labelledby": "lbl_001"},
        text="should be ignored",
    )
    # loc.page.locator("#lbl_001").inner_text(...) returns the referenced text.
    btn.page.locator.return_value.inner_text.return_value = "Confirm change"
    page = _make_page(locators=[btn])

    view, _ = describe_page(page)

    assert view["elements"][0]["name"] == "Confirm change"


def test_label_for_association_resolves_label_text():
    # Input has id="hostname_input", no aria-*, no inner_text, no placeholder.
    # A <label for="hostname_input"> elsewhere on the page provides the name.
    inp = _make_locator(
        tag="INPUT",
        attrs={"type": "text", "id": "hostname_input"},
        text="",
    )
    # loc.page.locator('label[for="hostname_input"]').inner_text(...) returns label text.
    inp.page.locator.return_value.inner_text.return_value = "Host Name *"
    page = _make_page(locators=[inp])

    view, _ = describe_page(page)

    assert view["elements"][0]["name"] == "Host Name *"


def test_title_fallback_when_no_other_label():
    # Input has title= but no aria-*, no inner_text, no placeholder, no id.
    inp = _make_locator(
        tag="INPUT",
        attrs={"type": "text", "title": "Tooltip Help Text"},
        text="",
    )
    page = _make_page(locators=[inp])

    view, _ = describe_page(page)

    assert view["elements"][0]["name"] == "Tooltip Help Text"


def test_name_attribute_fallback():
    # Input has name="switchName" but no aria-*, no inner_text, no placeholder,
    # no id, no title.
    inp = _make_locator(
        tag="INPUT",
        attrs={"type": "text", "name": "switchName"},
        text="",
    )
    page = _make_page(locators=[inp])

    view, _ = describe_page(page)

    assert view["elements"][0]["name"] == "switchName"


def test_id_fallback_uses_non_ng_id():
    # Input has id="hostname_field" but no aria-*, no inner_text, no placeholder,
    # no title, no name. The label[for] lookup returns a MagicMock (not a str),
    # so the isinstance check fails and falls through to the id fallback.
    inp = _make_locator(
        tag="INPUT",
        attrs={"type": "text", "id": "hostname_field"},
        text="",
    )
    # Default MagicMock return_value is a MagicMock, not a str — fails isinstance check.
    page = _make_page(locators=[inp])

    view, _ = describe_page(page)

    assert view["elements"][0]["name"] == "hostname_field"


def test_id_fallback_skips_ng_prefix():
    # Input has id="ng-12345" (Angular-autogenerated). The id fallback must
    # filter this out and return "".
    inp = _make_locator(
        tag="INPUT",
        attrs={"type": "text", "id": "ng-12345"},
        text="",
    )
    # Default MagicMock return_value for label lookup is not a str — falls through.
    page = _make_page(locators=[inp])

    view, _ = describe_page(page)

    assert view["elements"][0]["name"] == ""


# ---------------------------------------------------------------------------
# value / required for textbox + combobox
# ---------------------------------------------------------------------------


def test_textbox_emits_value_and_required():
    inp = _make_locator(
        tag="INPUT",
        attrs={"type": "text", "required": "", "placeholder": "VLAN ID"},
        input_value="42",
    )
    page = _make_page(locators=[inp])

    view, _ = describe_page(page)

    el = view["elements"][0]
    assert el["role"] == "textbox"
    assert el["value"] == "42"
    # HTML5 boolean attr — present even with empty value means required.
    assert el["required"] is True


def test_textbox_without_required_attribute():
    inp = _make_locator(
        tag="INPUT",
        attrs={"type": "text", "placeholder": "Optional field"},
        input_value="",
    )
    page = _make_page(locators=[inp])

    view, _ = describe_page(page)

    el = view["elements"][0]
    assert el["value"] == ""
    assert el["required"] is False


def test_combobox_emits_value_not_required():
    sel = _make_locator(
        tag="SELECT",
        attrs={"aria-label": "VLAN List"},
        input_value="VLAN46",
    )
    page = _make_page(locators=[sel])

    view, _ = describe_page(page)

    el = view["elements"][0]
    assert el["role"] == "combobox"
    assert el["value"] == "VLAN46"
    # `required` is a textbox-only concept — comboboxes don't carry it.
    assert "required" not in el


def test_button_does_not_emit_value_or_required():
    btn = _make_locator(tag="BUTTON", text="Apply")
    page = _make_page(locators=[btn])

    view, _ = describe_page(page)

    el = view["elements"][0]
    assert "value" not in el
    assert "required" not in el


# ---------------------------------------------------------------------------
# Token-budget sanity
# ---------------------------------------------------------------------------


def test_typical_view_fits_under_token_budget():
    # A realistic 30-element page; serialise and confirm the JSON length is
    # well inside the ~3.2 KB / ~800-token target.
    import json

    locators = [
        _make_locator(
            tag="BUTTON",
            text=f"Action {i}",
            bbox={
                "x": 100.0,
                "y": 200.0 + i * 5.0,
                "width": 80.0,
                "height": 28.0,
            },
        )
        for i in range(30)
    ]
    page = _make_page(locators=locators)

    view, _ = describe_page(page)

    serialised = json.dumps(view)
    # 4 chars per token rule of thumb -> ~800 tokens cap at 3 200 chars.
    assert len(serialised) < 3_200, f"view bloated to {len(serialised)} chars"


def test_worst_case_view_fits_under_realistic_budget():
    # Worst case: 30 textboxes with names truncated AT _MAX_NAME_LEN (50),
    # `value` + `required` populated. This is the page we'd see on a heavy
    # Cisco config form (e.g. interface settings, OSPF advanced).
    import json

    long_name = "x" * 50  # at _MAX_NAME_LEN
    locators = [
        _make_locator(
            tag="INPUT",
            attrs={"type": "text", "aria-label": long_name, "required": ""},
            input_value=f"val{i}",
            bbox={
                "x": 100.0,
                "y": 200.0 + i * 5.0,
                "width": 100.0,
                "height": 28.0,
            },
        )
        for i in range(30)
    ]
    page = _make_page(locators=locators)

    view, _ = describe_page(page)

    serialised = json.dumps(view)
    # Worst-case target: ~1400 tokens (~5600 chars at 4 chars/token).
    # 30 max-length textboxes each carrying value + required is pathological;
    # real Cisco pages are 5-15 elements with sub-30-char labels. Even this
    # pathological case is still <$0.001 per call at Haiku 4.5 pricing.
    assert len(serialised) < 5_600, f"worst-case view bloated to {len(serialised)} chars"

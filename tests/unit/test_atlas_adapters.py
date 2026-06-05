"""Unit tests for backend/webui_agent/atlas/adapters.py.

All tests use MagicMock Page/Locator stubs — no real Chromium.
Mirror style: test_semantic_dom.py / test_atlas_capture.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from backend.webui_agent.atlas.adapters import (
    ADAPTERS,
    CLICK_TIMEOUT_MS,
    FORM_TIMEOUT_MS,
    ButtonAdapter,
    CheckboxAdapter,
    InputAdapter,
    KendoComboboxAdapter,
    KendoGridAdapter,
    KendoNumericAdapter,
    LocatorResolutionError,
    RadioAdapter,
    get_adapter,
    locate,
    resolve_locator,
)
from backend.webui_agent.atlas.schema import FieldSpec, LocatorSpec

pytestmark = pytest.mark.webui


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_locspec(
    strategy: str = "css",
    value: str = "input[name='x']",
    role: str | None = None,
    name: str | None = None,
    fallbacks: list[LocatorSpec] | None = None,
) -> LocatorSpec:
    return LocatorSpec(
        strategy=strategy,
        role=role,
        name=name,
        value=value,
        fallbacks=fallbacks or [],
    )


def _make_field(
    key: str = "my_field",
    widget: str = "input",
    locspec: LocatorSpec | None = None,
    kendo_select_name: str | None = None,
) -> FieldSpec:
    if locspec is None:
        locspec = _make_locspec()
    return FieldSpec(
        key=key,
        label=key,
        role="textbox",
        widget=widget,
        locator=locspec,
        kendo_select_name=kendo_select_name,
    )


def _mock_loc(count: int = 1) -> MagicMock:
    """Return a MagicMock locator whose .count() returns *count*."""
    loc = MagicMock()
    loc.count.return_value = count
    return loc


def _mock_page(loc: MagicMock | None = None) -> MagicMock:
    """Return a MagicMock page whose .locator() returns *loc*."""
    page = MagicMock()
    if loc is not None:
        page.locator.return_value = loc
        page.get_by_role.return_value = loc
    return page


# ---------------------------------------------------------------------------
# resolve_locator
# ---------------------------------------------------------------------------


class TestResolveLocator:
    def test_css_strategy(self) -> None:
        page = MagicMock()
        fake_loc = MagicMock()
        page.locator.return_value = fake_loc

        locspec = _make_locspec(strategy="css", value="input[name='foo']")
        result = resolve_locator(page, locspec)

        page.locator.assert_called_once_with("input[name='foo']")
        assert result is fake_loc

    def test_get_by_role_strategy(self) -> None:
        page = MagicMock()
        fake_loc = MagicMock()
        page.get_by_role.return_value = fake_loc

        locspec = _make_locspec(strategy="get_by_role", role="textbox", name="IP Address")
        result = resolve_locator(page, locspec)

        page.get_by_role.assert_called_once_with("textbox", name="IP Address", exact=True)
        assert result is fake_loc

    def test_role_loose_strategy(self) -> None:
        page = MagicMock()
        fake_loc = MagicMock()
        page.get_by_role.return_value = fake_loc

        locspec = _make_locspec(strategy="role_loose", role="button", name="Apply to Device")
        result = resolve_locator(page, locspec)

        page.get_by_role.assert_called_once_with("button", name="Apply to Device", exact=False)
        assert result is fake_loc

    def test_ng_model_strategy(self) -> None:
        page = MagicMock()
        fake_loc = MagicMock()
        page.locator.return_value = fake_loc

        locspec = _make_locspec(strategy="ng_model", value="ctrl.ipAddress")
        result = resolve_locator(page, locspec)

        page.locator.assert_called_once_with("[ng-model='ctrl.ipAddress']")
        assert result is fake_loc

    def test_name_strategy(self) -> None:
        page = MagicMock()
        fake_loc = MagicMock()
        page.locator.return_value = fake_loc

        locspec = _make_locspec(strategy="name", value="ipAddress")
        result = resolve_locator(page, locspec)

        page.locator.assert_called_once_with("[name='ipAddress']")
        assert result is fake_loc


# ---------------------------------------------------------------------------
# locate
# ---------------------------------------------------------------------------


class TestLocate:
    def test_primary_found_returns_it(self) -> None:
        primary_loc = _mock_loc(count=1)
        page = MagicMock()
        page.get_by_role.return_value = primary_loc

        locspec = _make_locspec(strategy="get_by_role", role="textbox", name="Host")
        field = _make_field(locspec=locspec)

        result = locate(page, field)
        assert result is primary_loc

    def test_multi_match_returns_first_visible(self) -> None:
        """Cisco duplicates field names across Basic/Advanced sections — a
        [name='X'] locator can match several elements (live OSPF form: 4x
        name='processID'). locate must return the first VISIBLE match so fill()
        doesn't hit a strict-mode violation (the unknown_error from the smoke)."""
        loc = MagicMock()
        loc.count.return_value = 3
        nth0, nth1, nth2 = MagicMock(), MagicMock(), MagicMock()
        nth0.is_visible.return_value = False  # hidden Advanced copy
        nth1.is_visible.return_value = True  # the active Basic field
        nth2.is_visible.return_value = True
        loc.nth.side_effect = [nth0, nth1, nth2]
        page = MagicMock()
        page.locator.return_value = loc

        field = _make_field(
            locspec=_make_locspec(strategy="css", value="[name='processID']")
        )
        result = locate(page, field)
        assert result is nth1  # first visible match

    def test_multi_match_none_visible_falls_back_to_first(self) -> None:
        loc = MagicMock()
        loc.count.return_value = 2
        n0, n1 = MagicMock(), MagicMock()
        n0.is_visible.return_value = False
        n1.is_visible.return_value = False
        loc.nth.side_effect = [n0, n1]
        page = MagicMock()
        page.locator.return_value = loc

        field = _make_field(locspec=_make_locspec(strategy="css", value="[name='x']"))
        result = locate(page, field)
        assert result is loc.first

    def test_primary_zero_uses_fallback(self) -> None:
        primary_loc = _mock_loc(count=0)
        fallback_loc = _mock_loc(count=1)

        page = MagicMock()
        page.get_by_role.return_value = primary_loc
        page.locator.return_value = fallback_loc

        locspec = _make_locspec(
            strategy="get_by_role",
            role="textbox",
            name="Host",
            fallbacks=[_make_locspec(strategy="css", value="input[name='host']")],
        )
        field = _make_field(locspec=locspec)

        result = locate(page, field)
        assert result is fallback_loc

    def test_all_zero_raises_locator_resolution_error(self) -> None:
        zero_loc = _mock_loc(count=0)
        page = MagicMock()
        page.get_by_role.return_value = zero_loc
        page.locator.return_value = zero_loc

        locspec = _make_locspec(
            strategy="get_by_role",
            role="textbox",
            name="Missing",
            fallbacks=[_make_locspec(strategy="css", value="input.missing")],
        )
        field = _make_field(locspec=locspec)

        with pytest.raises(LocatorResolutionError):
            locate(page, field)

    def test_no_locator_raises_locator_resolution_error(self) -> None:
        page = MagicMock()
        field = FieldSpec(key="x", label="x", role="textbox", widget="input", locator=None)

        with pytest.raises(LocatorResolutionError):
            locate(page, field)

    def test_malformed_primary_falls_through_to_working_fallback(self) -> None:
        primary_loc = MagicMock()
        primary_loc.count.side_effect = Exception("bad selector")

        fallback_loc = _mock_loc(count=1)

        page = MagicMock()
        page.get_by_role.return_value = primary_loc
        page.locator.return_value = fallback_loc

        locspec = _make_locspec(
            strategy="get_by_role",
            role="textbox",
            name="Host",
            fallbacks=[_make_locspec(strategy="css", value="input[name='host']")],
        )
        field = _make_field(locspec=locspec)

        result = locate(page, field)
        assert result is fallback_loc


# ---------------------------------------------------------------------------
# InputAdapter
# ---------------------------------------------------------------------------


class TestInputAdapter:
    def test_apply_calls_fill(self) -> None:
        adapter = InputAdapter()
        loc = _mock_loc(count=1)
        page = MagicMock()
        page.get_by_role.return_value = loc

        field = _make_field(
            locspec=_make_locspec(strategy="get_by_role", role="textbox", name="IP")
        )
        adapter.apply(page, field, "10.0.0.1")

        loc.fill.assert_called_once_with("10.0.0.1", timeout=FORM_TIMEOUT_MS)

    def test_read_back_returns_input_value(self) -> None:
        adapter = InputAdapter()
        loc = _mock_loc(count=1)
        loc.input_value.return_value = "10.0.0.1"
        page = MagicMock()
        page.get_by_role.return_value = loc

        field = _make_field(
            locspec=_make_locspec(strategy="get_by_role", role="textbox", name="IP")
        )
        result = adapter.read_back(page, field)

        loc.input_value.assert_called_once_with(timeout=FORM_TIMEOUT_MS)
        assert result == "10.0.0.1"

    def test_input_adapter_read_back_asserts_equality(self) -> None:
        """set→read_back round-trip equals the set value (via mock)."""
        adapter = InputAdapter()
        loc = _mock_loc(count=1)
        page = MagicMock()
        page.get_by_role.return_value = loc

        field = _make_field(
            locspec=_make_locspec(strategy="get_by_role", role="textbox", name="IP")
        )

        # Simulate: after apply("192.168.1.1"), input_value returns same
        set_value = "192.168.1.1"
        loc.input_value.return_value = set_value

        adapter.apply(page, field, set_value)
        read = adapter.read_back(page, field)

        assert read == set_value


# ---------------------------------------------------------------------------
# KendoNumericAdapter
# ---------------------------------------------------------------------------


class TestKendoNumericAdapter:
    def test_apply_calls_fill(self) -> None:
        adapter = KendoNumericAdapter()
        loc = _mock_loc(count=1)
        page = MagicMock()
        page.locator.return_value = loc

        field = _make_field(widget="kendo_numeric")
        adapter.apply(page, field, "42")

        loc.fill.assert_called_once_with("42", timeout=FORM_TIMEOUT_MS)

    def test_read_back_returns_input_value(self) -> None:
        adapter = KendoNumericAdapter()
        loc = _mock_loc(count=1)
        loc.input_value.return_value = "42"
        page = MagicMock()
        page.locator.return_value = loc

        field = _make_field(widget="kendo_numeric")
        result = adapter.read_back(page, field)
        assert result == "42"


# ---------------------------------------------------------------------------
# CheckboxAdapter
# ---------------------------------------------------------------------------


class TestCheckboxAdapter:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (True, True),
            ("true", True),
            ("1", True),
            ("yes", True),
            ("on", True),
            (1, True),
            (False, False),
            ("false", False),
            ("0", False),
            ("no", False),
            ("", False),
            (None, False),
        ],
    )
    def test_apply_truthy_falsey(self, value: object, expected: bool) -> None:
        adapter = CheckboxAdapter()
        loc = _mock_loc(count=1)
        page = MagicMock()
        page.locator.return_value = loc

        field = _make_field(widget="checkbox")
        adapter.apply(page, field, value)

        loc.set_checked.assert_called_once_with(expected, timeout=FORM_TIMEOUT_MS)

    def test_read_back_returns_is_checked(self) -> None:
        adapter = CheckboxAdapter()
        loc = _mock_loc(count=1)
        loc.is_checked.return_value = True
        page = MagicMock()
        page.locator.return_value = loc

        field = _make_field(widget="checkbox")
        result = adapter.read_back(page, field)

        loc.is_checked.assert_called_once_with(timeout=FORM_TIMEOUT_MS)
        assert result is True


# ---------------------------------------------------------------------------
# RadioAdapter
# ---------------------------------------------------------------------------


class TestRadioAdapter:
    def test_apply_calls_check(self) -> None:
        adapter = RadioAdapter()
        loc = _mock_loc(count=1)
        page = MagicMock()
        page.locator.return_value = loc

        field = _make_field(widget="radio")
        adapter.apply(page, field, "any_value")

        loc.check.assert_called_once_with(timeout=FORM_TIMEOUT_MS)

    def test_read_back_returns_is_checked(self) -> None:
        adapter = RadioAdapter()
        loc = _mock_loc(count=1)
        loc.is_checked.return_value = False
        page = MagicMock()
        page.locator.return_value = loc

        field = _make_field(widget="radio")
        result = adapter.read_back(page, field)
        assert result is False


# ---------------------------------------------------------------------------
# ButtonAdapter
# ---------------------------------------------------------------------------


class TestButtonAdapter:
    def test_apply_calls_click_with_click_timeout(self) -> None:
        adapter = ButtonAdapter()
        loc = _mock_loc(count=1)
        page = MagicMock()
        page.get_by_role.return_value = loc

        field = _make_field(
            widget="button",
            locspec=_make_locspec(strategy="get_by_role", role="button", name="Apply"),
        )
        adapter.apply(page, field, "ignored value")

        loc.click.assert_called_once_with(timeout=CLICK_TIMEOUT_MS)

    def test_apply_value_ignored(self) -> None:
        """Any value (even None) must not affect click call."""
        adapter = ButtonAdapter()
        loc = _mock_loc(count=1)
        page = MagicMock()
        page.locator.return_value = loc

        field = _make_field(widget="button")
        adapter.apply(page, field, None)

        loc.click.assert_called_once_with(timeout=CLICK_TIMEOUT_MS)

    def test_read_back_returns_none(self) -> None:
        adapter = ButtonAdapter()
        page = MagicMock()
        field = _make_field(widget="button")
        assert adapter.read_back(page, field) is None


# ---------------------------------------------------------------------------
# KendoComboboxAdapter
# ---------------------------------------------------------------------------


class TestKendoComboboxAdapter:
    def _make_kendo_field(self, kendo_select_name: str | None = None) -> FieldSpec:
        locspec = _make_locspec(
            strategy="get_by_role",
            role="listbox",
            name="Subnet Mask",
        )
        return _make_field(
            key="subnet",
            widget="kendo_combobox",
            locspec=locspec,
            kendo_select_name=kendo_select_name,
        )

    def _make_kendo_page(self, loc: MagicMock) -> MagicMock:
        page = MagicMock()
        page.get_by_role.return_value = loc
        page.locator.return_value = loc
        return page

    # --- Strategy 1 success ---

    def test_kendo_combobox_widget_api_success(self) -> None:
        """Strategy 1 succeeds → no further calls, returns cleanly."""
        adapter = KendoComboboxAdapter()
        loc = _mock_loc(count=1)
        loc.evaluate.return_value = {"ok": True, "selected": "255.255.255.0"}
        loc.get_attribute.return_value = None  # aria-expanded not checked when s1 succeeds

        page = self._make_kendo_page(loc)
        field = self._make_kendo_field()

        # Should complete without error
        adapter.apply(page, field, "255.255.255.0")

        # evaluate was called once (strategy 1 JS)
        assert loc.evaluate.call_count == 1
        # click and set_checked must NOT have been called
        loc.click.assert_not_called()

    # --- Strategy 2: aria-controls scoping ---

    def test_kendo_combobox_opens_then_clicks_li_by_aria_controls(self) -> None:
        """Strategy 1 non-ok → strategy 2 opens widget and clicks scoped li."""
        adapter = KendoComboboxAdapter()

        loc = _mock_loc(count=1)
        loc.evaluate.return_value = {"ok": False, "reason": "kendo_unavailable"}

        # aria-expanded = not "true" → click to open
        # aria-controls = "subnet_listbox"
        def _get_attr(attr_name: str, **kw: object) -> str | None:
            if attr_name == "aria-expanded":
                return "false"
            if attr_name == "aria-controls":
                return "subnet_listbox"
            return None

        loc.get_attribute.side_effect = _get_attr

        # The scoped listbox locator
        listbox_loc = MagicMock()
        li_loc = MagicMock()
        # Now we call .first on the locator before .click — chain the mock accordingly.
        li_first = MagicMock()
        li_loc.first = li_first
        listbox_loc.locator.return_value = li_loc

        page = MagicMock()
        page.get_by_role.return_value = loc

        # page.locator("#subnet_listbox") → listbox_loc
        def _page_locator(selector: str, **kw: object) -> MagicMock:
            if selector == "#subnet_listbox":
                return listbox_loc
            return MagicMock()

        page.locator.side_effect = _page_locator

        field = self._make_kendo_field()
        adapter.apply(page, field, "255.255.255.0")

        # Widget was clicked to open
        loc.click.assert_called_once_with(timeout=FORM_TIMEOUT_MS)

        # Scoped listbox was used
        page.locator.assert_any_call("#subnet_listbox")

        # li.k-item with has_text was located; .first.click() was called
        listbox_loc.locator.assert_called_once_with("li.k-item", has_text="255.255.255.0")
        li_first.click.assert_called_once_with(timeout=FORM_TIMEOUT_MS)

    # --- PlaywrightTimeoutError propagates ---

    def test_kendo_combobox_timeout_bubbles_not_swallowed(self) -> None:
        """Strategy-2 timeout PROPAGATES — must not be swallowed into ValueError."""
        adapter = KendoComboboxAdapter()

        loc = _mock_loc(count=1)
        loc.evaluate.return_value = {"ok": False, "reason": "kendo_unavailable"}
        loc.get_attribute.return_value = None  # aria-expanded/aria-controls absent

        # body-wide li.k-item click raises PlaywrightTimeoutError via .first.click()
        li_loc = MagicMock()
        li_first = MagicMock()
        li_first.click.side_effect = PlaywrightTimeoutError("timed out waiting for li.k-item")
        li_loc.first = li_first

        page = MagicMock()
        page.get_by_role.return_value = loc
        page.locator.return_value = li_loc  # body-wide fallback locator chain

        field = self._make_kendo_field()

        with pytest.raises(PlaywrightTimeoutError):
            adapter.apply(page, field, "255.255.255.0")

    # --- Strategy 3 ValueError on value-not-in-options ---

    def test_kendo_combobox_value_not_in_options_raises_valueerror(self) -> None:
        """All 3 strategies exhaust → ValueError raised."""
        adapter = KendoComboboxAdapter()

        loc = _mock_loc(count=1)

        call_count: list[int] = [0]

        def _evaluate(js: str, val: str) -> dict:
            call_count[0] += 1
            if call_count[0] == 1:
                # Strategy 1
                return {"ok": False, "reason": "kendo_unavailable"}
            # Strategy 3
            return {"ok": False, "error": "value not in options. available: A, B"}

        loc.evaluate.side_effect = _evaluate

        # Strategy 2 fails with a structural error (not a timeout) so it
        # falls through to strategy 3.
        loc.get_attribute.return_value = None  # aria-expanded/aria-controls absent

        # The body-wide locator chain for strategy 2 raises a non-timeout error via .first.click().
        li_loc = MagicMock()
        li_first = MagicMock()
        li_first.click.side_effect = Exception("element detached from DOM")
        li_loc.first = li_first

        page = MagicMock()
        page.get_by_role.return_value = loc
        page.locator.return_value = li_loc

        field = self._make_kendo_field()

        with pytest.raises(ValueError, match="value not in options"):
            adapter.apply(page, field, "BOGUS")

    # --- read_back via kendo_select_name ---

    def test_read_back_via_kendo_select_name(self) -> None:
        """read_back uses the backing select when kendo_select_name is set."""
        adapter = KendoComboboxAdapter()

        backing_loc = MagicMock()
        backing_loc.input_value.return_value = "255.255.0.0"

        page = MagicMock()
        page.locator.return_value = backing_loc

        field = self._make_kendo_field(kendo_select_name="subnetMask")
        result = adapter.read_back(page, field)

        page.locator.assert_called_once_with("select[name='subnetMask']")
        backing_loc.input_value.assert_called_once_with(timeout=FORM_TIMEOUT_MS)
        assert result == "255.255.0.0"

    def test_read_back_no_kendo_select_name_uses_inner_text(self) -> None:
        """read_back falls back to inner_text when kendo_select_name is absent."""
        adapter = KendoComboboxAdapter()

        loc = _mock_loc(count=1)
        loc.inner_text.return_value = "255.255.255.0"

        page = MagicMock()
        page.get_by_role.return_value = loc

        field = self._make_kendo_field(kendo_select_name=None)
        result = adapter.read_back(page, field)

        loc.inner_text.assert_called_once()
        assert result == "255.255.255.0"


# ---------------------------------------------------------------------------
# KendoGridAdapter
# ---------------------------------------------------------------------------


class TestKendoGridAdapter:
    def test_apply_raises_not_implemented(self) -> None:
        adapter = KendoGridAdapter()
        page = MagicMock()
        field = _make_field(widget="kendo_grid")

        with pytest.raises(NotImplementedError, match="kendo_grid apply"):
            adapter.apply(page, field, "anything")

    def test_read_back_returns_none(self) -> None:
        adapter = KendoGridAdapter()
        page = MagicMock()
        field = _make_field(widget="kendo_grid")
        assert adapter.read_back(page, field) is None


# ---------------------------------------------------------------------------
# get_adapter
# ---------------------------------------------------------------------------


class TestGetAdapter:
    @pytest.mark.parametrize(
        "widget,expected_cls",
        [
            ("input", InputAdapter),
            ("kendo_numeric", KendoNumericAdapter),
            ("checkbox", CheckboxAdapter),
            ("radio", RadioAdapter),
            ("button", ButtonAdapter),
            ("kendo_combobox", KendoComboboxAdapter),
            ("kendo_grid", KendoGridAdapter),
        ],
    )
    def test_known_widgets_return_correct_adapter(
        self, widget: str, expected_cls: type
    ) -> None:
        adapter = get_adapter(widget)
        assert isinstance(adapter, expected_cls)

    def test_unknown_widget_returns_input_adapter(self) -> None:
        adapter = get_adapter("totally_unknown_widget_xyz")
        assert isinstance(adapter, InputAdapter)

    def test_adapters_dict_has_all_expected_keys(self) -> None:
        expected = {
            "input",
            "kendo_numeric",
            "checkbox",
            "radio",
            "button",
            "kendo_combobox",
            "kendo_grid",
        }
        assert set(ADAPTERS.keys()) == expected

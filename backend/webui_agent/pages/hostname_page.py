"""Page Object Model for Administration → Device Properties → Hostname.

Pure presentation-layer wrapper around the Playwright Page. Knows the
navigation path, the form fields, and the apply button — but nothing about
approvals, snapshots, or higher-level flow. Composed by `flows/change_hostname.py`.

Selectors come from `selectors/iosxe_default.yaml`; the POM does not hardcode
any locator. If the yaml changes, the POM keeps working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.core.logging import get_logger
from backend.webui_agent.browser import wait_for_networkidle
from backend.webui_agent.login import first_match
from backend.webui_agent.selectors import load_selectors

if TYPE_CHECKING:
    from playwright.sync_api import Page

log = get_logger(__name__)

# How long to let AngularJS render the sidebar after the post-login redirect.
# Empirically 5 s is plenty on a healthy C1111 — the inspector script proved
# this. Bumping it costs latency but doesn't add value unless the device is
# under heavy load.
DASHBOARD_SETTLE_MS = 5_000


class HostnameNavigationError(RuntimeError):
    """Raised when the page can't navigate to the hostname form (menu item missing)."""


class HostnameFieldNotFound(RuntimeError):
    """Raised when the hostname input element can't be located on the form."""


class HostnamePage:
    """Drive the Hostname section of the IOS XE WebUI.

    Usage (called by `flows/change_hostname.py`):

        hp = HostnamePage(page)
        hp.goto()
        old = hp.get_current_hostname()
        hp.set_hostname("LAB-R1")
        hp.apply()
    """

    def __init__(self, page: Page, selectors_map: str = "iosxe_default") -> None:
        self.page = page
        self._sel = load_selectors(selectors_map)

    # ---------------------------------------------------------------------
    # Navigation
    # ---------------------------------------------------------------------

    def goto(self) -> None:
        """Click Administration → Device Properties from any post-login page.

        AngularJS renders the sidebar AFTER the post-login redirect resolves,
        so we hard-wait DASHBOARD_SETTLE_MS for the menu to materialise. This
        matches the timing pattern that script 07_inspect_dashboard.py proved
        works — `wait_for(state="visible")` was less reliable because some
        menu items render as hidden parents with visible children.
        """
        log.info("hostname_page_goto_start")

        # Hard wait for the AngularJS sidebar to render — same wait that
        # 07_inspect_dashboard.py uses successfully.
        self.page.wait_for_timeout(DASHBOARD_SETTLE_MS)

        admin = first_match(self.page, self._sel["nav"]["administration"])
        if admin is None:
            raise HostnameNavigationError(
                "Administration menu not visible — priv-15 user required, "
                "or selectors out of date (run docs/codegen-howto.md)"
            )
        admin.click()
        wait_for_networkidle(self.page, 10_000)
        # Sub-menu pops on click but Angular may still be settling
        self.page.wait_for_timeout(1_500)

        dp = first_match(self.page, self._sel["hostname_nav"]["device_properties"])
        if dp is None:
            raise HostnameNavigationError("Device Properties submenu not visible")
        dp.click()
        wait_for_networkidle(self.page, 10_000)
        self.page.wait_for_timeout(1_500)

        log.info("hostname_page_goto_complete", url=self.page.url)

    # ---------------------------------------------------------------------
    # Form interactions
    # ---------------------------------------------------------------------

    def get_current_hostname(self) -> str:
        """Read the existing hostname from the form field."""
        loc = self._hostname_input_or_raise()
        value = loc.input_value()
        log.info("hostname_page_read", current=value)
        return value

    def set_hostname(self, new_name: str) -> None:
        """Clear the hostname field and type the new value.

        Uses triple_click + fill so we replace any existing value cleanly
        (Cisco's Angular forms don't always clear on .fill alone).
        """
        loc = self._hostname_input_or_raise()
        loc.triple_click()
        loc.fill(new_name)
        log.info("hostname_page_filled", new_name=new_name)

    def apply(self) -> None:
        """Click the Apply/Save button. The form posts, the WebUI returns to
        a success state — networkidle is suppressed because Angular keeps
        polling indefinitely."""
        btn = first_match(self.page, self._sel["hostname_form"]["apply_button"])
        if btn is None:
            raise HostnameFieldNotFound("Apply button not visible on hostname form")
        btn.click()
        wait_for_networkidle(self.page, 15_000)
        log.info("hostname_page_apply_clicked")

    # ---------------------------------------------------------------------
    # Internals
    # ---------------------------------------------------------------------

    def _hostname_input_or_raise(self):
        loc = first_match(self.page, self._sel["hostname_form"]["hostname_input"])
        if loc is None:
            raise HostnameFieldNotFound(
                "Hostname input not visible — check selectors/iosxe_default.yaml"
            )
        return loc

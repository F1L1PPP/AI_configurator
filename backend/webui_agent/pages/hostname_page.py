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

# Direct hash route for the hostname form on IOS XE 17.6.3a — captured by
# manual inspection (Administration → Device → General lands at #/general).
# Bypassing the sidebar makes the flow robust to the sidebar's flaky
# rendering under Playwright.
HOSTNAME_ROUTE = "/webui/#/general"
FORM_SETTLE_MS = 3_000


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
        """Navigate directly to the hostname form via hash route.

        Skips the sidebar (Administration → Device → General) and goes
        straight to /webui/#/general. The sidebar has been rendering
        unreliably under Playwright; the hash route works regardless.
        """
        log.info("hostname_page_goto_start", url=self.page.url)

        # Reconstruct the base URL from whatever route we're currently on
        parts = self.page.url.split("/webui/")
        base = parts[0] if parts else self.page.url.rstrip("/")
        target_url = f"{base}{HOSTNAME_ROUTE}"
        log.info("hostname_page_direct_nav", target=target_url)

        self.page.goto(target_url, wait_until="domcontentloaded", timeout=20_000)
        wait_for_networkidle(self.page, 10_000)
        # AngularJS still resolving + form rendering
        self.page.wait_for_timeout(FORM_SETTLE_MS)

        # Verify we actually landed on the form by probing for the input
        loc = first_match(self.page, self._sel["hostname_form"]["hostname_input"])
        if loc is None:
            self._dump_diagnostics("hostname-form-missing")
            raise HostnameNavigationError(
                f"Direct nav to {HOSTNAME_ROUTE} did not render the hostname "
                "form (input field not found). See structlog probe_count "
                "entries above for what the page actually has."
            )

        log.info("hostname_page_goto_complete", url=self.page.url)

    def _dump_diagnostics(self, label: str) -> None:
        """Log body text excerpt + count probes for the failing page."""
        try:
            body = self.page.locator("body").inner_text()[:1500]
        except Exception as exc:
            body = f"(failed to read body: {exc})"
        log.warning(
            "page_body_excerpt",
            label=label,
            url=self.page.url,
            text=body.replace("\n", " | "),
        )
        # Probe the candidates that should appear on the hostname form page
        for probe in (
            "input[type='text']",
            "text=Host Name",
            "text=Hostname",
            "label:has-text('Host Name')",
            "input[name='hostname']",
            "input[name='hostName']",
            "button:has-text('Apply')",
            "text=Administration",  # leftover from sidebar diagnostic
            "text=Device",
        ):
            try:
                cnt = self.page.locator(probe).count()
            except Exception as exc:
                cnt = f"ERR:{exc}"
            log.warning("probe_count", probe=probe, count=cnt)

    def _resolve_or_diagnose(self, label: str, strategies: list[dict]):
        """Walk strategies; log each one's match count for visibility."""
        for i, strat in enumerate(strategies):
            try:
                if "role" in strat:
                    name = strat.get("name")
                    loc = (
                        self.page.get_by_role(strat["role"], name=name)
                        if name
                        else self.page.get_by_role(strat["role"])
                    )
                    repr_ = f"role={strat['role']!r} name={name!r}"
                elif "label" in strat:
                    loc = self.page.get_by_label(strat["label"], exact=False)
                    repr_ = f"label={strat['label']!r}"
                elif "text" in strat:
                    loc = self.page.locator(f"text={strat['text']}")
                    repr_ = f"text={strat['text']!r}"
                elif "css" in strat:
                    loc = self.page.locator(strat["css"])
                    repr_ = f"css={strat['css']!r}"
                else:
                    continue
                count = loc.count()
                log.info(
                    "strategy_attempt",
                    target=label,
                    index=i,
                    selector=repr_,
                    count=count,
                )
                if count > 0:
                    return loc.first
            except Exception as exc:
                log.warning(
                    "strategy_error",
                    target=label,
                    index=i,
                    error=str(exc),
                )
        return None

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

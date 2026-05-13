"""Page Object Model for Configuration → Layer 2 → VLAN.

Pure presentation-layer wrapper around the Playwright Page. Knows the
navigation path (with 3-path fallback proven by `playwright_playground/
scripts/06_real_router_vlan_add.py`), the form fields, and the save
button — but nothing about approvals, snapshots, or higher-level flow.
Composed by `flows/add_access_vlan.py`.

Selectors come from `selectors/iosxe_default.yaml` (`vlan_nav` +
`vlan_form` chains). The POM hardcodes no locator; if the yaml changes,
the POM keeps working.
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

FORM_SETTLE_MS = 3_000
NAV_TIMEOUT_MS = 10_000


class VlanNavigationError(RuntimeError):
    """Raised when the VLAN page can't be reached via any of the three paths."""


class VlanFieldNotFound(RuntimeError):
    """Raised when the VLAN form's ID/name/save element can't be located."""


class VlanPage:
    """Drive the VLAN add form in the IOS XE WebUI.

    Usage (called by `flows/add_access_vlan.py`):

        vp = VlanPage(page)
        vp.goto()
        vp.click_add()
        vp.set_vlan_id(30)
        vp.set_vlan_name("OFFICE")
        vp.save()
    """

    def __init__(self, page: Page, selectors_map: str = "iosxe_default") -> None:
        self.page = page
        self._sel = load_selectors(selectors_map)

    # ---------------------------------------------------------------------
    # Navigation
    # ---------------------------------------------------------------------

    def goto(self) -> None:
        """Navigate to the VLAN list page via Configuration → Layer 2 → VLAN.

        Falls back to Path B (Configuration → LAN → VLAN) and Path C
        (any visible link containing "VLAN") if the primary path fails.
        Path discipline proven by playwright_playground/scripts/06.
        """
        log.info("vlan_page_goto_start", url=self.page.url)

        # Open Configuration menu
        cfg = first_match(self.page, self._sel["nav"]["configuration"])
        if cfg is None:
            self._dump_diagnostics("nav-no-configuration")
            raise VlanNavigationError(
                "Configuration menu not visible — sidebar may not have rendered "
                "or user lacks priv-15."
            )
        cfg.click()
        wait_for_networkidle(self.page, NAV_TIMEOUT_MS)
        self.page.wait_for_timeout(500)

        # Path A: Layer 2 → VLAN
        if self._try_path_a():
            log.info("vlan_page_path_a_succeeded", url=self.page.url)
        elif self._try_path_b():
            log.info("vlan_page_path_b_succeeded", url=self.page.url)
        elif self._try_path_c():
            log.info("vlan_page_path_c_succeeded", url=self.page.url)
        else:
            self._dump_diagnostics("nav-all-paths-failed")
            raise VlanNavigationError(
                "All three navigation paths (Layer 2 → VLAN, LAN → VLAN, "
                "any-link-containing-VLAN) failed. See structlog probe entries "
                "above and the DOM dump in artifacts/."
            )

        # AngularJS table render + form settle
        self.page.wait_for_timeout(FORM_SETTLE_MS)
        log.info("vlan_page_goto_complete", url=self.page.url)

    def _try_path_a(self) -> bool:
        """Layer 2 submenu → VLAN sub-item."""
        l2 = first_match(self.page, self._sel["vlan_nav"]["layer2"])
        if l2 is None:
            return False
        l2.click()
        wait_for_networkidle(self.page, NAV_TIMEOUT_MS)
        self.page.wait_for_timeout(500)
        vlan = first_match(self.page, self._sel["vlan_nav"]["vlan"])
        if vlan is None:
            return False
        vlan.click()
        wait_for_networkidle(self.page, NAV_TIMEOUT_MS)
        return True

    def _try_path_b(self) -> bool:
        """LAN submenu → VLAN sub-item (some IOS XE builds use LAN, not Layer 2)."""
        # Re-open Configuration since submenus may have collapsed
        cfg = first_match(self.page, self._sel["nav"]["configuration"])
        if cfg is not None:
            cfg.click()
            wait_for_networkidle(self.page, NAV_TIMEOUT_MS)
            self.page.wait_for_timeout(500)

        lan = self.page.locator("a:has-text('LAN'), span:has-text('LAN')").first
        if lan.count() == 0:
            return False
        lan.click()
        wait_for_networkidle(self.page, NAV_TIMEOUT_MS)
        self.page.wait_for_timeout(500)

        vlan = first_match(self.page, self._sel["vlan_nav"]["vlan"])
        if vlan is None:
            return False
        vlan.click()
        wait_for_networkidle(self.page, NAV_TIMEOUT_MS)
        return True

    def _try_path_c(self) -> bool:
        """Last resort: any visible nav link / button containing 'VLAN'."""
        any_vlan = self.page.locator(
            "a:has-text('VLAN'), button:has-text('VLAN'), span:has-text('VLAN')"
        )
        if any_vlan.count() == 0:
            return False
        any_vlan.first.click()
        wait_for_networkidle(self.page, NAV_TIMEOUT_MS)
        return True

    # ---------------------------------------------------------------------
    # Form interactions
    # ---------------------------------------------------------------------

    def click_add(self) -> None:
        """Open the Add VLAN form. Cisco WebUI shows a row of action buttons
        above the VLAN table; the Add button opens a modal/inline form."""
        btn = first_match(self.page, self._sel["vlan_form"]["add_button"])
        if btn is None:
            self._dump_diagnostics("add-button-missing")
            raise VlanFieldNotFound(
                "Add button not visible on VLAN page — selectors may need refresh."
            )
        btn.click()
        wait_for_networkidle(self.page, NAV_TIMEOUT_MS)
        self.page.wait_for_timeout(500)
        log.info("vlan_page_add_clicked")

    def set_vlan_id(self, vlan_id: int) -> None:
        """Fill the VLAN ID field. Accepts any 1..4094; the router validates."""
        loc = first_match(self.page, self._sel["vlan_form"]["vlan_id"])
        if loc is None:
            self._dump_diagnostics("vlan-id-field-missing")
            raise VlanFieldNotFound("VLAN ID input not visible — see input_inventory probe log.")
        loc.click()
        loc.fill(str(vlan_id))
        log.info("vlan_page_id_filled", vlan_id=vlan_id)

    def set_vlan_name(self, name: str) -> None:
        """Fill the VLAN Name field. Some IOS XE builds omit it — log + skip."""
        loc = first_match(self.page, self._sel["vlan_form"]["vlan_name"])
        if loc is None:
            log.warning("vlan_page_name_field_absent", name=name)
            return
        loc.click()
        loc.fill(name)
        log.info("vlan_page_name_filled", name=name)

    def save(self) -> None:
        """Click Save / Apply / OK. The form posts and the modal closes."""
        btn = first_match(self.page, self._sel["vlan_form"]["save_button"])
        if btn is None:
            self._dump_diagnostics("save-button-missing")
            raise VlanFieldNotFound("Save button not visible on VLAN form")
        btn.click()
        wait_for_networkidle(self.page, 15_000)
        log.info("vlan_page_save_clicked")

    # ---------------------------------------------------------------------
    # Diagnostics
    # ---------------------------------------------------------------------

    def _dump_diagnostics(self, lbl: str) -> None:
        """Log body text excerpt + probe counts + input inventory.

        Mirrors HostnamePage._dump_diagnostics — same probe protocol so
        diagnostics on a failed VLAN add look familiar to anyone who has
        debugged a failed hostname change. `lbl` not `label` to avoid
        shadowing (audit fix from Copilot review).
        """
        try:
            body = self.page.locator("body").inner_text()[:1500]
        except Exception as exc:
            body = f"(failed to read body: {exc})"
        log.warning(
            "vlan_page_body_excerpt",
            label=lbl,
            url=self.page.url,
            text=body.replace("\n", " | "),
        )

        for probe in (
            "button:has-text('Add')",
            "button:has-text('+')",
            "input[placeholder*='VLAN' i]",
            "input[id*='vlan' i]",
            "input[name*='vlan' i]",
            "input[type='number']",
            "button:has-text('Save')",
            "button:has-text('Apply')",
        ):
            try:
                cnt = self.page.locator(probe).count()
            except Exception as exc:
                cnt = f"ERR:{exc}"
            log.warning("vlan_probe_count", probe=probe, count=cnt)

        try:
            inputs = self.page.locator("input").all()
            for i, inp in enumerate(inputs[:15]):
                try:
                    log.warning(
                        "vlan_input_inventory",
                        index=i,
                        type=inp.get_attribute("type"),
                        name=inp.get_attribute("name"),
                        id=inp.get_attribute("id"),
                        placeholder=inp.get_attribute("placeholder"),
                        visible=inp.is_visible(),
                    )
                except Exception as exc:
                    log.warning("vlan_input_inventory_err", index=i, error=str(exc))
        except Exception as exc:
            log.warning("vlan_input_inventory_top_err", error=str(exc))

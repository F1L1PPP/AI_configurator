"""Page Object Model for the VLAN form (Configuration → Layer 2 → VLAN).

Pure presentation-layer wrapper around the Playwright Page. Knows the
direct hash route to the VLAN page, the tab structure (SVI / VLAN /
VLAN Group), the form fields, and the save button — but nothing about
approvals, snapshots, or higher-level flow. Composed by
`flows/add_access_vlan.py`.

**Navigation strategy: direct hash route, NOT the sidebar.** Day 5
proved that the Cisco IOS XE 17.6.3a sidebar renders unreliably under
Playwright — sometimes the Configuration menu just isn't there even
for priv-15 users. `HostnamePage.goto()` bypasses this by navigating
directly to `/webui/#/general`; we do the same here with
`/webui/#/vlan`.

Selectors come from `selectors/iosxe_default.yaml` (`vlan_form`
chains). The POM hardcodes no locator; if the yaml changes, the POM
keeps working.
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

# Direct hash route to the VLAN page on IOS XE 17.6.3a — captured by
# manual navigation per Filip's screenshots. Lands on the SVI tab by
# default; goto() then clicks the VLAN tab to reveal the VLAN table.
VLAN_ROUTE = "/webui/#/vlan"
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
        """Navigate directly to the VLAN form via hash route.

        Skips the sidebar (Configuration → Layer 2 → VLAN) and goes
        straight to `/webui/#/vlan`. The sidebar renders unreliably
        under Playwright; the hash route works regardless. Same fix
        Day 5 made for the hostname form.

        Lands on the **SVI** tab (default of 3). This method then
        clicks the **VLAN** tab so the VLAN table + Add button are
        visible — without that, click_add() can't find anything to
        click.
        """
        log.info("vlan_page_goto_start", url=self.page.url)

        # Reconstruct the base URL from whatever route we're currently on
        parts = self.page.url.split("/webui/")
        base = parts[0] if parts else self.page.url.rstrip("/")
        target_url = f"{base}{VLAN_ROUTE}"
        log.info("vlan_page_direct_nav", target=target_url)

        self.page.goto(target_url, wait_until="domcontentloaded", timeout=20_000)
        wait_for_networkidle(self.page, NAV_TIMEOUT_MS)
        # AngularJS / Kendo finish rendering tabs + table
        self.page.wait_for_timeout(FORM_SETTLE_MS)

        # Click the VLAN tab (default is SVI; we want VLAN). Two attempts:
        # the loaded yaml chain, then a permissive fallback.
        self._select_vlan_tab()

        log.info("vlan_page_goto_complete", url=self.page.url)

    def _select_vlan_tab(self) -> None:
        """Click the VLAN tab to switch from SVI to the VLAN table.

        The /webui/#/vlan page has three tabs (SVI / VLAN / VLAN Group)
        and the user lands on SVI by default. Clicking VLAN reveals
        the table where the Add button lives.

        If the tab can't be found, we log + continue rather than raise —
        some IOS XE builds may show the VLAN table on first load
        without tabs at all. click_add() will produce a clearer error
        if the page state is wrong.
        """
        tab = first_match(self.page, self._sel["vlan_form"]["vlan_tab"])
        if tab is None:
            log.warning("vlan_page_tab_not_found_continuing")
            return
        tab.click()
        wait_for_networkidle(self.page, NAV_TIMEOUT_MS)
        self.page.wait_for_timeout(800)
        log.info("vlan_page_tab_selected")

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

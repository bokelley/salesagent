"""
Smoke tests for inventory tree pages.

Verifies that initInventoryTree() is called correctly and pages render
without JS errors. These tests catch function-signature mismatches between
the tree partial and consumer templates.

Requires: running Docker stack with at least one tenant.
GAM-specific tests are skipped if the tenant uses a non-GAM adapter.
"""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.ui


class TestInventoryPageLoads:
    """All inventory-related pages load without JS errors."""

    def test_inventory_unified_loads(self, authenticated_page: Page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/tenant/default/inventory")
        page.wait_for_load_state("networkidle")

        # Verify we landed on an inventory page (not redirected to login)
        url = page.url
        assert "/login" not in url, f"Redirected to login: {url}"

        # No JS errors
        assert page.js_errors == [], f"JS errors on inventory unified: {page.js_errors}"

    def test_inventory_browser_tree_loads_if_gam(self, authenticated_page: Page, base_url):
        """On GAM tenants, the Browse Inventory page renders the ad unit tree."""
        page = authenticated_page
        page.goto(f"{base_url}/tenant/default/inventory/browse")
        page.wait_for_load_state("networkidle")

        # Tree container should exist and get populated by initInventoryTree
        tree = page.locator("#adUnitTree")
        expect(tree).to_be_visible(timeout=5000)

        tree_content = page.locator(
            "#adUnitTree .inventory-tree-view, #adUnitTree .tree-empty, #adUnitTree .tree-loading"
        )
        expect(tree_content.first).to_be_visible(timeout=5000)

        assert page.js_errors == [], f"JS errors on browse inventory tab: {page.js_errors}"

    def test_products_page_loads(self, authenticated_page: Page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/tenant/default/products")
        page.wait_for_load_state("networkidle")

        assert page.js_errors == [], f"JS errors on products page: {page.js_errors}"


class TestInventoryPickerTree:
    """Inventory picker modal loads tree via initInventoryTree."""

    def test_add_product_page_loads(self, authenticated_page: Page, base_url):
        """The add product page loads without JS errors.

        This page includes the inventory picker component which includes
        the tree partial — verifies no undefined function errors on load.
        """
        page = authenticated_page

        page.goto(f"{base_url}/tenant/default/products/add")
        page.wait_for_load_state("networkidle")

        assert page.js_errors == [], f"JS errors on add product page: {page.js_errors}"

    def test_picker_opens_without_js_errors(self, authenticated_page: Page, base_url):
        """Open the inventory picker modal and verify no JS errors."""
        page = authenticated_page

        page.goto(f"{base_url}/tenant/default/products/add")
        page.wait_for_load_state("networkidle")

        # Open the ad unit picker
        browse_btn = page.locator("text=Browse Ad Units").first

        browse_btn.click()
        page.wait_for_timeout(1000)

        assert page.js_errors == [], f"JS errors on inventory picker: {page.js_errors}"


class TestInventoryBundleEditor:
    """Inventory bundle editor smoke tests."""

    def test_add_bundle_picker_opens_without_js_errors(self, authenticated_page: Page, base_url):
        """The redesigned add-bundle page opens its in-page pickers without alerts or JS errors."""
        page = authenticated_page

        page.goto(f"{base_url}/tenant/default/inventory-profiles/add")
        page.wait_for_load_state("networkidle")

        expect(page.get_by_role("heading", name="Create inventory bundle")).to_be_visible(timeout=5000)

        page.get_by_role("button", name="Pick ad units").click()
        picker = page.locator("#inventory-picker")
        expect(picker).to_be_visible(timeout=5000)
        expect(page.locator("#inventory-picker-title")).to_contain_text("Pick ad units")
        expect(page.locator("#inventory-picker-list")).to_contain_text("Smoke Test Ad Unit", timeout=5000)
        page.locator("#inventory-picker input[data-inventory-id='smoke-au-001']").check()
        page.get_by_role("button", name="Apply selection").click()
        expect(picker).to_be_hidden(timeout=5000)
        expect(page.locator("#selected-ad-units")).to_contain_text("Smoke Test Ad Unit", timeout=5000)

        page.get_by_role("button", name="Pick placements").click()
        expect(picker).to_be_visible(timeout=5000)
        expect(page.locator("#inventory-picker-title")).to_contain_text("Pick placements")
        expect(page.locator("#inventory-picker-list")).to_contain_text("Smoke Test Placement", timeout=5000)

        assert page.js_errors == [], f"JS errors on bundle picker: {page.js_errors}"

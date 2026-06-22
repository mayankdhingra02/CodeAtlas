from __future__ import annotations

import os
import tempfile
import unittest


@unittest.skipUnless(os.environ.get("CODEATLAS_UI_URL"), "set CODEATLAS_UI_URL to run browser smoke tests")
class CodeAtlasUiSmokeTests(unittest.TestCase):
    def _screenshot_dir(self):
        configured = os.environ.get("CODEATLAS_UI_SCREENSHOT_DIR")
        if configured:
            os.makedirs(configured, exist_ok=True)
            return configured, None
        temp = tempfile.TemporaryDirectory(prefix="codeatlas-ui-")
        return temp.name, temp

    def _assert_canvas_has_pixels(self, page) -> None:
        nonblank = page.locator("#graphCanvas").evaluate(
            """canvas => {
                const ctx = canvas.getContext('2d');
                const width = canvas.width;
                const height = canvas.height;
                if (!ctx || !width || !height) return 0;
                const data = ctx.getImageData(0, 0, width, height).data;
                let pixels = 0;
                for (let i = 0; i < data.length; i += 16) {
                    if (data[i + 3] > 0 && (data[i] > 10 || data[i + 1] > 10 || data[i + 2] > 10)) pixels += 1;
                }
                return pixels;
            }"""
        )
        self.assertGreater(nonblank, 1000)

    def test_workflow_buttons_render_cards(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover - optional dependency guard
            self.skipTest(f"playwright is not installed: {exc}")

        url = os.environ["CODEATLAS_UI_URL"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.goto(url, wait_until="networkidle")
                self.assertGreater(page.locator("#graphCanvas").bounding_box()["width"], 300)
                self.assertTrue(page.locator("#buildBadge").is_visible())
                self.assertTrue(page.locator("#edgeContrastInput").is_visible())
                self.assertTrue(page.locator("#edgeBundlingInput").is_visible())
                self.assertEqual(page.locator("#fitSelectionBtn").count(), 1)
                self.assertEqual(page.locator("#undoToast").count(), 1)
                page.locator("#edgeContrastInput").evaluate("(input) => { input.value = '90'; input.dispatchEvent(new Event('input', { bubbles: true })); }")
                self.assertEqual(page.locator("#edgeContrastLabel").inner_text(), "90%")
                page.locator("#categoryFilters input").nth(2).uncheck()
                page.locator("#componentFilters input").first.wait_for(timeout=10000)
                page.locator("#componentFilters input").first.check(force=True)
                canvas = page.locator("#graphCanvas")
                box = canvas.bounding_box()
                self.assertIsNotNone(box)
                page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, button="right")
                self.assertEqual(page.locator("#contextOwnedNodeBtn").count(), 1)
                self.assertEqual(page.locator("#contextThirdPartyNodeBtn").count(), 1)
                self.assertTrue(page.get_by_text("Rule checks").is_visible())
                page.get_by_text("Verify plan").click()
                page.locator(".workflow-panel").first.wait_for(timeout=10000)
                self.assertTrue(page.get_by_text("Export JSON").is_visible())
            finally:
                browser.close()

    def test_visual_regression_surfaces(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover - optional dependency guard
            self.skipTest(f"playwright is not installed: {exc}")

        url = os.environ["CODEATLAS_UI_URL"]
        screenshot_dir, temp_dir = self._screenshot_dir()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    page.goto(url, wait_until="networkidle")
                    page.locator("#graphCanvas").wait_for(timeout=10000)
                    page.wait_for_timeout(500)
                    self._assert_canvas_has_pixels(page)
                    page.screenshot(path=os.path.join(screenshot_dir, "map.png"), full_page=True)
                    page.keyboard.press("Control+K")
                    page.locator("#commandPalette:not([hidden])").wait_for(timeout=3000)
                    self.assertTrue(page.get_by_text("Copy current map link").is_visible())
                    page.screenshot(path=os.path.join(screenshot_dir, "command-palette.png"), full_page=True)
                finally:
                    browser.close()
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

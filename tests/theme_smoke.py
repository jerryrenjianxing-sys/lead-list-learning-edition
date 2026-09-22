"""Check the built homepage's theme transition in Chromium; no API writes."""

import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

out = Path(__file__).resolve().parents[1] / ".cache/theme-review"
out.mkdir(parents=True, exist_ok=True)
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 850})
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(sys.argv[1])
    button = page.locator(".skill-theme")
    layer = page.locator(".skill-theme-transition")
    button.wait_for()
    assert button.locator("svg.lucide-moon").count() == 1
    page.screenshot(path=str(out / "light.png"))
    page.evaluate("""() => {
        window.themeFrames = [];
        const record = () => {
            const style = getComputedStyle(document.querySelector('.skill-theme-transition'));
            window.themeFrames.push({clip: style.clipPath, display: style.display});
            if (window.themeFrames.length < 80) requestAnimationFrame(record);
        };
        requestAnimationFrame(record);
    }""")
    button.click()
    layer.wait_for(state="visible")
    assert layer.evaluate("e => getComputedStyle(e).pointerEvents") == "none"
    page.screenshot(path=str(out / "transition.png"))
    layer.wait_for(state="hidden")
    assert page.locator("html").get_attribute("data-theme") == "dark"
    assert button.locator("svg.lucide-sun").count() == 1
    frames = page.evaluate("window.themeFrames")
    clips = {f["clip"] for f in frames if f["display"] != "none"}
    assert len(clips) > 2, "The circular wipe did not animate"
    page.screenshot(path=str(out / "dark.png"))
    page.reload()
    button.wait_for()
    assert page.locator("html").get_attribute("data-theme") == "dark"
    assert button.locator("svg.lucide-sun").count() == 1
    button.focus()
    page.keyboard.press("Enter")
    layer.wait_for(state="hidden")
    assert page.locator("html").get_attribute("data-theme") == "light"

    # Repeated input must end in the last requested state, without an orphan mask.
    button.evaluate("b => { b.click(); b.click(); b.click(); }")
    layer.wait_for(state="hidden")
    assert page.locator("html").get_attribute("data-theme") == "dark"
    button.evaluate("b => { b.click(); b.click(); }")
    assert page.locator("html").get_attribute("data-theme") == "dark"
    assert not layer.is_visible()

    # A navigation click must pass through the wipe and retain the chosen theme.
    button.click()
    layer.wait_for(state="visible")
    page.get_by_role("link", name="任务台", exact=True).click()
    page.locator(".wb-app").wait_for()
    assert page.locator("html").get_attribute("data-theme") == "light"
    assert page.locator(".skill-theme-transition").count() == 0
    page.get_by_role("link", name="Media Deep Researcher", exact=True).click()
    button.wait_for()

    page.emulate_media(reduced_motion="reduce")
    button.click()
    assert page.locator("html").get_attribute("data-theme") == "dark"
    assert not layer.is_visible()
    button.click()
    assert page.locator("html").get_attribute("data-theme") == "light"
    page.emulate_media(reduced_motion="no-preference")
    page.set_viewport_size({"width": 320, "height": 844})
    button.click()
    layer.wait_for(state="hidden")
    assert page.locator("html").get_attribute("data-theme") == "dark"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(out / "mobile-dark.png"), full_page=True)
    browser.close()
assert not errors, errors
result = {"ok": True, "checks": ["sun-moon", "circular-wipe", "keyboard", "persistence", "rapid-clicks", "navigation", "reduced-motion", "320px"], "page_errors": errors}
(out / "validation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result))

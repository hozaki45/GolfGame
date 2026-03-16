"""Simple Playwright test."""

from playwright.sync_api import sync_playwright

print("Starting Playwright test...")

try:
    with sync_playwright() as p:
        print("Launching browser...")
        browser = p.chromium.launch(headless=True)

        print("Creating page...")
        page = browser.new_page()

        print("Navigating to example.com...")
        page.goto("https://example.com", wait_until="networkidle")

        print("Getting title...")
        title = page.title()
        print(f"Page title: {title}")

        print("Closing browser...")
        browser.close()

    print("✓ Playwright test passed!")

except Exception as e:
    print(f"✗ Playwright test failed: {e}")
    import traceback
    traceback.print_exc()

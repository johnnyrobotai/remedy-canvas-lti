#!/usr/bin/env python3
"""Open Canvas login in a visible headed browser."""
import asyncio
import os
from playwright.async_api import async_playwright

async def main():
    canvas_url = os.environ.get("LOCAL_CANVAS_BASE_URL", "https://projectclu.com").rstrip("/")
    username = os.environ.get("LOCAL_CANVAS_USERNAME") or os.environ.get("CANVAS_USERNAME")
    password = os.environ.get("LOCAL_CANVAS_PASSWORD") or os.environ.get("CANVAS_PASSWORD")
    if not username or not password:
        raise SystemExit(
            "Set LOCAL_CANVAS_USERNAME and LOCAL_CANVAS_PASSWORD before running this helper."
        )

    async with async_playwright() as p:
        # Launch browser with visible window
        print("Launching visible Chrome browser...")
        browser = await p.chromium.launch(
            headless=False,
            args=['--window-size=1400,900']
        )

        context = await browser.new_context(
            viewport={'width': 1400, 'height': 900}
        )

        page = await context.new_page()

        # Navigate to Canvas login
        print("Navigating to Canvas login...")
        await page.goto(f"{canvas_url}/login/canvas")

        # Fill in credentials
        await page.fill('input[name="pseudonym_session[unique_id]"]', username)
        await page.fill('input[name="pseudonym_session[password]"]', password)

        print("\n" + "="*60)
        print("CANVAS LOGIN READY")
        print("="*60)
        print("Credentials loaded from environment.")
        print("="*60)
        print("\nBrowser window should be visible now!")
        print("Auto-logging in...")
        print("="*60 + "\n")

        # Click login button
        await page.click('button.Button--login')
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(3)

        # Check if logged in
        current_url = page.url
        print(f"Current URL: {current_url}")

        if "/login" not in current_url:
            print("✅ Logged in successfully!")
            await page.screenshot(path="/tmp/canvas_logged_in.png")
            print("Screenshot saved to /tmp/canvas_logged_in.png")
        else:
            print("⚠️  Login may have failed - still on login page.")
            await page.screenshot(path="/tmp/canvas_login_status.png")

        # Keep browser open
        print("\nBrowser will stay open for 60 seconds...")
        await asyncio.sleep(60)

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
Run Canvas Remedy LTI AutoRemedy on all 5 imported courses using Playwright.
"""
import asyncio
import os
from playwright.async_api import async_playwright
import sys

CANVAS_URL = os.environ.get("LOCAL_CANVAS_BASE_URL", "https://projectclu.com").rstrip("/")
USERNAME = os.environ.get("LOCAL_CANVAS_USERNAME") or os.environ.get("CANVAS_USERNAME")
PASSWORD = os.environ.get("LOCAL_CANVAS_PASSWORD") or os.environ.get("CANVAS_PASSWORD")
LTI_TOOL_NAME = "Accessibility (CLU)"

COURSES = [
    {"id": "20", "name": "COMM_C1000"},
    {"id": "21", "name": "ENGL_C1000"},
    {"id": "23", "name": "ENGL_C1001"},
    {"id": "24", "name": "PSYC_C1000"},
    {"id": "25", "name": "STATS_C1000"},
]


async def launch_lti_tool(page, course_id):
    """Launch Canvas Remedy LTI from within a Canvas course."""
    print(f"  Navigating to course {course_id}...")
    await page.goto(f"{CANVAS_URL}/courses/{course_id}")
    await page.wait_for_load_state("networkidle")

    # Look for Canvas Remedy LTI in course navigation
    print(f"  Looking for '{LTI_TOOL_NAME}' in course nav...")
    lti_link = await page.query_selector(f'a:has-text("{LTI_TOOL_NAME}")')

    if not lti_link:
        print(f"  ERROR: LTI tool '{LTI_TOOL_NAME}' not found in course navigation")
        # Take screenshot for debugging
        await page.screenshot(path=f"/tmp/course_{course_id}_nav.png")
        print(f"  Screenshot saved to /tmp/course_{course_id}_nav.png")
        return False

    print(f"  Clicking '{LTI_TOOL_NAME}'...")
    await lti_link.click()

    # Wait for LTI launch - may open in new tab or iframe
    await page.wait_for_load_state("networkidle")
    await asyncio.sleep(3)

    return True


async def run_autoremedy(page, course_name):
    """Run AutoRemedy in Canvas Remedy LTI."""
    print(f"  Waiting for Canvas Remedy LTI to load...")

    # Wait for Canvas Remedy LTI to fully load - look for Dashboard or Fix My Course link
    try:
        await page.wait_for_selector('text="Dashboard", text="Fix My Course", text="Scan Results"', timeout=30000)
    except:
        print(f"  Canvas Remedy LTI may have loaded, continuing...")

    # Take screenshot to see current state
    await page.screenshot(path=f"/tmp/canvas_remedy_{course_name}_loaded.png")
    print(f"  Screenshot saved: /tmp/canvas_remedy_{course_name}_loaded.png")

    # Look for "Fix My Course" button/link
    fix_link = await page.query_selector('a:has-text("Fix My Course"), button:has-text("Fix My Course")')

    if fix_link:
        print(f"  Clicking 'Fix My Course'...")
        await fix_link.click()
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)
    else:
        print(f"  'Fix My Course' not found, looking for alternative...")
        # Try Dashboard if Fix My Course not found
        dashboard_link = await page.query_selector('a:has-text("Dashboard")')
        if dashboard_link:
            print(f"  Clicking 'Dashboard' instead...")
            await dashboard_link.click()
            await page.wait_for_load_state("networkidle")

    # Look for AutoRemedy button
    print(f"  Looking for AutoRemedy button...")
    await page.screenshot(path=f"/tmp/canvas_remedy_{course_name}_before_autoremedy.png")

    autoremedy_btn = await page.query_selector('button:has-text("AutoRemedy"), button:has-text("Fix My Course"), a:has-text("AutoRemedy")')

    if autoremedy_btn:
        print(f"  Found AutoRemedy button, clicking...")
        await autoremedy_btn.click()
        await page.wait_for_load_state("networkidle")

        # Wait for AutoRemedy dialog/job to start
        await asyncio.sleep(3)
        await page.screenshot(path=f"/tmp/canvas_remedy_{course_name}_autoremedy_started.png")
        print(f"  Screenshot saved: /tmp/canvas_remedy_{course_name}_autoremedy_started.png")

        # Wait for completion (monitor progress)
        print(f"  Waiting for AutoRemedy to complete (this may take 5-10 minutes)...")
        max_wait = 600  # 10 minutes
        waited = 0

        while waited < max_wait:
            await asyncio.sleep(10)
            waited += 10

            # Check for completion indicators
            complete_msg = await page.query_selector('text="Complete", text="Done", text="Remediation Complete"')
            if complete_msg:
                print(f"  AutoRemedy completed after {waited}s!")
                break

            # Check progress
            progress = await page.query_selector('.progress-bar, [role="progressbar"]')
            if progress:
                progress_text = await progress.text_content()
                print(f"  Progress: {progress_text} ({waited}s elapsed)")

        await page.screenshot(path=f"/tmp/canvas_remedy_{course_name}_autoremedy_complete.png")
        print(f"  Final screenshot: /tmp/canvas_remedy_{course_name}_autoremedy_complete.png")

        return True
    else:
        print(f"  ERROR: AutoRemedy button not found")
        await page.screenshot(path=f"/tmp/canvas_remedy_{course_name}_error.png")
        return False


async def main():
    """Main function to run AutoRemedy on all courses."""
    if not USERNAME or not PASSWORD:
        raise RuntimeError(
            "Set LOCAL_CANVAS_USERNAME and LOCAL_CANVAS_PASSWORD before running this helper."
        )

    results = []

    async with async_playwright() as p:
        # Launch headed browser so user can see/interact
        print("Launching headed browser...")
        print("NOTE: Please log in to Canvas when the browser opens!")

        browser = await p.chromium.launch(headless=False, slow_mo=500)
        context = await browser.new_context(viewport={'width': 1400, 'height': 900})
        page = await context.new_page()

        try:
            # Navigate to Canvas - user needs to log in manually
            await page.goto(f"{CANVAS_URL}/login/canvas")
            await page.wait_for_load_state("networkidle")

            print("\n" + "="*60)
            print("LOGGING IN TO CANVAS")
            print("="*60 + "\n")

            # Auto-login
            await page.fill('input[name="pseudonym_session[unique_id]"]', USERNAME)
            await page.fill('input[name="pseudonym_session[password]"]', PASSWORD)
            await page.click('button.Button--login')

            # Wait for navigation
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(3)

            # Verify logged in by checking URL
            current_url = page.url
            if "/login" in current_url:
                print("ERROR: Login failed, still on login page")
                await page.screenshot(path="/tmp/login_failed.png")
                await browser.close()
                return 1

            print(f"Logged in! Current URL: {current_url}\n")

            # Process each course
            for course in COURSES:
                print(f"\n{'='*60}")
                print(f"Processing: {course['name']} (Course {course['id']})")
                print(f"{'='*60}")

                # Launch LTI tool
                success = await launch_lti_tool(page, course['id'])
                if not success:
                    results.append({"course": course['name'], "status": "FAILED", "error": "LTI launch failed"})
                    continue

                # Run AutoRemedy
                success = await run_autoremedy(page, course['name'])
                if success:
                    results.append({"course": course['name'], "status": "SUCCESS"})
                else:
                    results.append({"course": course['name'], "status": "FAILED", "error": "AutoRemedy failed"})

                # Go back to Canvas for next course
                print(f"  Going back to Canvas...")
                await page.goto(CANVAS_URL)
                await asyncio.sleep(2)

            # Print summary
            print("\n" + "="*60)
            print("AUTOREMEDY COMPLETE - SUMMARY")
            print("="*60)
            for r in results:
                status_icon = "✅" if r['status'] == "SUCCESS" else "❌"
                print(f"{status_icon} {r['course']}: {r['status']}")
                if 'error' in r:
                    print(f"   Error: {r['error']}")

            print("\nScreenshots saved to /tmp/")
            print("="*60)

        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()

        finally:
            print("\nKeeping browser open for 10 seconds...")
            await asyncio.sleep(10)
            await browser.close()

    return 0


if __name__ == "__main__":
    print("Canvas Remedy LTI AutoRemedy Runner")
    print("This script will open a browser and run AutoRemedy on all 5 courses.")
    print("You'll need to log in manually when prompted.\n")
    sys.exit(asyncio.run(main()))

#!/usr/bin/env python3
"""
Import Canvas courses from ZIP files using Playwright browser automation.
"""
import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright
import sys

CANVAS_URL = os.environ.get("LOCAL_CANVAS_BASE_URL", "https://projectclu.com").rstrip("/")
USERNAME = os.environ.get("LOCAL_CANVAS_USERNAME") or os.environ.get("CANVAS_USERNAME")
PASSWORD = os.environ.get("LOCAL_CANVAS_PASSWORD") or os.environ.get("CANVAS_PASSWORD")

COURSES = [
    {"name": "COMM_C1000", "file": "COMM_C1000_with_accessibility_issues.zip", "course_id": 19},
    {"name": "ENGL_C1000", "file": "ENGL_C1000_with_accessibility_issues.zip", "course_id": None},
    {"name": "ENGL_C1001", "file": "ENGL_C1001_with_accessibility_issues.zip", "course_id": None},
    {"name": "PSYC_C1000", "file": "PSYC_C1000_with_accessibility_issues.zip", "course_id": None},
    {"name": "STATS_C1000", "file": "STATS_C1000_with_accessibility_issues.zip", "course_id": None},
]

SAMPLE_DIR = os.environ.get(
    "CANVAS_SAMPLE_DIR",
    str(Path.home() / "Desktop/sample_courses_with_and_without_accessibility_issues"),
)


async def login(page):
    """Log in to Canvas."""
    if not USERNAME or not PASSWORD:
        raise RuntimeError(
            "Set LOCAL_CANVAS_USERNAME and LOCAL_CANVAS_PASSWORD before running this helper."
        )

    print("Navigating to Canvas login...")
    await page.goto(f"{CANVAS_URL}/login/canvas")
    await page.wait_for_load_state("networkidle")

    print("Filling login credentials...")
    await page.fill('input[name="pseudonym_session[unique_id]"]', USERNAME)
    await page.fill('input[name="pseudonym_session[password]"]', PASSWORD)

    print("Clicking login button...")
    # Try using the form submit instead of button click
    await page.press('input[name="pseudonym_session[password]"]', 'Enter')
    await page.wait_for_load_state("networkidle")

    # Take screenshot for debugging
    await page.screenshot(path="/tmp/canvas_after_login.png")
    print("Screenshot saved to /tmp/canvas_after_login.png")

    # Check if login succeeded
    if "/login" in page.url:
        print("ERROR: Login failed - still on login page")
        # Print page content for debugging
        content = await page.content()
        print(f"Page content: {content[:500]}...")
        return False

    print(f"Login successful! Current URL: {page.url}")
    return True


async def create_course(page, course_name, course_code):
    """Create a new course in Canvas."""
    print(f"Creating course: {course_name}...")

    # Navigate to admin courses page
    await page.goto(f"{CANVAS_URL}/accounts/1")
    await page.wait_for_load_state("networkidle")

    # Click "+ Course" button
    await page.click('a:has-text("+ Course")')
    await page.wait_for_selector('form[action="/accounts/1/courses"]')

    # Fill course details
    await page.fill('input[name="course[name]"]', f"{course_code} - {course_name} (Accessibility Test)")
    await page.fill('input[name="course[course_code]"]', course_code)

    # Submit form
    await page.click('button[type="submit"]')
    await page.wait_for_load_state("networkidle")

    # Get course ID from URL
    course_id = None
    if "/courses/" in page.url:
        course_id = page.url.split("/courses/")[1].split("/")[0]

    print(f"Course created with ID: {course_id}")
    return course_id


async def import_course_content(page, course_id, zip_file_path):
    """Import content from ZIP file into course."""
    print(f"Importing content to course {course_id} from {zip_file_path}...")

    # Navigate to course settings
    await page.goto(f"{CANVAS_URL}/courses/{course_id}/settings")
    await page.wait_for_load_state("networkidle")

    # Click "Import Course Content" in right sidebar
    import_link = await page.query_selector('a:has-text("Import Course Content")')
    if not import_link:
        print("ERROR: Import Course Content link not found")
        return False

    await import_link.click()
    await page.wait_for_load_state("networkidle")

    # Select content type (Unzip .zip file)
    await page.select_option('select[name="migration_type"]', 'zip_file')

    # Upload file
    file_input = await page.query_selector('input[type="file"]')
    if not file_input:
        print("ERROR: File input not found")
        return False

    await file_input.set_input_files(zip_file_path)

    # Wait for file upload to complete
    await page.wait_for_timeout(2000)

    # Click "Import" button
    await page.click('button[type="submit"]')

    # Wait for import to complete (may take several minutes for large files)
    print("Waiting for import to complete...")
    await page.wait_for_load_state("networkidle")

    # Check for success message
    success_msg = await page.query_selector('.ic-flash-success')
    if success_msg:
        print("Import started successfully!")
    else:
        print("Import may have failed - no success message found")

    # Wait for progress to complete
    max_wait = 600  # 10 minutes max
    waited = 0
    while waited < max_wait:
        await page.wait_for_timeout(5000)
        waited += 5

        # Check if import is complete
        progress = await page.query_selector('.migration-progress')
        if not progress:
            print("Import progress indicator no longer visible - may be complete")
            break

        print(f"Import in progress... ({waited}s)")

    # Publish the course
    print("Publishing course...")
    await page.goto(f"{CANVAS_URL}/courses/{course_id}/settings")
    await page.wait_for_load_state("networkidle")

    publish_btn = await page.query_selector('button:has-text("Publish")')
    if publish_btn:
        await publish_btn.click()
        await page.wait_for_timeout(1000)
        print("Course published!")

    return True


async def main():
    async with async_playwright() as p:
        # Launch browser
        browser = await p.chromium.launch(headless=False, slow_mo=100)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            # Log in
            if not await login(page):
                print("Login failed, exiting")
                await browser.close()
                return 1

            # Import each course
            for course in COURSES:
                print(f"\n{'='*60}")
                print(f"Processing: {course['name']}")
                print(f"{'='*60}")

                zip_path = f"{SAMPLE_DIR}/{course['file']}"

                # Create course if needed
                if course['course_id'] is None:
                    course_id = await create_course(page, course['name'], course['name'])
                    course['course_id'] = course_id
                else:
                    course_id = course['course_id']

                if not course_id:
                    print(f"ERROR: Could not get course ID for {course['name']}")
                    continue

                # Import content
                success = await import_course_content(page, course_id, zip_path)

                if success:
                    print(f"✅ Successfully imported {course['name']} to course {course_id}")
                else:
                    print(f"❌ Failed to import {course['name']}")

            print("\n" + "="*60)
            print("Import process complete!")
            print("="*60)
            for course in COURSES:
                print(f"  {course['name']}: Course ID {course['course_id']}")

        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()

        finally:
            # Keep browser open for a bit to see final state
            await page.wait_for_timeout(5000)
            await browser.close()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

#!/usr/bin/env python3
"""
Import a Canvas course from a ZIP file using the Canvas API.
"""
import sys
import os
import requests
import json

# Canvas instance (projectclu.com)
CANVAS_BASE_URL = "https://projectclu.com"
# Get API token from environment - use admin token from .env
CANVAS_API_TOKEN = os.getenv("CANVAS_API_TOKEN")
if not CANVAS_API_TOKEN:
    sys.exit("CANVAS_API_TOKEN is required (set in environment or .env)")

HEADERS = {
    "Authorization": f"Bearer {CANVAS_API_TOKEN}",
}

def upload_file_to_course(course_id, file_path):
    """Upload a file to a course and return the file_id."""
    filename = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)

    # Step 1: Request upload URL
    upload_url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/files"
    params = {
        "name": filename,
        "size": file_size,
        "content_type": "application/zip",
    }

    resp = requests.post(upload_url, headers=HEADERS, json=params)
    resp.raise_for_status()
    upload_data = resp.json()

    print(f"Got upload URL: {upload_data.get('upload_url', 'N/A')}")

    # Step 2: Upload file to the provided URL
    upload_url = upload_data["upload_url"]
    upload_params = upload_data["upload_params"]

    with open(file_path, "rb") as f:
        files = {"file": (filename, f, "application/zip")}
        upload_resp = requests.post(upload_url, data=upload_params, files=files)
        upload_resp.raise_for_status()

    # Step 3: Get the file ID from the response
    file_info = upload_resp.json()
    file_id = file_info.get("id")
    print(f"File uploaded successfully. File ID: {file_id}")

    return file_id

def create_content_migration(course_id, file_id):
    """Create a content migration using the uploaded file."""
    migration_url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/content_migrations"
    params = {
        "migration_type": "zip_file_importer",
        "settings": {
            "file_id": str(file_id),
        },
        "selective_import": False,
    }

    resp = requests.post(migration_url, headers=HEADERS, json=params)
    resp.raise_for_status()
    migration = resp.json()

    print(f"Migration created: ID {migration['id']}")
    print(f"Workflow state: {migration['workflow_state']}")
    print(f"Progress URL: {migration.get('progress_url', 'N/A')}")

    return migration

def check_migration_status(course_id, migration_id):
    """Check the status of a content migration."""
    migration_url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/content_migrations/{migration_id}"
    resp = requests.get(migration_url, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()

def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <course_id> <zip_file_path>")
        sys.exit(1)

    course_id = sys.argv[1]
    file_path = sys.argv[2]

    if not os.path.exists(file_path):
        print(f"Error: File not found: {file_path}")
        sys.exit(1)

    print(f"Importing {file_path} into course {course_id}...")

    # Upload the file
    file_id = upload_file_to_course(course_id, file_path)

    # Create the migration
    migration = create_content_migration(course_id, file_id)
    migration_id = migration["id"]

    # Check status
    print("\nMigration status:")
    import json
    print(json.dumps(migration, indent=2))

    print(f"\nTo check status later, run:")
    print(f"  curl -H 'Authorization: Bearer {CANVAS_API_TOKEN[:10]}...' \\")
    print(f"    '{CANVAS_BASE_URL}/api/v1/courses/{course_id}/content_migrations/{migration_id}'")

if __name__ == "__main__":
    main()

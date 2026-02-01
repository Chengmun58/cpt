"""
(sync_working_files_to_notion.py)

This script demonstrates how to automate the process of syncing rows from a Google
Sheet (containing file metadata) into a Notion database. The typical use case
is for tracking marketing collateral or operational documents—files stored on
Google Drive—with additional metadata such as category, brand and notes.

The workflow is:
1. Connect to Google Sheets/Drive using a service account.
2. Read the target worksheet and identify new rows to sync (based on a
   configurable `last_synced_row` value stored locally).
3. For each row:
   - If a `PDF Link` exists, use it directly as the file to upload.
   - Otherwise, attempt to export the file referenced in `Link` to PDF via
     Google Drive (Docs, Slides and Sheets support export).
   - Create or update an entry in the target Notion database with the relevant
     properties, attaching the file either as an external link or by uploading
     the binary via Notion's file API.

This script is a template: it omits robust error handling and full coverage
of all file types for brevity. Adjust the fields and logic to match your
Notion database schema.

Requirements:
```
pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib notion-client
```

Configure the following environment variables before running:
- `GOOGLE_SERVICE_ACCOUNT_FILE`: path to your Google service account JSON key.
- `SHEET_ID`: ID of the Google Sheet to read from.
- `WORKSHEET_NAME`: Name of the worksheet/tab to process (e.g. "Microneedling").
- `NOTION_API_KEY`: your Notion integration secret.
- `NOTION_DATABASE_ID`: ID of the Notion database (e.g. File Library).

Usage:
```
python sync_working_files_to_notion.py
```

After running, the script prints a summary of new records created and updates
a local state file (`last_synced_row.txt`) to keep track of progress.
"""

import os
from typing import Optional, Dict, List

import googleapiclient.discovery
from google.oauth2 import service_account
from notion_client import Client as NotionClient

# Constants
STATE_FILE = os.path.join(os.path.dirname(__file__), "last_synced_row.txt")


def get_google_services() -> Dict[str, any]:
    """Initialize Google Sheets and Drive API services using a service account."""
    creds_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not creds_path:
        raise RuntimeError(
            "Environment variable GOOGLE_SERVICE_ACCOUNT_FILE must point to the service account JSON"
        )
    creds = service_account.Credentials.from_service_account_file(
        creds_path,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ],
    )
    sheets_service = googleapiclient.discovery.build("sheets", "v4", credentials=creds)
    drive_service = googleapiclient.discovery.build("drive", "v3", credentials=creds)
    return {"sheets": sheets_service, "drive": drive_service}


def read_sheet_rows(
    sheets_service, sheet_id: str, worksheet_name: str, start_row: int = 2
) -> List[List[str]]:
    """
    Read all rows from a Google Sheet starting at a given row (1-indexed) and
    return them as a list of lists.
    """
    range_name = f"{worksheet_name}!A{start_row}:Z"
    result = (
        sheets_service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=range_name)
        .execute()
    )
    values = result.get("values", [])
    return values


def get_last_synced_row() -> int:
    """Retrieve the last synced row index from the state file, defaulting to 1."""
    if not os.path.exists(STATE_FILE):
        return 1
    with open(STATE_FILE, "r") as f:
        try:
            return int(f.read().strip())
        except ValueError:
            return 1


def update_last_synced_row(row_index: int) -> None:
    """Update the last synced row index in the state file."""
    with open(STATE_FILE, "w") as f:
        f.write(str(row_index))


def export_file_to_pdf(drive_service, file_url: str) -> Optional[bytes]:
    """
    Attempt to export a Google Docs/Sheets/Slides file to PDF. Accepts a share
    URL and returns the raw PDF bytes if export succeeds; otherwise returns None.
    """
    try:
        # Extract the file ID from the URL. Google share links are in the form
        # https://docs.google.com/{type}/d/{FILE_ID}/edit
        parts = file_url.split("/")
        if "drive.google.com" in file_url:
            # For direct drive link: /file/d/FILE_ID
            idx = parts.index("d") + 1
        else:
            # For Docs/Sheets/Slides: /d/FILE_ID
            idx = parts.index("d") + 1
        file_id = parts[idx]
    except (ValueError, IndexError):
        return None
    try:
        request = drive_service.files().export_media(fileId=file_id, mimeType="application/pdf")
        pdf_data = request.execute()
        return pdf_data
    except Exception:
        return None


def notion_create_entry(
    notion: NotionClient,
    database_id: str,
    properties: Dict,
    file_bytes: Optional[bytes] = None,
    file_name: Optional[str] = None,
) -> None:
    """
    Create a page in Notion with given properties. Optionally attach a file as
    external link or upload binary data. Note: uploading binary via API would
    require a publicly accessible URL or signed upload; this implementation
    attaches only the metadata and link.
    """
    children = []
    # File upload is not implemented here. To upload, first store the file
    # in an accessible location and then include it in the page's "files" property.
    notion.pages.create(
        parent={"database_id": database_id},
        properties=properties,
        children=children,
    )


def main() -> None:
    sheet_id = os.environ.get("SHEET_ID")
    worksheet_name = os.environ.get("WORKSHEET_NAME", "Microneedling")
    notion_key = os.environ.get("NOTION_API_KEY")
    notion_db_id = os.environ.get("NOTION_DATABASE_ID")
    if not all([sheet_id, notion_key, notion_db_id]):
        raise RuntimeError(
            "SHEET_ID, NOTION_API_KEY, and NOTION_DATABASE_ID environment variables must be set"
        )
    services = get_google_services()
    sheets_service = services["sheets"]
    drive_service = services["drive"]
    notion = NotionClient(auth=notion_key)

    last_row = get_last_synced_row()
    # Start reading from the next row after last synced
    start = last_row + 1
    rows = read_sheet_rows(sheets_service, sheet_id, worksheet_name, start_row=start)
    if not rows:
        print("No new rows to sync.")
        return
    current_row = last_row
    synced_count = 0
    for row in rows:
        current_row += 1
        # The sheet columns: [Notion URL, Description, Link, PDF Link, Category, Remark, File Date, Unnamed:7, Important]
        # Adjust indices based on your sheet
        description = row[1] if len(row) > 1 else ""
        link = row[2] if len(row) > 2 else ""
        pdf_link = row[3] if len(row) > 3 else ""
        category = row[4] if len(row) > 4 else ""
        remark = row[5] if len(row) > 5 else ""
        file_date = row[6] if len(row) > 6 else ""

        # Skip rows without description
        if not description:
            continue

        # Determine file source
        file_data = None
        file_name = None
        file_url_for_notion = None
        if pdf_link:
            file_url_for_notion = pdf_link
        elif link and link.startswith("http"):
            # Try exporting to PDF if it's a Google Docs/Slides file
            file_data = export_file_to_pdf(drive_service, link)
            if file_data:
                file_name = f"{description}.pdf"
        # Build Notion properties; adjust keys to match your database schema
        properties = {
            "File Name": {
                "title": [
                    {
                        "type": "text",
                        "text": {"content": description},
                    }
                ]
            },
        }
        if category:
            properties["Category"] = {"select": {"name": category}}
        if remark:
            properties["Remark"] = {"rich_text": [{"text": {"content": remark}}]}
        if file_date:
            properties["File Date"] = {"date": {"start": file_date}}
        # If we have a URL, store as a URL property; adjust property name as needed
        if file_url_for_notion:
            properties["Link"] = {"url": file_url_for_notion}
        # Create entry in Notion
        notion_create_entry(notion, notion_db_id, properties, file_data, file_name)
        synced_count += 1
    update_last_synced_row(current_row)
    print(f"Synced {synced_count} new records. Last synced row: {current_row}.")


if __name__ == "__main__":
    main()

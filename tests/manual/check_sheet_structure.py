"""
Check the actual structure of the Google Sheet
"""
import sys
import os

# Add project root to Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, project_root)

from app.services.google_sheets_service import GoogleSheetsService

spreadsheet_id = "1-L91pYnXCl646Oaob3vhvh_J1sBrv-Hl0SaF8CA1Ado"

sheets_service = GoogleSheetsService()
spreadsheet = sheets_service.client.open_by_key(spreadsheet_id)
worksheet = spreadsheet.worksheet('Sheet1')

all_values = worksheet.get_all_values()

print("First 15 rows of the sheet:")
print("=" * 120)
for i, row in enumerate(all_values[:15], 1):
    print(f"Row {i:2d}: {row[:6]}")  # Show first 6 columns
print("=" * 120)

"""
Quick test to check available sheet names
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

print("Available sheets in this spreadsheet:")
print("-" * 80)
for sheet in spreadsheet.worksheets():
    print(f"  - {sheet.title}")

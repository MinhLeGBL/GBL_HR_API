"""
Test to get input dictionary from Google Sheets
"""
import sys
import os
import json

# Add project root to Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, project_root)

from app.services.google_sheets_service import GoogleSheetsService

spreadsheet_id = "1-L91pYnXCl646Oaob3vhvh_J1sBrv-Hl0SaF8CA1Ado"
sheet_name = "Sheet1"  # Change this to the actual sheet name

sheets_service = GoogleSheetsService()

# Option 1: Get all sheet data (stores and employees)
print("=" * 80)
print("OPTION 1: Get All Sheet Data")
print("=" * 80)
try:
    all_data = sheets_service.get_sheet_data(spreadsheet_id, sheet_name)
    print(json.dumps(all_data, indent=2, ensure_ascii=False))
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80)
print("OPTION 2: Get Store Commission Input (formatted for API)")
print("=" * 80)

# Option 2: Get formatted input for a specific store
store_code = "RHN"  # Change this to test different stores
from_date = "2025-11-01 00:00:00"
to_date = "2025-11-30 23:59:59"

try:
    commission_input = sheets_service.get_store_commission_input(
        spreadsheet_id=spreadsheet_id,
        store_code=store_code,
        from_date=from_date,
        to_date=to_date,
        sheet_name=sheet_name
    )
    print(json.dumps(commission_input, indent=2, ensure_ascii=False))
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

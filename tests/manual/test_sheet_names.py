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

# Option 1: Get all sheet data (stores, employees, and query period)
print("=" * 80)
print("OPTION 1: Get All Sheet Data (including query period from selector)")
print("=" * 80)
try:
    all_data = sheets_service.get_sheet_data(spreadsheet_id, sheet_name)
    print(json.dumps(all_data, indent=2, ensure_ascii=False))
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80)
print("OPTION 2: Get Store Commission Input (using month/year from sheet)")
print("=" * 80)

# Option 2: Get formatted input for a specific store using sheet selector
store_code = "RHN"  # Change this to test different stores

try:
    commission_input = sheets_service.get_store_commission_input(
        spreadsheet_id=spreadsheet_id,
        store_code=store_code,
        sheet_name=sheet_name
    )
    print(json.dumps(commission_input, indent=2, ensure_ascii=False))
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

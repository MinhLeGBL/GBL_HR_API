"""
Test Google Sheets Service
"""
import sys
import os

# Add project root to Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, project_root)

from app.services.google_sheets_service import GoogleSheetsService
import json

def test_google_sheets_access(spreadsheet_id: str = None):
    """Test if we can access the Google Sheet"""

    # You'll need to provide the spreadsheet ID
    # It's the long string in your Google Sheets URL
    # https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit

    if spreadsheet_id is None:
        spreadsheet_id = input("Enter your Google Sheets spreadsheet ID: ").strip()

    if not spreadsheet_id:
        print("Error: Spreadsheet ID is required")
        return

    try:
        print("\n" + "="*80)
        print("Testing Google Sheets Access...")
        print("="*80)

        # Initialize the service
        print("\n1. Initializing Google Sheets service...")
        sheets_service = GoogleSheetsService()
        print("   [OK] Service initialized successfully")

        # Try to read the sheet data
        print("\n2. Reading sheet data...")
        sheet_data = sheets_service.get_sheet_data(spreadsheet_id, sheet_name='Sheet1')
        print("   [OK] Sheet data retrieved successfully")

        # Display store data
        print("\n3. Store Data Found:")
        print("-" * 80)
        for store in sheet_data['stores']:
            print(f"   Store Code: {store['store_code']}")
            print(f"   Target: {store['store_target']:,.0f}")
            print(f"   FP Ratio Target: {store['store_fp_ratio_target']:.2%}")
            print("-" * 80)

        # Display employee count by store
        print("\n4. Employee Data Found:")
        print("-" * 80)
        store_employee_counts = {}
        for emp in sheet_data['employees']:
            store_code = emp['store_code']
            store_employee_counts[store_code] = store_employee_counts.get(store_code, 0) + 1

        for store_code, count in store_employee_counts.items():
            print(f"   {store_code}: {count} employees")

        print(f"\n   Total employees: {len(sheet_data['employees'])}")

        # Show sample employee data
        if sheet_data['employees']:
            print("\n5. Sample Employee Data (first 3 employees):")
            print("-" * 80)
            for emp in sheet_data['employees'][:3]:
                print(f"   Store: {emp['store_code']}")
                print(f"   Code: {emp['employee_code']}")
                print(f"   Name: {emp['full_name']}")
                print(f"   Seniority: {emp['seniority']} years")
                print(f"   Personal Target: {emp['personal_target']:,.0f}")
                print(f"   Working Days: {emp['working_day_count']}")
                print(f"   Is Manager: {emp['is_manager']}")
                print(f"   Is Probation: {emp['is_probation']}")
                print("-" * 80)

        # Test the commission input formatting
        print("\n6. Testing commission input formatting for first store...")
        if sheet_data['stores']:
            first_store = sheet_data['stores'][0]
            store_code = first_store['store_code']

            commission_input = sheets_service.get_store_commission_input(
                spreadsheet_id=spreadsheet_id,
                store_code=store_code,
                from_date='2025-11-01 00:00:00',
                to_date='2025-11-30 23:59:59',
                sheet_name='Sheet1'
            )

            print(f"   [OK] Commission input formatted for store: {store_code}")
            print(f"   - Store target: {commission_input['store_target']:,.0f}")
            print(f"   - FP ratio target: {commission_input['store_fp_ratio_target']:.2%}")
            print(f"   - Date range: {commission_input['query_date']['from_date']} to {commission_input['query_date']['to_date']}")
            print(f"   - Employees: {len(commission_input['employees'])} employees")

        print("\n" + "="*80)
        print("[OK] All tests passed! Google Sheets integration is working correctly.")
        print("="*80)

    except FileNotFoundError as e:
        print(f"\n[ERROR] Error: Credentials file not found")
        print(f"   {e}")
        print(f"   Please ensure credentials.json is in the config/ directory")

    except Exception as e:
        print(f"\n[ERROR] Error: {e}")
        print(f"\nFull error details:")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    # Use spreadsheet ID from command line argument if provided
    spreadsheet_id = sys.argv[1] if len(sys.argv) > 1 else None
    test_google_sheets_access(spreadsheet_id)

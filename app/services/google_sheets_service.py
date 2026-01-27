"""
Google Sheets Service for reading commission input data
"""
import gspread
from google.oauth2.service_account import Credentials
from typing import Dict, List, Any, Optional
import os


class GoogleSheetsService:
    """Service for reading data from Google Sheets"""

    def __init__(self, credentials_file: Optional[str] = None):
        """
        Initialize Google Sheets service

        Args:
            credentials_file: Path to Google service account credentials JSON file
        """
        if credentials_file is None:
            # Try to find credentials file in config directory
            credentials_file = os.getenv('GOOGLE_CREDENTIALS_FILE')
            if credentials_file is None:
                # Check for default name first
                if os.path.exists('config/credentials.json'):
                    credentials_file = 'config/credentials.json'
                else:
                    # Look for any JSON file in config directory
                    config_dir = 'config'
                    if os.path.exists(config_dir):
                        json_files = [f for f in os.listdir(config_dir) if f.endswith('.json')]
                        if json_files:
                            credentials_file = os.path.join(config_dir, json_files[0])
                        else:
                            credentials_file = 'config/credentials.json'  # Will fail with proper error
                    else:
                        credentials_file = 'config/credentials.json'  # Will fail with proper error

        # Define the scopes
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets.readonly',
            'https://www.googleapis.com/auth/drive.readonly'
        ]

        try:
            # Authenticate with Google Sheets
            credentials = Credentials.from_service_account_file(
                credentials_file,
                scopes=scopes
            )
            self.client = gspread.authorize(credentials)
        except Exception as e:
            print(f"Failed to initialize Google Sheets client: {e}")
            self.client = None

    def get_sheet_data(self, spreadsheet_id: str, sheet_name: str = 'Sheet1') -> Dict[str, Any]:
        """
        Read data from Google Sheets

        Args:
            spreadsheet_id: Google Sheets spreadsheet ID
            sheet_name: Name of the sheet/tab to read from

        Returns:
            Dictionary containing store data and employee data
        """
        if not self.client:
            raise Exception("Google Sheets client not initialized")

        # Open the spreadsheet
        spreadsheet = self.client.open_by_key(spreadsheet_id)
        worksheet = spreadsheet.worksheet(sheet_name)

        # Get all values
        all_values = worksheet.get_all_values()

        # Parse store data (rows 3-7 in sheet = indices 2:7 in array)
        # Row 1: Title, Row 2: Headers, Rows 3-7: Data
        stores = self._parse_store_data(all_values[2:7])

        # Parse employee data (starting from row 11 in sheet = index 10: in array)
        # Row 10: Headers, Row 11+: Data
        employees = self._parse_employee_data(all_values[10:])

        return {
            'stores': stores,
            'employees': employees
        }

    def _parse_store_data(self, rows: List[List[str]]) -> List[Dict[str, Any]]:
        """
        Parse store data from sheet rows

        Args:
            rows: List of rows containing store data

        Returns:
            List of store dictionaries
        """
        stores = []
        for row in rows:
            if len(row) >= 3 and row[0]:  # Ensure row has data
                store_code = row[0].strip()

                # Parse target revenue
                target_str = row[1].replace(',', '').replace('.', '')
                store_target = float(target_str) if target_str else 0

                # Parse FP ratio target (convert percentage to decimal)
                fp_ratio_str = row[2].replace('%', '').strip()
                store_fp_ratio_target = float(fp_ratio_str) / 100 if fp_ratio_str else 0.65

                stores.append({
                    'store_code': store_code,
                    'store_target': store_target,
                    'store_fp_ratio_target': store_fp_ratio_target
                })

        return stores

    def _parse_employee_data(self, rows: List[List[str]]) -> List[Dict[str, Any]]:
        """
        Parse employee data from sheet rows

        Expected columns:
        0: No (skip)
        1: Store Code
        2: Employee Code
        3: Fullname
        4: Join year
        5: Seniority (years)
        6: is_probation (0 or 1)
        7: Contract
        8: Personal target
        9: is_manager (0 or 1)
        10: Working day count

        Args:
            rows: List of rows containing employee data

        Returns:
            List of employee dictionaries
        """
        employees = []
        for row in rows:
            if len(row) >= 11 and row[1]:  # Ensure row has store code
                store_code = row[1].strip()
                employee_code = row[2].strip()
                full_name = row[3].strip()

                # Parse seniority (years)
                seniority = int(row[5]) if row[5] and row[5].strip() else 0

                # Parse is_probation (0/1 or TRUE/FALSE to boolean)
                prob_value = row[6].strip().upper() if row[6] else ''
                if prob_value in ('TRUE', '1'):
                    is_probation = True
                elif prob_value in ('FALSE', '0', ''):
                    is_probation = False
                else:
                    is_probation = bool(int(row[6]))

                # Parse personal target
                target_str = row[8].replace(',', '').replace('.', '').strip() if len(row) > 8 else '0'
                # Handle empty, dash, or invalid values
                try:
                    personal_target = float(target_str) if target_str and target_str != '-' else 0
                except ValueError:
                    personal_target = 0

                # Parse is_manager (0/1 or TRUE/FALSE to boolean)
                manager_value = row[9].strip().upper() if len(row) > 9 and row[9] else ''
                if manager_value in ('TRUE', '1'):
                    is_manager = True
                elif manager_value in ('FALSE', '0', ''):
                    is_manager = False
                else:
                    is_manager = bool(int(row[9]))

                # Parse working day count
                working_day_count = int(row[10]) if len(row) > 10 and row[10] and row[10].strip() else 0

                employees.append({
                    'store_code': store_code,
                    'employee_code': employee_code,
                    'full_name': full_name,
                    'seniority': seniority,
                    'is_probation': is_probation,
                    'personal_target': personal_target,
                    'is_manager': is_manager,
                    'working_day_count': working_day_count
                })

        return employees

    def get_store_commission_input(
        self,
        spreadsheet_id: str,
        store_code: str,
        from_date: str,
        to_date: str,
        sheet_name: str = 'Sheet1'
    ) -> Dict[str, Any]:
        """
        Get formatted commission input data for a specific store

        Args:
            spreadsheet_id: Google Sheets spreadsheet ID
            store_code: Store code to get data for
            from_date: Start date for query period (YYYY-MM-DD HH:MI:SS)
            to_date: End date for query period (YYYY-MM-DD HH:MI:SS)
            sheet_name: Name of the sheet/tab to read from

        Returns:
            Dictionary formatted for commission API v2 endpoint
        """
        # Get all data from sheet
        sheet_data = self.get_sheet_data(spreadsheet_id, sheet_name)

        # Find the store
        store = next((s for s in sheet_data['stores'] if s['store_code'] == store_code), None)
        if not store:
            raise ValueError(f"Store {store_code} not found in sheet")

        # Filter employees for this store
        store_employees = [
            emp for emp in sheet_data['employees']
            if emp['store_code'] == store_code
        ]

        # Format for API
        return {
            'store_code': store['store_code'],
            'store_target': store['store_target'],
            'store_fp_ratio_target': store['store_fp_ratio_target'],
            'query_date': {
                'from_date': from_date,
                'to_date': to_date
            },
            'employees': [
                {
                    'employee_code': emp['employee_code'],
                    'full_name': emp['full_name'],
                    'seniority': emp['seniority'],
                    'personal_target': emp['personal_target'],
                    'working_day_count': emp['working_day_count'],
                    'is_manager': emp['is_manager'],
                    'is_probation': emp['is_probation']
                }
                for emp in store_employees
            ]
        }

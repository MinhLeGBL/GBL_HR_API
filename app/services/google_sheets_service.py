"""
Google Sheets Service for reading commission input data
"""
import gspread
from google.oauth2.service_account import Credentials
from typing import Dict, List, Any, Optional
import os
from datetime import datetime
import calendar
import pandas as pd


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
        # Note: Using full spreadsheets scope for both read and write operations
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
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

    def find_spreadsheet_by_title(self, title: str) -> str:
        """
        Find spreadsheet by title and return its ID

        Args:
            title: Title of the spreadsheet to find

        Returns:
            Spreadsheet ID

        Raises:
            Exception if spreadsheet not found
        """
        if not self.client:
            raise Exception("Google Sheets client not initialized")

        # Get all spreadsheets accessible to this service account
        spreadsheets = self.client.openall()

        # Find matching spreadsheet
        for spreadsheet in spreadsheets:
            if spreadsheet.title == title:
                return spreadsheet.id

        raise Exception(f"Spreadsheet with title '{title}' not found. Available sheets: {[s.title for s in spreadsheets]}")

    def get_sheet_data(self, spreadsheet_id: str, sheet_name: str = 'Sheet1') -> Dict[str, Any]:
        """
        Read data from Google Sheets

        Args:
            spreadsheet_id: Google Sheets spreadsheet ID
            sheet_name: Name of the sheet/tab to read from

        Returns:
            Dictionary containing store data, employee data, and query period
        """
        if not self.client:
            raise Exception("Google Sheets client not initialized")

        # Open the spreadsheet
        spreadsheet = self.client.open_by_key(spreadsheet_id)
        worksheet = spreadsheet.worksheet(sheet_name)

        # Get all values
        all_values = worksheet.get_all_values()

        # Parse month and year selector (Row 2, Col F = index [1][5] and Row 3, Col F = index [2][5])
        query_period = self._parse_query_period(all_values)

        # Parse store data (rows 3-7 in sheet = indices 2:7 in array)
        # Row 1: Title, Row 2: Headers, Rows 3-7: Data
        stores = self._parse_store_data(all_values[2:7])

        # Parse employee data (starting from row 11 in sheet = index 10: in array)
        # Row 10: Headers, Row 11+: Data
        employees = self._parse_employee_data(all_values[10:])

        return {
            'stores': stores,
            'employees': employees,
            'query_period': query_period
        }

    def _parse_query_period(self, all_values: List[List[str]]) -> Dict[str, str]:
        """
        Parse month and year from sheet selector and convert to query period

        Args:
            all_values: All sheet values

        Returns:
            Dictionary with from_date and to_date
        """
        try:
            # Month is in row 2, column H (index [1][7])
            month_str = all_values[1][7].strip()
            # Year is in row 3, column H (index [2][7])
            year_str = all_values[2][7].strip()

            # Convert month name to number
            month_map = {
                'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
                'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12,
                'January': 1, 'February': 2, 'March': 3, 'April': 4, 'May': 5, 'June': 6,
                'July': 7, 'August': 8, 'September': 9, 'October': 10, 'November': 11, 'December': 12
            }

            month = month_map.get(month_str, 1)  # Default to January if not found
            year = int(year_str) if year_str else datetime.now().year

            # Get the last day of the month
            last_day = calendar.monthrange(year, month)[1]

            # Format dates
            from_date = f"{year:04d}-{month:02d}-01 00:00:00"
            to_date = f"{year:04d}-{month:02d}-{last_day:02d} 23:59:59"

            return {
                'from_date': from_date,
                'to_date': to_date,
                'month': month_str,
                'year': year
            }
        except (IndexError, ValueError) as e:
            # Return default values if parsing fails
            now = datetime.now()
            year = now.year
            month = now.month
            last_day = calendar.monthrange(year, month)[1]
            return {
                'from_date': f"{year:04d}-{month:02d}-01 00:00:00",
                'to_date': f"{year:04d}-{month:02d}-{last_day:02d} 23:59:59",
                'month': now.strftime('%b'),
                'year': year
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

        Expected columns (updated with Employee Acc inserted at column 2):
        0: No (skip)
        1: Store Code
        2: Employee Acc (username) - MAPS TO EMPLOYEE.USER_NAME
        3: Employee Code
        4: Fullname
        5: Join year
        6: Seniority (years)
        7: is_probation (0 or 1)
        8: Contract
        9: Personal target
        10: is_manager (0 or 1)
        11: Working day count

        Args:
            rows: List of rows containing employee data

        Returns:
            List of employee dictionaries
        """
        employees = []
        for row in rows:
            if len(row) >= 11 and row[1]:  # Ensure row has store code
                store_code = row[1].strip()

                # Parse employee username (Employee Acc) - NOW AT COLUMN 2
                # This maps to EMPLOYEE.USER_NAME in Oracle database
                # Some employees may not have username (can't sell but get store commission share)
                employee_username = row[2].strip() if len(row) > 2 and row[2] else None

                employee_code = row[3].strip() if len(row) > 3 else ''
                full_name = row[4].strip() if len(row) > 4 else ''

                # Parse seniority (years)
                # Handle cases where seniority might be a date or non-integer value
                try:
                    seniority = int(row[6]) if len(row) > 6 and row[6] and row[6].strip() else 0
                except (ValueError, AttributeError):
                    seniority = 0

                # Parse is_probation (0/1 or TRUE/FALSE to boolean)
                prob_value = row[7].strip().upper() if len(row) > 7 and row[7] else ''
                if prob_value in ('TRUE', '1'):
                    is_probation = True
                elif prob_value in ('FALSE', '0', '', '-'):
                    is_probation = False
                else:
                    try:
                        is_probation = bool(int(row[7]))
                    except (ValueError, AttributeError):
                        is_probation = False

                # Parse personal target
                target_str = row[9].replace(',', '').replace('.', '').strip() if len(row) > 9 else '0'
                # Handle empty, dash, or invalid values
                try:
                    personal_target = float(target_str) if target_str and target_str != '-' else 0
                except ValueError:
                    personal_target = 0

                # Parse is_manager (0/1 or TRUE/FALSE to boolean)
                manager_value = row[10].strip().upper() if len(row) > 10 and row[10] else ''
                if manager_value in ('TRUE', '1'):
                    is_manager = True
                elif manager_value in ('FALSE', '0', '', '-'):
                    is_manager = False
                else:
                    try:
                        is_manager = bool(int(row[10]))
                    except (ValueError, AttributeError):
                        is_manager = False

                # Parse working day count
                try:
                    working_day_count = int(row[11]) if len(row) > 11 and row[11] and row[11].strip() else 0
                except (ValueError, AttributeError):
                    working_day_count = 0

                employees.append({
                    'store_code': store_code,
                    'employee_code': employee_code,
                    'employee_username': employee_username,
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
        sheet_name: str = 'Sheet1'
    ) -> Dict[str, Any]:
        """
        Get formatted commission input data for a specific store

        Args:
            spreadsheet_id: Google Sheets spreadsheet ID
            store_code: Store code to get data for
            sheet_name: Name of the sheet/tab to read from

        Returns:
            Dictionary formatted for commission API v2 endpoint
        """
        # Get all data from sheet
        sheet_data = self.get_sheet_data(spreadsheet_id, sheet_name)

        # Use query period from sheet selector
        from_date = sheet_data['query_period']['from_date']
        to_date = sheet_data['query_period']['to_date']

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

    def clear_output_ranges(self, spreadsheet_id: str, sheet_name: str = 'Sheet1') -> None:
        """
        Clear output value ranges in the Google Sheet (preserves formatting)

        Clears the following ranges:
        - D3:E7: Store output data (revenue, achievement, etc.)
        - N8: Summary cell
        - M11:Z200: Employee commission output data

        Args:
            spreadsheet_id: Google Sheets spreadsheet ID
            sheet_name: Name of the sheet/tab to clear ranges from
        """
        if not self.client:
            raise Exception("Google Sheets client not initialized")

        # Open the spreadsheet
        spreadsheet = self.client.open_by_key(spreadsheet_id)
        worksheet = spreadsheet.worksheet(sheet_name)

        # Define ranges to clear with their dimensions
        # D3:E7 = 5 rows x 2 columns
        # N8 = 1 row x 1 column
        # M11:Z200 = 190 rows x 14 columns

        ranges_data = [
            {
                'range': 'D3:E7',
                'values': [['' for _ in range(2)] for _ in range(5)]
            },
            {
                'range': 'N8',
                'values': [['']]
            },
            {
                'range': 'M11:Z200',
                'values': [['' for _ in range(14)] for _ in range(190)]
            }
        ]

        # Batch update all ranges with empty values (preserves formatting)
        worksheet.batch_update(ranges_data)

    def upload_commission_results(
        self,
        spreadsheet_id: str,
        store_commission_results: List[Dict[str, Any]],
        combined_commission_df: pd.DataFrame,
        sheet_name: str = 'Sheet1'
    ) -> None:
        """
        Upload commission calculation results to Google Sheets

        Args:
            spreadsheet_id: Google Sheets spreadsheet ID
            store_commission_results: List of dictionaries with store-level results, each containing:
                - store_code: str
                - achievement_pct: float
                - actual_fp_ratio: float
            combined_commission_df: DataFrame with employee commission data containing columns:
                - employee_code, store_code, store_achievement_pct,
                - store_commission_70pct, store_commission_30pct, manager_bonus, total_store_commission,
                - personal_commission_fp_under_100, personal_commission_discount_under_100,
                - personal_commission_over_100, personal_commission_jewelry,
                - personal_commission_vhernier, personal_commission_rosa_maria,
                - personal_commission_suitcase, personal_commission_hand_carry,
                - personal_commission_total, total_handout_commission
            sheet_name: Name of the sheet/tab to update
        """
        if not self.client:
            raise Exception("Google Sheets client not initialized")

        # Open the spreadsheet
        spreadsheet = self.client.open_by_key(spreadsheet_id)
        worksheet = spreadsheet.worksheet(sheet_name)

        # Get current sheet data to match store codes and employee codes
        all_values = worksheet.get_all_values()

        # ==================== UPDATE STORE DATA (Rows 3-7) ====================
        # Column A contains store codes, Column D for achievement %, Column E for FP ratio %
        store_updates = []

        for row_idx in range(2, 7):  # Rows 3-7 (indices 2-6)
            if row_idx < len(all_values):
                row = all_values[row_idx]
                if len(row) > 0 and row[0]:  # Column A has store code
                    sheet_store_code = row[0].strip()

                    # Find matching store in results list
                    matching_store = next(
                        (store for store in store_commission_results if store.get('store_code') == sheet_store_code),
                        None
                    )

                    if matching_store:
                        # Update achievement % (Column D)
                        achievement_pct = matching_store.get('achievement_pct', 0)
                        store_updates.append({
                            'range': f'D{row_idx + 1}',
                            'values': [[achievement_pct / 100]]  # Convert to decimal for percentage format
                        })

                        # Update FP ratio % (Column E)
                        actual_fp_ratio = matching_store.get('actual_fp_ratio', 0)
                        store_updates.append({
                            'range': f'E{row_idx + 1}',
                            'values': [[actual_fp_ratio]]  # Already in decimal format
                        })

        # ==================== UPDATE EMPLOYEE DATA (Row 11 downward) ====================
        # Column D contains employee codes for matching
        employee_updates = []

        for row_idx in range(10, len(all_values)):  # Starting from row 11 (index 10)
            row = all_values[row_idx]
            if len(row) > 3 and row[3]:  # Column D (index 3) has employee code
                sheet_employee_code = row[3].strip()

                # Find matching employee in DataFrame
                matching_rows = combined_commission_df[
                    combined_commission_df['employee_code'] == sheet_employee_code
                ]

                if len(matching_rows) > 0:
                    emp_data = matching_rows.iloc[0]
                    sheet_row = row_idx + 1  # Convert to 1-indexed row number

                    # Prepare employee commission data
                    # Convert numpy types to Python native types for JSON serialization
                    employee_updates.append({
                        'range': f'M{sheet_row}:Z{sheet_row}',
                        'values': [[
                            float(emp_data['store_commission_70pct']),          # M: Individual share
                            float(emp_data['store_commission_30pct']),          # N: Equal share
                            float(emp_data['manager_bonus']),                   # O: Manager bonus
                            float(emp_data['total_store_commission']),          # P: Total store commission
                            float(emp_data['personal_commission_fp_under_100']), # Q: FP commission ≤100%
                            float(emp_data['personal_commission_discount_under_100']), # R: Discount commission ≤100%
                            float(emp_data['personal_commission_over_100']),    # S: Over 100% bonus
                            float(emp_data['personal_commission_jewelry']),     # T: Jewelry commission
                            float(emp_data['personal_commission_vhernier']),    # U: Vhernier commission
                            float(emp_data['personal_commission_rosa_maria']),  # V: Rosa Maria commission
                            float(emp_data['personal_commission_suitcase']),    # W: Suitcase commission
                            float(emp_data['personal_commission_hand_carry']),  # X: Hand carry commission
                            float(emp_data['personal_commission_total']),       # Y: Total personal commission
                            float(emp_data['total_handout_commission'])         # Z: Total commission
                        ]]
                    })

        # ==================== UPDATE TOTAL COMMISSION SUMMARY (N8) ====================
        total_commission_handout = float(combined_commission_df['total_handout_commission'].sum())
        summary_update = [{
            'range': 'N8',
            'values': [[total_commission_handout]]
        }]

        # ==================== BATCH UPDATE ALL DATA ====================
        all_updates = store_updates + employee_updates + summary_update

        if all_updates:
            worksheet.batch_update(all_updates)

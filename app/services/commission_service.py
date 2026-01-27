"""
Commission Service - Business logic for store commission calculations
Based on the pseudocode algorithm with 4 main steps
"""
from typing import Dict, List, Any, Optional
import pandas as pd


class CommissionService:
    """Business logic layer for commission calculations"""

    def __init__(self, repository=None):
        """
        Initialize commission service

        Args:
            repository: Commission repository instance (injected for testability)
        """
        if repository is None:
            from app.repositories.commission_repository import CommissionRepository
            self.repository = CommissionRepository()
        else:
            self.repository = repository

    def calculate_store_commission(
        self,
        store_code: str,
        store_name: str,
        target_revenue: float,
        target_fp_ratio: float,
        year: int,
        month: int,
        start_date: str,
        end_date: str
    ) -> Dict[str, Any]:
        """
        Calculate commission for all employees in a store

        Args:
            store_code: Store code
            store_name: Store name
            target_revenue: Target revenue for the period
            target_fp_ratio: Target full price ratio (0.0 to 1.0)
            year: Year for the commission period
            month: Month for the commission period
            start_date: Period start date in 'YYYY-MM-DD HH:MI:SS' format
            end_date: Period end date in 'YYYY-MM-DD HH:MI:SS' format

        Returns:
            Dictionary containing commission results and eligibility status
        """
        # Get store sales data
        store_data = self.repository.get_store_sales_data(store_code, start_date, end_date)

        # Create targets dictionary from provided values
        targets = {
            'TARGET_REVENUE': target_revenue,
            'TARGET_FP_RATIO': target_fp_ratio
        }

        # Get employee sales data
        employee_sales = self.repository.get_employee_sales_data(store_code, start_date, end_date)

        # Get employee information (tenure, position)
        employee_info = self.repository.get_employee_info(store_code)

        # STEP 1: Check store eligibility and calculate achievement
        eligibility_result = self._check_store_eligibility(store_data, targets)

        if not eligibility_result['eligible']:
            return {
                'store_code': store_code,
                'store_name': store_name,
                'eligible': False,
                'reason': eligibility_result['reason'],
                'achievement_pct': eligibility_result.get('achievement_pct', 0),
                'target_revenue': target_revenue,
                'target_fp_ratio': target_fp_ratio,
                'employees': []
            }

        achievement_pct = eligibility_result['achievement_pct']

        # STEP 2: Calculate each employee's contribution to store pool
        employee_contributions = self._calculate_employee_contributions(
            employee_sales,
            employee_info,
            achievement_pct
        )

        # STEP 3: Calculate total store pool
        store_pool = sum(emp['contribution'] for emp in employee_contributions)

        # STEP 4: Distribute store pool to each employee
        employee_commissions = self._distribute_store_pool(
            employee_contributions,
            store_pool,
            achievement_pct
        )

        return {
            'store_code': store_code,
            'store_name': store_name,
            'eligible': True,
            'achievement_pct': achievement_pct,
            'target_revenue': target_revenue,
            'target_fp_ratio': target_fp_ratio,
            'store_pool': store_pool,
            'employee_count': len(employee_commissions),
            'employees': employee_commissions,
            'period': {
                'start_date': start_date,
                'end_date': end_date,
                'year': year,
                'month': month
            }
        }

    def get_commission_dataframe(
        self,
        store_code: str,
        store_name: str,
        target_revenue: float,
        target_fp_ratio: float,
        year: int,
        month: int,
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        """
        Get commission data as a pandas DataFrame

        Args:
            store_code: Store code
            store_name: Store name
            target_revenue: Target revenue for the period
            target_fp_ratio: Target full price ratio (0.0 to 1.0)
            year: Year for the commission period
            month: Month for the commission period
            start_date: Period start date
            end_date: Period end date

        Returns:
            DataFrame containing employee commission details
        """
        result = self.calculate_store_commission(
            store_code, store_name, target_revenue, target_fp_ratio,
            year, month, start_date, end_date
        )

        if not result['eligible']:
            # Return empty DataFrame with message
            return pd.DataFrame({
                'store_code': [store_code],
                'status': ['NOT ELIGIBLE'],
                'reason': [result.get('reason', 'Unknown')],
                'achievement_pct': [result.get('achievement_pct', 0)]
            })

        # Create DataFrame from employee commission data
        df = pd.DataFrame(result['employees'])

        # Add store-level information
        df['store_code'] = store_code
        df['achievement_pct'] = result['achievement_pct']
        df['store_pool'] = result['store_pool']
        df['period_year'] = year
        df['period_month'] = month

        # Reorder columns for better readability
        column_order = [
            'store_code',
            'employee_name',
            'is_manager',
            'tenure_months',
            'fp_revenue',
            'discounted_revenue',
            'contribution',
            'commission_70pct',
            'commission_30pct',
            'manager_bonus',
            'total_commission',
            'achievement_pct',
            'store_pool',
            'period_year',
            'period_month'
        ]

        # Only include columns that exist
        existing_columns = [col for col in column_order if col in df.columns]
        df = df[existing_columns]

        return df

    def _check_store_eligibility(
        self,
        store_data: Dict[str, Any],
        targets: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        STEP 1: Check store eligibility and calculate achievement percentage

        Args:
            store_data: Store sales data
            targets: Store targets

        Returns:
            Dictionary with eligibility status and achievement percentage
        """
        if not store_data or not targets:
            return {
                'eligible': False,
                'reason': 'Missing store data or targets',
                'achievement_pct': 0
            }

        actual_revenue = store_data.get('ACTUAL_REVENUE', 0)
        actual_fp_revenue = store_data.get('ACTUAL_FULL_PRICE_REVENUE', 0)
        actual_discounted_revenue = store_data.get('ACTUAL_DISCOUNTED_REVENUE', 0)
        target_revenue = targets.get('TARGET_REVENUE', 0)
        target_fp_ratio = targets.get('TARGET_FP_RATIO', 0.0)

        if target_revenue == 0:
            return {
                'eligible': False,
                'reason': 'Target revenue is zero',
                'achievement_pct': 0
            }

        # Calculate achievement percentage
        achievement_pct = (actual_revenue / target_revenue) * 100

        # Check 70% full price revenue requirement
        if actual_fp_revenue < (target_revenue * 0.70):
            return {
                'eligible': False,
                'reason': f'Full price revenue ({actual_fp_revenue:,.0f}) < 70% of target ({target_revenue * 0.70:,.0f})',
                'achievement_pct': achievement_pct
            }

        # Calculate actual FP ratio
        actual_fp_ratio = actual_fp_revenue / actual_revenue if actual_revenue > 0 else 0

        # Check if actual FP ratio meets target
        if actual_fp_ratio < target_fp_ratio:
            # Calculate discounted goods compensation requirement
            fp_shortage = (target_fp_ratio - actual_fp_ratio) * actual_revenue
            required_discounted = fp_shortage * 2.0  # 200% compensation

            if actual_discounted_revenue < required_discounted:
                return {
                    'eligible': False,
                    'reason': f'Discounted revenue ({actual_discounted_revenue:,.0f}) < required compensation ({required_discounted:,.0f})',
                    'achievement_pct': achievement_pct
                }

        return {
            'eligible': True,
            'achievement_pct': achievement_pct
        }

    def _calculate_employee_contributions(
        self,
        employee_sales: List[Dict[str, Any]],
        employee_info: List[Dict[str, Any]],
        achievement_pct: float
    ) -> List[Dict[str, Any]]:
        """
        STEP 2: Calculate each employee's contribution to store pool

        Args:
            employee_sales: List of employee sales data
            employee_info: List of employee information (tenure, position)
            achievement_pct: Store achievement percentage

        Returns:
            List of employee contribution data
        """
        # Create employee info lookup
        employee_lookup = {
            emp['FULL_NAME']: emp for emp in employee_info
        }

        contributions = []

        for emp_sale in employee_sales:
            emp_name = emp_sale.get('EMPLOYEE_FULL_NAME', emp_sale.get('EMPLOYEE_NAME', 'UNKNOWN'))
            fp_revenue = emp_sale.get('EMPLOYEE_FP_REVENUE', 0)
            discounted_revenue = emp_sale.get('EMPLOYEE_DISCOUNTED_REVENUE', 0)

            # Get employee info
            emp_info = employee_lookup.get(emp_name, {})
            tenure_months = emp_info.get('TENURE_MONTHS', 0)
            position = emp_info.get('POSITION', '')
            is_manager = 'MANAGER' in position.upper() or 'QUẢN LÝ' in position.upper()

            # Determine tier rates based on tenure and achievement
            tier_rates = self._get_tier_rates(tenure_months, achievement_pct)

            # Allocate FP revenue to tiers based on achievement_pct
            fp_by_tier = self._allocate_revenue_to_tiers(fp_revenue, achievement_pct)

            # Calculate commission from FP revenue
            fp_commission = sum(
                fp_by_tier[tier] * rate for tier, rate in tier_rates.items()
            )

            # Add discounted revenue commission (0.25% for 70-80% tier only)
            discounted_commission = 0
            if 70 <= achievement_pct < 80:
                discounted_commission = discounted_revenue * 0.0025

            total_contribution = fp_commission + discounted_commission

            contributions.append({
                'employee_name': emp_name,
                'tenure_months': tenure_months,
                'is_manager': is_manager,
                'fp_revenue': fp_revenue,
                'discounted_revenue': discounted_revenue,
                'fp_commission': fp_commission,
                'discounted_commission': discounted_commission,
                'contribution': total_contribution
            })

        return contributions

    def _get_tier_rates(self, tenure_months: int, achievement_pct: float) -> Dict[str, float]:
        """
        Get commission tier rates based on employee tenure and store achievement

        Args:
            tenure_months: Employee tenure in months
            achievement_pct: Store achievement percentage

        Returns:
            Dictionary mapping tier names to commission rates
        """
        # Tenure-based base rates (as percentages, convert to decimal)
        if tenure_months < 6:
            # New employee (< 6 months)
            base_rates = {
                '70-80': 0.0025,   # 0.25%
                '80-90': 0.0035,   # 0.35%
                '90-100': 0.0045,  # 0.45%
                '100+': 0.0055     # 0.55%
            }
        else:
            # Experienced employee (>= 6 months)
            base_rates = {
                '70-80': 0.0030,   # 0.30%
                '80-90': 0.0040,   # 0.40%
                '90-100': 0.0050,  # 0.50%
                '100+': 0.0060     # 0.60%
            }

        return base_rates

    def _allocate_revenue_to_tiers(
        self,
        total_revenue: float,
        achievement_pct: float
    ) -> Dict[str, float]:
        """
        Allocate employee revenue to tiers based on store achievement percentage

        Args:
            total_revenue: Employee's total FP revenue
            achievement_pct: Store achievement percentage

        Returns:
            Dictionary mapping tier names to revenue amounts
        """
        allocation = {
            '70-80': 0,
            '80-90': 0,
            '90-100': 0,
            '100+': 0
        }

        if achievement_pct < 70:
            # No commission if below 70%
            return allocation

        # Calculate revenue at each tier boundary
        # Assuming tier boundaries are based on achievement percentage ranges
        if achievement_pct >= 100:
            allocation['70-80'] = total_revenue * 0.10  # 10% at lowest tier
            allocation['80-90'] = total_revenue * 0.10  # 10% at second tier
            allocation['90-100'] = total_revenue * 0.10  # 10% at third tier
            allocation['100+'] = total_revenue * 0.70    # 70% at highest tier
        elif achievement_pct >= 90:
            allocation['70-80'] = total_revenue * 0.10
            allocation['80-90'] = total_revenue * 0.10
            allocation['90-100'] = total_revenue * 0.80
        elif achievement_pct >= 80:
            allocation['70-80'] = total_revenue * 0.10
            allocation['80-90'] = total_revenue * 0.90
        else:  # 70-80%
            allocation['70-80'] = total_revenue

        return allocation

    def _distribute_store_pool(
        self,
        employee_contributions: List[Dict[str, Any]],
        store_pool: float,
        achievement_pct: float
    ) -> List[Dict[str, Any]]:
        """
        STEP 4: Distribute store pool to each employee

        Args:
            employee_contributions: List of employee contributions
            store_pool: Total store commission pool
            achievement_pct: Store achievement percentage

        Returns:
            List of employee commission details
        """
        employee_count = len(employee_contributions)

        if employee_count == 0 or store_pool == 0:
            return []

        results = []

        for emp in employee_contributions:
            # Part A: 70% of pool based on contribution ratio
            contribution_ratio = emp['contribution'] / store_pool if store_pool > 0 else 0
            commission_70pct = 0.70 * store_pool * contribution_ratio

            # Part B: 30% of pool distributed equally
            commission_30pct = 0.30 * store_pool / employee_count

            # Personal commission
            personal_commission = commission_70pct + commission_30pct

            # Manager bonus (if achievement >= 100%)
            manager_bonus = 0
            if emp['is_manager'] and achievement_pct >= 100:
                manager_bonus = 3_000_000

            # Total commission
            total_commission = personal_commission + manager_bonus

            results.append({
                'employee_name': emp['employee_name'],
                'tenure_months': emp['tenure_months'],
                'is_manager': emp['is_manager'],
                'fp_revenue': emp['fp_revenue'],
                'discounted_revenue': emp['discounted_revenue'],
                'contribution': emp['contribution'],
                'commission_70pct': commission_70pct,
                'commission_30pct': commission_30pct,
                'manager_bonus': manager_bonus,
                'total_commission': total_commission
            })

        return results

    def calculate_personal_commissions(
        self,
        month: int,
        year: int,
        employees: List[Dict[str, Any]],
        store_code: str = None
    ) -> pd.DataFrame:
        """
        Calculate personal commissions for all employees based on the pseudocode algorithm

        Args:
            month: Month for commission calculation (1-12)
            year: Year for commission calculation
            employees: List of employee dictionaries with structure:
                {
                    'employee_code': str (HR code, not employee_id),
                    'personal_target': float,
                    'full_name': str (optional),
                    'department': str (optional)
                }
            store_code: Optional store code filter

        Returns:
            pandas DataFrame with columns:
            - employee_code
            - fullname
            - store_code
            - commission_fp
            - commission_discount
            - commission_jewelry
            - commission_vhernier
            - commission_rosa_maria
            - commission_100_and_below_fp
            - commission_100_and_below_discount
            - commission_over_100
            - total
        """
        # 2. VALIDATE INPUT
        if month is None or year is None or employees is None or len(employees) == 0:
            return pd.DataFrame({
                'error': ['Missing required fields: month, year, or employees list']
            })

        # 3. GET ALL SALES DATA AS PANDAS DATAFRAME (SINGLE QUERY)
        # Returns DataFrame with columns: sale_id, upc, employee_code, bill_number, store_code,
        # sale_date, sale_time, revenue_with_vat, revenue_before_vat, discount_rate,
        # is_jewelry, vendor_code, category, department
        all_sales_df = self.repository.get_personal_commission_sales_data(year, month)

        # Convert column names to lowercase for easier access
        all_sales_df.columns = all_sales_df.columns.str.lower()

        # 4. PREPARE RESULTS LIST
        results = []

        # 5. PROCESS EACH EMPLOYEE
        for employee_data in employees:
            employee_code = employee_data.get('employee_code')
            target = employee_data.get('personal_target')
            employee_name = employee_data.get('full_name', '')
            emp_store_code = store_code or employee_data.get('store_code', '')

            # Validate employee data
            if employee_code is None or target is None:
                results.append({
                    'employee_code': employee_code,
                    'fullname': employee_name,
                    'store_code': emp_store_code,
                    'commission_fp': 0,
                    'commission_discount': 0,
                    'commission_jewelry': 0,
                    'commission_vhernier': 0,
                    'commission_rosa_maria': 0,
                    'commission_100_and_below_fp': 0,
                    'commission_100_and_below_discount': 0,
                    'commission_over_100': 0,
                    'total': 0
                })
                continue

            # Filter ALL sales for this specific employee (by HR code)
            # Includes ALL items: jewelry + non-jewelry, all stores, all vendors
            employee_sales_df = all_sales_df[all_sales_df['employee_code'] == employee_code].copy()

            # Check if employee has any sales at all
            if len(employee_sales_df) == 0:
                results.append({
                    'employee_code': employee_code,
                    'fullname': employee_name,
                    'store_code': emp_store_code,
                    'commission_fp': 0,
                    'commission_discount': 0,
                    'commission_jewelry': 0,
                    'commission_vhernier': 0,
                    'commission_rosa_maria': 0,
                    'commission_100_and_below_fp': 0,
                    'commission_100_and_below_discount': 0,
                    'commission_over_100': 0,
                    'total': 0
                })
                continue

            # Calculate TOTAL revenue WITH VAT from ALL sources for eligibility check
            # (includes jewelry + non-jewelry, all stores, all vendors)
            total_revenue_with_vat = employee_sales_df['revenue_with_vat'].sum()

            # Calculate achievement rate based on TOTAL revenue WITH VAT from ALL sources
            achievement_rate = (total_revenue_with_vat / target) * 100 if target > 0 else 0

            # SEPARATE SALES BY TYPE for commission calculation
            # Non-jewelry sales
            non_jewelry_df = employee_sales_df[employee_sales_df['is_jewelry'] == 0].copy()

            # Jewelry sales
            jewelry_df = employee_sales_df[employee_sales_df['is_jewelry'] == 1].copy()

            # Calculate non-jewelry revenue breakdown
            # IMPORTANT: Use revenue_before_vat (non-VAT) for commission calculations
            non_jewelry_fp_df = non_jewelry_df[non_jewelry_df['discount_rate'] <= 0.30]
            non_jewelry_fp_revenue = non_jewelry_fp_df['revenue_before_vat'].sum()

            non_jewelry_disc_df = non_jewelry_df[non_jewelry_df['discount_rate'] > 0.30]
            non_jewelry_disc_revenue = non_jewelry_disc_df['revenue_before_vat'].sum()

            # GROUP BY BILL to calculate bill-level totals (for >100% tier calculation)
            # A bill can contain multiple items, and we need to check at BILL level when target is reached
            bill_totals_df = employee_sales_df.groupby('bill_number', sort=False).agg({
                'revenue_with_vat': 'sum',
                'sale_date': 'first',
                'sale_time': 'first'
            }).reset_index()

            # Sort bills chronologically
            bill_totals_df = bill_totals_df.sort_values(['sale_date', 'sale_time'])

            # Calculate running total by BILL (WITH VAT) to find when 100% target was reached
            bill_totals_df['running_total'] = bill_totals_df['revenue_with_vat'].cumsum()

            # Find full-price NON-JEWELRY sales after reaching 100% target (for >100% tier bonus)
            fp_non_jewelry_after_target = 0

            if achievement_rate > 100:
                # Find the BILL where target was first reached or exceeded
                target_reached_bills = bill_totals_df[bill_totals_df['running_total'] >= target]

                if len(target_reached_bills) > 0:
                    # Get the first bill that reached/exceeded target
                    target_bill_number = target_reached_bills.iloc[0]['bill_number']
                    target_bill_running_total = target_reached_bills.iloc[0]['running_total']
                    target_bill_revenue = target_reached_bills.iloc[0]['revenue_with_vat']

                    previous_total = target_bill_running_total - target_bill_revenue

                    # Get ALL items from the target-reaching bill
                    target_bill_items = employee_sales_df[employee_sales_df['bill_number'] == target_bill_number]

                    # Filter items from target bill: FP, non-jewelry, NOT TIT/TVL
                    target_bill_qualifying = target_bill_items[
                        (target_bill_items['discount_rate'] <= 0.30) &
                        (target_bill_items['is_jewelry'] == 0) &
                        (~target_bill_items['vendor_code'].isin(['TIT', 'TVL']))
                    ]

                    # If this bill pushed over target, apply 2% to ALL qualifying items in this bill
                    if previous_total < target:
                        fp_non_jewelry_after_target = target_bill_qualifying['revenue_before_vat'].sum()

                    # Get all bills AFTER the target-reaching bill
                    target_bill_index = bill_totals_df[bill_totals_df['bill_number'] == target_bill_number].index[0]
                    bills_after_target = bill_totals_df.iloc[target_bill_index + 1:]

                    # For each bill after target, find qualifying items
                    if len(bills_after_target) > 0:
                        bills_after_target_numbers = bills_after_target['bill_number'].tolist()

                        # Get all items from bills that came after target
                        items_after_target = employee_sales_df[
                            employee_sales_df['bill_number'].isin(bills_after_target_numbers)
                        ]

                        # Filter for: full-price, non-jewelry, NOT TIT/TVL
                        fp_non_jewelry_after_df = items_after_target[
                            (items_after_target['discount_rate'] <= 0.30) &
                            (items_after_target['is_jewelry'] == 0) &
                            (~items_after_target['vendor_code'].isin(['TIT', 'TVL']))
                        ]

                        # Add to fp_non_jewelry_after_target (use revenue_before_vat)
                        fp_non_jewelry_after_target = fp_non_jewelry_after_target + fp_non_jewelry_after_df['revenue_before_vat'].sum()

            # FINE JEWELRY COMMISSION (Completely independent of personal commission)
            # IMPORTANT: All jewelry commission calculations use revenue_before_vat (non-VAT revenue)
            # CRITICAL: Jewelry commission is paid REGARDLESS of achievement rate
            jewelry_commission = 0
            commission_rosa_maria = 0  # Track ROM EARRINGS commission separately
            commission_vhernier = 0  # Track VHN commission separately

            if len(jewelry_df) > 0:
                # Calculate jewelry commission by vendor and category
                # Rates: TVL/TIT = 500k per item, ROM EARRINGS = 3%, VHN = 1%, Others = 2%

                # 1. TVL and TIT vendors: Flat 500,000 VND per item (handle first to exclude from % calcs)
                tvl_tit_df = jewelry_df[
                    (jewelry_df['vendor_code'] == 'TVL') |
                    (jewelry_df['vendor_code'] == 'TIT')
                ]
                if len(tvl_tit_df) > 0:
                    tvl_tit_item_count = len(tvl_tit_df)
                    tvl_tit_commission = tvl_tit_item_count * 500000
                    jewelry_commission = jewelry_commission + tvl_tit_commission

                # Filter out TVL/TIT for percentage-based calculations
                jewelry_percent_df = jewelry_df[
                    ~jewelry_df['vendor_code'].isin(['TVL', 'TIT'])
                ]

                if len(jewelry_percent_df) > 0:
                    # 2. ROM EARRINGS: 3% on revenue (highest priority)
                    rom_earrings_df = jewelry_percent_df[
                        (jewelry_percent_df['vendor_code'] == 'ROM') &
                        (jewelry_percent_df['category'] == 'EARRINGS')
                    ]
                    if len(rom_earrings_df) > 0:
                        rom_earrings_revenue = rom_earrings_df['revenue_before_vat'].sum()
                        rom_earrings_commission = rom_earrings_revenue * 0.03
                        commission_rosa_maria = rom_earrings_commission  # Track separately
                        jewelry_commission = jewelry_commission + rom_earrings_commission

                    # 3. VHN vendor: 1% on revenue (excluding ROM EARRINGS already counted)
                    vhn_df = jewelry_percent_df[
                        (jewelry_percent_df['vendor_code'] == 'VHN') &
                        ~((jewelry_percent_df['vendor_code'] == 'ROM') &
                          (jewelry_percent_df['category'] == 'EARRINGS'))
                    ]
                    if len(vhn_df) > 0:
                        vhn_revenue = vhn_df['revenue_before_vat'].sum()
                        vhn_commission = vhn_revenue * 0.01
                        commission_vhernier = vhn_commission  # Track separately
                        jewelry_commission = jewelry_commission + vhn_commission

                    # 4. Other vendors (not VHN, not ROM EARRINGS, not TVL/TIT): 2% on revenue
                    other_vendors_df = jewelry_percent_df[
                        (jewelry_percent_df['vendor_code'] != 'VHN') &
                        ~((jewelry_percent_df['vendor_code'] == 'ROM') &
                          (jewelry_percent_df['category'] == 'EARRINGS'))
                    ]
                    if len(other_vendors_df) > 0:
                        other_vendors_revenue = other_vendors_df['revenue_before_vat'].sum()
                        other_vendors_commission = other_vendors_revenue * 0.02
                        jewelry_commission = jewelry_commission + other_vendors_commission

            # PERSONAL COMMISSION (Non-Jewelry) - Calculate based on NON-CUMULATIVE tiers
            # IMPORTANT: All commission calculations use revenue_before_vat (non-VAT revenue)
            # NOTE: Personal commission requires achievement_rate >= 50%
            personal_commission = 0
            commission_fp = 0  # Track FP commission separately
            commission_discount = 0  # Track discount commission separately
            commission_over_100 = 0  # Track over 100% bonus separately

            # TIER 1: 50% - 70% of target
            if achievement_rate >= 50 and achievement_rate < 70:
                # Calculate commission on non-VAT revenue
                commission_fp = non_jewelry_fp_revenue * 0.0025
                commission_discount = non_jewelry_disc_revenue * 0.00125
                personal_commission = commission_fp + commission_discount

            # TIER 2: 70% - 100% of target
            elif achievement_rate >= 70 and achievement_rate < 100:
                # Calculate commission on non-VAT revenue
                commission_fp = non_jewelry_fp_revenue * 0.005
                commission_discount = non_jewelry_disc_revenue * 0.0025
                personal_commission = commission_fp + commission_discount

            # TIER 3: 100% of target and above
            elif achievement_rate >= 100:
                # Base commission at 100% tier rates (on ALL non-jewelry sales)
                # Calculate commission on non-VAT revenue
                commission_fp = non_jewelry_fp_revenue * 0.01
                commission_discount = non_jewelry_disc_revenue * 0.005
                commission_up_to_target = commission_fp + commission_discount
                personal_commission = commission_up_to_target

                # TIER 4: Over 100% - Additional 2% bonus
                # Only on FP, non-jewelry, NOT TIT/TVL sales that occurred AFTER reaching 100% target
                # Calculate commission on non-VAT revenue
                if achievement_rate > 100 and fp_non_jewelry_after_target > 0:
                    commission_over_100 = fp_non_jewelry_after_target * 0.02

            # Calculate commission components
            # commission_100_and_below broken into: FP, discount, and jewelry
            # commission_over_100: Tier 4 bonus only
            commission_100_and_below_fp = commission_fp
            commission_100_and_below_discount = commission_discount
            commission_100_and_below = personal_commission + jewelry_commission
            total_commission = commission_100_and_below + commission_over_100

            # Add result for this employee (for DataFrame row)
            results.append({
                'employee_code': employee_code,
                'fullname': employee_name,
                'store_code': emp_store_code,
                'commission_fp': commission_fp,
                'commission_discount': commission_discount,
                'commission_jewelry': jewelry_commission,
                'commission_vhernier': commission_vhernier,
                'commission_rosa_maria': commission_rosa_maria,
                'commission_100_and_below_fp': commission_100_and_below_fp,
                'commission_100_and_below_discount': commission_100_and_below_discount,
                'commission_over_100': commission_over_100,
                'total': total_commission
            })

        # 6. CONVERT RESULTS TO DATAFRAME
        # Create pandas DataFrame with specified columns
        results_df = pd.DataFrame(results, columns=[
            'employee_code',
            'fullname',
            'store_code',
            'commission_fp',
            'commission_discount',
            'commission_jewelry',
            'commission_vhernier',
            'commission_rosa_maria',
            'commission_100_and_below_fp',
            'commission_100_and_below_discount',
            'commission_over_100',
            'total'
        ])

        # 7. RETURN DATAFRAME
        return results_df

    def calculate_store_commission_v2(
        self,
        store_code: str,
        store_target: float,
        store_fp_ratio_target: float,
        query_date: Dict[str, str],
        employees: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Calculate commission for a store following the updated pseudocode algorithm

        Args:
            store_code: Store code (e.g., "RHN", "HBT", "HDG")
            store_target: Store's revenue target
            store_fp_ratio_target: Target full-price ratio (0.0 to 1.0)
            query_date: Dictionary with 'from_date' and 'to_date' (YYYY-MM-DD HH:MI:SS format)
            employees: List of employee dictionaries with:
                - employee_code: String
                - full_name: String
                - seniority: Number (tenure in years)
                - personal_target: Number
                - working_day_count: Number
                - is_manager: Boolean
                - is_probation: Boolean

        Returns:
            Dictionary containing commission calculation results
        """
        from_date = query_date['from_date']
        to_date = query_date['to_date']

        # Derived parameters
        total_employee_count = len(employees)
        total_working_days = sum(emp['working_day_count'] for emp in employees)

        # Get store sales data from repository
        store_data = self.repository.get_store_sales_data(store_code, from_date, to_date)

        actual_revenue = store_data.get('ACTUAL_REVENUE', 0)
        actual_full_price_revenue = store_data.get('ACTUAL_FULL_PRICE_REVENUE', 0)
        actual_discounted_revenue = store_data.get('ACTUAL_DISCOUNTED_REVENUE', 0)

        # STEP 1: Check Store Eligibility & Calculate Achievement
        achievement_pct = (actual_revenue / store_target * 100) if store_target > 0 else 0
        actual_fp_ratio = (actual_full_price_revenue / actual_revenue) if actual_revenue > 0 else 0

        # Check if actual FP ratio meets target
        if actual_fp_ratio < store_fp_ratio_target:
            fp_shortage = (store_fp_ratio_target - actual_fp_ratio) * actual_revenue
            required_discounted = fp_shortage * 2.0  # 200% compensation

            if actual_discounted_revenue < required_discounted:
                return {
                    'eligible': False,
                    'reason': 'Not eligible for commission - discounted goods compensation insufficient',
                    'achievement_pct': achievement_pct,
                    'actual_fp_ratio': actual_fp_ratio,
                    'store_code': store_code,
                    'employees': []
                }

        # Check minimum 70% achievement (implicit from tier structure)
        if achievement_pct < 70:
            return {
                'eligible': False,
                'reason': 'Store achievement below 70%',
                'achievement_pct': achievement_pct,
                'store_code': store_code,
                'employees': []
            }

        # Get employee sales data from repository
        # For RHN/RWP stores, we need to include both stores' sales
        if store_code in ('RHN', 'RWP'):
            employee_sales_rhn = self.repository.get_employee_sales_data('RHN', from_date, to_date)
            employee_sales_rwp = self.repository.get_employee_sales_data('RWP', from_date, to_date)
            # Combine and deduplicate
            employee_sales_combined = employee_sales_rhn + employee_sales_rwp
            # Create lookup by employee code
            employee_sales_lookup = {}
            for emp_sale in employee_sales_combined:
                emp_code = emp_sale.get('EMPLOYEE_CODE')
                if emp_code not in employee_sales_lookup:
                    employee_sales_lookup[emp_code] = {
                        'fp_revenue': 0,
                        'disc_revenue': 0
                    }
                employee_sales_lookup[emp_code]['fp_revenue'] += emp_sale.get('EMPLOYEE_FP_REVENUE', 0)
                employee_sales_lookup[emp_code]['disc_revenue'] += emp_sale.get('EMPLOYEE_DISCOUNTED_REVENUE', 0)
        else:
            employee_sales_data = self.repository.get_employee_sales_data(store_code, from_date, to_date)
            # Create lookup by employee code
            employee_sales_lookup = {
                emp_sale.get('EMPLOYEE_CODE'): {
                    'fp_revenue': emp_sale.get('EMPLOYEE_FP_REVENUE', 0),
                    'disc_revenue': emp_sale.get('EMPLOYEE_DISCOUNTED_REVENUE', 0)
                }
                for emp_sale in employee_sales_data
            }

        # STEP 2: Calculate Each Employee's Contribution to Store Pool
        employee_contributions = []

        for employee in employees:
            employee_code = employee['employee_code']
            full_name = employee['full_name']
            seniority = employee['seniority']  # in years
            is_manager = employee['is_manager']
            is_probation = employee.get('is_probation', False)  # Default to False if not provided
            working_day_count = employee['working_day_count']

            # Get employee sales data using employee code for lookup
            emp_sales = employee_sales_lookup.get(employee_code, {'fp_revenue': 0, 'disc_revenue': 0})
            fp_revenue = emp_sales['fp_revenue']
            disc_revenue = emp_sales['disc_revenue']

            # Determine tier category based on seniority
            tier_category = "Senior" if seniority >= 3 else "Junior"

            # Calculate commission for each tier
            commission_tier_70_80 = 0
            commission_tier_80_90 = 0
            commission_tier_90_100 = 0
            commission_tier_100_plus = 0

            # Tier 70-80%: 0.5% FP, 0.25% disc (all employees)
            if achievement_pct >= 70:
                commission_tier_70_80 = (fp_revenue * 0.005) + (disc_revenue * 0.0025)

            # Tier 80-90%: 0.2% FP (senior) or 0.1% FP (junior), no disc
            if achievement_pct >= 80:
                rate = 0.002 if tier_category == "Senior" else 0.001
                commission_tier_80_90 = fp_revenue * rate

            # Tier 90-100%: 0.2% FP (senior) or 0.1% FP (junior), no disc
            if achievement_pct >= 90:
                rate = 0.002 if tier_category == "Senior" else 0.001
                commission_tier_90_100 = fp_revenue * rate

            # Tier 100%+: 0.1% FP (senior) or 0.05% FP (junior), ONLY on revenue exceeding 100%
            if achievement_pct > 100:
                rate = 0.001 if tier_category == "Senior" else 0.0005
                # Calculate revenue exceeding 100% target
                excess_ratio = (achievement_pct - 100) / 100
                excess_revenue = fp_revenue * excess_ratio
                commission_tier_100_plus = excess_revenue * rate

            # Sum total employee contribution
            employee_contribution = (
                commission_tier_70_80 +
                commission_tier_80_90 +
                commission_tier_90_100 +
                commission_tier_100_plus
            )

            employee_contributions.append({
                'employee_code': employee_code,
                'full_name': full_name,
                'seniority': seniority,
                'is_manager': is_manager,
                'is_probation': is_probation,
                'working_day_count': working_day_count,
                'tier_category': tier_category,
                'fp_revenue': fp_revenue,
                'disc_revenue': disc_revenue,
                'contribution': employee_contribution,
                'commission_breakdown': {
                    'tier_70_80': commission_tier_70_80,
                    'tier_80_90': commission_tier_80_90,
                    'tier_90_100': commission_tier_90_100,
                    'tier_100_plus': commission_tier_100_plus
                }
            })

        # STEP 3: Calculate Total Store Pool
        store_pool = sum(emp['contribution'] for emp in employee_contributions)

        # STEP 4: Distribute Store Pool to Each Employee
        results = []

        for emp in employee_contributions:
            # 4.1: Calculate Individual Share (70% based on contribution)
            individual_share = 0.70 * emp['contribution']

            # 4.2: Calculate Equal Share (30% pool distribution)
            if store_code in ('RHN', 'RWP'):
                # RHN, RWP: Distribute based on working day contribution
                working_day_ratio = emp['working_day_count'] / total_working_days if total_working_days > 0 else 0
                equal_share = 0.30 * store_pool * working_day_ratio
            else:
                # Other stores: Divide equally among employees
                equal_share = 0.30 * store_pool / total_employee_count if total_employee_count > 0 else 0

            # 4.3: Calculate Manager Bonus (if applicable)
            manager_bonus = 0
            if emp['is_manager']:
                if achievement_pct >= 100:
                    manager_bonus = 3_000_000
                elif achievement_pct >= 70:
                    manager_bonus = 750_000

            # 4.4: Calculate Total Store Commission
            # If employee is on probation, they only receive equal share portion
            if emp['is_probation']:
                total_store_commission = equal_share
            else:
                total_store_commission = individual_share + equal_share + manager_bonus

            results.append({
                'employee_code': emp['employee_code'],
                'full_name': emp['full_name'],
                'seniority': emp['seniority'],
                'is_manager': emp['is_manager'],
                'is_probation': emp['is_probation'],
                'working_day_count': emp['working_day_count'],
                'store_code': store_code,
                'fp_revenue': emp['fp_revenue'],
                'disc_revenue': emp['disc_revenue'],
                'individual_share': individual_share,
                'equal_share': equal_share,
                'manager_bonus': manager_bonus,
                'total_store_commission': total_store_commission
            })

        # STEP 5: Return Results
        return {
            'eligible': True,
            'store_code': store_code,
            'achievement_pct': achievement_pct,
            'actual_fp_ratio': actual_fp_ratio,
            'store_target': store_target,
            'actual_revenue': actual_revenue,
            'store_pool': store_pool,
            'total_employee_count': total_employee_count,
            'total_working_days': total_working_days,
            'employees': results
        }

    def calculate_batch_store_commissions(
        self,
        employees: List[Dict[str, Any]],
        stores: List[Dict[str, Any]],
        year: int,
        month: int,
        start_date: str,
        end_date: str
    ) -> Dict[str, Any]:
        """
        Calculate store commissions for multiple employees across multiple stores

        Args:
            employees: List of employee dicts with structure:
                {
                    'employee_code': str,
                    'employee_name': str,
                    'store_code': str,
                    'personal_target': float,
                    'seniority': int (tenure in months)
                }
            stores: List of store dicts with structure:
                {
                    'store_code': str,
                    'store_name': str (optional),
                    'target_revenue': float,
                    'target_fp_ratio': float (0.0 to 1.0)
                }
            year: Year for the commission period
            month: Month for the commission period
            start_date: Period start date in 'YYYY-MM-DD HH:MI:SS' format
            end_date: Period end date in 'YYYY-MM-DD HH:MI:SS' format

        Returns:
            Dictionary containing commission results for all employees
        """
        # Validate inputs
        if not employees or not stores:
            return {
                'success': False,
                'error': 'Both employees and stores arrays are required'
            }

        # Create lookup dictionaries
        store_lookup = {store['store_code']: store for store in stores}

        # Group employees by store
        employees_by_store = {}
        for emp in employees:
            store_code = emp.get('store_code')
            if store_code not in employees_by_store:
                employees_by_store[store_code] = []
            employees_by_store[store_code].append(emp)

        # Get unique store codes from employees
        store_codes = list(employees_by_store.keys())

        # Fetch store sales data for all stores
        stores_sales_data = self.repository.get_multiple_stores_sales_data(
            store_codes, start_date, end_date
        )

        # Fetch employee sales data for all stores
        stores_employee_sales = self.repository.get_multiple_stores_employee_sales_data(
            store_codes, start_date, end_date
        )

        # Process each store and calculate commissions
        all_employee_results = []
        store_results = []

        for store_code in store_codes:
            # Get store configuration from request
            store_config = store_lookup.get(store_code)
            if not store_config:
                # Skip if store config not provided
                for emp in employees_by_store[store_code]:
                    all_employee_results.append({
                        'employee_code': emp.get('employee_code'),
                        'employee_name': emp.get('employee_name'),
                        'store_code': store_code,
                        'eligible': False,
                        'reason': 'Store configuration not provided',
                        'total_commission': 0
                    })
                continue

            store_name = store_config.get('store_name', store_code)
            target_revenue = store_config.get('target_revenue', 0)
            target_fp_ratio = store_config.get('target_fp_ratio', 0.0)

            # Get store sales data
            store_data = stores_sales_data.get(store_code, {})

            # Create targets dictionary
            targets = {
                'TARGET_REVENUE': target_revenue,
                'TARGET_FP_RATIO': target_fp_ratio
            }

            # Check store eligibility
            eligibility_result = self._check_store_eligibility(store_data, targets)

            if not eligibility_result['eligible']:
                # Store not eligible - all employees get 0 commission
                for emp in employees_by_store[store_code]:
                    all_employee_results.append({
                        'employee_code': emp.get('employee_code'),
                        'employee_name': emp.get('employee_name'),
                        'store_code': store_code,
                        'store_name': store_name,
                        'eligible': False,
                        'reason': f"Store not eligible: {eligibility_result['reason']}",
                        'achievement_pct': eligibility_result.get('achievement_pct', 0),
                        'total_commission': 0
                    })

                store_results.append({
                    'store_code': store_code,
                    'store_name': store_name,
                    'eligible': False,
                    'reason': eligibility_result['reason'],
                    'achievement_pct': eligibility_result.get('achievement_pct', 0)
                })
                continue

            achievement_pct = eligibility_result['achievement_pct']

            # Get employee sales data for this store
            employee_sales_list = stores_employee_sales.get(store_code, [])

            # Create employee sales lookup by name
            employee_sales_lookup = {
                emp_sale.get('EMPLOYEE_FULL_NAME'): emp_sale
                for emp_sale in employee_sales_list
            }

            # Filter to only requested employees with their provided seniority
            requested_employees_data = []
            for emp in employees_by_store[store_code]:
                emp_name = emp.get('employee_name')
                emp_sales = employee_sales_lookup.get(emp_name)

                if emp_sales:
                    # Add seniority from request
                    requested_employees_data.append({
                        'EMPLOYEE_FULL_NAME': emp_name,
                        'EMPLOYEE_FP_REVENUE': emp_sales.get('EMPLOYEE_FP_REVENUE', 0),
                        'EMPLOYEE_DISCOUNTED_REVENUE': emp_sales.get('EMPLOYEE_DISCOUNTED_REVENUE', 0),
                        'TENURE_MONTHS': emp.get('seniority', 0)
                    })

            if not requested_employees_data:
                # No sales data for requested employees
                for emp in employees_by_store[store_code]:
                    all_employee_results.append({
                        'employee_code': emp.get('employee_code'),
                        'employee_name': emp.get('employee_name'),
                        'store_code': store_code,
                        'store_name': store_name,
                        'eligible': False,
                        'reason': 'No sales data found for employee',
                        'achievement_pct': achievement_pct,
                        'total_commission': 0
                    })
                continue

            # Calculate employee contributions with provided seniority
            employee_contributions = []
            for emp_data in requested_employees_data:
                emp_name = emp_data['EMPLOYEE_FULL_NAME']
                fp_revenue = emp_data['EMPLOYEE_FP_REVENUE']
                discounted_revenue = emp_data['EMPLOYEE_DISCOUNTED_REVENUE']
                tenure_months = emp_data['TENURE_MONTHS']

                # Check if manager (you may want to add this to request)
                is_manager = False

                # Determine tier rates based on tenure and achievement
                tier_rates = self._get_tier_rates(tenure_months, achievement_pct)

                # Allocate FP revenue to tiers
                fp_by_tier = self._allocate_revenue_to_tiers(fp_revenue, achievement_pct)

                # Calculate commission from FP revenue
                fp_commission = sum(
                    fp_by_tier[tier] * rate for tier, rate in tier_rates.items()
                )

                # Add discounted revenue commission (0.25% for 70-80% tier only)
                discounted_commission = 0
                if 70 <= achievement_pct < 80:
                    discounted_commission = discounted_revenue * 0.0025

                total_contribution = fp_commission + discounted_commission

                employee_contributions.append({
                    'employee_name': emp_name,
                    'tenure_months': tenure_months,
                    'is_manager': is_manager,
                    'fp_revenue': fp_revenue,
                    'discounted_revenue': discounted_revenue,
                    'fp_commission': fp_commission,
                    'discounted_commission': discounted_commission,
                    'contribution': total_contribution
                })

            # Calculate total store pool
            store_pool = sum(emp['contribution'] for emp in employee_contributions)

            # Distribute store pool to each employee
            employee_commissions = self._distribute_store_pool(
                employee_contributions,
                store_pool,
                achievement_pct
            )

            # Add employee codes to results
            for emp_commission in employee_commissions:
                emp_name = emp_commission['employee_name']
                # Find matching employee in request to get employee_code
                matching_emp = next(
                    (e for e in employees_by_store[store_code] if e.get('employee_name') == emp_name),
                    None
                )
                emp_code = matching_emp.get('employee_code', '') if matching_emp else ''

                all_employee_results.append({
                    'employee_code': emp_code,
                    'employee_name': emp_name,
                    'store_code': store_code,
                    'store_name': store_name,
                    'eligible': True,
                    'achievement_pct': achievement_pct,
                    'tenure_months': emp_commission['tenure_months'],
                    'is_manager': emp_commission['is_manager'],
                    'fp_revenue': emp_commission['fp_revenue'],
                    'discounted_revenue': emp_commission['discounted_revenue'],
                    'contribution': emp_commission['contribution'],
                    'commission_70pct': emp_commission['commission_70pct'],
                    'commission_30pct': emp_commission['commission_30pct'],
                    'manager_bonus': emp_commission['manager_bonus'],
                    'total_commission': emp_commission['total_commission']
                })

            store_results.append({
                'store_code': store_code,
                'store_name': store_name,
                'eligible': True,
                'achievement_pct': achievement_pct,
                'target_revenue': target_revenue,
                'target_fp_ratio': target_fp_ratio,
                'store_pool': store_pool,
                'employee_count': len(employee_commissions)
            })

        # Calculate summary statistics
        eligible_employees = [r for r in all_employee_results if r.get('eligible', False)]
        total_commission_payout = sum(r.get('total_commission', 0) for r in eligible_employees)

        return {
            'success': True,
            'period': {
                'year': year,
                'month': month,
                'start_date': start_date,
                'end_date': end_date
            },
            'summary': {
                'total_employees': len(all_employee_results),
                'eligible_employees': len(eligible_employees),
                'ineligible_employees': len(all_employee_results) - len(eligible_employees),
                'total_stores': len(store_codes),
                'eligible_stores': len([s for s in store_results if s.get('eligible', False)]),
                'total_commission_payout': total_commission_payout
            },
            'stores': store_results,
            'employees': all_employee_results
        }

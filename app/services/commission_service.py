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
        employees: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Calculate personal commissions for all employees based on the new algorithm

        Args:
            month: Month for commission calculation (1-12)
            year: Year for commission calculation
            employees: List of employee dictionaries with structure:
                {
                    'employee_id': str,
                    'target': float,
                    'employee_name': str (optional),
                    'department': str (optional)
                }

        Returns:
            Dictionary containing:
            {
                'success': bool,
                'month': int,
                'year': int,
                'summary': {
                    'total_employees': int,
                    'eligible_employees': int,
                    'ineligible_employees': int,
                    'total_commission_payout': float
                },
                'employee_commissions': List[Dict]
            }
        """
        # 2. VALIDATE INPUT
        if month is None or year is None or employees is None or len(employees) == 0:
            return {
                'success': False,
                'error': "Missing required fields: month, year, or employees list"
            }

        # 3. GET ALL SALES DATA AS PANDAS DATAFRAME
        sales_df = self.repository.get_personal_commission_sales_data(year, month)

        # 4. PREPARE RESULTS LIST
        results = []

        # 5. PROCESS EACH EMPLOYEE
        for employee_data in employees:
            employee_id = employee_data.get('employee_id')
            target = employee_data.get('target')
            employee_name = employee_data.get('employee_name', '')
            department = employee_data.get('department', '')

            # Validate employee data
            if employee_id is None or target is None:
                results.append({
                    'success': False,
                    'employee_id': employee_id,
                    'employee_name': employee_name,
                    'error': 'Missing employee_id or target'
                })
                continue

            # Filter sales for this specific employee
            employee_sales_df = sales_df[sales_df['employee_id'] == employee_id].copy()

            # Check if employee has any sales
            if len(employee_sales_df) == 0:
                results.append({
                    'success': True,
                    'eligible': False,
                    'reason': 'No sales data found',
                    'employee_id': employee_id,
                    'employee_name': employee_name,
                    'department': department,
                    'month': month,
                    'year': year,
                    'target': target,
                    'total_revenue': 0,
                    'achievement_rate': 0,
                    'commission': 0
                })
                continue

            # Calculate total revenue (all transactions are already paid)
            total_revenue_before_vat = employee_sales_df['revenue_before_vat'].sum()

            # Calculate full-price sales (discount_rate <= 0.30)
            full_price_df = employee_sales_df[employee_sales_df['discount_rate'] <= 0.30]
            full_price_sales = full_price_df['revenue_before_vat'].sum()

            # Calculate discounted sales (discount_rate > 0.30)
            discounted_df = employee_sales_df[employee_sales_df['discount_rate'] > 0.30]
            discounted_sales = discounted_df['revenue_before_vat'].sum()

            # Calculate achievement rate
            achievement_rate = (total_revenue_before_vat / target) * 100 if target > 0 else 0

            # Check minimum threshold
            if achievement_rate < 50:
                results.append({
                    'success': True,
                    'eligible': False,
                    'reason': 'Did not reach 50% of monthly target',
                    'employee_id': employee_id,
                    'employee_name': employee_name,
                    'department': department,
                    'month': month,
                    'year': year,
                    'target': target,
                    'total_revenue': total_revenue_before_vat,
                    'achievement_rate': achievement_rate,
                    'commission': 0
                })
                continue

            # Calculate running total to find when target was reached (for >100% bonus)
            employee_sales_df['running_total'] = employee_sales_df['revenue_before_vat'].cumsum()

            # Find full-price sales after reaching target (for >100% tier)
            full_price_sales_after_target = 0

            if achievement_rate > 100:
                # Find the row where target was first reached or exceeded
                target_reached_df = employee_sales_df[employee_sales_df['running_total'] >= target]

                if len(target_reached_df) > 0:
                    # Get the first sale that reached/exceeded target
                    first_target_row_index = target_reached_df.index[0]
                    first_target_row = employee_sales_df.loc[first_target_row_index]

                    previous_total = first_target_row['running_total'] - first_target_row['revenue_before_vat']

                    # If this sale pushed over target, calculate excess portion
                    if previous_total < target:
                        excess_from_this_sale = first_target_row['running_total'] - target

                        # Only count if it's full-price (discount_rate <= 0.30)
                        if first_target_row['discount_rate'] <= 0.30:
                            full_price_sales_after_target = full_price_sales_after_target + excess_from_this_sale

                    # Get all sales after the target-reaching sale
                    sales_after_target_df = employee_sales_df.loc[first_target_row_index + 1:]

                    # Filter for full-price items (discount_rate <= 0.30)
                    full_price_after_df = sales_after_target_df[sales_after_target_df['discount_rate'] <= 0.30]

                    # Add to full_price_sales_after_target
                    full_price_sales_after_target = full_price_sales_after_target + full_price_after_df['revenue_before_vat'].sum()

            # Calculate commission based on cumulative tiers
            commission = 0
            commission_breakdown = []

            # TIER 1: 50% - 70% of target
            if achievement_rate >= 50 and achievement_rate < 70:
                commission = (full_price_sales * 0.0025) + (discounted_sales * 0.00125)

                commission_breakdown.append({
                    'tier': '50-70%',
                    'full_price_rate': '0.25%',
                    'discounted_rate': '0.125%',
                    'full_price_sales': full_price_sales,
                    'discounted_sales': discounted_sales,
                    'commission': commission
                })

            # TIER 2: 70% - 100% of target
            elif achievement_rate >= 70 and achievement_rate < 100:
                commission = (full_price_sales * 0.0075) + (discounted_sales * 0.00375)

                commission_breakdown.append({
                    'tier': '70-100%',
                    'full_price_rate': '0.25% + 0.5% = 0.75%',
                    'discounted_rate': '0.125% + 0.25% = 0.375%',
                    'full_price_sales': full_price_sales,
                    'discounted_sales': discounted_sales,
                    'commission': commission
                })

            # TIER 3: 100% of target and above
            elif achievement_rate >= 100:
                # Base commission at 100% tier rates
                commission_up_to_target = (full_price_sales * 0.0175) + (discounted_sales * 0.00875)
                commission = commission_up_to_target

                commission_breakdown.append({
                    'tier': '100%',
                    'full_price_rate': '0.25% + 0.5% + 1% = 1.75%',
                    'discounted_rate': '0.125% + 0.25% + 0.5% = 0.875%',
                    'full_price_sales': full_price_sales,
                    'discounted_sales': discounted_sales,
                    'commission': commission_up_to_target
                })

                # TIER 4: Over 100% - Additional bonus
                if achievement_rate > 100 and full_price_sales_after_target > 0:
                    excess_commission = full_price_sales_after_target * 0.02
                    commission = commission + excess_commission

                    commission_breakdown.append({
                        'tier': 'Over 100% (Excess Bonus)',
                        'full_price_rate': '2%',
                        'discounted_rate': '0%',
                        'total_excess_revenue': total_revenue_before_vat - target,
                        'full_price_sales_after_target': full_price_sales_after_target,
                        'commission': excess_commission,
                        'note': 'Bonus only applies to full-price transactions that occurred after reaching 100% target'
                    })

            # Add result for this employee
            results.append({
                'success': True,
                'employee_id': employee_id,
                'employee_name': employee_name,
                'department': department,
                'month': month,
                'year': year,
                'eligible': True,
                'achievement_rate': achievement_rate,
                'target': target,
                'total_revenue': total_revenue_before_vat,
                'full_price_sales': full_price_sales,
                'discounted_sales': discounted_sales,
                'total_commission': commission,
                'commission_breakdown': commission_breakdown
            })

        # 6. CALCULATE SUMMARY STATISTICS
        eligible_results = [r for r in results if r.get('eligible') == True]
        total_commission_payout = sum([r['total_commission'] for r in eligible_results])

        # 7. RETURN ALL RESULTS
        return {
            'success': True,
            'month': month,
            'year': year,
            'summary': {
                'total_employees': len(results),
                'eligible_employees': len(eligible_results),
                'ineligible_employees': len(results) - len(eligible_results),
                'total_commission_payout': total_commission_payout
            },
            'employee_commissions': results
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

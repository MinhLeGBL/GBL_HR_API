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

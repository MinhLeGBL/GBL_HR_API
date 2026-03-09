"""
Commission Service - Business logic for store commission calculations
Based on the pseudocode algorithm with 4 main steps
"""
from typing import Dict, List, Any, Optional
import calendar
from datetime import date, timedelta
import pandas as pd
from app.core.database.connection import get_postgres_connection

# ============================================================================
# TEMPORARY SPECIAL COMMISSION RATES FOR RWD STORE
# ============================================================================
# Set to True to enable special rates for RWD store
# Set to False to revert to standard rates for all stores
ENABLE_RWD_SPECIAL_RATES = True

# RWD Special Rates (TEMPORARY - can be easily reverted)
RWD_SPECIAL_RATES = {
    'tier1': {'fp': 0.00375, 'discount': 0.001875},    # 50-70%: FP 0.375%, Discount 0.1875%
    'tier2': {'fp': 0.0075, 'discount': 0.00375},      # 70-100%: FP 0.75%, Discount 0.375%
    'tier3': {'fp': 0.015, 'discount': 0.0075},        # 100%+: FP 1.5%, Discount 0.75%
    'over_100': 0.015                                  # Over 100% bonus: 1.5%
}

# Standard Rates (used by all stores when RWD special rates are disabled)
STANDARD_RATES = {
    'tier1': {'fp': 0.0025, 'discount': 0.00125},     # 50-70%: FP 0.25%, Discount 0.125%
    'tier2': {'fp': 0.005, 'discount': 0.0025},       # 70-100%: FP 0.5%, Discount 0.25%
    'tier3': {'fp': 0.01, 'discount': 0.005},         # 100%+: FP 1%, Discount 0.5%
    'over_100': 0.01                                   # Over 100% bonus: 1% additional (items already get 1% from tier 3)
}
# ============================================================================

# ============================================================================
# EMPLOYEE-SPECIFIC COMMISSION EXCEPTIONS
# ============================================================================
# Employee who receives a flat rate on non-jewelry sales only when
# selling to a specific customer. No personal target, no achievement tiers.
EMPLOYEE_COMMISSION_EXCEPTIONS = {
    690036963000170943: {                               # employee SID
        'qualifying_customer_sid': 690837303000121462,
        'flat_rate': 0.007,                             # 0.7% on all non-jewelry
    }
}
# ============================================================================

# ============================================================================
# VENDOR CODE GROUPS
# ============================================================================
HC_VENDORS_1PCT = frozenset(['ATQ', 'AQU', 'ERE', 'GEO', 'GDC', 'BDA', 'SKY', 'CHI', 'MNC', 'DAP'])
HC_VENDORS_2PCT = frozenset(['CGI', 'ATS', 'VIS', 'LUI', 'NAN', 'NAK', 'ROM', 'SPK', 'TED', 'BRT'])
JEWELRY_OTHER_VENDORS = frozenset(['ATS', 'VIS', 'LUI', 'NAN', 'NAK', 'SPK', 'TED', 'BRT'])
SUITCASE_VENDORS = frozenset(['TVL', 'TIT'])

# ============================================================================
# COMMISSION RATES
# ============================================================================
HC_RATE_ROM_EARRINGS = 0.03
HC_RATE_1PCT = 0.01
HC_RATE_2PCT = 0.02
JEWELRY_RATE_ROM_EARRINGS = 0.03
JEWELRY_RATE_VHN = 0.01
JEWELRY_RATE_ROM_OTHER = 0.02
JEWELRY_RATE_OTHER = 0.02
SUITCASE_FLAT_AMOUNT = 500_000

# ============================================================================
# THRESHOLDS AND BONUSES
# ============================================================================
DISCOUNT_THRESHOLD = 0.30
MANAGER_BONUS_100_PLUS = 3_000_000
MANAGER_BONUS_70_PLUS = 750_000
POOL_INDIVIDUAL_RATIO = 0.70
POOL_EQUAL_RATIO = 0.30
FP_COMPENSATION_MULTIPLIER = 2.0
FP_REVENUE_MIN_RATIO = 0.70
EXCLUDED_DEPARTMENT = 'COSM'
# ============================================================================

# ============================================================================
# REVENUE TYPE CONSTANTS (for 7-type revenue breakdown)
# ============================================================================
REVENUE_TYPE_ORDER = ['full_price', 'markdown', 'jewelry', 'vhernier', 'rosa_maria', 'hand_carry', 'suitcase']
REVENUE_TYPE_LABELS = {
    'full_price': 'Full Price', 'markdown': 'Markdown', 'jewelry': 'Jewelry',
    'vhernier': 'Vhernier', 'rosa_maria': 'Rosa Maria',
    'hand_carry': 'Hand Carry', 'suitcase': 'Suitcase'
}
VALID_REVENUE_TYPES = frozenset(REVENUE_TYPE_ORDER)
# ============================================================================


def _query_store_employees(conn, month: int, year: int) -> List[Dict[str, Any]]:
    """
    Shared query: ALL store-type employees with period-accurate store/manager
    assignment, commission settings, and retailpro_username.
    Includes active and inactive employees — is_commission_active is the
    per-period authority on participation, not the global is_active flag.

    Uses a single LATERAL JOIN on employee_status_history (CR #16) to get
    the employee's state as of the requested period (nearest period <= target).

    Used by CommissionService._get_employees_with_username_for_period and
    CommissionSettingsService.get_commission_employees.

    Returns:
        List of dicts with keys: store_code, store_name, employee_code,
        full_name, join_date, retailpro_username, contract, is_manager,
        personal_target, working_day, is_commission_active, store_code_override.
    """
    cursor = conn.cursor()
    cursor.execute('''
        SELECT
            -- CR #15: store_code_override takes priority over history/default store
            COALESCE(override_store.store_code, esh_store.store_code, s.store_code) AS store_code,
            COALESCE(override_store.store_name, esh_store.store_name, s.store_name) AS store_name,
            e.employee_code,
            e.full_name,
            e.join_date,
            e.retailpro_username,
            ct.code AS contract,
            COALESCE(esh.is_manager, FALSE) AS is_manager,
            cs.personal_target,
            cs.working_day,
            COALESCE(cs.is_commission_active, esh.is_active, TRUE) AS is_commission_active,
            cs.store_code_override
        FROM employees e
        JOIN employee_types et  ON e.employee_type_id = et.id
        JOIN contract_types ct  ON e.contract_type_id = ct.id
        LEFT JOIN stores s      ON e.store_id = s.id
        -- Single LATERAL JOIN: employee status snapshot as of the requested period
        LEFT JOIN LATERAL (
            SELECT esh.is_active, esh.store_id, esh.department_id,
                   esh.contract_type_id, esh.is_manager
            FROM employee_status_history esh
            WHERE esh.employee_sid = e.sid
              AND (esh.period_year < %(year)s
                   OR (esh.period_year = %(year)s AND esh.period_month <= %(month)s))
            ORDER BY esh.period_year DESC, esh.period_month DESC
            LIMIT 1
        ) esh ON TRUE
        LEFT JOIN stores esh_store ON esh.store_id = esh_store.id
        LEFT JOIN commission_settings cs
            ON cs.employee_code = e.employee_code
           AND cs.month = %(month)s
           AND cs.year  = %(year)s
        -- CR #15: resolve override store name from store_code_override
        LEFT JOIN stores override_store ON override_store.store_code = cs.store_code_override
        WHERE et.code = 'STORE'
          AND COALESCE(esh.store_id, e.store_id) IS NOT NULL
        ORDER BY COALESCE(override_store.store_code, esh_store.store_code, s.store_code), e.employee_code
    ''', {'month': month, 'year': year})

    rows = cursor.fetchall()
    columns = [desc[0] for desc in cursor.description]
    cursor.close()
    return [dict(zip(columns, row)) for row in rows]


class CommissionService:
    """Business logic layer for commission calculations"""

    def __init__(self, repository=None):
        """
        Initialize commission service

        Args:
            repository: Commission repository instance (injected for testability)
        """
        if repository is None:
            from app.modules.commission.repository import CommissionRepository
            self.repository = CommissionRepository()
        else:
            self.repository = repository

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

        actual_fp_revenue = store_data.get('ACTUAL_FULL_PRICE_REVENUE', 0)
        actual_discounted_revenue = store_data.get('ACTUAL_DISCOUNTED_REVENUE', 0)
        actual_jewelry_revenue = store_data.get('ACTUAL_JEWELRY_REVENUE', 0)
        actual_suitcase_revenue = store_data.get('ACTUAL_SUITCASE_REVENUE', 0)
        # CR #21 V3: 7-type filtered total
        actual_revenue = actual_fp_revenue + actual_discounted_revenue + actual_jewelry_revenue + actual_suitcase_revenue
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
        if actual_fp_revenue < (target_revenue * FP_REVENUE_MIN_RATIO):
            return {
                'eligible': False,
                'reason': f'Full price revenue ({actual_fp_revenue:,.0f}) < 70% of target ({target_revenue * FP_REVENUE_MIN_RATIO:,.0f})',
                'achievement_pct': achievement_pct
            }

        # Calculate actual FP ratio
        actual_fp_ratio = actual_fp_revenue / actual_revenue if actual_revenue > 0 else 0

        # Check if actual FP ratio meets target
        if actual_fp_ratio < target_fp_ratio:
            # Calculate discounted goods compensation requirement
            fp_shortage = (target_fp_ratio - actual_fp_ratio) * actual_revenue
            required_discounted = fp_shortage * FP_COMPENSATION_MULTIPLIER  # 200% compensation

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

    # ========================================================================
    # Personal Commission Helper Methods
    # ========================================================================

    def _build_empty_personal_result(
        self,
        employee_code: str,
        employee_name: str,
        store_code: str
    ) -> Dict[str, Any]:
        """Build a zero-commission result dict for employees with no qualifying sales"""
        return {
            'employee_code': employee_code,
            'fullname': employee_name,
            'store_code': store_code,
            'commission_100_and_below_fp': 0,
            'commission_100_and_below_discount': 0,
            'commission_over_100': 0,
            'commission_jewelry': 0,
            'commission_vhernier': 0,
            'commission_rosa_maria': 0,
            'commission_suitcase': 0,
            'commission_hand_carry': 0,
            'total': 0
        }

    def _separate_sales_by_type(
        self,
        sales_df,
        hand_carry_upcs: list
    ) -> Dict[str, Any]:
        """
        Separate sales DataFrame into 4 categories using the priority chain:
        hand carry → suitcase → jewelry → non-jewelry (remainder)

        Args:
            sales_df: DataFrame with 'upc_clean', 'vendor_code', 'is_jewelry' columns
            hand_carry_upcs: List of UPCs for hand carry items

        Returns:
            Dict with keys: 'hand_carry', 'suitcase', 'jewelry', 'non_jewelry'
        """
        # Priority chain (cascading): hand_carry → suitcase → jewelry → non-jewelry
        # Each step removes matched items from the remainder so categories never overlap.
        # 1. Hand carry (highest priority) — by UPC match
        hand_carry_df = sales_df[sales_df['upc_clean'].isin(hand_carry_upcs)].copy()
        remainder = sales_df[~sales_df['upc_clean'].isin(hand_carry_upcs)]

        # 2. Suitcase (TVL/TIT) — removed from remainder before jewelry check
        suitcase_df = remainder[remainder['vendor_code'].isin(SUITCASE_VENDORS)].copy()
        remainder = remainder[~remainder['vendor_code'].isin(SUITCASE_VENDORS)]

        # 3. Jewelry (is_jewelry == 1) — from remainder after suitcase removed
        jewelry_df = remainder[remainder['is_jewelry'] == 1].copy()

        # 4. Non-jewelry = everything left
        non_jewelry_df = remainder[remainder['is_jewelry'] == 0].copy()

        return {
            'hand_carry': hand_carry_df,
            'suitcase': suitcase_df,
            'jewelry': jewelry_df,
            'non_jewelry': non_jewelry_df
        }

    def _calculate_hand_carry_commission(self, hand_carry_df) -> Dict[str, float]:
        """
        Calculate hand carry commission by vendor group.
        Uses revenue_with_vat.

        Returns:
            Dict with keys: 'total', 'rom_earrings', 'hc_1pct', 'hc_2pct'
        """
        result = {'total': 0, 'rom_earrings': 0, 'hc_1pct': 0, 'hc_2pct': 0}

        if len(hand_carry_df) == 0:
            return result

        # ROM EARRINGS: 3% on revenue WITH VAT
        rom_earrings_hc = hand_carry_df[
            (hand_carry_df['vendor_code'] == 'ROM') & (hand_carry_df['category'] == 'EARRINGS')
        ]
        if len(rom_earrings_hc) > 0:
            result['rom_earrings'] = rom_earrings_hc['revenue_with_vat'].sum() * HC_RATE_ROM_EARRINGS
            result['total'] += result['rom_earrings']

        # 1% vendors
        hc_1pct = hand_carry_df[hand_carry_df['vendor_code'].isin(HC_VENDORS_1PCT)]
        if len(hc_1pct) > 0:
            result['hc_1pct'] = hc_1pct['revenue_with_vat'].sum() * HC_RATE_1PCT
            result['total'] += result['hc_1pct']

        # 2% vendors (excluding ROM EARRINGS already counted at 3%)
        hc_2pct = hand_carry_df[
            (hand_carry_df['vendor_code'].isin(HC_VENDORS_2PCT)) &
            ~((hand_carry_df['vendor_code'] == 'ROM') & (hand_carry_df['category'] == 'EARRINGS'))
        ]
        if len(hc_2pct) > 0:
            result['hc_2pct'] = hc_2pct['revenue_with_vat'].sum() * HC_RATE_2PCT
            result['total'] += result['hc_2pct']

        return result

    def _calculate_suitcase_commission(self, suitcase_df) -> float:
        """Calculate suitcase commission: flat amount per item"""
        if len(suitcase_df) == 0:
            return 0
        return len(suitcase_df) * SUITCASE_FLAT_AMOUNT

    def _calculate_jewelry_commission(self, jewelry_df) -> Dict[str, float]:
        """
        Calculate jewelry commission by vendor group.
        Uses revenue_before_vat.

        Returns:
            Dict with keys: 'total', 'vhernier', 'rosa_maria'
        """
        result = {'total': 0, 'vhernier': 0, 'rosa_maria': 0}

        if len(jewelry_df) == 0:
            return result

        # ROM EARRINGS: 3%
        rom_earrings = jewelry_df[
            (jewelry_df['vendor_code'] == 'ROM') & (jewelry_df['category'] == 'EARRINGS')
        ]
        if len(rom_earrings) > 0:
            result['rosa_maria'] = rom_earrings['revenue_before_vat'].sum() * JEWELRY_RATE_ROM_EARRINGS
            result['total'] += result['rosa_maria']

        # VHN: 1%
        vhn = jewelry_df[jewelry_df['vendor_code'] == 'VHN']
        if len(vhn) > 0:
            result['vhernier'] = vhn['revenue_before_vat'].sum() * JEWELRY_RATE_VHN
            result['total'] += result['vhernier']

        # ROM Other (not EARRINGS): 2%
        rom_other = jewelry_df[
            (jewelry_df['vendor_code'] == 'ROM') & (jewelry_df['category'] != 'EARRINGS')
        ]
        if len(rom_other) > 0:
            result['total'] += rom_other['revenue_before_vat'].sum() * JEWELRY_RATE_ROM_OTHER

        # Other jewelry vendors: 2%
        other_jewelry = jewelry_df[jewelry_df['vendor_code'].isin(JEWELRY_OTHER_VENDORS)]
        if len(other_jewelry) > 0:
            result['total'] += other_jewelry['revenue_before_vat'].sum() * JEWELRY_RATE_OTHER

        return result

    # ========================================================================

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
            commission_70pct = POOL_INDIVIDUAL_RATIO * store_pool * contribution_ratio

            # Part B: 30% of pool distributed equally
            commission_30pct = POOL_EQUAL_RATIO * store_pool / employee_count

            # Personal commission
            personal_commission = commission_70pct + commission_30pct

            # Manager bonus (if achievement >= 100%)
            manager_bonus = 0
            if emp['is_manager'] and achievement_pct >= 100:
                manager_bonus = MANAGER_BONUS_100_PLUS

            # Total commission
            total_commission = personal_commission + manager_bonus

            results.append({
                'employee_code': emp.get('employee_code', ''),
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

    def _compute_revenue_by_type(self, sales_df, hand_carry_upcs) -> Dict[str, int]:
        """
        Compute 7-type revenue totals from a sales DataFrame using the standard priority chain:
        hand_carry → suitcase → jewelry (vhernier/rosa_maria/other) → non-jewelry (full_price/markdown).

        Args:
            sales_df: DataFrame with Oracle sale columns (lowercase). May be empty.
            hand_carry_upcs: List/set of UPCs that classify as hand carry.

        Returns:
            Dict with keys matching REVENUE_TYPE_ORDER, values are integer VND (0 if none).
        """
        zero = {t: 0 for t in REVENUE_TYPE_ORDER}

        if len(sales_df) == 0:
            return zero

        df = sales_df.copy()
        if 'upc_clean' not in df.columns:
            df['upc_clean'] = df['upc'].astype(str).str.strip()

        # Priority chain: hand_carry → suitcase → jewelry → non-jewelry
        hc_mask = df['upc_clean'].isin(hand_carry_upcs)
        hand_carry_df = df[hc_mask]
        remainder = df[~hc_mask]

        sc_mask = remainder['vendor_code'].isin(SUITCASE_VENDORS)
        suitcase_df = remainder[sc_mask]
        remainder2 = remainder[~sc_mask]

        jewelry_df = remainder2[remainder2['is_jewelry'] == 1]
        non_jewelry_df = remainder2[remainder2['is_jewelry'] == 0]

        # Jewelry sub-types (revenue_before_vat)
        vhn_df = jewelry_df[jewelry_df['vendor_code'] == 'VHN']
        rom_ear_df = jewelry_df[
            (jewelry_df['vendor_code'] == 'ROM') & (jewelry_df['category'] == 'EARRINGS')
        ]
        other_jw_mask = ~jewelry_df.index.isin(vhn_df.index) & ~jewelry_df.index.isin(rom_ear_df.index)
        other_jewelry_df = jewelry_df[other_jw_mask]

        # Non-jewelry sub-types
        fp_df = non_jewelry_df[non_jewelry_df['discount_rate'] <= DISCOUNT_THRESHOLD]
        md_df = non_jewelry_df[non_jewelry_df['discount_rate'] > DISCOUNT_THRESHOLD]

        # CR #21 spec: Step 2 base_amount uses revenue_with_vat for ALL types.
        # VAT conversion is handled internally by Step 3 (calculate endpoint).
        return {
            'full_price': int(fp_df['revenue_with_vat'].sum()),
            'markdown':   int(md_df['revenue_with_vat'].sum()),
            'jewelry':    int(other_jewelry_df['revenue_with_vat'].sum()),
            'vhernier':   int(vhn_df['revenue_with_vat'].sum()),
            'rosa_maria': int(rom_ear_df['revenue_with_vat'].sum()),
            'hand_carry': int(hand_carry_df['revenue_with_vat'].sum()),
            'suitcase':   int(suitcase_df['revenue_with_vat'].sum()),
        }

    def _create_adjustment_rows(
        self,
        adjustments_dict: Dict[str, int],
        employee_sid,
        employee_username: str,
        store_code: str,
        period_last_day
    ) -> pd.DataFrame:
        """
        Create synthetic sale rows for revenue adjustment injection into the Oracle DataFrame.

        Each adjustment type (except suitcase) becomes one synthetic row with a unique bill_number.
        The rows are dated one day after the period end so they sort after all real bills.
        Suitcase is excluded — its delta is applied directly to total_revenue_with_vat instead.

        Args:
            adjustments_dict: {revenue_type: signed_vnd_delta}
            employee_sid: Oracle employee SID for the row
            employee_username: Oracle username for the row
            store_code: Store code for the row
            period_last_day: Last calendar day of the period (date object)

        Returns:
            DataFrame with synthetic rows (empty if no applicable adjustments).
        """
        # Map each injectable revenue type to its Oracle column values
        TYPE_PROPS = {
            'full_price': {'vendor_code': '__ADJ__', 'is_jewelry': 0, 'discount_rate': 0.0,
                           'upc': '', 'category': 'ADJ', 'use_with_vat': False},
            'markdown':   {'vendor_code': '__ADJ__', 'is_jewelry': 0, 'discount_rate': 0.50,
                           'upc': '', 'category': 'ADJ', 'use_with_vat': False},
            'jewelry':    {'vendor_code': 'ATS',     'is_jewelry': 1, 'discount_rate': 0.0,
                           'upc': '', 'category': 'ADJ', 'use_with_vat': False},
            'vhernier':   {'vendor_code': 'VHN',     'is_jewelry': 1, 'discount_rate': 0.0,
                           'upc': '', 'category': 'ADJ', 'use_with_vat': False},
            'rosa_maria': {'vendor_code': 'ROM',     'is_jewelry': 1, 'discount_rate': 0.0,
                           'upc': '', 'category': 'EARRINGS', 'use_with_vat': False},
            'hand_carry': {'vendor_code': '__ADJ__', 'is_jewelry': 0, 'discount_rate': 0.0,
                           'upc': '__ADJ_HC__', 'category': 'ADJ', 'use_with_vat': True},
            # suitcase: NOT injected — handled via total_revenue_with_vat delta
        }

        adj_sale_date = period_last_day + timedelta(days=1)
        rows = []

        for rev_type, delta in adjustments_dict.items():
            if rev_type not in TYPE_PROPS or delta == 0:
                continue
            props = TYPE_PROPS[rev_type]
            upc_val = props['upc']
            revenue_with_vat = delta
            revenue_before_vat = 0 if props['use_with_vat'] else delta
            rows.append({
                'employee_sid':      employee_sid,
                'employee_username': employee_username,
                'store_code':        store_code,
                'bill_number':       f'__ADJ_{rev_type.upper()}__',
                'sale_date':         adj_sale_date,
                'sale_time':         '23:59:59',
                'upc':               upc_val,
                'upc_clean':         upc_val,
                'vendor_code':       props['vendor_code'],
                'is_jewelry':        props['is_jewelry'],
                'discount_rate':     props['discount_rate'],
                'category':          props['category'],
                'department':        'ADJ',  # Synthetic department; excluded from COSM filter naturally
                'customer_sid':      None,
                'revenue_before_vat': revenue_before_vat,
                'revenue_with_vat':   revenue_with_vat,
            })

        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    def _get_employees_with_username_for_period(
        self, month: int, year: int
    ) -> List[Dict[str, Any]]:
        """
        Get active store employees with period-accurate store/manager assignment,
        plus retailpro_username for Oracle sales matching.

        Returns:
            Flat list of dicts: employee_code, full_name, join_date, retailpro_username,
            store_code, store_name, is_manager, personal_target, working_day
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return []

            records = _query_store_employees(conn, month, year)
            result = []
            for record in records:
                join_date = record['join_date']
                contract = record.get('contract', '').upper() if record.get('contract') else ''
                result.append({
                    'store_code':        record['store_code'],
                    'store_name':        record['store_name'],
                    'employee_code':     record['employee_code'],
                    'full_name':         record['full_name'],
                    'join_date':         join_date.isoformat() if hasattr(join_date, 'isoformat') else join_date,
                    'retailpro_username': record['retailpro_username'],
                    'contract':          contract,
                    'is_manager':        record['is_manager'],
                    'personal_target':   record['personal_target'],
                    'working_day':       record['working_day'],
                })
            return result

        except Exception as e:
            print(f"[ERROR] _get_employees_with_username_for_period failed: {e}")
            return []

        finally:
            if conn:
                conn.close()

    def calculate_personal_commissions(
        self,
        month: int,
        year: int,
        employees: List[Dict[str, Any]],
        store_code: str = None,
        revenue_adjustments: Optional[Dict[str, Dict[str, int]]] = None,
        preloaded_sales_df: Optional[pd.DataFrame] = None,
        preloaded_hand_carry_upcs: Optional[List[str]] = None
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
            - employee_code: Employee HR code
            - fullname: Employee full name
            - store_code: Store code
            - commission_100_and_below_fp: Full-price commission at 100% and below (non-jewelry, achievement-based)
            - commission_100_and_below_discount: Discounted commission at 100% and below (non-jewelry, achievement-based)
            - commission_over_100: Over 100% bonus commission (2% on qualifying FP non-jewelry items)
            - commission_jewelry: Total jewelry commission (VHN, ROM, other jewelry vendors)
            - commission_vhernier: VHN jewelry commission (1%)
            - commission_rosa_maria: ROM EARRINGS jewelry commission (3%)
            - commission_suitcase: Suitcase commission (TVL/TIT 500k per item)
            - commission_hand_carry: Total hand carry commission (all vendors combined)
            - total: Total personal commission (sum of all components)
        """
        # 2. VALIDATE INPUT
        if month is None or year is None or employees is None or len(employees) == 0:
            return pd.DataFrame({
                'error': ['Missing required fields: month, year, or employees list']
            })

        # Compute last day of period (needed for adjustment row injection dates)
        last_day_num = calendar.monthrange(year, month)[1]
        period_last_day = date(year, month, last_day_num)

        # 3. GET ALL SALES DATA AS PANDAS DATAFRAME (use preloaded if available)
        if preloaded_sales_df is not None:
            all_sales_df = preloaded_sales_df
        else:
            all_sales_df = self.repository.get_personal_commission_sales_data(year, month)
            all_sales_df.columns = all_sales_df.columns.str.lower()

        # Query hand carry item UPC list (use preloaded if available)
        if preloaded_hand_carry_upcs is not None:
            hand_carry_upcs = preloaded_hand_carry_upcs
        else:
            hand_carry_upcs = self.repository.get_hand_carry_upcs()

        # Create mapping from employee_username to employee_sid for internal processing
        # Use employee_username (from Google Sheets "Employee Acc" column) to match EMPLOYEE.USER_NAME
        # Use employee_sid (unique, stable identifier) for filtering sales data
        # Use employee_code (human-readable) only for output
        employee_username_to_sid = all_sales_df[['employee_username', 'employee_sid']].drop_duplicates()
        employee_username_to_sid_dict = employee_username_to_sid.set_index('employee_username')['employee_sid'].to_dict()

        # 4. PREPARE RESULTS LIST
        results = []

        # 5. PROCESS EACH EMPLOYEE
        for employee_data in employees:
            employee_code = employee_data.get('employee_code')
            employee_username = employee_data.get('employee_username')  # From Google Sheets "Employee Acc"
            target = employee_data.get('personal_target')
            employee_name = employee_data.get('full_name', '')
            emp_store_code = store_code or employee_data.get('store_code', '')

            # Validate employee data
            if employee_code is None or target is None:
                results.append(self._build_empty_personal_result(employee_code, employee_name, emp_store_code))
                continue

            # Check if employee has username (can make sales)
            # Employees without username cannot sell items but are included for store commission share
            if not employee_username or employee_username not in employee_username_to_sid_dict:
                results.append(self._build_empty_personal_result(employee_code, employee_name, emp_store_code))
                continue

            # Get employee_sid from username mapping (for robust filtering)
            employee_sid = employee_username_to_sid_dict[employee_username]

            # Get revenue adjustments for this employee (if any)
            emp_adjustments = (revenue_adjustments or {}).get(employee_code, {})

            # Filter ALL sales for this specific employee (by employee_sid for accuracy)
            # Includes ALL items: jewelry + non-jewelry, all stores, all vendors
            employee_sales_df = all_sales_df[all_sales_df['employee_sid'] == employee_sid].copy()

            # Check if employee has any sales at all (and no adjustments to apply)
            if len(employee_sales_df) == 0 and not emp_adjustments:
                results.append(self._build_empty_personal_result(employee_code, employee_name, emp_store_code))
                continue

            # ================================================================
            # EMPLOYEE-SPECIFIC COMMISSION EXCEPTION PATH
            # ================================================================
            # Check if this employee has a special flat-rate commission rule.
            # Exception employees: no personal target, no achievement tiers,
            # flat rate on non-jewelry sold to a qualifying customer only.
            # Jewelry, hand carry, and suitcase commissions use normal rules.
            if employee_sid in EMPLOYEE_COMMISSION_EXCEPTIONS:
                exception = EMPLOYEE_COMMISSION_EXCEPTIONS[employee_sid]
                qualifying_customer = exception['qualifying_customer_sid']
                flat_rate = exception['flat_rate']

                # COSM exclusion still applies
                exc_sales_df = employee_sales_df[employee_sales_df['department'] != EXCLUDED_DEPARTMENT].copy()
                exc_sales_df['upc_clean'] = exc_sales_df['upc'].astype(str).str.strip()

                # Separate sales by type (same priority chain as main path)
                separated = self._separate_sales_by_type(exc_sales_df, hand_carry_upcs)

                # Apply customer filter ONLY to non-jewelry remainder
                qualifying_sales = separated['non_jewelry'][
                    separated['non_jewelry']['customer_sid'] == qualifying_customer
                ]
                exc_flat_commission = qualifying_sales['revenue_before_vat'].sum() * flat_rate

                # Hand carry, suitcase, jewelry use normal rules (no customer filter)
                hc = self._calculate_hand_carry_commission(separated['hand_carry'])
                sc = self._calculate_suitcase_commission(separated['suitcase'])
                jw = self._calculate_jewelry_commission(separated['jewelry'])

                exc_total = exc_flat_commission + hc['total'] + sc + jw['total']

                results.append({
                    'employee_code': employee_code,
                    'fullname': employee_name,
                    'store_code': emp_store_code,
                    'commission_100_and_below_fp': exc_flat_commission,
                    'commission_100_and_below_discount': 0,
                    'commission_over_100': 0,
                    'commission_jewelry': jw['total'],
                    'commission_vhernier': jw['vhernier'],
                    'commission_rosa_maria': jw['rosa_maria'],
                    'commission_suitcase': sc,
                    'commission_hand_carry': hc['total'],
                    'total': exc_total
                })
                continue
            # ================================================================

            # Calculate TOTAL revenue WITH VAT from ALL sources for eligibility check
            # IMPORTANT: COSM items are INCLUDED in total revenue for achievement calculation
            # (includes jewelry + non-jewelry, all stores, all vendors, INCLUDING COSM)
            total_revenue_with_vat = employee_sales_df['revenue_with_vat'].sum()

            # Add all revenue adjustments to achievement total
            # (suitcase for achievement only; other types also affect commission via injected rows)
            if emp_adjustments:
                for delta in emp_adjustments.values():
                    total_revenue_with_vat += delta

            # Calculate achievement rate based on TOTAL revenue WITH VAT from ALL sources
            achievement_rate = (total_revenue_with_vat / target) * 100 if target > 0 else 0

            # GROUP BY BILL to calculate bill-level totals (for >100% tier calculation)
            # IMPORTANT: Use the FULL sales data (INCLUDING COSM) for the running total,
            # because COSM revenue counts toward reaching the personal target.
            # The running total determines WHEN the employee reached their target.
            # NOTE: Both the running total and the personal target use revenue_with_vat.
            # Adjustment values are treated as with_vat = before_vat (same amount).
            bill_totals_df = employee_sales_df.groupby('bill_number', sort=False).agg({
                'revenue_with_vat': 'sum',
                'sale_date': 'first',
                'sale_time': 'first'
            }).reset_index()

            # Sort bills chronologically
            bill_totals_df = bill_totals_df.sort_values(['sale_date', 'sale_time'])

            # Prepend adjustments as a synthetic "first bill" so they shift the running
            # total but never fall after the target-crossing bill. This means adjustment
            # revenue affects WHEN the target is reached but never earns over-100% rate.
            if emp_adjustments:
                adj_total = sum(emp_adjustments.values())
                if adj_total != 0:
                    earliest_date = bill_totals_df['sale_date'].iloc[0] if len(bill_totals_df) > 0 else period_last_day
                    adj_bill = pd.DataFrame([{
                        'bill_number': '__ADJ__',
                        'revenue_with_vat': adj_total,
                        'sale_date': earliest_date,
                        'sale_time': '00:00:00',  # Earliest possible time — always first
                    }])
                    bill_totals_df = pd.concat([adj_bill, bill_totals_df], ignore_index=True)

            # Calculate running total by BILL (WITH VAT) to find when 100% target was reached
            bill_totals_df['running_total'] = bill_totals_df['revenue_with_vat'].cumsum()

            # EXCLUDE COSM department items from commission calculations
            # IMPORTANT: COSM items COUNT toward achievement and running total
            # but do NOT earn commission
            # This exclusion affects: FP, discount, jewelry, hand carry, suitcase commissions
            employee_sales_df = employee_sales_df[employee_sales_df['department'] != EXCLUDED_DEPARTMENT].copy()

            # SEPARATE SALES BY TYPE for commission calculation
            # Clean UPC for matching (required by _separate_sales_by_type)
            employee_sales_df['upc_clean'] = employee_sales_df['upc'].astype(str).str.strip()

            # Inject synthetic rows for revenue adjustments (all types except suitcase)
            local_hand_carry_upcs = hand_carry_upcs
            if emp_adjustments:
                adj_rows = self._create_adjustment_rows(
                    emp_adjustments, employee_sid, employee_username, emp_store_code, period_last_day
                )
                if not adj_rows.empty:
                    employee_sales_df = pd.concat([employee_sales_df, adj_rows], ignore_index=True)
                if emp_adjustments.get('hand_carry', 0) != 0:
                    local_hand_carry_upcs = list(hand_carry_upcs) + ['__ADJ_HC__']

            separated = self._separate_sales_by_type(employee_sales_df, local_hand_carry_upcs)
            hand_carry_df = separated['hand_carry']
            suitcase_df = separated['suitcase']
            jewelry_df = separated['jewelry']
            non_jewelry_df = separated['non_jewelry']

            # Calculate non-jewelry revenue breakdown
            # IMPORTANT: Use revenue_before_vat (non-VAT) for commission calculations
            non_jewelry_fp_df = non_jewelry_df[non_jewelry_df['discount_rate'] <= DISCOUNT_THRESHOLD]
            non_jewelry_fp_revenue = non_jewelry_fp_df['revenue_before_vat'].sum()

            non_jewelry_disc_df = non_jewelry_df[non_jewelry_df['discount_rate'] > DISCOUNT_THRESHOLD]
            non_jewelry_disc_revenue = non_jewelry_disc_df['revenue_before_vat'].sum()

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

                    # Filter items from target bill: FP, non-jewelry, NOT TIT/TVL, NOT hand carry
                    target_bill_qualifying = target_bill_items[
                        (target_bill_items['discount_rate'] <= DISCOUNT_THRESHOLD) &
                        (target_bill_items['is_jewelry'] == 0) &
                        (~target_bill_items['vendor_code'].isin(SUITCASE_VENDORS)) &
                        (~target_bill_items['upc_clean'].isin(local_hand_carry_upcs))
                    ].copy()

                    # If this bill pushed over target, use item-level calculation
                    # OPTIMIZATION: Sort items by selling price (ascending) and count row-by-row
                    # Only items that pushed over 100% and items after qualify for over 100% bonus
                    if previous_total < target and len(target_bill_qualifying) > 0:
                        # Sort qualifying items by revenue_with_vat (ascending) - lowest price first
                        target_bill_qualifying = target_bill_qualifying.sort_values('revenue_with_vat')

                        # Calculate running total for each item in this bill (using revenue_with_vat)
                        target_bill_qualifying['item_running_total'] = previous_total + target_bill_qualifying['revenue_with_vat'].cumsum()

                        # Find items that are at or after the 100% threshold
                        items_over_target = target_bill_qualifying[target_bill_qualifying['item_running_total'] >= target]

                        if len(items_over_target) > 0:
                            # Sum revenue (BEFORE VAT) from items that reached/exceeded target
                            fp_non_jewelry_after_target = items_over_target['revenue_before_vat'].sum()

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

                        # Filter for: full-price, non-jewelry, NOT TIT/TVL, NOT hand carry
                        fp_non_jewelry_after_df = items_after_target[
                            (items_after_target['discount_rate'] <= DISCOUNT_THRESHOLD) &
                            (items_after_target['is_jewelry'] == 0) &
                            (~items_after_target['vendor_code'].isin(SUITCASE_VENDORS)) &
                            (~items_after_target['upc_clean'].isin(local_hand_carry_upcs))
                        ]

                        # Add to fp_non_jewelry_after_target (use revenue_before_vat)
                        fp_non_jewelry_after_target = fp_non_jewelry_after_target + fp_non_jewelry_after_df['revenue_before_vat'].sum()

            # HAND CARRY, SUITCASE, JEWELRY COMMISSIONS
            # All paid REGARDLESS of achievement rate
            hc_result = self._calculate_hand_carry_commission(hand_carry_df)
            hand_carry_commission = hc_result['total']

            suitcase_commission = self._calculate_suitcase_commission(suitcase_df)

            jw_result = self._calculate_jewelry_commission(jewelry_df)
            jewelry_commission = jw_result['total']
            commission_rosa_maria = jw_result['rosa_maria']
            commission_vhernier = jw_result['vhernier']

            # PERSONAL COMMISSION (Non-Jewelry) - Calculate based on NON-CUMULATIVE tiers
            # IMPORTANT: All commission calculations use revenue_before_vat (non-VAT revenue)
            # NOTE: Personal commission requires achievement_rate >= 50%
            personal_commission = 0
            commission_fp = 0  # Track FP commission separately
            commission_discount = 0  # Track discount commission separately
            commission_over_100 = 0  # Track over 100% bonus separately

            # ============================================================================
            # DETERMINE WHICH COMMISSION RATES TO USE
            # ============================================================================
            # Check if this store should use special rates (TEMPORARY - easy to revert)
            use_special_rates = ENABLE_RWD_SPECIAL_RATES and emp_store_code == 'RWD'
            rates = RWD_SPECIAL_RATES if use_special_rates else STANDARD_RATES
            # ============================================================================

            # TIER 1: 50% - 70% of target
            if achievement_rate >= 50 and achievement_rate < 70:
                # Calculate commission on non-VAT revenue
                commission_fp = non_jewelry_fp_revenue * rates['tier1']['fp']
                commission_discount = non_jewelry_disc_revenue * rates['tier1']['discount']
                personal_commission = commission_fp + commission_discount

            # TIER 2: 70% - 100% of target
            elif achievement_rate >= 70 and achievement_rate < 100:
                # Calculate commission on non-VAT revenue
                commission_fp = non_jewelry_fp_revenue * rates['tier2']['fp']
                commission_discount = non_jewelry_disc_revenue * rates['tier2']['discount']
                personal_commission = commission_fp + commission_discount

            # TIER 3: 100% of target and above
            elif achievement_rate >= 100:
                # Base commission at 100% tier rates (on ALL non-jewelry sales)
                # Calculate commission on non-VAT revenue
                commission_fp = non_jewelry_fp_revenue * rates['tier3']['fp']
                commission_discount = non_jewelry_disc_revenue * rates['tier3']['discount']
                commission_up_to_target = commission_fp + commission_discount
                personal_commission = commission_up_to_target

                # TIER 4: Over 100% - Additional bonus
                # Only on FP, non-jewelry, NOT TIT/TVL sales that occurred AFTER reaching 100% target
                # Calculate commission on non-VAT revenue
                if achievement_rate > 100 and fp_non_jewelry_after_target > 0:
                    commission_over_100 = fp_non_jewelry_after_target * rates['over_100']

            # Calculate commission components
            # commission_100_and_below includes: FP, discount, jewelry, suitcase, and hand carry
            # commission_over_100: Tier 4 bonus only
            commission_100_and_below_fp = commission_fp
            commission_100_and_below_discount = commission_discount
            commission_100_and_below = personal_commission + jewelry_commission + suitcase_commission + hand_carry_commission
            total_commission = commission_100_and_below + commission_over_100

            # Add result for this employee (for DataFrame row)
            results.append({
                'employee_code': employee_code,
                'fullname': employee_name,
                'store_code': emp_store_code,
                'commission_100_and_below_fp': commission_100_and_below_fp,
                'commission_100_and_below_discount': commission_100_and_below_discount,
                'commission_over_100': commission_over_100,
                'commission_jewelry': jewelry_commission,
                'commission_vhernier': commission_vhernier,
                'commission_rosa_maria': commission_rosa_maria,
                'commission_suitcase': suitcase_commission,
                'commission_hand_carry': hand_carry_commission,
                'total': total_commission
            })

        # 6. CONVERT RESULTS TO DATAFRAME
        # Create pandas DataFrame with specified columns
        results_df = pd.DataFrame(results, columns=[
            'employee_code',
            'fullname',
            'store_code',
            'commission_100_and_below_fp',
            'commission_100_and_below_discount',
            'commission_over_100',
            'commission_jewelry',
            'commission_vhernier',
            'commission_rosa_maria',
            'commission_suitcase',
            'commission_hand_carry',
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
        employees: List[Dict[str, Any]],
        hand_carry_upcs: Optional[List[str]] = None,
        all_sales_df: Optional[pd.DataFrame] = None
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
            hand_carry_upcs: Optional list of hand carry UPCs (from PostgreSQL rps.carrier_item)
            all_sales_df: Optional row-level sales DataFrame for hand carry FP/discounted adjustment

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

        actual_full_price_revenue = store_data.get('ACTUAL_FULL_PRICE_REVENUE', 0)
        actual_discounted_revenue = store_data.get('ACTUAL_DISCOUNTED_REVENUE', 0)
        actual_jewelry_revenue = store_data.get('ACTUAL_JEWELRY_REVENUE', 0)
        actual_suitcase_revenue = store_data.get('ACTUAL_SUITCASE_REVENUE', 0)
        # CR #21 V3: 7-type filtered total (FP + markdown + jewelry + suitcase)
        actual_revenue = actual_full_price_revenue + actual_discounted_revenue + actual_jewelry_revenue + actual_suitcase_revenue

        # STEP 1: Check Store Eligibility & Calculate Achievement
        achievement_pct = (actual_revenue / store_target * 100) if store_target > 0 else 0
        actual_fp_ratio = (actual_full_price_revenue / actual_revenue) if actual_revenue > 0 else 0

        # Check if actual FP ratio meets target
        if actual_fp_ratio < store_fp_ratio_target:
            fp_shortage = (store_fp_ratio_target - actual_fp_ratio) * actual_revenue
            required_discounted = fp_shortage * FP_COMPENSATION_MULTIPLIER  # 200% compensation

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
        # IMPORTANT: Match by employee_username (not employee_code) since CUSTOMER.UDF4_STRING may be NULL
        # For RHN/RWP stores, we need to include both stores' sales
        if store_code in ('RHN', 'RWP'):
            employee_sales_rhn = self.repository.get_employee_sales_data('RHN', from_date, to_date)
            employee_sales_rwp = self.repository.get_employee_sales_data('RWP', from_date, to_date)
            # Combine and deduplicate
            employee_sales_combined = employee_sales_rhn + employee_sales_rwp
            # Create lookup by employee_username (more reliable than employee_code)
            employee_sales_lookup = {}
            for emp_sale in employee_sales_combined:
                emp_username = emp_sale.get('EMPLOYEE_USERNAME')
                if emp_username and emp_username not in employee_sales_lookup:
                    employee_sales_lookup[emp_username] = {
                        'fp_revenue': 0,
                        'disc_revenue': 0
                    }
                if emp_username:
                    employee_sales_lookup[emp_username]['fp_revenue'] += emp_sale.get('EMPLOYEE_FP_REVENUE', 0)
                    employee_sales_lookup[emp_username]['disc_revenue'] += emp_sale.get('EMPLOYEE_DISCOUNTED_REVENUE', 0)
        else:
            employee_sales_data = self.repository.get_employee_sales_data(store_code, from_date, to_date)
            # Create lookup by employee_username (more reliable than employee_code)
            employee_sales_lookup = {}
            for emp_sale in employee_sales_data:
                emp_username = emp_sale.get('EMPLOYEE_USERNAME')
                if emp_username:
                    employee_sales_lookup[emp_username] = {
                        'fp_revenue': emp_sale.get('EMPLOYEE_FP_REVENUE', 0),
                        'disc_revenue': emp_sale.get('EMPLOYEE_DISCOUNTED_REVENUE', 0)
                    }

        # Hand carry adjustment: EMPLOYEE_SALES_DATA FP/discounted includes hand carry items
        # which should NOT count toward store commission tiers. Subtract hand carry FP/discounted
        # amounts per employee using the row-level personal sales data.
        # Uses doc_store_code (transaction store) to match the same-store filter in EMPLOYEE_SALES_DATA,
        # since employees can sell at different stores and those cross-store sales are already excluded
        # from FP/discounted by the query's CASE expression.
        if hand_carry_upcs and all_sales_df is not None and len(all_sales_df) > 0:
            hc_upc_set = set(hand_carry_upcs)
            # Filter to hand carry items, non-WJEW department
            hc_df = all_sales_df[
                (all_sales_df['upc_clean'].isin(hc_upc_set)) &
                (all_sales_df['department'] != 'WJEW')
            ].copy()

            if len(hc_df) > 0:
                # Apply same store filter as EMPLOYEE_SALES_DATA FP/discounted CASE
                # using doc_store_code (where the transaction happened, not employee's home store)
                if store_code in ('RHN', 'RWP'):
                    # For RHN/RWP: employees from either store, transactions at either store
                    hc_df = hc_df[
                        (hc_df['store_code'].isin(['RHN', 'RWP'])) &
                        (hc_df['doc_store_code'].isin(['RHN', 'RWP']))
                    ]
                else:
                    # For other stores: employee's home store matches AND transaction at same store
                    hc_df = hc_df[
                        (hc_df['store_code'] == store_code) &
                        (hc_df['doc_store_code'] == store_code)
                    ]

                if len(hc_df) > 0:
                    for emp_username, emp_data in employee_sales_lookup.items():
                        emp_hc = hc_df[hc_df['employee_username'] == emp_username]
                        if len(emp_hc) > 0:
                            # FP hand carry: discount_rate <= 0.3 (matches EMPLOYEE_SALES_DATA FP CASE)
                            fp_hc = emp_hc[emp_hc['discount_rate'] <= DISCOUNT_THRESHOLD]['revenue_before_vat'].sum()
                            # Discounted hand carry: discount_rate > 0.3
                            disc_hc = emp_hc[emp_hc['discount_rate'] > DISCOUNT_THRESHOLD]['revenue_before_vat'].sum()
                            emp_data['fp_revenue'] = emp_data['fp_revenue'] - fp_hc
                            emp_data['disc_revenue'] = emp_data['disc_revenue'] - disc_hc

        # STEP 2: Calculate Each Employee's Contribution to Store Pool
        employee_contributions = []

        for employee in employees:
            employee_code = employee['employee_code']
            employee_username = employee.get('employee_username')  # From Google Sheets "Employee Acc" column
            full_name = employee['full_name']
            seniority = employee['seniority']  # in years
            is_manager = employee['is_manager']
            is_probation = employee.get('is_probation', False)  # Default to False if not provided
            working_day_count = employee['working_day_count']

            # Get employee sales data using employee_username for lookup (more reliable than employee_code)
            # Some employees may not have username (can't sell but get store commission share)
            emp_sales = employee_sales_lookup.get(employee_username, {'fp_revenue': 0, 'disc_revenue': 0}) if employee_username else {'fp_revenue': 0, 'disc_revenue': 0}
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
            individual_share = POOL_INDIVIDUAL_RATIO * emp['contribution']

            # 4.2: Calculate Equal Share (30% pool distribution)
            if store_code in ('RHN', 'RWP'):
                # RHN, RWP: Distribute based on working day contribution
                working_day_ratio = emp['working_day_count'] / total_working_days if total_working_days > 0 else 0
                equal_share = POOL_EQUAL_RATIO * store_pool * working_day_ratio
            else:
                # Other stores: Divide equally among employees
                equal_share = POOL_EQUAL_RATIO * store_pool / total_employee_count if total_employee_count > 0 else 0

            # 4.3: Calculate Manager Bonus (if applicable)
            manager_bonus = 0
            if emp['is_manager']:
                if achievement_pct >= 100:
                    manager_bonus = MANAGER_BONUS_100_PLUS
                elif achievement_pct >= 70:
                    manager_bonus = MANAGER_BONUS_70_PLUS

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

            # Create employee sales lookup by employee_code (more reliable than name matching)
            # EMPLOYEE_CODE comes from Oracle's CUSTOMER.UDF4_STRING
            employee_sales_lookup = {
                emp_sale.get('EMPLOYEE_CODE'): emp_sale
                for emp_sale in employee_sales_list
                if emp_sale.get('EMPLOYEE_CODE')
            }

            # Filter to only requested employees with their provided seniority
            requested_employees_data = []
            for emp in employees_by_store[store_code]:
                emp_name = emp.get('employee_name')
                emp_code = emp.get('employee_code')
                emp_sales = employee_sales_lookup.get(emp_code)

                if emp_sales:
                    # Add seniority from request
                    requested_employees_data.append({
                        'EMPLOYEE_FULL_NAME': emp_name,
                        'EMPLOYEE_CODE': emp_code,
                        'EMPLOYEE_FP_REVENUE': emp_sales.get('EMPLOYEE_FP_REVENUE', 0),
                        'EMPLOYEE_DISCOUNTED_REVENUE': emp_sales.get('EMPLOYEE_DISCOUNTED_REVENUE', 0),
                        'TENURE_MONTHS': emp.get('seniority', 0),
                        'IS_MANAGER': emp.get('is_manager', False)
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
                emp_code = emp_data['EMPLOYEE_CODE']
                fp_revenue = emp_data['EMPLOYEE_FP_REVENUE']
                discounted_revenue = emp_data['EMPLOYEE_DISCOUNTED_REVENUE']
                tenure_months = emp_data['TENURE_MONTHS']

                is_manager = emp_data['IS_MANAGER']

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
                    'employee_code': emp_code,
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

            # Map results back to employees using employee_code
            for emp_commission in employee_commissions:
                emp_code = emp_commission.get('employee_code', '')
                emp_name = emp_commission['employee_name']

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

    def calculate_combined_commission(
        self,
        store_commission_result: Dict[str, Any],
        personal_commission_df: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Combine store commission and personal commission into a comprehensive report

        Args:
            store_commission_result: Output from calculate_store_commission_v2 containing:
                - eligible: bool
                - achievement_pct: float
                - employees: list of dicts with employee_code, individual_share, equal_share, manager_bonus, total_store_commission
            personal_commission_df: DataFrame from calculate_personal_commissions with columns:
                - employee_code
                - fullname
                - store_code
                - commission_100_and_below_fp
                - commission_100_and_below_discount
                - commission_over_100
                - commission_jewelry
                - commission_vhernier
                - commission_rosa_maria
                - commission_suitcase
                - commission_hand_carry
                - total

        Returns:
            DataFrame with combined commission data including:
                - employee_code
                - employee_name
                - store_code
                - store_achievement_pct
                - store_commission_70pct (individual_share)
                - store_commission_30pct (equal_share)
                - manager_bonus
                - total_store_commission
                - personal_commission_fp_under_100
                - personal_commission_discount_under_100
                - personal_commission_over_100
                - personal_commission_jewelry
                - personal_commission_vhernier
                - personal_commission_rosa_maria
                - personal_commission_suitcase
                - personal_commission_hand_carry
                - personal_commission_total
                - total_handout_commission
        """
        # Extract store achievement percentage
        store_achievement_pct = store_commission_result.get('achievement_pct', 0)
        is_eligible = store_commission_result.get('eligible', False)

        # Create lookup dictionary for store commission by employee code
        store_commission_lookup = {}
        if is_eligible and 'employees' in store_commission_result:
            for emp in store_commission_result['employees']:
                store_commission_lookup[emp['employee_code']] = {
                    'individual_share': emp.get('individual_share', 0),
                    'equal_share': emp.get('equal_share', 0),
                    'manager_bonus': emp.get('manager_bonus', 0),
                    'total_store_commission': emp.get('total_store_commission', 0)
                }

        # Create result list
        combined_results = []

        # Iterate through personal commission DataFrame
        for _, row in personal_commission_df.iterrows():
            employee_code = row['employee_code']

            # Get store commission for this employee (0 if not found or store not eligible)
            store_comm = store_commission_lookup.get(employee_code, {
                'individual_share': 0,
                'equal_share': 0,
                'manager_bonus': 0,
                'total_store_commission': 0
            })

            # Calculate total handout commission
            total_handout = store_comm['total_store_commission'] + row['total']

            combined_results.append({
                'employee_code': employee_code,
                'employee_name': row['fullname'],
                'store_code': row['store_code'],
                'store_achievement_pct': store_achievement_pct,
                'store_commission_70pct': store_comm['individual_share'],
                'store_commission_30pct': store_comm['equal_share'],
                'manager_bonus': store_comm['manager_bonus'],
                'total_store_commission': store_comm['total_store_commission'],
                'personal_commission_fp_under_100': row['commission_100_and_below_fp'],
                'personal_commission_discount_under_100': row['commission_100_and_below_discount'],
                'personal_commission_over_100': row['commission_over_100'],
                'personal_commission_jewelry': row['commission_jewelry'],
                'personal_commission_vhernier': row['commission_vhernier'],
                'personal_commission_rosa_maria': row['commission_rosa_maria'],
                'personal_commission_suitcase': row['commission_suitcase'],
                'personal_commission_hand_carry': row['commission_hand_carry'],
                'personal_commission_total': row['total'],
                'total_handout_commission': total_handout
            })

        # Convert to DataFrame
        result_df = pd.DataFrame(combined_results, columns=[
            'employee_code',
            'employee_name',
            'store_code',
            'store_achievement_pct',
            'store_commission_70pct',
            'store_commission_30pct',
            'manager_bonus',
            'total_store_commission',
            'personal_commission_fp_under_100',
            'personal_commission_discount_under_100',
            'personal_commission_over_100',
            'personal_commission_jewelry',
            'personal_commission_vhernier',
            'personal_commission_rosa_maria',
            'personal_commission_suitcase',
            'personal_commission_hand_carry',
            'personal_commission_total',
            'total_handout_commission'
        ])

        return result_df

    def calculate_commissions_for_period(
        self,
        month: int,
        year: int,
    ) -> Dict[str, Any]:
        """
        Full commission calculation pipeline for a given month/year.

        Reads employees and store settings from PostgreSQL, fetches Oracle sales data,
        applies revenue adjustments, calculates all commission components,
        and returns the per-employee breakdown.

        Args:
            month: Commission month (1-12)
            year:  Commission year

        Returns:
            Dict with success, month, year, stores (nested employees with result objects)
        """
        try:
            # 1. Get employees from PostgreSQL (includes retailpro_username, join_date)
            all_employees = self._get_employees_with_username_for_period(month, year)
            if not all_employees:
                return {'success': False, 'error': 'No employees found for this period'}

            # 2. Get store settings from PostgreSQL
            store_settings_result = CommissionStoreSettingsService().get_commission_stores(month, year)
            if not store_settings_result.get('success'):
                return {'success': False, 'error': 'Failed to load store settings'}
            store_settings_map = {s['store_code']: s for s in store_settings_result.get('stores', [])}

            # 3. Load revenue adjustments → {employee_code: {revenue_type: delta}}
            adjustments_dict: Dict[str, Dict[str, int]] = {}
            conn = get_postgres_connection()
            if conn:
                try:
                    cursor = conn.cursor()
                    cursor.execute(
                        'SELECT employee_code, revenue_type, adjustment '
                        'FROM commission_revenue_adjustments WHERE month = %s AND year = %s',
                        (month, year)
                    )
                    for emp_code, rev_type, delta in cursor.fetchall():
                        if emp_code not in adjustments_dict:
                            adjustments_dict[emp_code] = {}
                        adjustments_dict[emp_code][rev_type] = delta
                    cursor.close()
                finally:
                    conn.close()

            # 4. Compute period date range for store commission queries
            last_day_num = calendar.monthrange(year, month)[1]
            start_date = f'{year:04d}-{month:02d}-01 00:00:00'
            end_date = f'{year:04d}-{month:02d}-{last_day_num:02d} 23:59:59'
            query_date = {'from_date': start_date, 'to_date': end_date}

            # 5. Group employees by store, compute seniority
            # Use the period end date (not today) so seniority is accurate for the
            # commission month, even when calculations are run retroactively.
            period_end_date = date(year, month, last_day_num)
            employees_by_store: Dict[str, List[Dict]] = {}
            for emp in all_employees:
                store_code = emp['store_code']
                if store_code not in employees_by_store:
                    employees_by_store[store_code] = []

                # Compute seniority in years from join_date relative to period end
                join_date_raw = emp.get('join_date')
                if join_date_raw:
                    if isinstance(join_date_raw, str):
                        from datetime import datetime as _dt
                        join_date_obj = _dt.strptime(join_date_raw, '%Y-%m-%d').date()
                    else:
                        join_date_obj = join_date_raw
                    seniority_years = (period_end_date - join_date_obj).days / 365.25
                else:
                    seniority_years = 0

                contract = emp.get('contract', '').upper()
                is_probation = contract == 'PROBATION'

                employees_by_store[store_code].append({
                    'employee_code':   emp['employee_code'],
                    'employee_username': emp.get('retailpro_username'),
                    'full_name':       emp['full_name'],
                    'store_code':      store_code,
                    'personal_target': emp.get('personal_target'),
                    'is_manager':      emp.get('is_manager') or False,
                    'working_day':     emp.get('working_day') or 0,
                    'working_day_count': emp.get('working_day') or 0,
                    'seniority':       seniority_years,
                    'is_probation':    is_probation,
                })

            # 5b. Load hand carry UPCs and row-level sales data once (shared by all stores)
            hand_carry_upcs = self.repository.get_hand_carry_upcs()
            all_sales_df = self.repository.get_personal_commission_sales_data(year, month)
            all_sales_df.columns = all_sales_df.columns.str.lower()
            if 'upc_clean' not in all_sales_df.columns and 'upc' in all_sales_df.columns:
                all_sales_df['upc_clean'] = all_sales_df['upc'].astype(str).str.strip()

            # 6. Process each store
            all_combined_dfs = []
            all_store_results = []

            for store_code, store_employees in employees_by_store.items():
                ss = store_settings_map.get(store_code, {})
                store_target = ss.get('store_target') or 0
                fp_ratio_raw = ss.get('fp_ratio_target')
                store_fp_ratio = (fp_ratio_raw / 100.0) if fp_ratio_raw is not None else 0.0

                # Store commission (uses Oracle store/employee sales data)
                store_result = self.calculate_store_commission_v2(
                    store_code=store_code,
                    store_target=store_target,
                    store_fp_ratio_target=store_fp_ratio,
                    query_date=query_date,
                    employees=store_employees,
                    hand_carry_upcs=hand_carry_upcs,
                    all_sales_df=all_sales_df
                )

                # Personal commission (with revenue adjustments applied, using preloaded data)
                personal_df = self.calculate_personal_commissions(
                    month=month,
                    year=year,
                    employees=store_employees,
                    store_code=store_code,
                    revenue_adjustments=adjustments_dict,
                    preloaded_sales_df=all_sales_df,
                    preloaded_hand_carry_upcs=hand_carry_upcs
                )

                # Combine store + personal
                combined_df = self.calculate_combined_commission(
                    store_commission_result=store_result,
                    personal_commission_df=personal_df
                )

                all_store_results.append(store_result)
                all_combined_dfs.append(combined_df)

            if not all_combined_dfs:
                return {'success': False, 'error': 'No commission results to process'}

            final_df = pd.concat(all_combined_dfs, ignore_index=True)

            # 7. Build response
            stores_response = []
            for store_result in all_store_results:
                sc = store_result['store_code']
                store_df = final_df[final_df['store_code'] == sc]

                employees_response = []
                for _, row in store_df.iterrows():
                    jewelry_net = (
                        float(row.get('personal_commission_jewelry', 0) or 0)
                        - float(row.get('personal_commission_vhernier', 0) or 0)
                        - float(row.get('personal_commission_rosa_maria', 0) or 0)
                    )
                    employees_response.append({
                        'employee_code': row['employee_code'],
                        'full_name':     row['employee_name'],
                        'result': {
                            'individual':            int(row.get('store_commission_70pct', 0) or 0),
                            'shared':                int(row.get('store_commission_30pct', 0) or 0),
                            'manager':               int(row.get('manager_bonus', 0) or 0),
                            'fp_below_target':       int(row.get('personal_commission_fp_under_100', 0) or 0),
                            'discount_below_target': int(row.get('personal_commission_discount_under_100', 0) or 0),
                            'over_target':           int(row.get('personal_commission_over_100', 0) or 0),
                            'jewelry':               int(jewelry_net),
                            'vhernier':              int(row.get('personal_commission_vhernier', 0) or 0),
                            'rosa_maria':            int(row.get('personal_commission_rosa_maria', 0) or 0),
                            'suitcase':              int(row.get('personal_commission_suitcase', 0) or 0),
                            'hand_carry':            int(row.get('personal_commission_hand_carry', 0) or 0),
                            'store_total':           int(row.get('total_store_commission', 0) or 0),
                            'personal_total':        int(row.get('personal_commission_total', 0) or 0),
                            'employee_total':        int(row.get('total_handout_commission', 0) or 0),
                        }
                    })

                stores_response.append({
                    'store_code':    sc,
                    'store_name':    store_result.get('store_name', sc),
                    'eligible':      store_result.get('eligible', False),
                    'achievement_pct': store_result.get('achievement_pct', 0),
                    'employees':     employees_response,
                })

            result = {
                'success':            True,
                'month':              month,
                'year':               year,
                'stores_processed':   len(employees_by_store),
                'employees_processed': len(final_df),
                'stores':             stores_response,
            }
            return result

        except Exception as e:
            return {'success': False, 'error': str(e)}


class CommissionSettingsService:
    """Service for managing per-employee commission settings in PostgreSQL"""

    def init_database(self) -> Dict[str, Any]:
        """Create commission_settings table if it does not exist"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS commission_settings (
                    id SERIAL PRIMARY KEY,
                    employee_code VARCHAR(50) NOT NULL,
                    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
                    year INTEGER NOT NULL,
                    -- is_manager is NOT used; kept for schema compat. The authoritative
                    -- source is employee_manager_history, resolved at query time.
                    is_manager BOOLEAN DEFAULT FALSE,
                    personal_target BIGINT,
                    working_day INTEGER,
                    is_commission_active BOOLEAN DEFAULT TRUE,
                    store_code_override VARCHAR(20) DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (employee_code, month, year)
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_commission_settings_period
                ON commission_settings (month, year)
            ''')
            # Migrations: add columns if table already existed without them
            cursor.execute('''
                ALTER TABLE commission_settings
                ADD COLUMN IF NOT EXISTS is_commission_active BOOLEAN DEFAULT TRUE
            ''')
            cursor.execute('''
                ALTER TABLE commission_settings
                ADD COLUMN IF NOT EXISTS store_code_override VARCHAR(20) DEFAULT NULL
            ''')
            conn.commit()
            cursor.close()
            return {'success': True}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}

        finally:
            if conn:
                conn.close()

    def get_commission_employees(self, month: int, year: int) -> Dict[str, Any]:
        """
        Get active store employees grouped by store, with commission settings for the given period.

        Uses shared _query_store_employees() for the LATERAL JOIN query, then groups
        results by store for the settings UI.

        Args:
            month: Commission month (1-12)
            year:  Commission year

        Returns:
            Dict with keys 'month', 'year', 'stores' (list of store dicts with nested employees)
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

            records = _query_store_employees(conn, month, year)

            # Group rows by store
            stores_map: Dict[str, Any] = {}
            for record in records:
                store_code = record['store_code']

                if store_code not in stores_map:
                    stores_map[store_code] = {
                        'store_code': store_code,
                        'store_name': record['store_name'],
                        'employees': []
                    }

                join_date = record['join_date']
                stores_map[store_code]['employees'].append({
                    'employee_code': record['employee_code'],
                    'full_name': record['full_name'],
                    'join_date': join_date.isoformat() if join_date else None,
                    'contract': record['contract'].lower() if record['contract'] else None,
                    'is_manager': record['is_manager'],
                    'personal_target': record['personal_target'],
                    'working_day': record['working_day'],
                    'is_commission_active': record['is_commission_active'],
                    'store_code_override': record['store_code_override']
                })

            return {
                'success': True,
                'month': month,
                'year': year,
                'stores': list(stores_map.values())
            }

        except Exception as e:
            return {'success': False, 'error': str(e)}

        finally:
            if conn:
                conn.close()

    def update_commission_settings(
        self,
        employee_code: str,
        month: int,
        year: int,
        personal_target: int,
        working_day: int,
        is_commission_active: bool = True,
        store_code_override: str = None,
        is_manager: bool = None,
        contract: str = None
    ) -> Dict[str, Any]:
        """
        Upsert commission settings for one employee for a given period.

        CR #17: When is_manager or contract are provided, also upserts
        employee_status_history for the target (month, year) period.

        Args:
            employee_code:        Employee code (e.g. "GL013")
            month:                Commission month (1-12)
            year:                 Commission year
            personal_target:      Personal sales target (VND)
            working_day:          Number of working days in the period
            is_commission_active: Whether employee participates in commission (default True)
            store_code_override:  Override store for this period (None = use history)
            is_manager:           Override is_manager for this period (None = no change)
            contract:             Contract type code for this period (None = no change)

        Returns:
            Dict with 'success' bool and updated settings on success
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

            cursor = conn.cursor()

            # CR #17: if is_manager or contract provided, upsert employee_status_history
            if is_manager is not None or contract is not None:
                snapshot_err = self._upsert_employee_status_snapshot(
                    cursor, employee_code, month, year, is_manager, contract
                )
                if snapshot_err:
                    conn.rollback()
                    cursor.close()
                    return snapshot_err

            cursor.execute('''
                INSERT INTO commission_settings
                    (employee_code, month, year, personal_target, working_day,
                     is_commission_active, store_code_override, updated_at)
                VALUES
                    (%(employee_code)s, %(month)s, %(year)s,
                     %(personal_target)s, %(working_day)s,
                     %(is_commission_active)s, %(store_code_override)s,
                     CURRENT_TIMESTAMP)
                ON CONFLICT (employee_code, month, year) DO UPDATE SET
                    personal_target      = EXCLUDED.personal_target,
                    working_day          = EXCLUDED.working_day,
                    is_commission_active = EXCLUDED.is_commission_active,
                    store_code_override  = EXCLUDED.store_code_override,
                    updated_at           = CURRENT_TIMESTAMP
                RETURNING employee_code, month, year, is_manager, personal_target,
                          working_day, is_commission_active, store_code_override
            ''', {
                'employee_code': employee_code,
                'month': month,
                'year': year,
                'personal_target': personal_target,
                'working_day': working_day,
                'is_commission_active': is_commission_active,
                'store_code_override': store_code_override
            })

            row = cursor.fetchone()
            columns = [desc[0] for desc in cursor.description]
            conn.commit()
            cursor.close()

            result = dict(zip(columns, row))
            return {'success': True, 'data': result}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}

        finally:
            if conn:
                conn.close()

    def _upsert_employee_status_snapshot(
        self, cursor, employee_code: str, month: int, year: int,
        is_manager: bool = None, contract: str = None
    ):
        """
        CR #17: Upsert employee_status_history for a given period.
        Carries forward tracked fields from the latest snapshot or employees table.

        Returns None on success, or an error dict on failure.
        """
        # Look up the employee by code
        cursor.execute('''
            SELECT e.sid, e.is_active, e.store_id, e.department_id,
                   e.contract_type_id
            FROM employees e
            WHERE e.employee_code = %s
        ''', (employee_code,))
        emp = cursor.fetchone()
        if not emp:
            return {'success': False, 'error': f'Employee not found: {employee_code}'}

        employee_sid, emp_is_active, emp_store_id, emp_department_id, emp_contract_type_id = emp

        # Get latest snapshot to carry forward values
        cursor.execute('''
            SELECT is_active, store_id, department_id, contract_type_id, is_manager
            FROM employee_status_history
            WHERE employee_sid = %s
            ORDER BY period_year DESC, period_month DESC
            LIMIT 1
        ''', (employee_sid,))
        latest = cursor.fetchone()

        if latest:
            snap_is_active, snap_store_id, snap_department_id, snap_contract_type_id, snap_is_manager = latest
        else:
            snap_is_active = emp_is_active
            snap_store_id = emp_store_id
            snap_department_id = emp_department_id
            snap_contract_type_id = emp_contract_type_id
            snap_is_manager = False

        # Apply overrides from request
        final_is_manager = is_manager if is_manager is not None else snap_is_manager
        final_contract_type_id = snap_contract_type_id

        if contract is not None:
            cursor.execute(
                'SELECT id FROM contract_types WHERE LOWER(code) = LOWER(%s)',
                (contract,)
            )
            ct_row = cursor.fetchone()
            if not ct_row:
                return {'success': False, 'error': f'Invalid contract type: {contract}'}
            final_contract_type_id = ct_row[0]

        # Upsert the snapshot
        cursor.execute('''
            INSERT INTO employee_status_history
                (employee_sid, period_month, period_year, is_active, store_id,
                 department_id, contract_type_id, is_manager)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (employee_sid, period_month, period_year) DO UPDATE SET
                is_active = EXCLUDED.is_active,
                store_id = EXCLUDED.store_id,
                department_id = EXCLUDED.department_id,
                contract_type_id = EXCLUDED.contract_type_id,
                is_manager = EXCLUDED.is_manager,
                updated_at = NOW()
        ''', (employee_sid, month, year, snap_is_active, snap_store_id,
              snap_department_id, final_contract_type_id, final_is_manager))

        return None


class CommissionStoreSettingsService:
    """
    Manages per-period store-level commission settings:
    store_target (VND) and fp_ratio_target (integer percent, e.g. 60 for 60%).
    """

    def init_database(self) -> Dict[str, Any]:
        """
        Create commission_store_settings table (idempotent).

        fp_ratio_target is stored as INTEGER percent (e.g. 60 = 60%).
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS commission_store_settings (
                    id SERIAL PRIMARY KEY,
                    store_code VARCHAR(50) NOT NULL,
                    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
                    year INTEGER NOT NULL,
                    store_target BIGINT,
                    fp_ratio_target INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (store_code, month, year)
                )
            ''')
            conn.commit()
            cursor.close()
            return {'success': True}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}

        finally:
            if conn:
                conn.close()

    def get_commission_stores(self, month: int, year: int) -> Dict[str, Any]:
        """
        Return all stores LEFT JOINed with their commission settings for month/year.
        Every store is included; stores without a settings row return nulls for editable fields.
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

            cursor = conn.cursor()
            cursor.execute('''
                SELECT
                    s.store_code,
                    s.store_name,
                    css.store_target,
                    css.fp_ratio_target
                FROM stores s
                LEFT JOIN commission_store_settings css
                    ON css.store_code = s.store_code
                   AND css.month = %(month)s
                   AND css.year  = %(year)s
                ORDER BY s.store_code
            ''', {'month': month, 'year': year})

            rows = cursor.fetchall()
            cursor.close()

            stores = [
                {
                    'store_code': row[0],
                    'store_name': row[1],
                    'store_target': row[2],
                    'fp_ratio_target': row[3]
                }
                for row in rows
            ]

            return {'success': True, 'month': month, 'year': year, 'stores': stores}

        except Exception as e:
            return {'success': False, 'error': str(e)}

        finally:
            if conn:
                conn.close()

    def update_commission_store_settings(
        self,
        store_code: str,
        month: int,
        year: int,
        store_target,
        fp_ratio_target
    ) -> Dict[str, Any]:
        """
        Upsert commission settings for one store for a given period.

        Args:
            store_code:       Store code (e.g. "RWT") — must exist in stores table
            month:            Commission month (1-12)
            year:             Commission year
            store_target:     Store revenue target (VND integer or None)
            fp_ratio_target:  FP ratio target as integer percent (e.g. 60 for 60%) or None

        Returns:
            Dict with 'success' bool and saved record on success, or error message.
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

            cursor = conn.cursor()

            # Validate store_code exists
            cursor.execute('SELECT 1 FROM stores WHERE store_code = %s', (store_code,))
            if not cursor.fetchone():
                cursor.close()
                return {'success': False, 'error': f'Store not found: {store_code}'}

            cursor.execute('''
                INSERT INTO commission_store_settings
                    (store_code, month, year, store_target, fp_ratio_target, updated_at)
                VALUES
                    (%(store_code)s, %(month)s, %(year)s,
                     %(store_target)s, %(fp_ratio_target)s,
                     CURRENT_TIMESTAMP)
                ON CONFLICT (store_code, month, year) DO UPDATE SET
                    store_target     = EXCLUDED.store_target,
                    fp_ratio_target  = EXCLUDED.fp_ratio_target,
                    updated_at       = CURRENT_TIMESTAMP
                RETURNING store_code, month, year, store_target, fp_ratio_target
            ''', {
                'store_code': store_code,
                'month': month,
                'year': year,
                'store_target': store_target,
                'fp_ratio_target': fp_ratio_target
            })

            row = cursor.fetchone()
            columns = [desc[0] for desc in cursor.description]
            conn.commit()
            cursor.close()

            result = dict(zip(columns, row))
            return {'success': True, 'data': result}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}

        finally:
            if conn:
                conn.close()


class CommissionRevenueService:
    """
    Manages per-employee per-period revenue adjustment deltas in PostgreSQL,
    and provides a live revenue breakdown by querying Oracle sales data.
    """

    def init_database(self) -> Dict[str, Any]:
        """Create commission_revenue_adjustments table (idempotent)."""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS commission_revenue_adjustments (
                    id SERIAL PRIMARY KEY,
                    employee_code VARCHAR(50) NOT NULL,
                    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
                    year INTEGER NOT NULL,
                    revenue_type VARCHAR(50) NOT NULL CHECK (revenue_type IN (
                        'full_price', 'markdown', 'jewelry', 'vhernier', 'rosa_maria',
                        'hand_carry', 'suitcase'
                    )),
                    adjustment BIGINT NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (employee_code, month, year, revenue_type)
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_commission_revenue_adj_period
                ON commission_revenue_adjustments (month, year)
            ''')
            conn.commit()
            cursor.close()
            return {'success': True}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}

        finally:
            if conn:
                conn.close()

    def save_revenue_adjustments(
        self,
        employee_code: str,
        month: int,
        year: int,
        adjustments: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Upsert revenue adjustment deltas for an employee for a given period.

        Args:
            employee_code: Employee code (e.g. "GL013")
            month: Commission month (1-12)
            year: Commission year
            adjustments: List of {'revenue_type': str, 'adjustment': int}

        Returns:
            Dict with success bool, saved adjustment list on success, or error string.
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

            cursor = conn.cursor()

            # Validate employee_code exists
            cursor.execute('SELECT 1 FROM employees WHERE employee_code = %s', (employee_code,))
            if not cursor.fetchone():
                cursor.close()
                return {'success': False, 'error': f'Employee not found: {employee_code}', 'not_found': True}

            # Validate each adjustment item
            for item in adjustments:
                if 'revenue_type' not in item or 'adjustment' not in item:
                    cursor.close()
                    return {
                        'success': False,
                        'error': 'Each adjustment must have revenue_type and adjustment'
                    }
                if item['revenue_type'] not in VALID_REVENUE_TYPES:
                    cursor.close()
                    return {
                        'success': False,
                        'error': f"Invalid revenue_type: '{item['revenue_type']}'. "
                                 f"Valid types: {sorted(VALID_REVENUE_TYPES)}"
                    }
                try:
                    int(item['adjustment'])
                except (ValueError, TypeError):
                    cursor.close()
                    return {
                        'success': False,
                        'error': f"adjustment must be an integer, got: {item['adjustment']!r}"
                    }

            # Upsert each adjustment
            saved = []
            for item in adjustments:
                rev_type = item['revenue_type']
                delta = int(item['adjustment'])
                cursor.execute('''
                    INSERT INTO commission_revenue_adjustments
                        (employee_code, month, year, revenue_type, adjustment, updated_at)
                    VALUES
                        (%(employee_code)s, %(month)s, %(year)s, %(rev_type)s, %(delta)s,
                         CURRENT_TIMESTAMP)
                    ON CONFLICT (employee_code, month, year, revenue_type) DO UPDATE SET
                        adjustment = EXCLUDED.adjustment,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING revenue_type, adjustment
                ''', {
                    'employee_code': employee_code,
                    'month':         month,
                    'year':          year,
                    'rev_type':      rev_type,
                    'delta':         delta,
                })
                row = cursor.fetchone()
                saved.append({'revenue_type': row[0], 'adjustment': row[1]})

            conn.commit()
            cursor.close()

            return {
                'success': True,
                'data': {
                    'employee_code': employee_code,
                    'month':         month,
                    'year':          year,
                    'adjustments':   saved,
                }
            }

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}

        finally:
            if conn:
                conn.close()

    def _load_adjustments(self, month: int, year: int) -> Dict[str, Dict[str, int]]:
        """Load all revenue adjustments for a period → {employee_code: {type: delta}}."""
        result: Dict[str, Dict[str, int]] = {}
        conn = get_postgres_connection()
        if not conn:
            return result
        try:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT employee_code, revenue_type, adjustment '
                'FROM commission_revenue_adjustments WHERE month = %s AND year = %s',
                (month, year)
            )
            for emp_code, rev_type, delta in cursor.fetchall():
                if emp_code not in result:
                    result[emp_code] = {}
                result[emp_code][rev_type] = delta
            cursor.close()
        finally:
            conn.close()
        return result

    def get_revenue_breakdown(self, month: int, year: int) -> Dict[str, Any]:
        """
        Return live Oracle revenue breakdown per employee per 7 revenue types,
        with stored adjustment deltas applied, grouped by store.

        Args:
            month: Commission month (1-12)
            year:  Commission year

        Returns:
            Dict with success, month, year, revenue_types, stores (nested employees with revenue)
        """
        commission_service = CommissionService()

        # 1. Get employees from PostgreSQL
        all_employees = commission_service._get_employees_with_username_for_period(month, year)

        # 2. Get store settings
        store_settings_result = CommissionStoreSettingsService().get_commission_stores(month, year)
        store_settings_map = {}
        if store_settings_result.get('success'):
            store_settings_map = {s['store_code']: s for s in store_settings_result.get('stores', [])}

        # 3. Oracle sales + HC UPCs
        oracle_warning = None
        try:
            sales_df = commission_service.repository.get_personal_commission_sales_data(year, month)
            sales_df.columns = sales_df.columns.str.lower()
            sales_df['upc_clean'] = sales_df['upc'].astype(str).str.strip()
            hand_carry_upcs = commission_service.repository.get_hand_carry_upcs()
        except Exception as e:
            sales_df = pd.DataFrame()
            hand_carry_upcs = []
            oracle_warning = f'Could not fetch live Oracle sales data: {e}'
            print(f"[WARN] {oracle_warning}")

        # 4. Load saved adjustments
        adjustments = self._load_adjustments(month, year)

        # 5. Per-employee revenue breakdown grouped by store
        stores_map: Dict[str, Any] = {}

        for emp in all_employees:
            store_code = emp['store_code']
            employee_code = emp['employee_code']
            retailpro_username = emp.get('retailpro_username')

            # Filter Oracle sales by username
            if retailpro_username and len(sales_df) > 0 and 'employee_username' in sales_df.columns:
                emp_sales = sales_df[sales_df['employee_username'] == retailpro_username]
            else:
                emp_sales = pd.DataFrame()

            # 7-type base revenue totals from Oracle
            base = commission_service._compute_revenue_by_type(emp_sales, hand_carry_upcs)

            # CR #12: personal_total_vat = sum(revenue_with_vat) directly from Oracle,
            # independent of the per-type base_amount breakdown (which uses before-VAT for most types).
            if len(emp_sales) > 0 and 'revenue_with_vat' in emp_sales.columns:
                personal_total_vat = int(emp_sales['revenue_with_vat'].sum())
            else:
                personal_total_vat = 0

            # CR #13: full_price with-VAT total for this employee (for store FP ratio)
            if len(emp_sales) > 0 and 'revenue_with_vat' in emp_sales.columns:
                separated = commission_service._separate_sales_by_type(emp_sales, hand_carry_upcs)
                non_jewelry = separated['non_jewelry']
                fp_sales = non_jewelry[non_jewelry['discount_rate'] <= DISCOUNT_THRESHOLD]
                personal_fp_vat = int(fp_sales['revenue_with_vat'].sum()) if len(fp_sales) > 0 else 0
            else:
                personal_fp_vat = 0

            # Apply adjustments
            emp_adj = adjustments.get(employee_code, {})
            revenue = []
            personal_total_adjusted = 0
            for rev_type in REVENUE_TYPE_ORDER:
                base_amount = base.get(rev_type, 0)
                delta = emp_adj.get(rev_type, 0)
                adjusted = base_amount + delta
                revenue.append({
                    'revenue_type':    rev_type,
                    'base_amount':     base_amount,
                    'adjustment':      delta,
                    'adjusted_amount': adjusted,
                })
                personal_total_adjusted += adjusted

            if store_code not in stores_map:
                ss = store_settings_map.get(store_code, {})
                stores_map[store_code] = {
                    'store_code':           store_code,
                    'store_name':           emp['store_name'],
                    'store_target':         ss.get('store_target'),
                    # Sum of tracked employees' personal revenue (not full store Oracle total).
                    # Used as a preview indicator; the real store commission uses get_store_sales_data.
                    'store_total_vat':      0,
                    'store_fp_total_vat':   0,
                    'store_total_adjusted': 0,
                    'employees':            [],
                }

            stores_map[store_code]['store_total_vat'] += personal_total_vat
            stores_map[store_code]['store_fp_total_vat'] += personal_fp_vat
            stores_map[store_code]['store_total_adjusted'] += personal_total_adjusted
            stores_map[store_code]['employees'].append({
                'employee_code':         employee_code,
                'full_name':             emp['full_name'],
                'personal_target':       emp.get('personal_target'),
                'personal_total_vat':    personal_total_vat,
                'personal_total_adjusted': personal_total_adjusted,
                'revenue':               revenue,
            })

        # 6. CR #21: Replace employee-sum store totals with location-based Oracle totals
        last_day_num = calendar.monthrange(year, month)[1]
        start_date = f'{year:04d}-{month:02d}-01 00:00:00'
        end_date = f'{year:04d}-{month:02d}-{last_day_num:02d} 23:59:59'

        for store_code, store_data in stores_map.items():
            try:
                oracle_store = commission_service.repository.get_store_sales_data(
                    store_code, start_date, end_date
                )
                if oracle_store:
                    # CR #21 V3: store_total_vat = sum of 4 classified buckets (7-type filtered)
                    fp = int(oracle_store.get('ACTUAL_FULL_PRICE_REVENUE', 0) or 0)
                    md = int(oracle_store.get('ACTUAL_DISCOUNTED_REVENUE', 0) or 0)
                    jewelry = int(oracle_store.get('ACTUAL_JEWELRY_REVENUE', 0) or 0)
                    suitcase = int(oracle_store.get('ACTUAL_SUITCASE_REVENUE', 0) or 0)
                    store_data['store_total_vat'] = fp + md + jewelry + suitcase
                    store_data['store_fp_total_vat'] = fp
            except Exception as e:
                print(f"[WARN] CR #21: Could not fetch location-based store data for {store_code}: {e}")
                # Falls back to employee-sum totals (already set)

        result = {
            'success': True,
            'month':   month,
            'year':    year,
            'revenue_types': [
                {'code': t, 'label': REVENUE_TYPE_LABELS[t]} for t in REVENUE_TYPE_ORDER
            ],
            'stores': list(stores_map.values()),
        }
        if oracle_warning:
            result['oracle_warning'] = oracle_warning
        return result

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
# SIDs are stored as strings (v1.0.2) to dodge float64 precision loss on
# the 18-digit Oracle SIDs — see CommissionRepository.get_all_sales_data.
EMPLOYEE_COMMISSION_EXCEPTIONS = {
    '690036963000170943': {                             # employee SID
        'qualifying_customer_sid': '690837303000121462',
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
COSM_EXCLUDED_VENDOR = 'HEA'  # Only HEA vendor in COSM dept → SYSADMIN (no commission)

# CR #61 (2026-06-01): from April 2026 onward, HEA products are reclassified as
# fashion (same tier rate + over-target bonus as the fashion category).
# Pre-cutoff months keep the original SYSADMIN routing so historical
# calculations remain stable. The cutoff key is the calculation's target month,
# not the bill's creation month — calculate for April 2026 uses the new rule
# regardless of when individual bills were created within that period.
HEA_AS_FASHION_FROM_MONTH = '2026-04'


def hea_is_fashion(year: int, month: int) -> bool:
    """Whether the (year, month) calculation period treats COSM+HEA items as
    fashion (True) or excludes them via SYSADMIN routing (False).
    See HEA_AS_FASHION_FROM_MONTH for the cutoff."""
    return f'{year:04d}-{month:02d}' >= HEA_AS_FASHION_FROM_MONTH

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
HOME_DECOR_RATE = 0.01

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
# Departments excluded from over-target bonus qualification:
# HOME items earn flat 1% (home_decor), COSM+HEA items earn nothing (other/SYSADMIN).
# Non-HEA COSM items are classified as fashion and DO qualify for over-target.
OVER_TARGET_EXCLUDED_DEPT = 'HOME'
# ============================================================================

# ============================================================================
# REVENUE TYPE CONSTANTS (CR #27: 8 categories with FP/MD split)
# ============================================================================
REVENUE_TYPE_ORDER = ['fashion', 'jewelry', 'vhernier', 'rosa_maria', 'hand_carry', 'suitcase', 'home_decor', 'other']
REVENUE_TYPE_LABELS = {
    'fashion': 'Fashion', 'jewelry': 'Jewelry',
    'vhernier': 'Vhernier', 'rosa_maria': 'Rosa Maria',
    'hand_carry': 'Hand Carry', 'suitcase': 'Suitcase',
    'home_decor': 'Home Decor', 'other': 'Other'
}
# Adjustment types: each category has _fp and _md variants
VALID_ADJUSTMENT_TYPES = frozenset(
    f'{cat}_{tier}' for cat in REVENUE_TYPE_ORDER for tier in ('fp', 'md')
)
# Keep backward compat alias for any remaining references
VALID_REVENUE_TYPES = VALID_ADJUSTMENT_TYPES
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

    # Legacy v1 helpers (_check_store_eligibility, _get_tier_rates,
    # _allocate_revenue_to_tiers, _distribute_store_pool) and
    # calculate_batch_store_commissions removed — superseded by v2.

    def _compute_revenue_by_type(
        self, sales_df, hand_carry_upcs,
        year: Optional[int] = None, month: Optional[int] = None,
    ) -> Dict[str, Dict[str, int]]:
        """
        Compute 8-category revenue totals with FP/MD split from a sales DataFrame.
        Priority chain: hand_carry → suitcase → other (COSM, pre-cutoff only) →
        home_decor → jewelry → fashion.

        Args:
            sales_df: DataFrame with Oracle sale columns (lowercase). May be empty.
            hand_carry_upcs: List/set of UPCs that classify as hand carry.
            year, month: optional calculation period. When provided and
                `hea_is_fashion(year, month)` is True, COSM+HEA items flow into
                the fashion bucket instead of the legacy 'other' bucket
                (CR #61, effective `HEA_AS_FASHION_FROM_MONTH`). When None,
                defaults to pre-cutoff behavior for backward compatibility.

        Returns:
            Dict with keys matching REVENUE_TYPE_ORDER, each value is
            {'fp': int, 'md': int, 'total': int}.
        """
        zero = {t: {'fp': 0, 'md': 0, 'total': 0} for t in REVENUE_TYPE_ORDER}

        if len(sales_df) == 0:
            return zero

        df = sales_df.copy()
        if 'upc_clean' not in df.columns:
            df['upc_clean'] = df['upc'].astype(str).str.strip()

        period_treats_hea_as_fashion = (
            year is not None and month is not None
            and hea_is_fashion(year, month)
        )

        # Priority chain: hand_carry → suitcase → other (COSM+HEA, pre-cutoff only)
        #                  → home_decor → jewelry → fashion
        hc_mask = df['upc_clean'].isin(hand_carry_upcs)
        hand_carry_df = df[hc_mask]
        remainder = df[~hc_mask]

        sc_mask = remainder['vendor_code'].isin(SUITCASE_VENDORS)
        suitcase_df = remainder[sc_mask]
        remainder2 = remainder[~sc_mask]

        # COSM dept + HEA vendor: pre-cutoff route to 'other' (no commission);
        # post-cutoff fall through to fashion (CR #61).
        if period_treats_hea_as_fashion:
            other_df = remainder2.iloc[0:0]   # empty — HEA flows to fashion
            remainder3 = remainder2
        else:
            cosm_mask = (remainder2['department'] == 'COSM') & (remainder2['vendor_code'] == COSM_EXCLUDED_VENDOR)
            other_df = remainder2[cosm_mask]
            remainder3 = remainder2[~cosm_mask]

        home_mask = remainder3['department'] == 'HOME'
        home_decor_df = remainder3[home_mask]
        remainder4 = remainder3[~home_mask]

        jewelry_df = remainder4[remainder4['is_jewelry'] == 1]
        non_jewelry_df = remainder4[remainder4['is_jewelry'] == 0]

        # Jewelry sub-types
        vhn_df = jewelry_df[jewelry_df['vendor_code'] == 'VHN']
        rom_ear_df = jewelry_df[
            (jewelry_df['vendor_code'] == 'ROM') & (jewelry_df['category'] == 'EARRINGS')
        ]
        other_jw_mask = ~jewelry_df.index.isin(vhn_df.index) & ~jewelry_df.index.isin(rom_ear_df.index)
        other_jewelry_df = jewelry_df[other_jw_mask]

        def _split_fp_md(cat_df):
            """Split a category DataFrame into FP/MD by discount_rate threshold."""
            if len(cat_df) == 0:
                return {'fp': 0, 'md': 0, 'total': 0}
            fp = cat_df[cat_df['discount_rate'] <= DISCOUNT_THRESHOLD]
            md = cat_df[cat_df['discount_rate'] > DISCOUNT_THRESHOLD]
            fp_val = int(fp['revenue_with_vat'].sum())
            md_val = int(md['revenue_with_vat'].sum())
            return {'fp': fp_val, 'md': md_val, 'total': fp_val + md_val}

        # CR #27: Each category gets FP/MD split based on discount_rate
        return {
            'fashion':    _split_fp_md(non_jewelry_df),
            'jewelry':    _split_fp_md(other_jewelry_df),
            'vhernier':   _split_fp_md(vhn_df),
            'rosa_maria': _split_fp_md(rom_ear_df),
            'hand_carry': _split_fp_md(hand_carry_df),
            'suitcase':   _split_fp_md(suitcase_df),
            'home_decor': _split_fp_md(home_decor_df),
            'other':      _split_fp_md(other_df),
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
        # Map each injectable adjustment type to its Oracle column values.
        # CR #27: types are now category_tier (e.g. fashion_fp, fashion_md).
        # discount_rate determines FP (<=0.30) vs MD (>0.30) classification.
        TYPE_PROPS = {
            'fashion_fp':    {'vendor_code': '__ADJ__', 'is_jewelry': 0, 'discount_rate': 0.0,
                              'upc': '', 'category': 'ADJ', 'use_with_vat': False},
            'fashion_md':    {'vendor_code': '__ADJ__', 'is_jewelry': 0, 'discount_rate': 0.50,
                              'upc': '', 'category': 'ADJ', 'use_with_vat': False},
            'jewelry_fp':    {'vendor_code': 'ATS',     'is_jewelry': 1, 'discount_rate': 0.0,
                              'upc': '', 'category': 'ADJ', 'use_with_vat': False},
            'jewelry_md':    {'vendor_code': 'ATS',     'is_jewelry': 1, 'discount_rate': 0.50,
                              'upc': '', 'category': 'ADJ', 'use_with_vat': False},
            'vhernier_fp':   {'vendor_code': 'VHN',     'is_jewelry': 1, 'discount_rate': 0.0,
                              'upc': '', 'category': 'ADJ', 'use_with_vat': False},
            'vhernier_md':   {'vendor_code': 'VHN',     'is_jewelry': 1, 'discount_rate': 0.50,
                              'upc': '', 'category': 'ADJ', 'use_with_vat': False},
            'rosa_maria_fp': {'vendor_code': 'ROM',     'is_jewelry': 1, 'discount_rate': 0.0,
                              'upc': '', 'category': 'EARRINGS', 'use_with_vat': False},
            'rosa_maria_md': {'vendor_code': 'ROM',     'is_jewelry': 1, 'discount_rate': 0.50,
                              'upc': '', 'category': 'EARRINGS', 'use_with_vat': False},
            'hand_carry_fp': {'vendor_code': '__ADJ__', 'is_jewelry': 0, 'discount_rate': 0.0,
                              'upc': '__ADJ_HC__', 'category': 'ADJ', 'use_with_vat': True},
            'hand_carry_md': {'vendor_code': '__ADJ__', 'is_jewelry': 0, 'discount_rate': 0.50,
                              'upc': '__ADJ_HC__', 'category': 'ADJ', 'use_with_vat': True},
            # suitcase_fp/md: NOT injected — handled via total_revenue_with_vat delta
            # home_decor_fp/md: injected with HOME department
            'home_decor_fp': {'vendor_code': '__ADJ__', 'is_jewelry': 0, 'discount_rate': 0.0,
                              'upc': '', 'category': 'ADJ', 'department': 'HOME', 'use_with_vat': False},
            'home_decor_md': {'vendor_code': '__ADJ__', 'is_jewelry': 0, 'discount_rate': 0.50,
                              'upc': '', 'category': 'ADJ', 'department': 'HOME', 'use_with_vat': False},
            # other_fp/md: not injectable (COSM items only)
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
                'department':        props.get('department', 'ADJ'),  # HOME for home_decor adjustments
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

    def _separate_sales_by_type(self, sales_df, hand_carry_upcs):
        """Separate a sales DataFrame by type for commission calculation.

        Priority chain: hand_carry -> suitcase -> home_decor -> jewelry -> non_jewelry.
        Note: COSM items are already re-attributed to SYSADMIN in the repository,
        so they won't appear in employee sales DataFrames.
        Returns a dict of DataFrames.
        """
        if len(sales_df) == 0:
            empty = sales_df.copy()
            return {'hand_carry': empty, 'suitcase': empty, 'home_decor': empty,
                    'jewelry': empty, 'non_jewelry': empty}

        df = sales_df

        hc_mask = df['upc_clean'].isin(hand_carry_upcs)
        hand_carry_df = df[hc_mask]
        remainder = df[~hc_mask]

        sc_mask = remainder['vendor_code'].isin(SUITCASE_VENDORS)
        suitcase_df = remainder[sc_mask]
        remainder2 = remainder[~sc_mask]

        home_mask = remainder2['department'] == 'HOME'
        home_decor_df = remainder2[home_mask]
        remainder3 = remainder2[~home_mask]

        jewelry_df = remainder3[remainder3['is_jewelry'] == 1]
        non_jewelry_df = remainder3[remainder3['is_jewelry'] == 0]

        return {
            'hand_carry': hand_carry_df,
            'suitcase': suitcase_df,
            'home_decor': home_decor_df,
            'jewelry': jewelry_df,
            'non_jewelry': non_jewelry_df,
        }

    def _calculate_hand_carry_commission(self, hand_carry_df):
        """Calculate hand carry commission by vendor rate groups.

        Returns dict with 'total' key (float).
        """
        if len(hand_carry_df) == 0:
            return {'total': 0}

        total = 0
        for _, row in hand_carry_df.iterrows():
            vendor = row['vendor_code']
            is_jewelry = row.get('is_jewelry', 0)
            category = row.get('category', '')
            revenue = row['revenue_with_vat']

            # ROM EARRINGS get 3%
            if vendor == 'ROM' and category == 'EARRINGS':
                total += revenue * HC_RATE_ROM_EARRINGS
            elif vendor in HC_VENDORS_1PCT:
                total += revenue * HC_RATE_1PCT
            elif vendor in HC_VENDORS_2PCT:
                total += revenue * HC_RATE_2PCT
            else:
                total += revenue * HC_RATE_1PCT  # Default 1%

        return {'total': total}

    def _calculate_suitcase_commission(self, suitcase_df):
        """Calculate suitcase commission: flat amount per item.

        Returns float total.
        """
        if len(suitcase_df) == 0:
            return 0
        return len(suitcase_df) * SUITCASE_FLAT_AMOUNT

    def _calculate_home_decor_commission(self, home_decor_df):
        """Calculate home decor commission: flat 1% on revenue_before_vat.

        Returns float total.
        """
        if len(home_decor_df) == 0:
            return 0
        return home_decor_df['revenue_before_vat'].sum() * HOME_DECOR_RATE

    def _calculate_jewelry_commission(self, jewelry_df):
        """Calculate jewelry commission by vendor/category.

        Returns dict with 'total', 'vhernier', 'rosa_maria' keys (floats).
        """
        if len(jewelry_df) == 0:
            return {'total': 0, 'vhernier': 0, 'rosa_maria': 0}

        vhn_df = jewelry_df[jewelry_df['vendor_code'] == 'VHN']
        rom_ear_df = jewelry_df[
            (jewelry_df['vendor_code'] == 'ROM') & (jewelry_df['category'] == 'EARRINGS')
        ]
        other_mask = ~jewelry_df.index.isin(vhn_df.index) & ~jewelry_df.index.isin(rom_ear_df.index)
        other_df = jewelry_df[other_mask]

        commission_vhernier = vhn_df['revenue_before_vat'].sum() * JEWELRY_RATE_VHN
        commission_rosa_maria = rom_ear_df['revenue_before_vat'].sum() * JEWELRY_RATE_ROM_EARRINGS
        commission_other = other_df['revenue_before_vat'].sum() * JEWELRY_RATE_OTHER

        total = commission_vhernier + commission_rosa_maria + commission_other
        return {
            'total': total,
            'vhernier': commission_vhernier,
            'rosa_maria': commission_rosa_maria,
        }

    @staticmethod
    def _upsert_payable_bill_rates(entries):
        """CR #61: batch UPSERT into `payable_bill_rates`. Idempotent — re-runs
        for the same month overwrite. `entries` is a list of tuples:
        (bill_sid, upc, revenue_type, fp_or_md, effective_rate, source_month).
        Swallows errors so a snapshot failure never aborts the commission calc."""
        if not entries:
            return
        conn = get_postgres_connection()
        if not conn:
            print('[WARN] CR #61 snapshot skipped — could not connect to PostgreSQL')
            return
        try:
            with conn.cursor() as cur:
                cur.executemany('''
                    INSERT INTO payable_bill_rates
                        (bill_sid, upc, revenue_type, fp_or_md, effective_rate, source_month)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (bill_sid, upc) DO UPDATE SET
                        revenue_type   = EXCLUDED.revenue_type,
                        fp_or_md       = EXCLUDED.fp_or_md,
                        effective_rate = EXCLUDED.effective_rate,
                        source_month   = EXCLUDED.source_month,
                        updated_at     = NOW()
                ''', entries)
                conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            print(f'[WARN] CR #61 snapshot UPSERT failed: {e}')
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @staticmethod
    def _classify_item_rate(
        row, rates, achievement_rate, hand_carry_upcs,
        ot_qualifying_sale_ids: set, ot_blended_rates_by_sale_id: dict,
        hea_as_fashion_period: bool,
    ):
        """CR #61: classify a single line item and compute its `effective_rate`
        for the payable_bill_rates snapshot.

        Returns `(revenue_type, fp_or_md, rate)` or `None` if the item should
        not be snapshotted (e.g. suitcase items where the commission is a flat
        per-item amount and the "rate" formulation doesn't apply).

        `fp_or_md` is `'fp'` / `'md'` only for fashion; `None` otherwise.
        """
        vendor = row.get('vendor_code')
        upc_clean = row.get('upc_clean')
        department = row.get('department')
        is_jewelry = row.get('is_jewelry', 0)
        category = row.get('category', '')
        sale_id = row.get('sale_id')

        # Priority chain matches _separate_sales_by_type:
        # hand_carry → suitcase → home_decor → jewelry → fashion.
        # Pre-cutoff COSM+HEA → 'other' (rate 0); post-cutoff falls through to fashion.

        if upc_clean in hand_carry_upcs:
            # Hand carry rate by vendor + category.
            if vendor == 'ROM' and category == 'EARRINGS':
                return ('hand_carry', None, HC_RATE_ROM_EARRINGS)
            if vendor in HC_VENDORS_1PCT:
                return ('hand_carry', None, HC_RATE_1PCT)
            if vendor in HC_VENDORS_2PCT:
                return ('hand_carry', None, HC_RATE_2PCT)
            return ('hand_carry', None, HC_RATE_1PCT)

        if vendor in SUITCASE_VENDORS:
            # Suitcase is a flat 500K per item — the rate formulation doesn't
            # apply. Skip the snapshot row; Account Payable special-cases
            # revenue_type='suitcase' on its end.
            return None

        if department == 'HOME':
            return ('home_decor', None, HOME_DECOR_RATE)

        # COSM+HEA: pre-cutoff → 'other' with rate 0; post-cutoff → fashion.
        if department == 'COSM' and vendor == COSM_EXCLUDED_VENDOR and not hea_as_fashion_period:
            return ('other', None, 0.0)

        if is_jewelry == 1:
            if vendor == 'VHN':
                return ('vhernier', None, JEWELRY_RATE_VHN)
            if vendor == 'ROM' and category == 'EARRINGS':
                return ('rosa_maria', None, JEWELRY_RATE_ROM_EARRINGS)
            return ('jewelry', None, JEWELRY_RATE_OTHER)

        # Fashion (non-jewelry, non-HC, non-suitcase, non-HOME, possibly HEA post-cutoff).
        discount_rate = row.get('discount_rate', 0)
        is_fp = discount_rate <= DISCOUNT_THRESHOLD
        fp_or_md = 'fp' if is_fp else 'md'
        rate_kind = 'fp' if is_fp else 'discount'

        # Below-50% achievement → no fashion commission earned, rate = 0.
        if achievement_rate < 50:
            return ('fashion', fp_or_md, 0.0)
        if achievement_rate < 70:
            tier_rate = rates['tier1'][rate_kind]
        elif achievement_rate < 100:
            tier_rate = rates['tier2'][rate_kind]
        else:
            tier_rate = rates['tier3'][rate_kind]

        # Over-target bonus only applies to FP items at tier 3 (>100%).
        if is_fp and achievement_rate > 100:
            if sale_id in ot_blended_rates_by_sale_id:
                # The single bill-crossing item gets a proportional blended rate.
                return ('fashion', 'fp', ot_blended_rates_by_sale_id[sale_id])
            if sale_id in ot_qualifying_sale_ids:
                # Fully-over-target FP qualifying item: tier3 + over_100.
                return ('fashion', 'fp', tier_rate + rates['over_100'])

        return ('fashion', fp_or_md, tier_rate)

    def _build_empty_personal_result(self, employee_code, employee_name, store_code):
        """Return a zero-commission result dict for an employee with no qualifying sales."""
        return {
            'employee_code': employee_code,
            'fullname': employee_name,
            'store_code': store_code,
            'commission_fashion_fp': 0,
            'commission_fashion_md': 0,
            'commission_over_100': 0,
            'commission_jewelry': 0,
            'commission_vhernier': 0,
            'commission_rosa_maria': 0,
            'commission_suitcase': 0,
            'commission_hand_carry': 0,
            'commission_home_decor': 0,
            'total': 0,
            # CR #59: AR payable / withholding — zero for empty result
            'withheld': 0,
            'withheld_by_category': {},
            'released': 0,
            'released_by_category': {},
            'payout': 0,
        }

    def _calculate_withheld_by_category(
        self,
        employee_sales_df,
        non_jewelry_fp_df,
        non_jewelry_disc_df,
        hand_carry_df,
        suitcase_df,
        home_decor_df,
        jewelry_df,
        rates,
        achievement_rate,
        fp_non_jewelry_after_target,
        target_bill_items_local,
        bills_after_target_local,
        hand_carry_upcs,
    ) -> Dict[str, float]:
        """Compute the withheld commission per category for one employee.

        Withheld = the portion of each category's commission that's tied to
        line items on AR bills still unpaid at end of month. Computed by
        multiplying each item's revenue by its `unpaid_ratio` (set upstream
        from `get_unpaid_bill_amounts`), then applying the same rate the
        category uses for the normal commission calculation.

        Returns dict keyed by the same names used in the response's
        `withheld_by_category` field. Keys with value 0 may be returned; the
        caller filters out zeros before serializing.
        """
        out = {
            'fashion_fp':   0.0,
            'fashion_md':   0.0,
            'over_target':  0.0,
            'jewelry':      0.0,
            'vhernier':     0.0,
            'rosa_maria':   0.0,
            'suitcase':     0.0,
            'hand_carry':   0.0,
            'home_decor':   0.0,
        }

        # Pick the tier rate the way the main path does — withholding uses
        # the same rate as the corresponding commission line so the ratio
        # holds exactly: withheld = commission × unpaid_ratio (per item).
        if achievement_rate >= 100:
            tier_key = 'tier3'
        elif achievement_rate >= 70:
            tier_key = 'tier2'
        elif achievement_rate >= 50:
            tier_key = 'tier1'
        else:
            tier_key = None  # No fashion commission, hence no fashion withholding

        # ── Fashion FP / MD ─────────────────────────────────────────────
        if tier_key is not None:
            fp_unpaid_rev = (
                non_jewelry_fp_df['revenue_before_vat'] * non_jewelry_fp_df['unpaid_ratio']
            ).sum()
            md_unpaid_rev = (
                non_jewelry_disc_df['revenue_before_vat'] * non_jewelry_disc_df['unpaid_ratio']
            ).sum()
            out['fashion_fp'] = float(fp_unpaid_rev) * rates[tier_key]['fp']
            out['fashion_md'] = float(md_unpaid_rev) * rates[tier_key]['discount']

        # ── Over-target bonus ──────────────────────────────────────────
        # Same items that contributed to fp_non_jewelry_after_target, weighted
        # by unpaid_ratio. We reconstruct from locals captured at call time:
        # `target_bill_items_local` and `bills_after_target_local`.
        if achievement_rate > 100 and fp_non_jewelry_after_target > 0:
            ot_unpaid_rev = 0.0
            # Items from the target-crossing bill (FP qualifying portion).
            if target_bill_items_local is not None and len(target_bill_items_local) > 0:
                ot_qual = target_bill_items_local[target_bill_items_local.get('is_ot_qualifying', False)]
                ot_unpaid_rev += float(
                    (ot_qual['revenue_before_vat'] * ot_qual['unpaid_ratio']).sum()
                )
            # Items from bills after target.
            if bills_after_target_local is not None and len(bills_after_target_local) > 0:
                ot_unpaid_rev += float(
                    (bills_after_target_local['revenue_before_vat']
                     * bills_after_target_local['unpaid_ratio']).sum()
                )
            out['over_target'] = ot_unpaid_rev * rates['over_100']

        # ── Non-fashion categories share their compute with the exception
        #     path. See _withheld_for_non_fashion for jewelry / vhernier /
        #     rosa_maria / hand_carry / suitcase / home_decor breakdown.
        out.update(self._withheld_for_non_fashion(
            hand_carry_df=hand_carry_df,
            suitcase_df=suitcase_df,
            home_decor_df=home_decor_df,
            jewelry_df=jewelry_df,
        ))
        return out

    @staticmethod
    def _withheld_for_non_fashion(
        hand_carry_df, suitcase_df, home_decor_df, jewelry_df,
    ) -> Dict[str, float]:
        """Compute withholding for the categories whose rate logic is
        identical between the main path and the exception path.

        Returns dict with keys: jewelry (total), vhernier, rosa_maria,
        hand_carry, suitcase, home_decor.
        """
        result: Dict[str, float] = {
            'jewelry':    0.0,
            'vhernier':   0.0,
            'rosa_maria': 0.0,
            'hand_carry': 0.0,
            'suitcase':   0.0,
            'home_decor': 0.0,
        }

        # ── Jewelry (VHN 1% / ROM-EAR 3% / other 2%) ────────────────────
        if len(jewelry_df) > 0:
            vhn = jewelry_df[jewelry_df['vendor_code'] == 'VHN']
            rom_ear = jewelry_df[
                (jewelry_df['vendor_code'] == 'ROM')
                & (jewelry_df['category'] == 'EARRINGS')
            ]
            other_mask = ~jewelry_df.index.isin(vhn.index) & ~jewelry_df.index.isin(rom_ear.index)
            other = jewelry_df[other_mask]
            result['vhernier'] = float(
                (vhn['revenue_before_vat'] * vhn['unpaid_ratio']).sum()
            ) * JEWELRY_RATE_VHN
            result['rosa_maria'] = float(
                (rom_ear['revenue_before_vat'] * rom_ear['unpaid_ratio']).sum()
            ) * JEWELRY_RATE_ROM_EARRINGS
            jw_other_withheld = float(
                (other['revenue_before_vat'] * other['unpaid_ratio']).sum()
            ) * JEWELRY_RATE_OTHER
            result['jewelry'] = result['vhernier'] + result['rosa_maria'] + jw_other_withheld

        # ── Hand carry (per-vendor rates on with-VAT revenue) ───────────
        if len(hand_carry_df) > 0:
            hc_total = 0.0
            for _, row in hand_carry_df.iterrows():
                vendor = row['vendor_code']
                category = row.get('category', '')
                weighted_rev = row['revenue_with_vat'] * row['unpaid_ratio']
                if vendor == 'ROM' and category == 'EARRINGS':
                    hc_total += weighted_rev * HC_RATE_ROM_EARRINGS
                elif vendor in HC_VENDORS_1PCT:
                    hc_total += weighted_rev * HC_RATE_1PCT
                elif vendor in HC_VENDORS_2PCT:
                    hc_total += weighted_rev * HC_RATE_2PCT
                else:
                    hc_total += weighted_rev * HC_RATE_1PCT
            result['hand_carry'] = hc_total

        # ── Suitcase (flat per item × unpaid_ratio) ─────────────────────
        if len(suitcase_df) > 0:
            result['suitcase'] = float(
                (suitcase_df['unpaid_ratio'] * SUITCASE_FLAT_AMOUNT).sum()
            )

        # ── Home decor (flat 1% on revenue_before_vat) ──────────────────
        if len(home_decor_df) > 0:
            result['home_decor'] = float(
                (home_decor_df['revenue_before_vat'] * home_decor_df['unpaid_ratio']).sum()
            ) * HOME_DECOR_RATE

        return result

    def calculate_personal_commissions(
        self,
        month: int,
        year: int,
        employees: List[Dict[str, Any]],
        store_code: str = None,
        revenue_adjustments: Optional[Dict[str, Dict[str, int]]] = None,
        preloaded_sales_df: Optional[pd.DataFrame] = None,
        preloaded_hand_carry_upcs: Optional[List[str]] = None,
        achievement_overrides: Optional[Dict[str, float]] = None,
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
            - commission_fashion_fp: Fashion FP commission (non-jewelry, achievement-based)
            - commission_fashion_md: Fashion MD commission (non-jewelry, achievement-based)
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
            all_sales_df = self.repository.get_all_sales_data(year, month)

        # Query hand carry item UPC list (use preloaded if available)
        if preloaded_hand_carry_upcs is not None:
            hand_carry_upcs = preloaded_hand_carry_upcs
        else:
            hand_carry_upcs = self.repository.get_hand_carry_upcs()

        # CR #59: pull unpaid AR bills for the target month and annotate each
        # sales row with its bill's unpaid_ratio. Items on bills that are paid
        # by end-of-month (or were never on AR) get unpaid_ratio=0. The ratio
        # is used to compute per-category withholding alongside the normal
        # commission calculation — withheld_category = unpaid_ratio-weighted
        # revenue × the same rate the category uses.
        unpaid_bills_map = self.repository.get_unpaid_bill_amounts(year, month)
        if 'bill_sid' in all_sales_df.columns:
            all_sales_df = all_sales_df.copy()
            all_sales_df['unpaid_ratio'] = all_sales_df['bill_sid'].map(
                lambda sid: unpaid_bills_map.get(sid, {}).get('unpaid_ratio', 0.0)
                            if sid is not None else 0.0
            )
        else:
            # Old preloaded data without bill_sid: cannot detect payable.
            all_sales_df = all_sales_df.copy()
            all_sales_df['unpaid_ratio'] = 0.0

        # Create mapping from employee_username to employee_sid for internal processing
        # Use employee_sid (unique, stable identifier) for filtering sales data
        # Use employee_code (human-readable) only for output
        # Filter to rows with employee data (unified query LEFT JOINs employee, so some rows have NULLs)
        emp_rows = all_sales_df[all_sales_df['employee_username'].notna()]
        employee_username_to_sid = emp_rows[['employee_username', 'employee_sid']].drop_duplicates()
        employee_username_to_sid_dict = employee_username_to_sid.set_index('employee_username')['employee_sid'].to_dict()

        # 4. PREPARE RESULTS LIST
        results = []
        # CR #61: rate snapshot entries accumulated per employee, batch-UPSERTed
        # at the end so a snapshot failure never blocks the response.
        rate_snapshot_entries: List[tuple] = []
        _hea_as_fashion_period = hea_is_fashion(year, month)
        _source_month_str = f'{year:04d}-{month:02d}'

        # 5. PROCESS EACH EMPLOYEE
        for employee_data in employees:
            employee_code = employee_data.get('employee_code')
            employee_username = employee_data.get('employee_username')
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

                # CR #61: HEA-as-fashion post-cutoff means we don't exclude
                # COSM+HEA items from per-employee processing. Pre-cutoff
                # behavior preserved for historical commission stability.
                if hea_is_fashion(year, month):
                    exc_sales_df = employee_sales_df.copy()
                else:
                    exc_sales_df = employee_sales_df[
                        ~((employee_sales_df['department'] == 'COSM') & (employee_sales_df['vendor_code'] == COSM_EXCLUDED_VENDOR))
                    ].copy()
                exc_sales_df['upc_clean'] = exc_sales_df['upc'].astype(str).str.strip()

                # Separate sales by type (same priority chain as main path)
                separated = self._separate_sales_by_type(exc_sales_df, hand_carry_upcs)

                # Apply customer filter ONLY to non-jewelry remainder
                qualifying_sales = separated['non_jewelry'][
                    separated['non_jewelry']['customer_sid'] == qualifying_customer
                ]
                exc_flat_commission = qualifying_sales['revenue_before_vat'].sum() * flat_rate

                # Hand carry, suitcase, home decor, jewelry use normal rules (no customer filter)
                hc = self._calculate_hand_carry_commission(separated['hand_carry'])
                sc = self._calculate_suitcase_commission(separated['suitcase'])
                hd = self._calculate_home_decor_commission(separated['home_decor'])
                jw = self._calculate_jewelry_commission(separated['jewelry'])

                exc_total = exc_flat_commission + hc['total'] + sc + hd + jw['total']

                # CR #59: withholding for the exception employee.
                # The exception path uses a flat 0.7% on qualifying-customer
                # non-jewelry sales (not the standard tier-based fashion path),
                # so we compute fashion_fp withholding directly. The other
                # categories (jewelry / hand_carry / suitcase / home_decor)
                # use the same rules as the main path, so we delegate those
                # to the focused per-category helpers.
                exc_withheld = {
                    'fashion_fp':  float(
                        (qualifying_sales['revenue_before_vat'] * qualifying_sales['unpaid_ratio']).sum()
                    ) * flat_rate,
                    'fashion_md':  0.0,
                    'over_target': 0.0,
                    **self._withheld_for_non_fashion(
                        hand_carry_df=separated['hand_carry'],
                        suitcase_df=separated['suitcase'],
                        home_decor_df=separated['home_decor'],
                        jewelry_df=separated['jewelry'],
                    ),
                }
                exc_withheld_total = sum(exc_withheld.values())
                exc_payout = exc_total - exc_withheld_total

                results.append({
                    'employee_code': employee_code,
                    'fullname': employee_name,
                    'store_code': emp_store_code,
                    'commission_fashion_fp': exc_flat_commission,
                    'commission_fashion_md': 0,
                    'commission_over_100': 0,
                    'commission_jewelry': jw['total'],
                    'commission_vhernier': jw['vhernier'],
                    'commission_rosa_maria': jw['rosa_maria'],
                    'commission_suitcase': sc,
                    'commission_hand_carry': hc['total'],
                    'commission_home_decor': hd,
                    'total': exc_total,
                    # CR #59: AR payable / withholding
                    'withheld': exc_withheld_total,
                    'withheld_by_category': {k: v for k, v in exc_withheld.items() if v != 0},
                    'released': 0,
                    'released_by_category': {},
                    'payout': exc_payout,
                })
                continue
            # ================================================================

            # Calculate TOTAL revenue WITH VAT from ALL sources for eligibility check
            # IMPORTANT: All items (including HOME) are INCLUDED in total revenue for achievement calculation
            # (includes jewelry + non-jewelry, all stores, all vendors, INCLUDING COSM)
            total_revenue_with_vat = employee_sales_df['revenue_with_vat'].sum()

            # Add all revenue adjustments to achievement total
            # (suitcase for achievement only; other types also affect commission via injected rows)
            if emp_adjustments:
                for delta in emp_adjustments.values():
                    total_revenue_with_vat += delta

            # CR #29: Use frontend-supplied achievement if provided (authoritative live-edited data)
            if achievement_overrides and employee_code in achievement_overrides:
                achievement_rate = achievement_overrides[employee_code]
            else:
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

            # Sort bills by date then bill_number (matches Excel methodology)
            # and reset index so label == position (.iloc[] used later)
            bill_totals_df = bill_totals_df.sort_values(['sale_date', 'bill_number']).reset_index(drop=True)

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

            # Exclude COSM+HEA items from commission calculations (pre-cutoff only).
            # CR #61: from `HEA_AS_FASHION_FROM_MONTH`, HEA items are reclassified
            # as fashion and earn commission — skip this filter for those periods.
            # Non-HEA COSM items always pass through (earn commission as fashion).
            # HOME items are NEVER excluded here — they earn commission via home_decor.
            if not hea_is_fashion(year, month):
                employee_sales_df = employee_sales_df[
                    ~((employee_sales_df['department'] == 'COSM') & (employee_sales_df['vendor_code'] == COSM_EXCLUDED_VENDOR))
                ].copy()
            else:
                employee_sales_df = employee_sales_df.copy()

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
                if emp_adjustments.get('hand_carry_fp', 0) != 0 or emp_adjustments.get('hand_carry_md', 0) != 0:
                    local_hand_carry_upcs = list(hand_carry_upcs) + ['__ADJ_HC__']

            separated = self._separate_sales_by_type(employee_sales_df, local_hand_carry_upcs)
            hand_carry_df = separated['hand_carry']
            suitcase_df = separated['suitcase']
            home_decor_df = separated['home_decor']
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
            # CR #59: explicitly initialise the OT-related DataFrames so the
            # withholding helper below can always reference them — they get
            # populated only inside `if achievement_rate > 100`, but we always
            # pass them down (previously fetched via `locals().get(...)`,
            # which silently returned None and broke if the names ever
            # changed). Now the contract is explicit.
            target_bill_items = None
            fp_non_jewelry_after_df = None

            if achievement_rate > 100:
                # Find the BILL where target was first reached or exceeded
                target_reached_bills = bill_totals_df[bill_totals_df['running_total'] >= target]

                if len(target_reached_bills) > 0:
                    # Get the first bill that reached/exceeded target
                    target_bill_number = target_reached_bills.iloc[0]['bill_number']
                    target_bill_running_total = target_reached_bills.iloc[0]['running_total']
                    target_bill_revenue = target_reached_bills.iloc[0]['revenue_with_vat']

                    previous_total = target_bill_running_total - target_bill_revenue

                    # Get ALL items from the target-reaching bill (not just FP qualifying)
                    # ALL items contribute to the running total since target is based on total revenue
                    target_bill_items = employee_sales_df[employee_sales_df['bill_number'] == target_bill_number].copy()

                    # Mark which items are FP qualifying for over-target bonus.
                    # CR #61: post-cutoff months don't exclude COSM+HEA — they
                    # qualify as fashion. Pre-cutoff keeps them out.
                    _ot_cosm_hea_exclude = (
                        pd.Series(True, index=target_bill_items.index)
                        if hea_is_fashion(year, month)
                        else (~((target_bill_items['department'] == 'COSM')
                                & (target_bill_items['vendor_code'] == COSM_EXCLUDED_VENDOR)))
                    )
                    target_bill_items['is_ot_qualifying'] = (
                        (target_bill_items['discount_rate'] <= DISCOUNT_THRESHOLD) &
                        (target_bill_items['is_jewelry'] == 0) &
                        (~target_bill_items['vendor_code'].isin(SUITCASE_VENDORS)) &
                        (target_bill_items['department'] != OVER_TARGET_EXCLUDED_DEPT) &
                        _ot_cosm_hea_exclude &
                        (~target_bill_items['upc_clean'].isin(local_hand_carry_upcs))
                    )

                    # If this bill pushed over target, use item-level calculation
                    # Sort: FP qualifying items first (ascending), then non-qualifying (ascending)
                    # This minimizes over-target payout by filling "before target" space with FP items first
                    # The FIRST item that crosses the target line gets a proportional share
                    # Only FP qualifying items earn over-target commission
                    if previous_total < target and target_bill_items['is_ot_qualifying'].any():
                        # Sort: qualifying first (0), then non-qualifying (1), ascending by price within each group
                        target_bill_items['_sort_group'] = (~target_bill_items['is_ot_qualifying']).astype(int)
                        target_bill_items = target_bill_items.sort_values(['_sort_group', 'revenue_with_vat'])

                        # Build running total using ALL items (FP + MD + everything)
                        target_bill_items['item_running_total'] = previous_total + target_bill_items['revenue_with_vat'].cumsum()

                        # Find items that are at or after the 100% threshold
                        items_over_target = target_bill_items[target_bill_items['item_running_total'] >= target]

                        if len(items_over_target) > 0:
                            first_crossing = items_over_target.iloc[0]
                            amount_over_vat = first_crossing['item_running_total'] - target

                            # First crossing item: proportional share if FP qualifying
                            if first_crossing['is_ot_qualifying'] and first_crossing['revenue_with_vat'] != 0:
                                proportional_before_vat = first_crossing['revenue_before_vat'] * (
                                    amount_over_vat / first_crossing['revenue_with_vat']
                                )
                                fp_non_jewelry_after_target = proportional_before_vat
                            # else: first crossing is non-qualifying (MD/jewelry/etc), no OT from it

                            # Remaining items: only count FP qualifying ones
                            if len(items_over_target) > 1:
                                remaining_qualifying = items_over_target.iloc[1:][items_over_target.iloc[1:]['is_ot_qualifying']]
                                fp_non_jewelry_after_target += remaining_qualifying['revenue_before_vat'].sum()

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

                        # Filter for: full-price, non-jewelry, NOT TIT/TVL, NOT hand carry, NOT HOME.
                        # CR #61: HEA qualifies post-cutoff (fashion); pre-cutoff excluded.
                        _after_target_cosm_hea_exclude = (
                            pd.Series(True, index=items_after_target.index)
                            if hea_is_fashion(year, month)
                            else (~((items_after_target['department'] == 'COSM')
                                    & (items_after_target['vendor_code'] == COSM_EXCLUDED_VENDOR)))
                        )
                        fp_non_jewelry_after_df = items_after_target[
                            (items_after_target['discount_rate'] <= DISCOUNT_THRESHOLD) &
                            (items_after_target['is_jewelry'] == 0) &
                            (~items_after_target['vendor_code'].isin(SUITCASE_VENDORS)) &
                            (items_after_target['department'] != OVER_TARGET_EXCLUDED_DEPT) &
                            _after_target_cosm_hea_exclude &
                            (~items_after_target['upc_clean'].isin(local_hand_carry_upcs))
                        ]

                        # Add to fp_non_jewelry_after_target (use revenue_before_vat)
                        fp_non_jewelry_after_target = fp_non_jewelry_after_target + fp_non_jewelry_after_df['revenue_before_vat'].sum()

            # HAND CARRY, SUITCASE, HOME DECOR, JEWELRY COMMISSIONS
            # All paid REGARDLESS of achievement rate
            hc_result = self._calculate_hand_carry_commission(hand_carry_df)
            hand_carry_commission = hc_result['total']

            suitcase_commission = self._calculate_suitcase_commission(suitcase_df)

            home_decor_commission = self._calculate_home_decor_commission(home_decor_df)

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
            # CR #27: renamed to fashion_fp / fashion_md
            commission_fashion_fp = commission_fp
            commission_fashion_md = commission_discount
            commission_100_and_below = personal_commission + jewelry_commission + suitcase_commission + hand_carry_commission + home_decor_commission
            total_commission = commission_100_and_below + commission_over_100

            # ─────────────────────────────────────────────────────────────
            # CR #59: per-category WITHHELD commission (AR payable)
            # ─────────────────────────────────────────────────────────────
            # Withheld = sum(item_commission × item.unpaid_ratio) per category.
            # Computed by re-running each category's commission formula with
            # revenue replaced by revenue × unpaid_ratio.
            withheld = self._calculate_withheld_by_category(
                employee_sales_df=employee_sales_df,
                non_jewelry_fp_df=non_jewelry_fp_df,
                non_jewelry_disc_df=non_jewelry_disc_df,
                hand_carry_df=hand_carry_df,
                suitcase_df=suitcase_df,
                home_decor_df=home_decor_df,
                jewelry_df=jewelry_df,
                rates=rates,
                achievement_rate=achievement_rate,
                fp_non_jewelry_after_target=fp_non_jewelry_after_target,
                target_bill_items_local=target_bill_items,
                bills_after_target_local=fp_non_jewelry_after_df,
                hand_carry_upcs=local_hand_carry_upcs,
            )
            withheld_total = sum(withheld.values())
            payout = total_commission - withheld_total  # released=0 in Phase B

            # ─────────────────────────────────────────────────────────────
            # CR #61: per-item rate snapshot
            # ─────────────────────────────────────────────────────────────
            # For each line item this employee sold in this period, record
            # the effective rate that the pipeline would apply. Account
            # Payable (CR #60) reads this when computing release commission.
            ot_qualifying_sale_ids: set = set()
            ot_blended_rates_by_sale_id: dict = {}
            if achievement_rate > 100 and fp_non_jewelry_after_target > 0:
                # Items from the target-crossing bill that exceeded the target.
                if target_bill_items is not None and len(target_bill_items) > 0:
                    items_over = target_bill_items[
                        target_bill_items['item_running_total'] >= target
                    ]
                    for idx_i, (_idx, ot_row) in enumerate(items_over.iterrows()):
                        if not ot_row.get('is_ot_qualifying'):
                            continue
                        sid = ot_row['sale_id']
                        if idx_i == 0 and ot_row['revenue_with_vat'] != 0:
                            # Crossing item — blended rate.
                            amount_over_vat = ot_row['item_running_total'] - target
                            over_ratio = amount_over_vat / ot_row['revenue_with_vat']
                            blended = rates['tier3']['fp'] + over_ratio * rates['over_100']
                            ot_blended_rates_by_sale_id[sid] = blended
                        else:
                            ot_qualifying_sale_ids.add(sid)
                # Items from bills after the target-crossing bill.
                if fp_non_jewelry_after_df is not None and len(fp_non_jewelry_after_df) > 0:
                    ot_qualifying_sale_ids.update(fp_non_jewelry_after_df['sale_id'])

            for _, item_row in employee_sales_df.iterrows():
                bill_sid = item_row.get('bill_sid')
                upc = item_row.get('upc_clean')
                if not bill_sid or not upc:
                    continue
                classified = self._classify_item_rate(
                    item_row, rates, achievement_rate, local_hand_carry_upcs,
                    ot_qualifying_sale_ids, ot_blended_rates_by_sale_id,
                    _hea_as_fashion_period,
                )
                if classified is None:
                    continue
                rev_type, fp_or_md, rate = classified
                rate_snapshot_entries.append((
                    str(bill_sid), str(upc), rev_type, fp_or_md,
                    float(rate), _source_month_str,
                ))

            # Add result for this employee (for DataFrame row)
            results.append({
                'employee_code': employee_code,
                'fullname': employee_name,
                'store_code': emp_store_code,
                'commission_fashion_fp': commission_fashion_fp,
                'commission_fashion_md': commission_fashion_md,
                'commission_over_100': commission_over_100,
                'commission_jewelry': jewelry_commission,
                'commission_vhernier': commission_vhernier,
                'commission_rosa_maria': commission_rosa_maria,
                'commission_suitcase': suitcase_commission,
                'commission_hand_carry': hand_carry_commission,
                'commission_home_decor': home_decor_commission,
                'total': total_commission,
                # CR #59: AR payable / withholding
                'withheld': withheld_total,
                'withheld_by_category': {k: v for k, v in withheld.items() if v != 0},
                'released': 0,                          # Phase C populates
                'released_by_category': {},             # Phase C populates
                'payout': payout,
            })

        # CR #61: batch-UPSERT the per-item rate snapshot for Account Payable.
        # Best-effort: failures here log a warning but don't block the response.
        self._upsert_payable_bill_rates(rate_snapshot_entries)

        # 6. CONVERT RESULTS TO DATAFRAME
        # Create pandas DataFrame with specified columns
        results_df = pd.DataFrame(results, columns=[
            'employee_code',
            'fullname',
            'store_code',
            'commission_fashion_fp',
            'commission_fashion_md',
            'commission_over_100',
            'commission_jewelry',
            'commission_vhernier',
            'commission_rosa_maria',
            'commission_suitcase',
            'commission_hand_carry',
            'commission_home_decor',
            'total',
            # CR #59: AR payable / withholding (Phase B)
            'withheld',
            'withheld_by_category',
            'released',
            'released_by_category',
            'payout',
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
        all_sales_df: Optional[pd.DataFrame] = None,
        eligible_override: Optional[bool] = None,
        achievement_override: Optional[float] = None,
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

        # Load unified sales data if not preloaded
        if all_sales_df is None:
            # Parse year/month from from_date (format: 'YYYY-MM-DD HH:MI:SS')
            year = int(from_date[:4])
            month = int(from_date[5:7])
            all_sales_df = self.repository.get_all_sales_data(year, month)
        if hand_carry_upcs is None:
            hand_carry_upcs = self.repository.get_hand_carry_upcs()

        # Classify store revenue from unified DataFrame (CR #24 / CR #25)
        # Store view: filter by doc_store_code (location-based, includes all transactions)
        hc_upcs = hand_carry_upcs or []
        if len(all_sales_df) > 0:
            store_df = all_sales_df[all_sales_df['doc_store_code'] == store_code]
        else:
            store_df = pd.DataFrame()
        # CR #61: derive year/month from query_date for HEA-as-fashion cutoff
        _year_from_qd = int(query_date['from_date'][:4])
        _month_from_qd = int(query_date['from_date'][5:7])
        store_revenue = self._compute_revenue_by_type(
            store_df, hc_upcs, year=_year_from_qd, month=_month_from_qd,
        )

        # CR #27: store_revenue values are now {fp, md, total} dicts
        actual_full_price_revenue = sum(v.get('fp', 0) for v in store_revenue.values())
        actual_discounted_revenue = sum(v.get('md', 0) for v in store_revenue.values())
        actual_jewelry_revenue = (store_revenue.get('jewelry', {}).get('total', 0) +
                                  store_revenue.get('vhernier', {}).get('total', 0) +
                                  store_revenue.get('rosa_maria', {}).get('total', 0))
        actual_suitcase_revenue = store_revenue.get('suitcase', {}).get('total', 0)
        actual_hand_carry_revenue = store_revenue.get('hand_carry', {}).get('total', 0)
        # 8-category total (all classified items)
        actual_revenue = sum(v.get('total', 0) for v in store_revenue.values())

        # STEP 1: Check Store Eligibility & Calculate Achievement
        # CR #29: Use frontend-supplied values when provided
        if achievement_override is not None:
            achievement_pct = achievement_override
        else:
            achievement_pct = (actual_revenue / store_target * 100) if store_target > 0 else 0
        actual_fp_ratio = (actual_full_price_revenue / actual_revenue) if actual_revenue > 0 else 0

        if eligible_override is not None:
            # CR #29: Frontend already evaluated eligibility — skip re-check
            if not eligible_override:
                return {
                    'eligible': False,
                    'reason': 'Not eligible (frontend-evaluated)',
                    'achievement_pct': achievement_pct,
                    'actual_fp_ratio': actual_fp_ratio,
                    'store_code': store_code,
                    'employees': []
                }
        else:
            # Original eligibility checks
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

        # Compute employee FP/discounted from unified DataFrame
        # Filters: employee with username, non-WJEW, non-hand-carry, same-store logic
        hc_upc_set = set(hc_upcs) if hc_upcs else set()
        employee_sales_lookup = {}
        if all_sales_df is not None and len(all_sales_df) > 0:
            # Filter to rows with employee data, exclude WJEW and hand carry
            emp_df = all_sales_df[
                all_sales_df['employee_username'].notna() &
                (all_sales_df['department'] != 'WJEW')
            ].copy()
            if hc_upc_set:
                emp_df = emp_df[~emp_df['upc_clean'].isin(hc_upc_set)]

            # Apply cross-store logic
            if store_code in ('RHN', 'RWP'):
                emp_df = emp_df[
                    emp_df['store_code'].isin(['RHN', 'RWP']) &
                    emp_df['doc_store_code'].isin(['RHN', 'RWP'])
                ]
            else:
                emp_df = emp_df[
                    (emp_df['store_code'] == store_code) &
                    (emp_df['doc_store_code'] == store_code)
                ]

            # Group by employee and split FP/discounted (uses revenue_before_vat)
            for username, group in emp_df.groupby('employee_username'):
                fp_rev = group[group['discount_rate'] <= DISCOUNT_THRESHOLD]['revenue_before_vat'].sum()
                disc_rev = group[group['discount_rate'] > DISCOUNT_THRESHOLD]['revenue_before_vat'].sum()
                employee_sales_lookup[username] = {
                    'fp_revenue': int(fp_rev),
                    'disc_revenue': int(disc_rev)
                }

        # STEP 2: Calculate Each Employee's Contribution to Store Pool
        employee_contributions = []

        for employee in employees:
            employee_code = employee['employee_code']
            employee_username = employee.get('employee_username')
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
                - commission_fashion_fp
                - commission_fashion_md
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
                - personal_commission_fashion_fp
                - personal_commission_fashion_md
                - personal_commission_over_100
                - personal_commission_jewelry
                - personal_commission_vhernier
                - personal_commission_rosa_maria
                - personal_commission_suitcase
                - personal_commission_hand_carry
                - personal_commission_home_decor
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

            # CR #59: pass withholding fields through (Phase B).
            # Personal withholding only — store-pool (individual / equal /
            # manager) is not withheld per CR #59.
            personal_withheld = row.get('withheld', 0) or 0
            payout_total = total_handout - personal_withheld     # released=0 in Phase B

            combined_results.append({
                'employee_code': employee_code,
                'employee_name': row['fullname'],
                'store_code': row['store_code'],
                'store_achievement_pct': store_achievement_pct,
                'store_commission_70pct': store_comm['individual_share'],
                'store_commission_30pct': store_comm['equal_share'],
                'manager_bonus': store_comm['manager_bonus'],
                'total_store_commission': store_comm['total_store_commission'],
                'personal_commission_fashion_fp': row['commission_fashion_fp'],
                'personal_commission_fashion_md': row['commission_fashion_md'],
                'personal_commission_over_100': row['commission_over_100'],
                'personal_commission_jewelry': row['commission_jewelry'],
                'personal_commission_vhernier': row['commission_vhernier'],
                'personal_commission_rosa_maria': row['commission_rosa_maria'],
                'personal_commission_suitcase': row['commission_suitcase'],
                'personal_commission_hand_carry': row['commission_hand_carry'],
                'personal_commission_home_decor': row['commission_home_decor'],
                'personal_commission_total': row['total'],
                'total_handout_commission': total_handout,
                # CR #59
                'withheld': personal_withheld,
                'withheld_by_category': row.get('withheld_by_category', {}) or {},
                'released': row.get('released', 0) or 0,
                'released_by_category': row.get('released_by_category', {}) or {},
                'payout': payout_total,
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
            'personal_commission_fashion_fp',
            'personal_commission_fashion_md',
            'personal_commission_over_100',
            'personal_commission_jewelry',
            'personal_commission_vhernier',
            'personal_commission_rosa_maria',
            'personal_commission_suitcase',
            'personal_commission_hand_carry',
            'personal_commission_home_decor',
            'personal_commission_total',
            'total_handout_commission',
            # CR #59: AR payable / withholding (Phase B)
            'withheld',
            'withheld_by_category',
            'released',
            'released_by_category',
            'payout',
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
            # Load adjustments summed across stores
            revenue_service = CommissionRevenueService()
            adjustments_dict = revenue_service._load_adjustments(month, year, per_store=False)

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

            # 5b. Load unified sales data + hand carry UPCs once (shared by all stores)
            hand_carry_upcs = self.repository.get_hand_carry_upcs()
            all_sales_df = self.repository.get_all_sales_data(year, month)

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
                    # CR #59 Phase B: AR payable / withholding fields.
                    # withheld_by_category / released_by_category come through as
                    # dicts from the combined DataFrame; serialise per-category
                    # values as ints. We compute `withheld` total as the sum of
                    # the int-cast category values so the invariant
                    # `withheld == sum(withheld_by_category.values())` always
                    # holds — avoids ±1 rounding drift between a separately-cast
                    # total and the sum of independently-cast categories.
                    withheld_by_cat = row.get('withheld_by_category') or {}
                    if not isinstance(withheld_by_cat, dict):
                        withheld_by_cat = {}
                    released_by_cat = row.get('released_by_category') or {}
                    if not isinstance(released_by_cat, dict):
                        released_by_cat = {}
                    withheld_by_cat_int = {k: int(v) for k, v in withheld_by_cat.items()}
                    released_by_cat_int = {k: int(v) for k, v in released_by_cat.items()}
                    withheld_total_int = sum(withheld_by_cat_int.values())
                    released_total_int = sum(released_by_cat_int.values())
                    employee_total_int = int(row.get('total_handout_commission', 0) or 0)

                    employees_response.append({
                        'employee_code': row['employee_code'],
                        'full_name':     row['employee_name'],
                        'result': {
                            'individual':            int(row.get('store_commission_70pct', 0) or 0),
                            'shared':                int(row.get('store_commission_30pct', 0) or 0),
                            'manager':               int(row.get('manager_bonus', 0) or 0),
                            'fashion_fp':            int(row.get('personal_commission_fashion_fp', 0) or 0),
                            'fashion_md':            int(row.get('personal_commission_fashion_md', 0) or 0),
                            'over_target':           int(row.get('personal_commission_over_100', 0) or 0),
                            'jewelry':               int(jewelry_net),
                            'vhernier':              int(row.get('personal_commission_vhernier', 0) or 0),
                            'rosa_maria':            int(row.get('personal_commission_rosa_maria', 0) or 0),
                            'suitcase':              int(row.get('personal_commission_suitcase', 0) or 0),
                            'hand_carry':            int(row.get('personal_commission_hand_carry', 0) or 0),
                            'home_decor':            int(row.get('personal_commission_home_decor', 0) or 0),
                            'store_total':           int(row.get('total_store_commission', 0) or 0),
                            'personal_total':        int(row.get('personal_commission_total', 0) or 0),
                            'employee_total':        employee_total_int,
                            # CR #59 Phase B
                            'withheld':              withheld_total_int,
                            'withheld_by_category':  withheld_by_cat_int,
                            'released':              released_total_int,
                            'released_by_category':  released_by_cat_int,
                            'payout':                employee_total_int - withheld_total_int + released_total_int,
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

    def calculate_commissions_v2(
        self,
        month: int,
        year: int,
        stores_data: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        CR #29: Calculate commissions using frontend-supplied revenue context.

        Frontend sends stores, employees, per-category revenue (base + adjustments),
        derived totals, achievement rates, and eligibility. Backend still queries Oracle
        for transaction-level data (needed for over-target bill-by-bill logic and store
        contribution calculation), but uses frontend values for tier/eligibility decisions.

        Args:
            month: Commission month (1-12)
            year:  Commission year
            stores_data: List of store dicts from frontend request

        Returns:
            Dict with success, month, year, stores (nested employees with result objects)
        """
        try:
            # 1. Look up employee metadata from DB (join_date, full_name, retailpro_username, working_day)
            all_db_employees = self._get_employees_with_username_for_period(month, year)
            emp_meta_map = {e['employee_code']: e for e in all_db_employees}

            # 2. Compute period date range for store commission queries
            last_day_num = calendar.monthrange(year, month)[1]
            start_date = f'{year:04d}-{month:02d}-01 00:00:00'
            end_date = f'{year:04d}-{month:02d}-{last_day_num:02d} 23:59:59'
            query_date = {'from_date': start_date, 'to_date': end_date}
            period_end_date = date(year, month, last_day_num)

            # 3. Load unified sales data + hand carry UPCs once (shared by all stores)
            hand_carry_upcs = self.repository.get_hand_carry_upcs()
            all_sales_df = self.repository.get_all_sales_data(year, month)

            # 4. Process each store from frontend data
            all_combined_dfs = []
            all_store_results = []

            for store_data in stores_data:
                store_code = store_data['store_code']
                store_target = store_data.get('store_target', 0) or 0
                fp_ratio_raw = store_data.get('fp_ratio_target')
                store_fp_ratio = (fp_ratio_raw / 100.0) if fp_ratio_raw is not None else 0.0
                store_eligible = store_data.get('store_eligible', False)
                store_achievement = store_data.get('store_achievement', 0)

                # Build employee list with DB metadata + frontend overrides
                store_employees = []
                achievement_overrides = {}
                adjustments_dict: Dict[str, Dict[str, int]] = {}

                for emp_data in store_data.get('employees', []):
                    emp_code = emp_data['employee_code']
                    db_emp = emp_meta_map.get(emp_code, {})

                    # Compute seniority from DB join_date
                    join_date_raw = db_emp.get('join_date')
                    if join_date_raw:
                        if isinstance(join_date_raw, str):
                            from datetime import datetime as _dt
                            join_date_obj = _dt.strptime(join_date_raw, '%Y-%m-%d').date()
                        else:
                            join_date_obj = join_date_raw
                        seniority_years = (period_end_date - join_date_obj).days / 365.25
                    else:
                        seniority_years = 0

                    contract = (emp_data.get('contract') or '').upper()
                    is_probation = contract == 'PROBATION'

                    store_employees.append({
                        'employee_code':   emp_code,
                        'employee_username': db_emp.get('retailpro_username'),
                        'full_name':       db_emp.get('full_name', emp_code),
                        'store_code':      store_code,
                        'personal_target': emp_data.get('personal_target') or 0,
                        'is_manager':      emp_data.get('is_manager', False),
                        'working_day':     db_emp.get('working_day') or 0,
                        'working_day_count': db_emp.get('working_day') or 0,
                        'seniority':       seniority_years,
                        'is_probation':    is_probation,
                    })

                    # CR #29: Use frontend achievement for tier determination
                    achievement_overrides[emp_code] = emp_data.get('personal_achievement', 0)

                    # Build adjustments dict from frontend revenue data
                    # Convert { revenue_type: "fashion", fp_adjustment: X, md_adjustment: Y }
                    # → { employee_code: { "fashion_fp": X, "fashion_md": Y } }
                    emp_adjustments: Dict[str, int] = {}
                    for rev in emp_data.get('revenue', []):
                        rev_type = rev.get('revenue_type', '')
                        fp_adj = rev.get('fp_adjustment', 0) or 0
                        md_adj = rev.get('md_adjustment', 0) or 0
                        if fp_adj != 0:
                            emp_adjustments[f'{rev_type}_fp'] = int(fp_adj)
                        if md_adj != 0:
                            emp_adjustments[f'{rev_type}_md'] = int(md_adj)
                    if emp_adjustments:
                        adjustments_dict[emp_code] = emp_adjustments

                if not store_employees:
                    continue

                # Store commission (with frontend eligibility/achievement override)
                store_result = self.calculate_store_commission_v2(
                    store_code=store_code,
                    store_target=store_target,
                    store_fp_ratio_target=store_fp_ratio,
                    query_date=query_date,
                    employees=store_employees,
                    hand_carry_upcs=hand_carry_upcs,
                    all_sales_df=all_sales_df,
                    eligible_override=store_eligible,
                    achievement_override=store_achievement,
                )

                # Personal commission (with frontend achievement override + adjustments from request)
                personal_df = self.calculate_personal_commissions(
                    month=month,
                    year=year,
                    employees=store_employees,
                    store_code=store_code,
                    revenue_adjustments=adjustments_dict,
                    preloaded_sales_df=all_sales_df,
                    preloaded_hand_carry_upcs=hand_carry_upcs,
                    achievement_overrides=achievement_overrides,
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

            # Build response (same shape as v1)
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
                    # CR #59 Phase B: AR payable / withholding fields.
                    # withheld_by_category / released_by_category come through as
                    # dicts from the combined DataFrame; serialise per-category
                    # values as ints. We compute `withheld` total as the sum of
                    # the int-cast category values so the invariant
                    # `withheld == sum(withheld_by_category.values())` always
                    # holds — avoids ±1 rounding drift between a separately-cast
                    # total and the sum of independently-cast categories.
                    withheld_by_cat = row.get('withheld_by_category') or {}
                    if not isinstance(withheld_by_cat, dict):
                        withheld_by_cat = {}
                    released_by_cat = row.get('released_by_category') or {}
                    if not isinstance(released_by_cat, dict):
                        released_by_cat = {}
                    withheld_by_cat_int = {k: int(v) for k, v in withheld_by_cat.items()}
                    released_by_cat_int = {k: int(v) for k, v in released_by_cat.items()}
                    withheld_total_int = sum(withheld_by_cat_int.values())
                    released_total_int = sum(released_by_cat_int.values())
                    employee_total_int = int(row.get('total_handout_commission', 0) or 0)

                    employees_response.append({
                        'employee_code': row['employee_code'],
                        'full_name':     row['employee_name'],
                        'result': {
                            'individual':            int(row.get('store_commission_70pct', 0) or 0),
                            'shared':                int(row.get('store_commission_30pct', 0) or 0),
                            'manager':               int(row.get('manager_bonus', 0) or 0),
                            'fashion_fp':            int(row.get('personal_commission_fashion_fp', 0) or 0),
                            'fashion_md':            int(row.get('personal_commission_fashion_md', 0) or 0),
                            'over_target':           int(row.get('personal_commission_over_100', 0) or 0),
                            'jewelry':               int(jewelry_net),
                            'vhernier':              int(row.get('personal_commission_vhernier', 0) or 0),
                            'rosa_maria':            int(row.get('personal_commission_rosa_maria', 0) or 0),
                            'suitcase':              int(row.get('personal_commission_suitcase', 0) or 0),
                            'hand_carry':            int(row.get('personal_commission_hand_carry', 0) or 0),
                            'home_decor':            int(row.get('personal_commission_home_decor', 0) or 0),
                            'store_total':           int(row.get('total_store_commission', 0) or 0),
                            'personal_total':        int(row.get('personal_commission_total', 0) or 0),
                            'employee_total':        employee_total_int,
                            # CR #59 Phase B
                            'withheld':              withheld_total_int,
                            'withheld_by_category':  withheld_by_cat_int,
                            'released':              released_total_int,
                            'released_by_category':  released_by_cat_int,
                            'payout':                employee_total_int - withheld_total_int + released_total_int,
                        }
                    })

                stores_response.append({
                    'store_code':    sc,
                    'store_name':    store_result.get('store_name', sc),
                    'eligible':      store_result.get('eligible', False),
                    'achievement_pct': store_result.get('achievement_pct', 0),
                    'employees':     employees_response,
                })

            return {
                'success':            True,
                'month':              month,
                'year':               year,
                'stores_processed':   len(stores_data),
                'employees_processed': len(final_df),
                'stores':             stores_response,
            }

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

        # Get nearest snapshot at or before target period to carry forward values
        cursor.execute('''
            SELECT is_active, store_id, department_id, contract_type_id, is_manager
            FROM employee_status_history
            WHERE employee_sid = %s
              AND (period_year < %s OR (period_year = %s AND period_month <= %s))
            ORDER BY period_year DESC, period_month DESC
            LIMIT 1
        ''', (employee_sid, year, year, month))
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
                        'fashion_fp', 'fashion_md', 'jewelry_fp', 'jewelry_md',
                        'vhernier_fp', 'vhernier_md', 'rosa_maria_fp', 'rosa_maria_md',
                        'hand_carry_fp', 'hand_carry_md', 'suitcase_fp', 'suitcase_md',
                        'home_decor_fp', 'home_decor_md', 'other_fp', 'other_md'
                    )),
                    adjustment BIGINT NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (employee_code, month, year, revenue_type)
                )
            ''')
            # CR #27: Migrate existing table — drop ALL constraints first, then migrate data, then re-add
            # Step 1: Drop all check constraints (must happen BEFORE data migration)
            cursor.execute('ALTER TABLE commission_revenue_adjustments DROP CONSTRAINT IF EXISTS commission_revenue_adjustments_revenue_type_check')
            cursor.execute('ALTER TABLE commission_revenue_adjustments DROP CONSTRAINT IF EXISTS commission_revenue_adjustments_month_check')
            cursor.execute('''
                DO $$
                DECLARE
                    r RECORD;
                BEGIN
                    FOR r IN
                        SELECT con.conname
                        FROM pg_constraint con
                        WHERE con.conrelid = 'commission_revenue_adjustments'::regclass
                          AND con.contype = 'c'
                    LOOP
                        EXECUTE 'ALTER TABLE commission_revenue_adjustments DROP CONSTRAINT ' || quote_ident(r.conname);
                    END LOOP;
                END $$;
            ''')
            # Step 2: Migrate old type codes (safe now that CHECK constraints are dropped)
            cursor.execute('''
                UPDATE commission_revenue_adjustments SET revenue_type = 'fashion_fp'
                WHERE revenue_type = 'full_price'
            ''')
            cursor.execute('''
                UPDATE commission_revenue_adjustments SET revenue_type = 'fashion_md'
                WHERE revenue_type = 'markdown'
            ''')
            # Step 3: Delete any rows with types not in the new set
            cursor.execute('''
                DELETE FROM commission_revenue_adjustments
                WHERE revenue_type NOT IN (
                    'fashion_fp', 'fashion_md', 'jewelry_fp', 'jewelry_md',
                    'vhernier_fp', 'vhernier_md', 'rosa_maria_fp', 'rosa_maria_md',
                    'hand_carry_fp', 'hand_carry_md', 'suitcase_fp', 'suitcase_md',
                    'home_decor_fp', 'home_decor_md', 'other_fp', 'other_md'
                )
            ''')
            # Step 4: Re-add check constraints
            cursor.execute('''
                ALTER TABLE commission_revenue_adjustments
                ADD CONSTRAINT commission_revenue_adjustments_month_check
                CHECK (month BETWEEN 1 AND 12)
            ''')
            cursor.execute('''
                ALTER TABLE commission_revenue_adjustments
                ADD CONSTRAINT commission_revenue_adjustments_revenue_type_check
                CHECK (revenue_type IN (
                    'fashion_fp', 'fashion_md', 'jewelry_fp', 'jewelry_md',
                    'vhernier_fp', 'vhernier_md', 'rosa_maria_fp', 'rosa_maria_md',
                    'hand_carry_fp', 'hand_carry_md', 'suitcase_fp', 'suitcase_md',
                    'home_decor_fp', 'home_decor_md', 'other_fp', 'other_md'
                ))
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_commission_revenue_adj_period
                ON commission_revenue_adjustments (month, year)
            ''')
            # CR #28: Add store_code column and update unique constraint
            cursor.execute('''
                DO $$
                BEGIN
                    -- Add store_code column if it doesn't exist
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'commission_revenue_adjustments'
                          AND column_name = 'store_code'
                    ) THEN
                        ALTER TABLE commission_revenue_adjustments
                            ADD COLUMN store_code VARCHAR(20) NOT NULL DEFAULT '';
                    END IF;
                END $$;
            ''')
            # CR #28: Drop old unique constraint, add new one with store_code
            cursor.execute('''
                DO $$
                DECLARE
                    constraint_name TEXT;
                BEGIN
                    -- Find and drop existing unique constraint on (employee_code, month, year, revenue_type)
                    SELECT con.conname INTO constraint_name
                    FROM pg_constraint con
                    WHERE con.conrelid = 'commission_revenue_adjustments'::regclass
                        AND con.contype = 'u'
                    LIMIT 1;

                    IF constraint_name IS NOT NULL THEN
                        EXECUTE 'ALTER TABLE commission_revenue_adjustments DROP CONSTRAINT ' || constraint_name;
                    END IF;
                END $$;
            ''')
            cursor.execute('''
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'uq_commission_revenue_adjustments'
                          AND conrelid = 'commission_revenue_adjustments'::regclass
                    ) THEN
                        ALTER TABLE commission_revenue_adjustments
                            ADD CONSTRAINT uq_commission_revenue_adjustments
                            UNIQUE (employee_code, month, year, revenue_type, store_code);
                    END IF;
                END $$;
            ''')
            # CR #28: Migration — delete pre-CR#28 rows that lack store_code
            cursor.execute('''
                DELETE FROM commission_revenue_adjustments WHERE store_code = ''
            ''')

            # CR #61: per-item rate snapshot written by POST /commission/calculate
            # so Account Payable (CR #60) can compute release commission at the
            # exact rate the original commission used. Shared read by the
            # account_payable module.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payable_bill_rates (
                    bill_sid       VARCHAR(40)    NOT NULL,
                    upc            VARCHAR(50)    NOT NULL,
                    revenue_type   VARCHAR(20)    NOT NULL,
                    fp_or_md       CHAR(2),
                    effective_rate NUMERIC(10,8)  NOT NULL,
                    source_month   CHAR(7)        NOT NULL,
                    updated_at     TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (bill_sid, upc)
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_payable_bill_rates_period
                    ON payable_bill_rates (source_month)
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
        store_code: str,
        adjustments: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Upsert revenue adjustment deltas for an employee at a specific store for a given period.

        Args:
            employee_code: Employee code (e.g. "GL013")
            month: Commission month (1-12)
            year: Commission year
            store_code: Transaction location store code (CR #28)
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
                        (employee_code, month, year, revenue_type, store_code, adjustment, updated_at)
                    VALUES
                        (%(employee_code)s, %(month)s, %(year)s, %(rev_type)s, %(store_code)s,
                         %(delta)s, CURRENT_TIMESTAMP)
                    ON CONFLICT (employee_code, month, year, revenue_type, store_code) DO UPDATE SET
                        adjustment = EXCLUDED.adjustment,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING revenue_type, adjustment
                ''', {
                    'employee_code': employee_code,
                    'month':         month,
                    'year':          year,
                    'rev_type':      rev_type,
                    'store_code':    store_code,
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
                    'store_code':    store_code,
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

    def _load_adjustments(self, month: int, year: int, per_store: bool = False):
        """
        Load all revenue adjustments for a period.

        Args:
            per_store: If False (default), sums across all store_codes →
                       {employee_code: {type: delta}}
                       If True, returns per-store breakdown →
                       {employee_code: {store_code: {type: delta}}}
        """
        conn = get_postgres_connection()
        if not conn:
            return {}
        try:
            cursor = conn.cursor()
            if per_store:
                # CR #28: Per-store adjustments for store-view
                cursor.execute(
                    'SELECT employee_code, store_code, revenue_type, adjustment '
                    'FROM commission_revenue_adjustments WHERE month = %s AND year = %s',
                    (month, year)
                )
                result: Dict[str, Dict[str, Dict[str, int]]] = {}
                for emp_code, sc, rev_type, delta in cursor.fetchall():
                    result.setdefault(emp_code, {}).setdefault(sc, {})[rev_type] = int(delta)
            else:
                # Summed across stores via SQL GROUP BY
                cursor.execute(
                    'SELECT employee_code, revenue_type, SUM(adjustment) '
                    'FROM commission_revenue_adjustments WHERE month = %s AND year = %s '
                    'GROUP BY employee_code, revenue_type',
                    (month, year)
                )
                result: Dict[str, Dict[str, int]] = {}
                for emp_code, rev_type, delta in cursor.fetchall():
                    result.setdefault(emp_code, {})[rev_type] = int(delta)
            cursor.close()
            return result
        finally:
            conn.close()

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

        # 3. Oracle unified sales data + HC UPCs
        oracle_warning = None
        try:
            sales_df = commission_service.repository.get_all_sales_data(year, month)
            hand_carry_upcs = commission_service.repository.get_hand_carry_upcs()
        except Exception as e:
            sales_df = pd.DataFrame()
            hand_carry_upcs = []
            oracle_warning = f'Could not fetch live Oracle sales data: {e}'
            print(f"[WARN] {oracle_warning}")

        # CR #59: annotate sales_df with per-row unpaid_ratio for AR payable
        # detection. payable_amount is later computed per category by re-running
        # _compute_revenue_by_type on a revenue-weighted copy of the DataFrame.
        try:
            unpaid_bills_map = commission_service.repository.get_unpaid_bill_amounts(year, month)
        except Exception as e:
            unpaid_bills_map = {}
            print(f"[WARN] Could not fetch unpaid AR bills: {e}")
        if len(sales_df) > 0 and 'bill_sid' in sales_df.columns:
            sales_df = sales_df.copy()
            sales_df['unpaid_ratio'] = sales_df['bill_sid'].map(
                lambda sid: unpaid_bills_map.get(sid, {}).get('unpaid_ratio', 0.0)
                            if sid is not None else 0.0
            )

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
            base = commission_service._compute_revenue_by_type(
                emp_sales, hand_carry_upcs, year=year, month=month,
            )

            # CR #59: per-category payable revenue (before-VAT). Computed by
            # re-running the same categorisation logic on a revenue-weighted
            # copy of the DataFrame: payable_revenue_per_item =
            # item_revenue × unpaid_ratio. Bills paid by end of month or not
            # on AR have unpaid_ratio = 0, contributing nothing.
            if len(emp_sales) > 0 and 'unpaid_ratio' in emp_sales.columns:
                emp_payable_df = emp_sales.copy()
                emp_payable_df['revenue_before_vat'] = (
                    emp_payable_df['revenue_before_vat'] * emp_payable_df['unpaid_ratio']
                )
                emp_payable_df['revenue_with_vat'] = (
                    emp_payable_df['revenue_with_vat'] * emp_payable_df['unpaid_ratio']
                )
                payable_by_type = commission_service._compute_revenue_by_type(
                    emp_payable_df, hand_carry_upcs, year=year, month=month,
                )
            else:
                payable_by_type = {}

            # CR #12: personal_total_vat = sum(revenue_with_vat) directly from Oracle,
            # independent of the per-type base_amount breakdown (which uses before-VAT for most types).
            if len(emp_sales) > 0 and 'revenue_with_vat' in emp_sales.columns:
                personal_total_vat = int(emp_sales['revenue_with_vat'].sum())
            else:
                personal_total_vat = 0

            # CR #27: FP total = sum of FP from all categories
            personal_fp_vat = sum(v.get('fp', 0) for v in base.values())

            # Apply adjustments — CR #27: per-type-per-tier
            emp_adj = adjustments.get(employee_code, {})
            revenue = []
            personal_total_adjusted = 0
            personal_payable_amount = 0
            for rev_type in REVENUE_TYPE_ORDER:
                type_base = base.get(rev_type, {'fp': 0, 'md': 0, 'total': 0})
                fp_base = type_base.get('fp', 0)
                md_base = type_base.get('md', 0)
                fp_delta = emp_adj.get(f'{rev_type}_fp', 0)
                md_delta = emp_adj.get(f'{rev_type}_md', 0)
                fp_adjusted = fp_base + fp_delta
                md_adjusted = md_base + md_delta
                total_adjusted = fp_adjusted + md_adjusted
                # CR #59: per-category payable_amount (revenue, not commission).
                # Single number per category — sum of FP+MD payable. Same VAT
                # treatment as the category's revenue numbers (most are
                # before-VAT; hand_carry uses with-VAT — see _compute_revenue_by_type).
                payable_type = payable_by_type.get(rev_type, {'fp': 0, 'md': 0, 'total': 0})
                payable_amount = int(payable_type.get('total', 0))
                personal_payable_amount += payable_amount
                revenue.append({
                    'revenue_type': rev_type,
                    'fp':  {'base_amount': fp_base, 'adjustment': fp_delta, 'adjusted_amount': fp_adjusted},
                    'md':  {'base_amount': md_base, 'adjustment': md_delta, 'adjusted_amount': md_adjusted},
                    'total': total_adjusted,
                    'payable_amount': payable_amount,
                })
                personal_total_adjusted += total_adjusted

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
                'payable_amount':        personal_payable_amount,  # CR #59
                'revenue':               revenue,
            })

        # 6. CR #24: Location-based store totals from unified DataFrame
        # Filter by doc_store_code for each store → _compute_revenue_by_type()
        if len(sales_df) > 0:
            for store_code, store_data in stores_map.items():
                store_df = sales_df[sales_df['doc_store_code'] == store_code]
                if len(store_df) > 0:
                    store_revenue = commission_service._compute_revenue_by_type(
                        store_df, hand_carry_upcs, year=year, month=month,
                    )
                    store_data['store_total_vat'] = sum(v.get('total', 0) for v in store_revenue.values())
                    store_data['store_fp_total_vat'] = sum(v.get('fp', 0) for v in store_revenue.values())
                    store_data['store_revenue_by_type'] = store_revenue

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

    def get_store_view_breakdown(self, month: int, year: int) -> Dict[str, Any]:
        """
        CR #26: Revenue breakdown grouped by transaction location (doc_store_code).

        Shows all contributors (employees + SYSADMIN) who generated revenue at each
        physical store, regardless of their roster assignment. Cross-store sales and
        COSM re-attributed items appear under the store where the transaction happened.

        Args:
            month: Commission month (1-12)
            year:  Commission year

        Returns:
            Dict with stores, each containing contributors with FP/MD revenue per category.
        """
        commission_service = CommissionService()

        # 1. Employees from PostgreSQL (for roster assignment + retailpro_username mapping)
        all_employees = commission_service._get_employees_with_username_for_period(month, year)

        # Build username → employee info lookup
        username_to_emp: Dict[str, Dict[str, Any]] = {}
        for emp in all_employees:
            if emp.get('retailpro_username'):
                username_to_emp[emp['retailpro_username']] = emp

        # 2. Store settings (for store_target)
        store_settings_result = CommissionStoreSettingsService().get_commission_stores(month, year)
        store_settings_map = {}
        if store_settings_result.get('success'):
            store_settings_map = {s['store_code']: s for s in store_settings_result.get('stores', [])}

        # 3. Store name lookup from PostgreSQL
        store_names: Dict[str, str] = {}
        conn = None
        try:
            conn = get_postgres_connection()
            if conn:
                cursor = conn.cursor()
                cursor.execute('SELECT store_code, store_name FROM stores')
                for code, name in cursor.fetchall():
                    store_names[code] = name
                cursor.close()
        finally:
            if conn:
                conn.close()

        # 4. Oracle unified sales data + HC UPCs
        oracle_warning = None
        try:
            sales_df = commission_service.repository.get_all_sales_data(year, month)
            hand_carry_upcs = commission_service.repository.get_hand_carry_upcs()
        except Exception as e:
            sales_df = pd.DataFrame()
            hand_carry_upcs = []
            oracle_warning = f'Could not fetch live Oracle sales data: {e}'
            print(f"[WARN] {oracle_warning}")

        # CR #59: annotate sales_df with per-row unpaid_ratio for AR payable
        try:
            unpaid_bills_map = commission_service.repository.get_unpaid_bill_amounts(year, month)
        except Exception as e:
            unpaid_bills_map = {}
            print(f"[WARN] Could not fetch unpaid AR bills: {e}")
        if len(sales_df) > 0 and 'bill_sid' in sales_df.columns:
            sales_df = sales_df.copy()
            sales_df['unpaid_ratio'] = sales_df['bill_sid'].map(
                lambda sid: unpaid_bills_map.get(sid, {}).get('unpaid_ratio', 0.0)
                            if sid is not None else 0.0
            )

        # 5. Load saved adjustments — CR #28: per-store for store-view
        adjustments = self._load_adjustments(month, year, per_store=True)

        # 6. Group sales by doc_store_code → employee_username
        stores_map: Dict[str, Any] = {}

        if len(sales_df) > 0 and 'doc_store_code' in sales_df.columns:
            for doc_store, store_group in sales_df.groupby('doc_store_code', dropna=False):
                doc_store_code = str(doc_store) if doc_store else 'UNKNOWN'
                if doc_store_code == 'nan':
                    doc_store_code = 'UNKNOWN'

                ss = store_settings_map.get(doc_store_code, {})

                # Store-level totals
                store_revenue = commission_service._compute_revenue_by_type(
                    store_group, hand_carry_upcs, year=year, month=month,
                )
                store_total_vat = sum(v.get('total', 0) for v in store_revenue.values())
                store_fp_total_vat = sum(v.get('fp', 0) for v in store_revenue.values())

                contributors = []

                for username, emp_group in store_group.groupby('employee_username', dropna=False):
                    username_str = str(username) if username else None
                    if username_str == 'nan':
                        username_str = None

                    # Resolve employee info from roster
                    emp_info = username_to_emp.get(username_str, {}) if username_str else {}
                    employee_code = emp_info.get('employee_code') or username_str or 'UNKNOWN'
                    full_name = emp_info.get('full_name') or ('System (COSM)' if username_str == 'SYSADMIN' else username_str or 'Unknown')
                    assigned_store_code = emp_info.get('store_code') if emp_info else None
                    is_cross_store = (
                        assigned_store_code is not None
                        and assigned_store_code != doc_store_code
                    )

                    # Per-contributor revenue by type with FP/MD split
                    base = commission_service._compute_revenue_by_type(
                        emp_group, hand_carry_upcs, year=year, month=month,
                    )

                    # CR #59: per-contributor per-category payable revenue
                    if len(emp_group) > 0 and 'unpaid_ratio' in emp_group.columns:
                        emp_payable_df = emp_group.copy()
                        emp_payable_df['revenue_before_vat'] = (
                            emp_payable_df['revenue_before_vat'] * emp_payable_df['unpaid_ratio']
                        )
                        emp_payable_df['revenue_with_vat'] = (
                            emp_payable_df['revenue_with_vat'] * emp_payable_df['unpaid_ratio']
                        )
                        payable_by_type = commission_service._compute_revenue_by_type(
                            emp_payable_df, hand_carry_upcs, year=year, month=month,
                        )
                    else:
                        payable_by_type = {}

                    # Raw Oracle with-VAT total for this contributor at this store
                    if 'revenue_with_vat' in emp_group.columns:
                        contributor_total_vat = int(emp_group['revenue_with_vat'].sum())
                    else:
                        contributor_total_vat = 0

                    # CR #28: Apply per-store adjustments
                    emp_adj = adjustments.get(employee_code, {}).get(doc_store_code, {})
                    revenue = []
                    personal_total_adjusted = 0
                    contributor_payable_amount = 0
                    for rev_type in REVENUE_TYPE_ORDER:
                        type_base = base.get(rev_type, {'fp': 0, 'md': 0, 'total': 0})
                        fp_base = type_base.get('fp', 0)
                        md_base = type_base.get('md', 0)
                        fp_delta = emp_adj.get(f'{rev_type}_fp', 0)
                        md_delta = emp_adj.get(f'{rev_type}_md', 0)
                        fp_adjusted = fp_base + fp_delta
                        md_adjusted = md_base + md_delta
                        total_adjusted = fp_adjusted + md_adjusted
                        # CR #59: per-category payable_amount
                        payable_type = payable_by_type.get(rev_type, {'fp': 0, 'md': 0, 'total': 0})
                        payable_amount = int(payable_type.get('total', 0))
                        contributor_payable_amount += payable_amount
                        revenue.append({
                            'revenue_type': rev_type,
                            'fp':  {'base_amount': fp_base, 'adjustment': fp_delta, 'adjusted_amount': fp_adjusted},
                            'md':  {'base_amount': md_base, 'adjustment': md_delta, 'adjusted_amount': md_adjusted},
                            'total': total_adjusted,
                            'payable_amount': payable_amount,
                        })
                        personal_total_adjusted += total_adjusted

                    contributors.append({
                        'employee_code':         employee_code,
                        'full_name':             full_name,
                        'assigned_store_code':   assigned_store_code,
                        'is_cross_store':        is_cross_store,
                        'revenue':               revenue,
                        'personal_total_adjusted': personal_total_adjusted,
                        'contributor_total_vat':  contributor_total_vat,
                        'payable_amount':        contributor_payable_amount,   # CR #59
                    })

                stores_map[doc_store_code] = {
                    'store_code':        doc_store_code,
                    'store_name':        store_names.get(doc_store_code, doc_store_code),
                    'store_target':      ss.get('store_target'),
                    'store_total_vat':   store_total_vat,
                    'store_fp_total_vat': store_fp_total_vat,
                    'contributors':      contributors,
                }

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

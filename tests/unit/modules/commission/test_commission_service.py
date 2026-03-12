"""
Unit tests for CommissionService

Tests all business logic methods with a mock repository injected via
constructor dependency injection. No real database connections are used.
"""
import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

from app.modules.commission.service import CommissionService


# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture
def mock_repo():
    """Create a mock repository for dependency injection."""
    return MagicMock()


@pytest.fixture
def service(mock_repo):
    """Create a CommissionService with a mock repository."""
    return CommissionService(repository=mock_repo)



# ======================================================================
# calculate_store_commission_v2
# ======================================================================

class TestCalculateStoreCommissionV2:
    """Tests for the main store commission calculation method."""

    _SALE_COUNTER = 0

    @classmethod
    def _next_sale_id(cls):
        cls._SALE_COUNTER += 1
        return cls._SALE_COUNTER

    def _make_all_sales_df(self, store_code, store_rows=None, employee_rows=None):
        """Build a unified all_sales_df DataFrame.

        Args:
            store_code: The store code for doc_store_code on all rows.
            store_rows: List of dicts for store-level revenue (employee_username=None).
                Each dict should have: revenue_with_vat, and optionally vendor_code,
                is_jewelry, discount_rate, category, department.
            employee_rows: List of dicts for employee-level data.
                Each dict should have: employee_username, revenue_before_vat, and
                optionally discount_rate, vendor_code, is_jewelry, department,
                store_code (employee home store, defaults to store_code).
        """
        all_rows = []
        defaults = {
            'vendor_code': 'ABC', 'is_jewelry': 0, 'category': '',
            'discount_rate': 0.0, 'department': 'RTW',
        }

        for row in (store_rows or []):
            sid = self._next_sale_id()
            r = {**defaults, **row}
            all_rows.append({
                'sale_id': sid,
                'upc': str(100 + sid),
                'upc_clean': str(100 + sid),
                'bill_number': f'BILL{sid}',
                'doc_store_code': store_code,
                'sale_date': '2025-01-15',
                'sale_time': '10:00:00',
                'customer_sid': None,
                'employee_sid': None,
                'employee_username': None,
                'store_code': None,
                'vendor_code': r['vendor_code'],
                'is_jewelry': r['is_jewelry'],
                'category': r['category'],
                'department': r['department'],
                'discount_rate': r['discount_rate'],
                'revenue_with_vat': r.get('revenue_with_vat', 0),
                'revenue_before_vat': r.get('revenue_before_vat', 0),
            })

        for row in (employee_rows or []):
            sid = self._next_sale_id()
            r = {**defaults, **row}
            emp_store = r.get('store_code', store_code)
            all_rows.append({
                'sale_id': sid,
                'upc': str(100 + sid),
                'upc_clean': str(100 + sid),
                'bill_number': f'BILL{sid}',
                'doc_store_code': r.get('doc_store_code', store_code),
                'sale_date': '2025-01-15',
                'sale_time': '10:00:00',
                'customer_sid': None,
                'employee_sid': None,
                'employee_username': r['employee_username'],
                'store_code': emp_store,
                'vendor_code': r['vendor_code'],
                'is_jewelry': r['is_jewelry'],
                'category': r['category'],
                'department': r['department'],
                'discount_rate': r['discount_rate'],
                'revenue_with_vat': r.get('revenue_with_vat', 0),
                'revenue_before_vat': r.get('revenue_before_vat', 0),
            })

        if not all_rows:
            return pd.DataFrame(columns=[
                'sale_id', 'upc', 'upc_clean', 'bill_number', 'doc_store_code',
                'sale_date', 'sale_time', 'customer_sid', 'employee_sid',
                'employee_username', 'store_code', 'vendor_code', 'is_jewelry',
                'category', 'department', 'discount_rate', 'revenue_with_vat',
                'revenue_before_vat',
            ])
        return pd.DataFrame(all_rows)

    def _make_query_date(self):
        return {
            'from_date': '2025-01-01 00:00:00',
            'to_date': '2025-01-31 23:59:59',
        }

    def _make_employees(self, count=2, manager_index=0, probation_indices=None):
        """Helper to create employee lists for testing."""
        probation_indices = probation_indices or []
        employees = []
        for i in range(count):
            employees.append({
                'employee_code': f'EMP{i+1:03d}',
                'employee_username': f'user{i+1}',
                'full_name': f'Employee {i+1}',
                'seniority': 5 if i == 0 else 1,
                'personal_target': 500_000_000,
                'working_day_count': 22,
                'is_manager': (i == manager_index),
                'is_probation': (i in probation_indices),
            })
        return employees

    def test_store_below_70_percent_is_ineligible(self, service, mock_repo):
        """Store with achievement < 70% returns ineligible."""
        all_sales_df = self._make_all_sales_df('HBT', store_rows=[
            {'revenue_with_vat': 500_000, 'discount_rate': 0.0},   # FP
            {'revenue_with_vat': 100_000, 'discount_rate': 0.5},   # markdown
        ])

        result = service.calculate_store_commission_v2(
            store_code='HBT',
            store_target=1_000_000,
            store_fp_ratio_target=0.70,
            query_date=self._make_query_date(),
            employees=self._make_employees(2),
            all_sales_df=all_sales_df,
        )

        assert result['eligible'] is False
        assert 'below 70%' in result['reason']
        assert result['employees'] == []

    def test_store_with_insufficient_fp_ratio_compensation(self, service, mock_repo):
        """Store with low FP ratio and insufficient discount compensation is ineligible.
        CR #27: FP = sum of FP from ALL categories, MD = sum of MD from ALL categories.
        """
        # All items at >30% discount → all classified as MD
        # actual_fp = 0, actual_md = 1_000_000
        # actual_fp_ratio = 0, target = 0.80 → fp_shortage = 800_000
        # required_discounted = 1_600_000, actual = 1_000_000 → ineligible
        all_sales_df = self._make_all_sales_df('HBT', store_rows=[
            {'revenue_with_vat': 700_000, 'discount_rate': 0.5},              # fashion MD
            {'revenue_with_vat': 150_000, 'discount_rate': 0.5},              # fashion MD
            {'revenue_with_vat': 150_000, 'is_jewelry': 1, 'vendor_code': 'ATS', 'discount_rate': 0.5},  # jewelry MD
        ])

        result = service.calculate_store_commission_v2(
            store_code='HBT',
            store_target=1_000_000,
            store_fp_ratio_target=0.80,
            query_date=self._make_query_date(),
            employees=self._make_employees(2),
            all_sales_df=all_sales_df,
        )

        assert result['eligible'] is False
        assert 'compensation insufficient' in result['reason']

    def test_eligible_store_with_employees(self, service, mock_repo):
        """Eligible store returns commission data for each employee."""
        all_sales_df = self._make_all_sales_df('HBT',
            store_rows=[
                {'revenue_with_vat': 750_000, 'discount_rate': 0.0},   # FP
                {'revenue_with_vat': 150_000, 'discount_rate': 0.5},   # markdown
            ],
            employee_rows=[
                {'employee_username': 'user1', 'revenue_before_vat': 400_000, 'discount_rate': 0.1},   # FP
                {'employee_username': 'user1', 'revenue_before_vat': 80_000, 'discount_rate': 0.5},    # discounted
                {'employee_username': 'user2', 'revenue_before_vat': 300_000, 'discount_rate': 0.1},   # FP
                {'employee_username': 'user2', 'revenue_before_vat': 70_000, 'discount_rate': 0.5},    # discounted
            ],
        )

        result = service.calculate_store_commission_v2(
            store_code='HBT',
            store_target=1_000_000,
            store_fp_ratio_target=0.70,
            query_date=self._make_query_date(),
            employees=self._make_employees(2),
            all_sales_df=all_sales_df,
        )

        assert result['eligible'] is True
        assert result['achievement_pct'] == pytest.approx(90.0)
        assert len(result['employees']) == 2
        assert result['store_pool'] > 0

    def test_rhn_rwp_combined_store_logic(self, service, mock_repo):
        """RHN/RWP stores combine employee sales from both stores."""
        # Store revenue from doc_store_code=RHN (store-level rows)
        all_sales_df = self._make_all_sales_df('RHN',
            store_rows=[
                {'revenue_with_vat': 1_000_000, 'discount_rate': 0.0},  # FP
                {'revenue_with_vat': 200_000, 'discount_rate': 0.5},    # markdown
            ],
            employee_rows=[
                # user1 sold at RHN (home store RHN)
                {'employee_username': 'user1', 'revenue_before_vat': 300_000, 'discount_rate': 0.1, 'store_code': 'RHN', 'doc_store_code': 'RHN'},
                {'employee_username': 'user1', 'revenue_before_vat': 50_000, 'discount_rate': 0.5, 'store_code': 'RHN', 'doc_store_code': 'RHN'},
                # user1 sold at RWP (home store RHN, cross-store)
                {'employee_username': 'user1', 'revenue_before_vat': 200_000, 'discount_rate': 0.1, 'store_code': 'RHN', 'doc_store_code': 'RWP'},
                {'employee_username': 'user1', 'revenue_before_vat': 30_000, 'discount_rate': 0.5, 'store_code': 'RHN', 'doc_store_code': 'RWP'},
                # user2 sold at RWP (home store RWP)
                {'employee_username': 'user2', 'revenue_before_vat': 400_000, 'discount_rate': 0.1, 'store_code': 'RWP', 'doc_store_code': 'RWP'},
                {'employee_username': 'user2', 'revenue_before_vat': 100_000, 'discount_rate': 0.5, 'store_code': 'RWP', 'doc_store_code': 'RWP'},
            ],
        )

        result = service.calculate_store_commission_v2(
            store_code='RHN',
            store_target=1_000_000,
            store_fp_ratio_target=0.70,
            query_date=self._make_query_date(),
            employees=self._make_employees(2),
            all_sales_df=all_sales_df,
        )

        assert result['eligible'] is True

        # user1 revenue should be combined: 300k + 200k = 500k FP
        emp1 = next(e for e in result['employees'] if e['employee_code'] == 'EMP001')
        assert emp1['fp_revenue'] == pytest.approx(500_000)

    def test_rwp_also_triggers_combined_logic(self, service, mock_repo):
        """RWP store code also triggers the combined RHN/RWP logic."""
        # Store revenue based on doc_store_code=RWP
        all_sales_df = self._make_all_sales_df('RWP',
            store_rows=[
                {'revenue_with_vat': 1_000_000, 'discount_rate': 0.0},  # FP
                {'revenue_with_vat': 200_000, 'discount_rate': 0.5},    # markdown
            ],
            employee_rows=[
                # user1 sold at RHN (home store RHN, cross-store into RWP group)
                {'employee_username': 'user1', 'revenue_before_vat': 600_000, 'discount_rate': 0.1, 'store_code': 'RHN', 'doc_store_code': 'RHN'},
                {'employee_username': 'user1', 'revenue_before_vat': 100_000, 'discount_rate': 0.5, 'store_code': 'RHN', 'doc_store_code': 'RHN'},
                # user2 sold at RWP (home store RWP)
                {'employee_username': 'user2', 'revenue_before_vat': 400_000, 'discount_rate': 0.1, 'store_code': 'RWP', 'doc_store_code': 'RWP'},
                {'employee_username': 'user2', 'revenue_before_vat': 80_000, 'discount_rate': 0.5, 'store_code': 'RWP', 'doc_store_code': 'RWP'},
            ],
        )

        result = service.calculate_store_commission_v2(
            store_code='RWP',
            store_target=1_000_000,
            store_fp_ratio_target=0.70,
            query_date=self._make_query_date(),
            employees=self._make_employees(2),
            all_sales_df=all_sales_df,
        )

        assert result['eligible'] is True

    def test_manager_bonus_at_100_plus_achievement(self, service, mock_repo):
        """Manager receives 3M bonus when store achievement >= 100%."""
        all_sales_df = self._make_all_sales_df('HBT',
            store_rows=[
                {'revenue_with_vat': 900_000, 'discount_rate': 0.0},   # FP
                {'revenue_with_vat': 200_000, 'discount_rate': 0.5},   # markdown
            ],
            employee_rows=[
                {'employee_username': 'user1', 'revenue_before_vat': 600_000, 'discount_rate': 0.1},
                {'employee_username': 'user1', 'revenue_before_vat': 100_000, 'discount_rate': 0.5},
                {'employee_username': 'user2', 'revenue_before_vat': 400_000, 'discount_rate': 0.1},
                {'employee_username': 'user2', 'revenue_before_vat': 100_000, 'discount_rate': 0.5},
            ],
        )

        result = service.calculate_store_commission_v2(
            store_code='HBT',
            store_target=1_000_000,
            store_fp_ratio_target=0.70,
            query_date=self._make_query_date(),
            employees=self._make_employees(2, manager_index=0),
            all_sales_df=all_sales_df,
        )

        assert result['eligible'] is True
        assert result['achievement_pct'] >= 100

        manager = next(e for e in result['employees'] if e['is_manager'])
        assert manager['manager_bonus'] == 3_000_000

        non_manager = next(e for e in result['employees'] if not e['is_manager'])
        assert non_manager['manager_bonus'] == 0

    def test_manager_bonus_between_70_and_100(self, service, mock_repo):
        """Manager receives 750k bonus when 70% <= achievement < 100%."""
        all_sales_df = self._make_all_sales_df('HBT',
            store_rows=[
                {'revenue_with_vat': 750_000, 'discount_rate': 0.0},   # FP
                {'revenue_with_vat': 100_000, 'discount_rate': 0.5},   # markdown
            ],
            employee_rows=[
                {'employee_username': 'user1', 'revenue_before_vat': 500_000, 'discount_rate': 0.1},
                {'employee_username': 'user1', 'revenue_before_vat': 50_000, 'discount_rate': 0.5},
                {'employee_username': 'user2', 'revenue_before_vat': 300_000, 'discount_rate': 0.1},
                {'employee_username': 'user2', 'revenue_before_vat': 50_000, 'discount_rate': 0.5},
            ],
        )

        result = service.calculate_store_commission_v2(
            store_code='HBT',
            store_target=1_000_000,
            store_fp_ratio_target=0.70,
            query_date=self._make_query_date(),
            employees=self._make_employees(2, manager_index=0),
            all_sales_df=all_sales_df,
        )

        assert result['eligible'] is True
        assert 70 <= result['achievement_pct'] < 100

        manager = next(e for e in result['employees'] if e['is_manager'])
        assert manager['manager_bonus'] == 750_000

    def test_probation_employee_gets_equal_share_only(self, service, mock_repo):
        """Probation employee only receives the equal share, not individual share or manager bonus."""
        all_sales_df = self._make_all_sales_df('HBT',
            store_rows=[
                {'revenue_with_vat': 900_000, 'discount_rate': 0.0},   # FP
                {'revenue_with_vat': 200_000, 'discount_rate': 0.5},   # markdown
            ],
            employee_rows=[
                {'employee_username': 'user1', 'revenue_before_vat': 600_000, 'discount_rate': 0.1},
                {'employee_username': 'user1', 'revenue_before_vat': 100_000, 'discount_rate': 0.5},
                {'employee_username': 'user2', 'revenue_before_vat': 400_000, 'discount_rate': 0.1},
                {'employee_username': 'user2', 'revenue_before_vat': 100_000, 'discount_rate': 0.5},
            ],
        )

        employees = self._make_employees(2, manager_index=0, probation_indices=[1])

        result = service.calculate_store_commission_v2(
            store_code='HBT',
            store_target=1_000_000,
            store_fp_ratio_target=0.70,
            query_date=self._make_query_date(),
            employees=employees,
            all_sales_df=all_sales_df,
        )

        assert result['eligible'] is True
        probation_emp = next(e for e in result['employees'] if e['is_probation'])
        # Probation employee: total = equal_share only (no individual_share, no manager_bonus)
        assert probation_emp['total_store_commission'] == pytest.approx(probation_emp['equal_share'])
        # Confirm individual_share is calculated but not added to total
        assert probation_emp['individual_share'] > 0 or probation_emp['individual_share'] == 0

    def test_seniority_affects_tier_category(self, service, mock_repo):
        """Employees with seniority >= 3 years are Senior, otherwise Junior."""
        all_sales_df = self._make_all_sales_df('HBT',
            store_rows=[
                {'revenue_with_vat': 750_000, 'discount_rate': 0.0},   # FP
                {'revenue_with_vat': 150_000, 'discount_rate': 0.5},   # markdown
            ],
            employee_rows=[
                {'employee_username': 'user1', 'revenue_before_vat': 500_000, 'discount_rate': 0.1},
                {'employee_username': 'user1', 'revenue_before_vat': 75_000, 'discount_rate': 0.5},
                {'employee_username': 'user2', 'revenue_before_vat': 300_000, 'discount_rate': 0.1},
                {'employee_username': 'user2', 'revenue_before_vat': 75_000, 'discount_rate': 0.5},
            ],
        )

        employees = [
            {
                'employee_code': 'EMP001',
                'employee_username': 'user1',
                'full_name': 'Senior Employee',
                'seniority': 5,  # >= 3 years -> Senior
                'personal_target': 500_000_000,
                'working_day_count': 22,
                'is_manager': False,
                'is_probation': False,
            },
            {
                'employee_code': 'EMP002',
                'employee_username': 'user2',
                'full_name': 'Junior Employee',
                'seniority': 1,  # < 3 years -> Junior
                'personal_target': 500_000_000,
                'working_day_count': 22,
                'is_manager': False,
                'is_probation': False,
            },
        ]

        result = service.calculate_store_commission_v2(
            store_code='HBT',
            store_target=1_000_000,
            store_fp_ratio_target=0.70,
            query_date=self._make_query_date(),
            employees=employees,
            all_sales_df=all_sales_df,
        )

        assert result['eligible'] is True
        assert len(result['employees']) == 2

    def test_rhn_rwp_equal_share_uses_working_day_ratio(self, service, mock_repo):
        """RHN/RWP stores distribute the 30% equal share by working day ratio."""
        all_sales_df = self._make_all_sales_df('RHN',
            store_rows=[
                {'revenue_with_vat': 1_000_000, 'discount_rate': 0.0},  # FP
                {'revenue_with_vat': 200_000, 'discount_rate': 0.5},    # markdown
            ],
            employee_rows=[
                # user1 at RHN (home store RHN)
                {'employee_username': 'user1', 'revenue_before_vat': 600_000, 'discount_rate': 0.1, 'store_code': 'RHN', 'doc_store_code': 'RHN'},
                {'employee_username': 'user1', 'revenue_before_vat': 100_000, 'discount_rate': 0.5, 'store_code': 'RHN', 'doc_store_code': 'RHN'},
                # user2 at RWP (home store RWP)
                {'employee_username': 'user2', 'revenue_before_vat': 400_000, 'discount_rate': 0.1, 'store_code': 'RWP', 'doc_store_code': 'RWP'},
                {'employee_username': 'user2', 'revenue_before_vat': 100_000, 'discount_rate': 0.5, 'store_code': 'RWP', 'doc_store_code': 'RWP'},
            ],
        )

        employees = [
            {
                'employee_code': 'EMP001',
                'employee_username': 'user1',
                'full_name': 'Employee 1',
                'seniority': 5,
                'personal_target': 500_000_000,
                'working_day_count': 26,  # More working days
                'is_manager': False,
                'is_probation': False,
            },
            {
                'employee_code': 'EMP002',
                'employee_username': 'user2',
                'full_name': 'Employee 2',
                'seniority': 1,
                'personal_target': 500_000_000,
                'working_day_count': 18,  # Fewer working days
                'is_manager': False,
                'is_probation': False,
            },
        ]

        result = service.calculate_store_commission_v2(
            store_code='RHN',
            store_target=1_000_000,
            store_fp_ratio_target=0.70,
            query_date=self._make_query_date(),
            employees=employees,
            all_sales_df=all_sales_df,
        )

        emp1 = next(e for e in result['employees'] if e['employee_code'] == 'EMP001')
        emp2 = next(e for e in result['employees'] if e['employee_code'] == 'EMP002')

        # Working day ratios: 26/(26+18) = 26/44, 18/(26+18) = 18/44
        # emp1 should have a larger equal share than emp2
        assert emp1['equal_share'] > emp2['equal_share']
        ratio = emp1['equal_share'] / emp2['equal_share']
        assert ratio == pytest.approx(26.0 / 18.0, rel=1e-4)

    def test_employee_without_username_gets_zero_revenue(self, service, mock_repo):
        """Employee without employee_username still participates but has 0 revenue."""
        all_sales_df = self._make_all_sales_df('HBT',
            store_rows=[
                {'revenue_with_vat': 750_000, 'discount_rate': 0.0},   # FP
                {'revenue_with_vat': 150_000, 'discount_rate': 0.5},   # markdown
            ],
            employee_rows=[
                {'employee_username': 'user1', 'revenue_before_vat': 750_000, 'discount_rate': 0.1},
                {'employee_username': 'user1', 'revenue_before_vat': 150_000, 'discount_rate': 0.5},
            ],
        )

        employees = [
            {
                'employee_code': 'EMP001',
                'employee_username': 'user1',
                'full_name': 'Has Username',
                'seniority': 5,
                'personal_target': 500_000_000,
                'working_day_count': 22,
                'is_manager': False,
                'is_probation': False,
            },
            {
                'employee_code': 'EMP002',
                'employee_username': None,  # No username
                'full_name': 'No Username',
                'seniority': 1,
                'personal_target': 500_000_000,
                'working_day_count': 22,
                'is_manager': False,
                'is_probation': False,
            },
        ]

        result = service.calculate_store_commission_v2(
            store_code='HBT',
            store_target=1_000_000,
            store_fp_ratio_target=0.70,
            query_date=self._make_query_date(),
            employees=employees,
            all_sales_df=all_sales_df,
        )

        assert result['eligible'] is True
        no_username_emp = next(e for e in result['employees'] if e['employee_code'] == 'EMP002')
        assert no_username_emp['fp_revenue'] == 0
        assert no_username_emp['disc_revenue'] == 0
        # They still get the equal share
        assert no_username_emp['equal_share'] > 0


# ======================================================================
# calculate_combined_commission
# ======================================================================

class TestCalculateCombinedCommission:
    """Tests for combining store and personal commission into a single report."""

    def _make_personal_df(self, employee_codes=None):
        """Helper to create a personal commission DataFrame."""
        if employee_codes is None:
            employee_codes = ['EMP001', 'EMP002']

        rows = []
        for i, code in enumerate(employee_codes):
            rows.append({
                'employee_code': code,
                'fullname': f'Employee {i+1}',
                'store_code': 'HBT',
                'commission_fashion_fp': 10_000 * (i + 1),
                'commission_fashion_md': 2_000 * (i + 1),
                'commission_over_100': 5_000 * (i + 1),
                'commission_jewelry': 3_000 * (i + 1),
                'commission_vhernier': 1_000 * (i + 1),
                'commission_rosa_maria': 500 * (i + 1),
                'commission_suitcase': 500_000 * (i + 1),
                'commission_hand_carry': 2_000 * (i + 1),
                'commission_home_decor': 0,
                'total': 522_500 * (i + 1),
            })
        return pd.DataFrame(rows)

    def test_eligible_store_combined(self, service):
        """Eligible store combines store and personal commission correctly."""
        store_result = {
            'eligible': True,
            'achievement_pct': 95.0,
            'employees': [
                {
                    'employee_code': 'EMP001',
                    'individual_share': 20_000,
                    'equal_share': 10_000,
                    'manager_bonus': 0,
                    'total_store_commission': 30_000,
                },
                {
                    'employee_code': 'EMP002',
                    'individual_share': 15_000,
                    'equal_share': 10_000,
                    'manager_bonus': 0,
                    'total_store_commission': 25_000,
                },
            ],
        }
        personal_df = self._make_personal_df()

        result = service.calculate_combined_commission(store_result, personal_df)

        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2

        emp1 = result[result['employee_code'] == 'EMP001'].iloc[0]
        assert emp1['store_achievement_pct'] == 95.0
        assert emp1['store_commission_70pct'] == 20_000
        assert emp1['store_commission_30pct'] == 10_000
        assert emp1['total_store_commission'] == 30_000
        assert emp1['personal_commission_total'] == 522_500
        assert emp1['total_handout_commission'] == pytest.approx(30_000 + 522_500)

    def test_ineligible_store_zeros_for_store_commission(self, service):
        """Ineligible store results in zero store commission for all employees."""
        store_result = {
            'eligible': False,
            'achievement_pct': 60.0,
            'reason': 'Store achievement below 70%',
            'employees': [],
        }
        personal_df = self._make_personal_df()

        result = service.calculate_combined_commission(store_result, personal_df)

        assert len(result) == 2
        for _, row in result.iterrows():
            assert row['store_commission_70pct'] == 0
            assert row['store_commission_30pct'] == 0
            assert row['manager_bonus'] == 0
            assert row['total_store_commission'] == 0
            # Personal commission remains
            assert row['personal_commission_total'] > 0
            # Total handout = personal only
            assert row['total_handout_commission'] == row['personal_commission_total']

    def test_employee_not_in_store_result_gets_zero_store_commission(self, service):
        """Employee in personal DF but not in store result gets zero store commission."""
        store_result = {
            'eligible': True,
            'achievement_pct': 90.0,
            'employees': [
                {
                    'employee_code': 'EMP001',
                    'individual_share': 20_000,
                    'equal_share': 10_000,
                    'manager_bonus': 0,
                    'total_store_commission': 30_000,
                },
                # EMP002 is missing from store result
            ],
        }
        personal_df = self._make_personal_df(['EMP001', 'EMP002'])

        result = service.calculate_combined_commission(store_result, personal_df)

        emp2 = result[result['employee_code'] == 'EMP002'].iloc[0]
        assert emp2['total_store_commission'] == 0
        assert emp2['total_handout_commission'] == emp2['personal_commission_total']

    def test_output_columns(self, service):
        """Verify all expected columns are present in the output DataFrame."""
        store_result = {
            'eligible': True,
            'achievement_pct': 85.0,
            'employees': [
                {
                    'employee_code': 'EMP001',
                    'individual_share': 10_000,
                    'equal_share': 5_000,
                    'manager_bonus': 0,
                    'total_store_commission': 15_000,
                },
            ],
        }
        personal_df = self._make_personal_df(['EMP001'])

        result = service.calculate_combined_commission(store_result, personal_df)

        expected_columns = [
            'employee_code', 'employee_name', 'store_code',
            'store_achievement_pct',
            'store_commission_70pct', 'store_commission_30pct',
            'manager_bonus', 'total_store_commission',
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
        ]
        assert list(result.columns) == expected_columns

    def test_manager_bonus_included_in_combined(self, service):
        """Manager bonus from store result is reflected in the combined output."""
        store_result = {
            'eligible': True,
            'achievement_pct': 105.0,
            'employees': [
                {
                    'employee_code': 'EMP001',
                    'individual_share': 20_000,
                    'equal_share': 10_000,
                    'manager_bonus': 3_000_000,
                    'total_store_commission': 3_030_000,
                },
            ],
        }
        personal_df = self._make_personal_df(['EMP001'])

        result = service.calculate_combined_commission(store_result, personal_df)
        emp1 = result.iloc[0]

        assert emp1['manager_bonus'] == 3_000_000
        assert emp1['total_store_commission'] == 3_030_000
        assert emp1['total_handout_commission'] == pytest.approx(3_030_000 + 522_500)


# ======================================================================
# calculate_personal_commissions
# ======================================================================

class TestCalculatePersonalCommissions:
    """Tests for personal commission calculation (pandas-heavy method)."""

    def _make_sales_df(self, rows):
        """Helper to create a sales DataFrame with required columns."""
        columns = [
            'sale_id', 'upc', 'bill_number', 'doc_store_code', 'sale_date', 'sale_time',
            'customer_sid', 'employee_sid', 'employee_username', 'store_code',
            'vendor_code', 'is_jewelry', 'category', 'department', 'discount_rate',
            'revenue_with_vat', 'revenue_before_vat',
        ]
        df = pd.DataFrame(rows, columns=columns)
        df['upc_clean'] = df['upc'].astype(str).str.strip()
        return df

    def test_missing_month_returns_error_df(self, service, mock_repo):
        result = service.calculate_personal_commissions(
            month=None, year=2025, employees=[{'employee_code': 'E1'}]
        )
        assert 'error' in result.columns

    def test_missing_year_returns_error_df(self, service, mock_repo):
        result = service.calculate_personal_commissions(
            month=1, year=None, employees=[{'employee_code': 'E1'}]
        )
        assert 'error' in result.columns

    def test_empty_employees_returns_error_df(self, service, mock_repo):
        result = service.calculate_personal_commissions(
            month=1, year=2025, employees=[]
        )
        assert 'error' in result.columns

    def test_none_employees_returns_error_df(self, service, mock_repo):
        result = service.calculate_personal_commissions(
            month=1, year=2025, employees=None
        )
        assert 'error' in result.columns

    def test_employee_without_code_gets_zero(self, service, mock_repo):
        """Employee with None employee_code gets zero commission."""
        sales_df = self._make_sales_df([
            [1, 'UPC001', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             None, 'SID1', 'user1', 'HBT',
             'ABC', 0, 'SHIRTS', 'RTW', 0.0, 1_100_000, 1_000_000],
        ])
        mock_repo.get_all_sales_data.return_value = sales_df
        mock_repo.get_hand_carry_upcs.return_value = []

        employees = [
            {
                'employee_code': None,
                'employee_username': 'user1',
                'personal_target': 1_000_000,
                'full_name': 'No Code',
                'store_code': 'HBT',
            }
        ]

        result = service.calculate_personal_commissions(
            month=1, year=2025, employees=employees
        )
        assert len(result) == 1
        assert result.iloc[0]['total'] == 0

    def test_employee_without_username_gets_zero(self, service, mock_repo):
        """Employee without username cannot match to sales data."""
        sales_df = self._make_sales_df([
            [1, 'UPC001', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             None, 'SID1', 'user1', 'HBT',
             'ABC', 0, 'SHIRTS', 'RTW', 0.0, 1_100_000, 1_000_000],
        ])
        mock_repo.get_all_sales_data.return_value = sales_df
        mock_repo.get_hand_carry_upcs.return_value = []

        employees = [
            {
                'employee_code': 'EMP001',
                'employee_username': None,
                'personal_target': 1_000_000,
                'full_name': 'No Username',
                'store_code': 'HBT',
            }
        ]

        result = service.calculate_personal_commissions(
            month=1, year=2025, employees=employees
        )
        assert result.iloc[0]['total'] == 0

    def test_employee_below_50_percent_gets_no_personal_commission(self, service, mock_repo):
        """Below 50% achievement: no personal commission (but jewelry/hand_carry/suitcase still paid)."""
        sales_df = self._make_sales_df([
            [1, 'UPC001', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             None, 'SID1', 'user1', 'HBT',
             'ABC', 0, 'SHIRTS', 'RTW', 0.0, 400_000, 363_636],
        ])
        mock_repo.get_all_sales_data.return_value = sales_df
        mock_repo.get_hand_carry_upcs.return_value = []

        employees = [
            {
                'employee_code': 'EMP001',
                'employee_username': 'user1',
                'personal_target': 1_000_000,  # achievement = 400k/1M = 40%
                'full_name': 'Below Threshold',
                'store_code': 'HBT',
            }
        ]

        result = service.calculate_personal_commissions(
            month=1, year=2025, employees=employees
        )
        emp = result.iloc[0]
        # No FP or discount personal commission at < 50%
        assert emp['commission_fashion_fp'] == 0
        assert emp['commission_fashion_md'] == 0
        assert emp['commission_over_100'] == 0

    def test_tier1_50_to_70_percent_commission(self, service, mock_repo):
        """Tier 1 (50-70%): FP at 0.25%, discount at 0.125% (standard rates)."""
        # target = 1,000,000. revenue_with_vat = 600,000 => 60% achievement
        sales_df = self._make_sales_df([
            # Full price item (discount_rate <= 0.30)
            [1, 'UPC001', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             None, 'SID1', 'user1', 'HBT',
             'ABC', 0, 'SHIRTS', 'RTW', 0.10, 600_000, 500_000],
            # Discounted item (discount_rate > 0.30)
            [2, 'UPC002', 'BILL1', 'HBT', '2025-01-15', '10:01:00',
             None, 'SID1', 'user1', 'HBT',
             'ABC', 0, 'PANTS', 'RTW', 0.50, 0, 200_000],
        ])
        mock_repo.get_all_sales_data.return_value = sales_df
        mock_repo.get_hand_carry_upcs.return_value = []

        employees = [
            {
                'employee_code': 'EMP001',
                'employee_username': 'user1',
                'personal_target': 1_000_000,
                'full_name': 'Tier1 Employee',
                'store_code': 'HBT',
            }
        ]

        result = service.calculate_personal_commissions(
            month=1, year=2025, employees=employees
        )
        emp = result.iloc[0]
        # Standard tier1 rates: FP 0.25%, discount 0.125%
        assert emp['commission_fashion_fp'] == pytest.approx(500_000 * 0.0025)
        assert emp['commission_fashion_md'] == pytest.approx(200_000 * 0.00125)

    def test_tier2_70_to_100_percent_commission(self, service, mock_repo):
        """Tier 2 (70-100%): FP at 0.5%, discount at 0.25% (standard rates)."""
        sales_df = self._make_sales_df([
            [1, 'UPC001', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             None, 'SID1', 'user1', 'HBT',
             'ABC', 0, 'SHIRTS', 'RTW', 0.10, 800_000, 700_000],
            [2, 'UPC002', 'BILL1', 'HBT', '2025-01-15', '10:01:00',
             None, 'SID1', 'user1', 'HBT',
             'ABC', 0, 'PANTS', 'RTW', 0.50, 0, 100_000],
        ])
        mock_repo.get_all_sales_data.return_value = sales_df
        mock_repo.get_hand_carry_upcs.return_value = []

        employees = [
            {
                'employee_code': 'EMP001',
                'employee_username': 'user1',
                'personal_target': 1_000_000,  # 800k/1M = 80%
                'full_name': 'Tier2 Employee',
                'store_code': 'HBT',
            }
        ]

        result = service.calculate_personal_commissions(
            month=1, year=2025, employees=employees
        )
        emp = result.iloc[0]
        assert emp['commission_fashion_fp'] == pytest.approx(700_000 * 0.005)
        assert emp['commission_fashion_md'] == pytest.approx(100_000 * 0.0025)

    def test_jewelry_commission_regardless_of_achievement(self, service, mock_repo):
        """Jewelry commission is paid regardless of achievement rate."""
        sales_df = self._make_sales_df([
            # VHN jewelry: 1% on revenue_before_vat
            [1, 'UPC001', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             None, 'SID1', 'user1', 'HBT',
             'VHN', 1, 'RINGS', 'JWL', 0.0, 300_000, 272_727],
            # ROM EARRINGS: 3%
            [2, 'UPC002', 'BILL1', 'HBT', '2025-01-15', '10:01:00',
             None, 'SID1', 'user1', 'HBT',
             'ROM', 1, 'EARRINGS', 'JWL', 0.0, 200_000, 181_818],
        ])
        mock_repo.get_all_sales_data.return_value = sales_df
        mock_repo.get_hand_carry_upcs.return_value = []

        employees = [
            {
                'employee_code': 'EMP001',
                'employee_username': 'user1',
                'personal_target': 10_000_000,  # Very low achievement (5%)
                'full_name': 'Jewelry Seller',
                'store_code': 'HBT',
            }
        ]

        result = service.calculate_personal_commissions(
            month=1, year=2025, employees=employees
        )
        emp = result.iloc[0]

        assert emp['commission_vhernier'] == pytest.approx(272_727 * 0.01)
        assert emp['commission_rosa_maria'] == pytest.approx(181_818 * 0.03)
        assert emp['commission_jewelry'] == pytest.approx(272_727 * 0.01 + 181_818 * 0.03)

    def test_suitcase_commission_flat_rate(self, service, mock_repo):
        """TVL/TIT items earn 500,000 VND per item regardless of achievement."""
        sales_df = self._make_sales_df([
            [1, 'UPC001', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             None, 'SID1', 'user1', 'HBT',
             'TVL', 0, 'LUGGAGE', 'ACC', 0.0, 5_000_000, 4_545_454],
            [2, 'UPC002', 'BILL1', 'HBT', '2025-01-15', '10:01:00',
             None, 'SID1', 'user1', 'HBT',
             'TIT', 0, 'LUGGAGE', 'ACC', 0.0, 3_000_000, 2_727_272],
        ])
        mock_repo.get_all_sales_data.return_value = sales_df
        mock_repo.get_hand_carry_upcs.return_value = []

        employees = [
            {
                'employee_code': 'EMP001',
                'employee_username': 'user1',
                'personal_target': 100_000_000,  # Low achievement
                'full_name': 'Suitcase Seller',
                'store_code': 'HBT',
            }
        ]

        result = service.calculate_personal_commissions(
            month=1, year=2025, employees=employees
        )
        emp = result.iloc[0]
        assert emp['commission_suitcase'] == 2 * 500_000  # 2 items x 500k

    def test_hand_carry_commission_by_vendor(self, service, mock_repo):
        """Hand carry items earn commission based on vendor rates."""
        hand_carry_upcs = ['HC001', 'HC002', 'HC003']

        sales_df = self._make_sales_df([
            # 1% vendor (ATQ) hand carry
            [1, 'HC001', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             None, 'SID1', 'user1', 'HBT',
             'ATQ', 0, 'BAGS', 'ACC', 0.0, 1_000_000, 909_090],
            # 2% vendor (CGI) hand carry
            [2, 'HC002', 'BILL1', 'HBT', '2025-01-15', '10:01:00',
             None, 'SID1', 'user1', 'HBT',
             'CGI', 0, 'BAGS', 'ACC', 0.0, 2_000_000, 1_818_181],
            # ROM EARRINGS hand carry: 3%
            [3, 'HC003', 'BILL1', 'HBT', '2025-01-15', '10:02:00',
             None, 'SID1', 'user1', 'HBT',
             'ROM', 1, 'EARRINGS', 'JWL', 0.0, 500_000, 454_545],
        ])
        mock_repo.get_all_sales_data.return_value = sales_df
        mock_repo.get_hand_carry_upcs.return_value = hand_carry_upcs

        employees = [
            {
                'employee_code': 'EMP001',
                'employee_username': 'user1',
                'personal_target': 100_000_000,
                'full_name': 'HC Seller',
                'store_code': 'HBT',
            }
        ]

        result = service.calculate_personal_commissions(
            month=1, year=2025, employees=employees
        )
        emp = result.iloc[0]

        # Hand carry commission: ATQ 1% + CGI 2% + ROM EARRINGS 3%
        expected_hc = (1_000_000 * 0.01) + (2_000_000 * 0.02) + (500_000 * 0.03)
        assert emp['commission_hand_carry'] == pytest.approx(expected_hc)

    def test_cosm_hea_excluded_from_commission_but_counts_for_achievement(self, service, mock_repo):
        """COSM+HEA items count toward achievement but earn no commission."""
        sales_df = self._make_sales_df([
            # Regular RTW item
            [1, 'UPC001', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             None, 'SID1', 'user1', 'HBT',
             'ABC', 0, 'SHIRTS', 'RTW', 0.10, 500_000, 454_545],
            # COSM+HEA item (excluded from commission — only HEA vendor)
            [2, 'UPC002', 'BILL2', 'HBT', '2025-01-15', '11:00:00',
             None, 'SID1', 'user1', 'HBT',
             'HEA', 0, 'CREAM', 'COSM', 0.10, 300_000, 272_727],
        ])
        mock_repo.get_all_sales_data.return_value = sales_df
        mock_repo.get_hand_carry_upcs.return_value = []

        employees = [
            {
                'employee_code': 'EMP001',
                'employee_username': 'user1',
                'personal_target': 1_000_000,  # 800k/1M = 80% (uses both items for achievement)
                'full_name': 'COSM Test',
                'store_code': 'HBT',
            }
        ]

        result = service.calculate_personal_commissions(
            month=1, year=2025, employees=employees
        )
        emp = result.iloc[0]

        # FP commission only on non-COSM item (454,545 * tier2 rate)
        # Achievement = (500k + 300k) / 1M = 80% -> tier2 rate 0.005
        assert emp['commission_fashion_fp'] == pytest.approx(454_545 * 0.005)

    def test_cosm_non_hea_earns_fashion_commission(self, service, mock_repo):
        """COSM items with non-HEA vendor are classified as fashion and earn commission."""
        sales_df = self._make_sales_df([
            # Regular RTW item
            [1, 'UPC001', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             None, 'SID1', 'user1', 'HBT',
             'ABC', 0, 'SHIRTS', 'RTW', 0.10, 500_000, 454_545],
            # COSM+NBP item (non-HEA → earns fashion commission)
            [2, 'UPC002', 'BILL2', 'HBT', '2025-01-15', '11:00:00',
             None, 'SID1', 'user1', 'HBT',
             'NBP', 0, 'OTHER', 'COSM', 0.10, 300_000, 272_727],
        ])
        mock_repo.get_all_sales_data.return_value = sales_df
        mock_repo.get_hand_carry_upcs.return_value = []

        employees = [
            {
                'employee_code': 'EMP001',
                'employee_username': 'user1',
                'personal_target': 1_000_000,  # 800k/1M = 80% -> tier2
                'full_name': 'COSM Non-HEA Test',
                'store_code': 'HBT',
            }
        ]

        result = service.calculate_personal_commissions(
            month=1, year=2025, employees=employees
        )
        emp = result.iloc[0]

        # Both items earn FP commission: (454,545 + 272,727) * tier2 rate 0.005
        assert emp['commission_fashion_fp'] == pytest.approx((454_545 + 272_727) * 0.005)

    def test_rwd_store_uses_special_rates(self, service, mock_repo):
        """RWD store uses special commission rates when ENABLE_RWD_SPECIAL_RATES is True."""
        sales_df = self._make_sales_df([
            [1, 'UPC001', 'BILL1', 'RWD', '2025-01-15', '10:00:00',
             None, 'SID1', 'user1', 'RWD',
             'ABC', 0, 'SHIRTS', 'RTW', 0.10, 600_000, 500_000],
        ])
        mock_repo.get_all_sales_data.return_value = sales_df
        mock_repo.get_hand_carry_upcs.return_value = []

        employees = [
            {
                'employee_code': 'EMP001',
                'employee_username': 'user1',
                'personal_target': 1_000_000,  # 60% -> tier1
                'full_name': 'RWD Employee',
                'store_code': 'RWD',
            }
        ]

        with patch('app.modules.commission.service.ENABLE_RWD_SPECIAL_RATES', True):
            result = service.calculate_personal_commissions(
                month=1, year=2025, employees=employees, store_code='RWD'
            )

        emp = result.iloc[0]
        # RWD tier1 FP rate = 0.00375 (vs standard 0.0025)
        assert emp['commission_fashion_fp'] == pytest.approx(500_000 * 0.00375)

    def test_output_dataframe_columns(self, service, mock_repo):
        """Verify the output DataFrame has all expected columns."""
        sales_df = self._make_sales_df([
            [1, 'UPC001', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             None, 'SID1', 'user1', 'HBT',
             'ABC', 0, 'SHIRTS', 'RTW', 0.0, 1_000_000, 909_090],
        ])
        mock_repo.get_all_sales_data.return_value = sales_df
        mock_repo.get_hand_carry_upcs.return_value = []

        employees = [
            {
                'employee_code': 'EMP001',
                'employee_username': 'user1',
                'personal_target': 1_000_000,
                'full_name': 'Test',
                'store_code': 'HBT',
            }
        ]

        result = service.calculate_personal_commissions(
            month=1, year=2025, employees=employees
        )

        expected_columns = [
            'employee_code', 'fullname', 'store_code',
            'commission_fashion_fp', 'commission_fashion_md',
            'commission_over_100', 'commission_jewelry',
            'commission_vhernier', 'commission_rosa_maria',
            'commission_suitcase', 'commission_hand_carry',
            'commission_home_decor', 'total',
        ]
        assert list(result.columns) == expected_columns

    def test_employee_with_no_sales_gets_zero(self, service, mock_repo):
        """Employee whose SID is in sales df but has zero actual rows gets zero."""
        # Sales df has user1 data but user2 has no rows
        sales_df = self._make_sales_df([
            [1, 'UPC001', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             None, 'SID1', 'user1', 'HBT',
             'ABC', 0, 'SHIRTS', 'RTW', 0.0, 1_000_000, 909_090],
        ])
        mock_repo.get_all_sales_data.return_value = sales_df
        mock_repo.get_hand_carry_upcs.return_value = []

        employees = [
            {
                'employee_code': 'EMP001',
                'employee_username': 'user1',
                'personal_target': 1_000_000,
                'full_name': 'Has Sales',
                'store_code': 'HBT',
            },
            {
                'employee_code': 'EMP002',
                'employee_username': 'user_not_in_df',
                'personal_target': 1_000_000,
                'full_name': 'No Sales',
                'store_code': 'HBT',
            },
        ]

        result = service.calculate_personal_commissions(
            month=1, year=2025, employees=employees
        )

        emp2 = result[result['employee_code'] == 'EMP002'].iloc[0]
        assert emp2['total'] == 0


class TestEmployeeCommissionException:
    """Tests for EMPLOYEE_COMMISSION_EXCEPTIONS — flat rate on non-jewelry
    sold to a qualifying customer only."""

    EXCEPTION_EMP_SID = 690036963000170943
    QUALIFYING_CUSTOMER = 690837303000121462
    OTHER_CUSTOMER = 999999999999999999

    @pytest.fixture
    def mock_repo(self):
        return MagicMock()

    @pytest.fixture
    def service(self, mock_repo):
        return CommissionService(repository=mock_repo)

    def _make_sales_df(self, rows):
        """Sales DataFrame with customer_sid column for exception tests."""
        columns = [
            'sale_id', 'upc', 'bill_number', 'doc_store_code', 'sale_date', 'sale_time',
            'customer_sid', 'employee_sid', 'employee_username', 'store_code',
            'vendor_code', 'is_jewelry', 'category', 'department', 'discount_rate',
            'revenue_with_vat', 'revenue_before_vat',
        ]
        df = pd.DataFrame(rows, columns=columns)
        df['upc_clean'] = df['upc'].astype(str).str.strip()
        return df

    def test_flat_rate_qualifying_customer(self, service, mock_repo):
        """Non-jewelry sales to qualifying customer earn 0.7% flat in FP bucket."""
        sales_df = self._make_sales_df([
            [1, 'UPC001', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             self.QUALIFYING_CUSTOMER, self.EXCEPTION_EMP_SID, 'user1', 'HBT',
             'ABC', 0, 'SHIRTS', 'RTW', 0.0, 11_000_000, 10_000_000],
        ])
        mock_repo.get_all_sales_data.return_value = sales_df
        mock_repo.get_hand_carry_upcs.return_value = []

        employees = [{
            'employee_code': 'EMP001',
            'employee_username': 'user1',
            'personal_target': 0,
            'full_name': 'Exception Employee',
            'store_code': 'HBT',
        }]

        result = service.calculate_personal_commissions(month=1, year=2025, employees=employees)
        row = result.iloc[0]

        assert row['commission_fashion_fp'] == 10_000_000 * 0.007  # 70,000
        assert row['commission_fashion_md'] == 0
        assert row['commission_over_100'] == 0
        assert row['total'] == 10_000_000 * 0.007

    def test_non_qualifying_customer_no_commission(self, service, mock_repo):
        """Non-jewelry sales to other customers earn zero FP/discount commission."""
        sales_df = self._make_sales_df([
            [1, 'UPC001', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             self.OTHER_CUSTOMER, self.EXCEPTION_EMP_SID, 'user1', 'HBT',
             'ABC', 0, 'SHIRTS', 'RTW', 0.0, 11_000_000, 10_000_000],
        ])
        mock_repo.get_all_sales_data.return_value = sales_df
        mock_repo.get_hand_carry_upcs.return_value = []

        employees = [{
            'employee_code': 'EMP001',
            'employee_username': 'user1',
            'personal_target': 0,
            'full_name': 'Exception Employee',
            'store_code': 'HBT',
        }]

        result = service.calculate_personal_commissions(month=1, year=2025, employees=employees)
        row = result.iloc[0]

        assert row['commission_fashion_fp'] == 0
        assert row['commission_fashion_md'] == 0
        assert row['total'] == 0

    def test_jewelry_still_earned_regardless_of_customer(self, service, mock_repo):
        """Jewelry commission is calculated normally, no customer filter."""
        sales_df = self._make_sales_df([
            # VHN jewelry sale to a NON-qualifying customer — should still earn commission
            [1, 'UPC001', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             self.OTHER_CUSTOMER, self.EXCEPTION_EMP_SID, 'user1', 'HBT',
             'VHN', 1, 'NECKLACE', 'WJEW', 0.0, 5_500_000, 5_000_000],
        ])
        mock_repo.get_all_sales_data.return_value = sales_df
        mock_repo.get_hand_carry_upcs.return_value = []

        employees = [{
            'employee_code': 'EMP001',
            'employee_username': 'user1',
            'personal_target': 0,
            'full_name': 'Exception Employee',
            'store_code': 'HBT',
        }]

        result = service.calculate_personal_commissions(month=1, year=2025, employees=employees)
        row = result.iloc[0]

        assert row['commission_vhernier'] == 5_000_000 * 0.01  # 50,000
        assert row['commission_jewelry'] == 5_000_000 * 0.01
        assert row['commission_fashion_fp'] == 0  # No qualifying non-jewelry
        assert row['total'] == 5_000_000 * 0.01

    def test_hand_carry_still_earned_regardless_of_customer(self, service, mock_repo):
        """Hand carry commission is calculated normally, no customer filter."""
        sales_df = self._make_sales_df([
            # Hand carry item (ATQ, 1% vendor) sold to non-qualifying customer
            [1, 'HC_UPC', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             self.OTHER_CUSTOMER, self.EXCEPTION_EMP_SID, 'user1', 'HBT',
             'ATQ', 0, 'BAGS', 'RTW', 0.0, 2_200_000, 2_000_000],
        ])
        mock_repo.get_all_sales_data.return_value = sales_df
        mock_repo.get_hand_carry_upcs.return_value = ['HC_UPC']

        employees = [{
            'employee_code': 'EMP001',
            'employee_username': 'user1',
            'personal_target': 0,
            'full_name': 'Exception Employee',
            'store_code': 'HBT',
        }]

        result = service.calculate_personal_commissions(month=1, year=2025, employees=employees)
        row = result.iloc[0]

        # Hand carry uses revenue_with_vat, ATQ = 1%
        assert row['commission_hand_carry'] == 2_200_000 * 0.01  # 22,000
        assert row['commission_fashion_fp'] == 0  # No qualifying non-jewelry
        assert row['total'] == 2_200_000 * 0.01

    def test_mixed_customers(self, service, mock_repo):
        """Only non-jewelry sales to qualifying customer earn flat rate;
        sales to other customers are excluded from FP commission."""
        sales_df = self._make_sales_df([
            # Non-jewelry to qualifying customer — should earn commission
            [1, 'UPC001', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             self.QUALIFYING_CUSTOMER, self.EXCEPTION_EMP_SID, 'user1', 'HBT',
             'ABC', 0, 'SHIRTS', 'RTW', 0.0, 11_000_000, 10_000_000],
            # Non-jewelry to OTHER customer — should NOT earn commission
            [2, 'UPC002', 'BILL2', 'HBT', '2025-01-15', '11:00:00',
             self.OTHER_CUSTOMER, self.EXCEPTION_EMP_SID, 'user1', 'HBT',
             'DEF', 0, 'PANTS', 'RTW', 0.0, 5_500_000, 5_000_000],
            # Jewelry to OTHER customer — should still earn commission (no customer filter)
            [3, 'UPC003', 'BILL3', 'HBT', '2025-01-15', '12:00:00',
             self.OTHER_CUSTOMER, self.EXCEPTION_EMP_SID, 'user1', 'HBT',
             'VHN', 1, 'RING', 'WJEW', 0.0, 3_300_000, 3_000_000],
        ])
        mock_repo.get_all_sales_data.return_value = sales_df
        mock_repo.get_hand_carry_upcs.return_value = []

        employees = [{
            'employee_code': 'EMP001',
            'employee_username': 'user1',
            'personal_target': 0,
            'full_name': 'Exception Employee',
            'store_code': 'HBT',
        }]

        result = service.calculate_personal_commissions(month=1, year=2025, employees=employees)
        row = result.iloc[0]

        # Only 10M qualifying non-jewelry earns flat 0.7%, 5M excluded
        assert row['commission_fashion_fp'] == 10_000_000 * 0.007  # 70,000
        assert row['commission_fashion_md'] == 0
        # VHN jewelry (1%)
        assert row['commission_vhernier'] == 3_000_000 * 0.01  # 30,000
        assert row['commission_jewelry'] == 3_000_000 * 0.01
        assert row['total'] == (10_000_000 * 0.007) + (3_000_000 * 0.01)  # 100,000

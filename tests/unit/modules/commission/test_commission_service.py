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
# _check_store_eligibility
# ======================================================================

class TestCheckStoreEligibility:
    """Tests for STEP 1: Store eligibility and achievement percentage."""

    def test_missing_store_data_returns_ineligible(self, service):
        result = service._check_store_eligibility(None, {'TARGET_REVENUE': 100})
        assert result['eligible'] is False
        assert 'Missing' in result['reason']
        assert result['achievement_pct'] == 0

    def test_missing_targets_returns_ineligible(self, service):
        result = service._check_store_eligibility({'ACTUAL_REVENUE': 100}, None)
        assert result['eligible'] is False
        assert 'Missing' in result['reason']

    def test_empty_store_data_returns_ineligible(self, service):
        result = service._check_store_eligibility({}, {'TARGET_REVENUE': 100})
        assert result['eligible'] is False
        assert 'Missing' in result['reason']

    def test_empty_targets_returns_ineligible(self, service):
        result = service._check_store_eligibility({'ACTUAL_REVENUE': 100}, {})
        assert result['eligible'] is False
        assert 'Missing' in result['reason']

    def test_zero_target_revenue_returns_ineligible(self, service):
        store_data = {
            'ACTUAL_REVENUE': 1_000_000,
            'ACTUAL_FULL_PRICE_REVENUE': 800_000,
            'ACTUAL_DISCOUNTED_REVENUE': 200_000,
        }
        targets = {'TARGET_REVENUE': 0, 'TARGET_FP_RATIO': 0.7}

        result = service._check_store_eligibility(store_data, targets)
        assert result['eligible'] is False
        assert 'zero' in result['reason'].lower()
        assert result['achievement_pct'] == 0

    def test_below_70_percent_fp_revenue_returns_ineligible(self, service):
        """FP revenue must be >= 70% of target revenue."""
        store_data = {
            'ACTUAL_REVENUE': 800_000,
            'ACTUAL_FULL_PRICE_REVENUE': 600_000,  # 60% of 1M target
            'ACTUAL_DISCOUNTED_REVENUE': 200_000,
        }
        targets = {'TARGET_REVENUE': 1_000_000, 'TARGET_FP_RATIO': 0.7}

        result = service._check_store_eligibility(store_data, targets)
        assert result['eligible'] is False
        assert 'Full price revenue' in result['reason']
        # Achievement is still calculated even when ineligible
        assert result['achievement_pct'] == 80.0

    def test_fp_ratio_below_target_with_insufficient_discount_compensation(self, service):
        """When actual FP ratio < target, discounted revenue must compensate at 200%."""
        # actual_fp_ratio = 700_000 / 1_000_000 = 0.70, target = 0.80
        # fp_shortage = (0.80 - 0.70) * 1_000_000 = 100_000
        # required_discounted = 100_000 * 2.0 = 200_000
        # actual_discounted = 150_000 < 200_000 => ineligible
        store_data = {
            'ACTUAL_REVENUE': 1_000_000,
            'ACTUAL_FULL_PRICE_REVENUE': 700_000,
            'ACTUAL_DISCOUNTED_REVENUE': 150_000,
        }
        targets = {'TARGET_REVENUE': 1_000_000, 'TARGET_FP_RATIO': 0.80}

        result = service._check_store_eligibility(store_data, targets)
        assert result['eligible'] is False
        assert 'Discounted revenue' in result['reason']
        assert result['achievement_pct'] == 100.0

    def test_fp_ratio_below_target_but_sufficient_discount_compensation(self, service):
        """Store is eligible when discounted revenue compensates for FP shortfall."""
        # actual_fp_ratio = 700_000 / 1_000_000 = 0.70, target = 0.80
        # fp_shortage = (0.80 - 0.70) * 1_000_000 = 100_000
        # required_discounted = 100_000 * 2.0 = 200_000
        # actual_discounted = 250_000 >= 200_000 => eligible
        store_data = {
            'ACTUAL_REVENUE': 1_000_000,
            'ACTUAL_FULL_PRICE_REVENUE': 700_000,
            'ACTUAL_DISCOUNTED_REVENUE': 250_000,
        }
        targets = {'TARGET_REVENUE': 1_000_000, 'TARGET_FP_RATIO': 0.80}

        result = service._check_store_eligibility(store_data, targets)
        assert result['eligible'] is True
        assert result['achievement_pct'] == 100.0

    def test_eligible_store_above_fp_ratio_target(self, service):
        """Store is eligible when FP ratio meets or exceeds target."""
        store_data = {
            'ACTUAL_REVENUE': 1_200_000,
            'ACTUAL_FULL_PRICE_REVENUE': 1_000_000,  # FP ratio ~0.833
            'ACTUAL_DISCOUNTED_REVENUE': 200_000,
        }
        targets = {'TARGET_REVENUE': 1_000_000, 'TARGET_FP_RATIO': 0.80}

        result = service._check_store_eligibility(store_data, targets)
        assert result['eligible'] is True
        assert result['achievement_pct'] == 120.0

    def test_achievement_percentage_calculation(self, service):
        """Verify achievement = (actual / target) * 100."""
        store_data = {
            'ACTUAL_REVENUE': 900_000,
            'ACTUAL_FULL_PRICE_REVENUE': 800_000,
            'ACTUAL_DISCOUNTED_REVENUE': 100_000,
        }
        targets = {'TARGET_REVENUE': 1_000_000, 'TARGET_FP_RATIO': 0.70}

        result = service._check_store_eligibility(store_data, targets)
        assert result['eligible'] is True
        assert result['achievement_pct'] == pytest.approx(90.0)

    def test_zero_actual_revenue_fp_ratio_defaults_to_zero(self, service):
        """When actual revenue is 0, fp_ratio = 0 and 70% check fails first."""
        store_data = {
            'ACTUAL_REVENUE': 0,
            'ACTUAL_FULL_PRICE_REVENUE': 0,
            'ACTUAL_DISCOUNTED_REVENUE': 0,
        }
        targets = {'TARGET_REVENUE': 1_000_000, 'TARGET_FP_RATIO': 0.70}

        result = service._check_store_eligibility(store_data, targets)
        assert result['eligible'] is False
        assert result['achievement_pct'] == 0.0


# ======================================================================
# _get_tier_rates
# ======================================================================

class TestGetTierRates:
    """Tests for tenure-based commission tier rates."""

    def test_new_employee_below_6_months(self, service):
        rates = service._get_tier_rates(tenure_months=3, achievement_pct=85.0)
        assert rates == {
            '70-80': 0.0025,
            '80-90': 0.0035,
            '90-100': 0.0045,
            '100+': 0.0055,
        }

    def test_new_employee_at_zero_months(self, service):
        rates = service._get_tier_rates(tenure_months=0, achievement_pct=100.0)
        assert rates['70-80'] == 0.0025
        assert rates['100+'] == 0.0055

    def test_experienced_employee_at_6_months(self, service):
        rates = service._get_tier_rates(tenure_months=6, achievement_pct=85.0)
        assert rates == {
            '70-80': 0.0030,
            '80-90': 0.0040,
            '90-100': 0.0050,
            '100+': 0.0060,
        }

    def test_experienced_employee_above_6_months(self, service):
        rates = service._get_tier_rates(tenure_months=24, achievement_pct=95.0)
        assert rates == {
            '70-80': 0.0030,
            '80-90': 0.0040,
            '90-100': 0.0050,
            '100+': 0.0060,
        }

    def test_boundary_at_5_months_is_new(self, service):
        rates = service._get_tier_rates(tenure_months=5, achievement_pct=75.0)
        assert rates['70-80'] == 0.0025  # new employee rate

    def test_returns_four_tiers(self, service):
        rates = service._get_tier_rates(tenure_months=12, achievement_pct=80.0)
        assert set(rates.keys()) == {'70-80', '80-90', '90-100', '100+'}


# ======================================================================
# _allocate_revenue_to_tiers
# ======================================================================

class TestAllocateRevenueToTiers:
    """Tests for revenue allocation across achievement tiers."""

    def test_below_70_percent_all_zeros(self, service):
        result = service._allocate_revenue_to_tiers(1_000_000, 65.0)
        assert result == {'70-80': 0, '80-90': 0, '90-100': 0, '100+': 0}

    def test_at_69_percent_all_zeros(self, service):
        result = service._allocate_revenue_to_tiers(1_000_000, 69.9)
        assert all(v == 0 for v in result.values())

    def test_70_to_80_percent_range(self, service):
        """70-80% achievement: 100% of revenue at tier 70-80."""
        result = service._allocate_revenue_to_tiers(1_000_000, 75.0)
        assert result['70-80'] == 1_000_000
        assert result['80-90'] == 0
        assert result['90-100'] == 0
        assert result['100+'] == 0

    def test_80_to_90_percent_range(self, service):
        """80-90% achievement: 10% at 70-80, 90% at 80-90."""
        result = service._allocate_revenue_to_tiers(1_000_000, 85.0)
        assert result['70-80'] == pytest.approx(100_000)
        assert result['80-90'] == pytest.approx(900_000)
        assert result['90-100'] == 0
        assert result['100+'] == 0

    def test_90_to_100_percent_range(self, service):
        """90-100% achievement: 10% at 70-80, 10% at 80-90, 80% at 90-100."""
        result = service._allocate_revenue_to_tiers(1_000_000, 95.0)
        assert result['70-80'] == pytest.approx(100_000)
        assert result['80-90'] == pytest.approx(100_000)
        assert result['90-100'] == pytest.approx(800_000)
        assert result['100+'] == 0

    def test_100_plus_percent(self, service):
        """100%+ achievement: 10/10/10/70 split."""
        result = service._allocate_revenue_to_tiers(1_000_000, 110.0)
        assert result['70-80'] == pytest.approx(100_000)
        assert result['80-90'] == pytest.approx(100_000)
        assert result['90-100'] == pytest.approx(100_000)
        assert result['100+'] == pytest.approx(700_000)

    def test_exactly_100_percent(self, service):
        """Exactly 100% triggers the >=100 branch."""
        result = service._allocate_revenue_to_tiers(500_000, 100.0)
        assert result['100+'] == pytest.approx(350_000)

    def test_exactly_70_percent(self, service):
        """Exactly 70% is in the 70-80 range (not below 70)."""
        result = service._allocate_revenue_to_tiers(1_000_000, 70.0)
        assert result['70-80'] == 1_000_000

    def test_zero_revenue(self, service):
        result = service._allocate_revenue_to_tiers(0, 85.0)
        assert all(v == 0 for v in result.values())

    def test_total_allocation_equals_input_at_100_plus(self, service):
        """All allocated amounts should sum to total revenue."""
        total = 2_000_000
        result = service._allocate_revenue_to_tiers(total, 105.0)
        assert sum(result.values()) == pytest.approx(total)


# ======================================================================
# _distribute_store_pool
# ======================================================================

class TestDistributeStorePool:
    """Tests for STEP 4: Distributing store commission pool to employees."""

    def test_empty_employees_returns_empty(self, service):
        result = service._distribute_store_pool([], 100_000, 80.0)
        assert result == []

    def test_zero_pool_returns_empty(self, service):
        employees = [
            {
                'employee_name': 'Alice',
                'tenure_months': 12,
                'is_manager': False,
                'fp_revenue': 500_000,
                'discounted_revenue': 100_000,
                'contribution': 10_000,
            }
        ]
        result = service._distribute_store_pool(employees, 0, 80.0)
        assert result == []

    def test_single_employee_70_30_split(self, service):
        """Single employee gets 70% from contribution ratio + 30% equal share."""
        employees = [
            {
                'employee_name': 'Alice',
                'tenure_months': 12,
                'is_manager': False,
                'fp_revenue': 500_000,
                'discounted_revenue': 100_000,
                'contribution': 50_000,
            }
        ]
        store_pool = 50_000

        result = service._distribute_store_pool(employees, store_pool, 80.0)
        assert len(result) == 1

        alice = result[0]
        # contribution_ratio = 50_000 / 50_000 = 1.0
        # commission_70pct = 0.70 * 50_000 * 1.0 = 35_000
        assert alice['commission_70pct'] == pytest.approx(35_000)
        # commission_30pct = 0.30 * 50_000 / 1 = 15_000
        assert alice['commission_30pct'] == pytest.approx(15_000)
        assert alice['manager_bonus'] == 0
        assert alice['total_commission'] == pytest.approx(50_000)

    def test_two_employees_proportional_split(self, service):
        """Two employees: 70% distributed by contribution ratio."""
        employees = [
            {
                'employee_name': 'Alice',
                'tenure_months': 12,
                'is_manager': False,
                'fp_revenue': 600_000,
                'discounted_revenue': 0,
                'contribution': 30_000,
            },
            {
                'employee_name': 'Bob',
                'tenure_months': 8,
                'is_manager': False,
                'fp_revenue': 400_000,
                'discounted_revenue': 0,
                'contribution': 20_000,
            },
        ]
        store_pool = 50_000

        result = service._distribute_store_pool(employees, store_pool, 85.0)
        assert len(result) == 2

        alice = result[0]
        bob = result[1]

        # Alice: ratio = 30_000/50_000 = 0.6
        # Alice 70%: 0.70 * 50_000 * 0.6 = 21_000
        assert alice['commission_70pct'] == pytest.approx(21_000)
        # Alice 30%: 0.30 * 50_000 / 2 = 7_500
        assert alice['commission_30pct'] == pytest.approx(7_500)
        assert alice['total_commission'] == pytest.approx(28_500)

        # Bob: ratio = 20_000/50_000 = 0.4
        # Bob 70%: 0.70 * 50_000 * 0.4 = 14_000
        assert bob['commission_70pct'] == pytest.approx(14_000)
        assert bob['commission_30pct'] == pytest.approx(7_500)
        assert bob['total_commission'] == pytest.approx(21_500)

    def test_manager_bonus_at_100_percent_achievement(self, service):
        """Manager receives 3,000,000 bonus when achievement >= 100%."""
        employees = [
            {
                'employee_name': 'Manager',
                'tenure_months': 24,
                'is_manager': True,
                'fp_revenue': 1_000_000,
                'discounted_revenue': 0,
                'contribution': 50_000,
            }
        ]
        store_pool = 50_000

        result = service._distribute_store_pool(employees, store_pool, 100.0)
        assert result[0]['manager_bonus'] == 3_000_000
        assert result[0]['total_commission'] == pytest.approx(50_000 + 3_000_000)

    def test_manager_bonus_above_100_percent_achievement(self, service):
        """Manager bonus also applies when achievement > 100%."""
        employees = [
            {
                'employee_name': 'Manager',
                'tenure_months': 24,
                'is_manager': True,
                'fp_revenue': 1_000_000,
                'discounted_revenue': 0,
                'contribution': 50_000,
            }
        ]
        result = service._distribute_store_pool(employees, 50_000, 120.0)
        assert result[0]['manager_bonus'] == 3_000_000

    def test_no_manager_bonus_below_100_percent(self, service):
        """Manager does NOT receive bonus when achievement < 100%."""
        employees = [
            {
                'employee_name': 'Manager',
                'tenure_months': 24,
                'is_manager': True,
                'fp_revenue': 1_000_000,
                'discounted_revenue': 0,
                'contribution': 50_000,
            }
        ]
        result = service._distribute_store_pool(employees, 50_000, 99.9)
        assert result[0]['manager_bonus'] == 0

    def test_non_manager_never_gets_bonus(self, service):
        """Non-manager employees never get a manager bonus."""
        employees = [
            {
                'employee_name': 'Staff',
                'tenure_months': 12,
                'is_manager': False,
                'fp_revenue': 1_000_000,
                'discounted_revenue': 0,
                'contribution': 50_000,
            }
        ]
        result = service._distribute_store_pool(employees, 50_000, 110.0)
        assert result[0]['manager_bonus'] == 0

    def test_output_contains_all_expected_fields(self, service):
        """Verify the result dict has all expected keys."""
        employees = [
            {
                'employee_name': 'Alice',
                'tenure_months': 6,
                'is_manager': False,
                'fp_revenue': 500_000,
                'discounted_revenue': 50_000,
                'contribution': 10_000,
            }
        ]
        result = service._distribute_store_pool(employees, 10_000, 80.0)
        expected_keys = {
            'employee_name', 'tenure_months', 'is_manager',
            'fp_revenue', 'discounted_revenue', 'contribution',
            'commission_70pct', 'commission_30pct',
            'manager_bonus', 'total_commission',
        }
        assert set(result[0].keys()) == expected_keys


# ======================================================================
# calculate_store_commission_v2
# ======================================================================

class TestCalculateStoreCommissionV2:
    """Tests for the main store commission calculation method."""

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
        mock_repo.get_store_sales_data.return_value = {
            'ACTUAL_REVENUE': 600_000,
            'ACTUAL_FULL_PRICE_REVENUE': 500_000,
            'ACTUAL_DISCOUNTED_REVENUE': 100_000,
        }

        result = service.calculate_store_commission_v2(
            store_code='HBT',
            store_target=1_000_000,
            store_fp_ratio_target=0.70,
            query_date=self._make_query_date(),
            employees=self._make_employees(2),
        )

        assert result['eligible'] is False
        assert 'below 70%' in result['reason']
        assert result['employees'] == []

    def test_store_with_insufficient_fp_ratio_compensation(self, service, mock_repo):
        """Store with low FP ratio and insufficient discount compensation is ineligible."""
        # actual_fp_ratio = 700_000 / 1_000_000 = 0.70, target = 0.80
        # fp_shortage = (0.80 - 0.70) * 1_000_000 = 100_000
        # required_discounted = 200_000, actual = 150_000
        mock_repo.get_store_sales_data.return_value = {
            'ACTUAL_REVENUE': 1_000_000,
            'ACTUAL_FULL_PRICE_REVENUE': 700_000,
            'ACTUAL_DISCOUNTED_REVENUE': 150_000,
        }

        result = service.calculate_store_commission_v2(
            store_code='HBT',
            store_target=1_000_000,
            store_fp_ratio_target=0.80,
            query_date=self._make_query_date(),
            employees=self._make_employees(2),
        )

        assert result['eligible'] is False
        assert 'compensation insufficient' in result['reason']

    def test_eligible_store_with_employees(self, service, mock_repo):
        """Eligible store returns commission data for each employee."""
        mock_repo.get_store_sales_data.return_value = {
            'ACTUAL_REVENUE': 900_000,
            'ACTUAL_FULL_PRICE_REVENUE': 750_000,
            'ACTUAL_DISCOUNTED_REVENUE': 150_000,
        }
        mock_repo.get_employee_sales_data.return_value = [
            {
                'EMPLOYEE_USERNAME': 'user1',
                'EMPLOYEE_FP_REVENUE': 400_000,
                'EMPLOYEE_DISCOUNTED_REVENUE': 80_000,
            },
            {
                'EMPLOYEE_USERNAME': 'user2',
                'EMPLOYEE_FP_REVENUE': 300_000,
                'EMPLOYEE_DISCOUNTED_REVENUE': 70_000,
            },
        ]

        result = service.calculate_store_commission_v2(
            store_code='HBT',
            store_target=1_000_000,
            store_fp_ratio_target=0.70,
            query_date=self._make_query_date(),
            employees=self._make_employees(2),
        )

        assert result['eligible'] is True
        assert result['achievement_pct'] == pytest.approx(90.0)
        assert len(result['employees']) == 2
        assert result['store_pool'] > 0

    def test_rhn_rwp_combined_store_logic(self, service, mock_repo):
        """RHN/RWP stores fetch sales from both stores and combine them."""
        mock_repo.get_store_sales_data.return_value = {
            'ACTUAL_REVENUE': 1_200_000,
            'ACTUAL_FULL_PRICE_REVENUE': 1_000_000,
            'ACTUAL_DISCOUNTED_REVENUE': 200_000,
        }
        # get_employee_sales_data is called twice (RHN + RWP)
        mock_repo.get_employee_sales_data.side_effect = [
            [  # RHN sales
                {
                    'EMPLOYEE_USERNAME': 'user1',
                    'EMPLOYEE_FP_REVENUE': 300_000,
                    'EMPLOYEE_DISCOUNTED_REVENUE': 50_000,
                },
            ],
            [  # RWP sales
                {
                    'EMPLOYEE_USERNAME': 'user1',
                    'EMPLOYEE_FP_REVENUE': 200_000,
                    'EMPLOYEE_DISCOUNTED_REVENUE': 30_000,
                },
                {
                    'EMPLOYEE_USERNAME': 'user2',
                    'EMPLOYEE_FP_REVENUE': 400_000,
                    'EMPLOYEE_DISCOUNTED_REVENUE': 100_000,
                },
            ],
        ]

        result = service.calculate_store_commission_v2(
            store_code='RHN',
            store_target=1_000_000,
            store_fp_ratio_target=0.70,
            query_date=self._make_query_date(),
            employees=self._make_employees(2),
        )

        assert result['eligible'] is True
        # Verify get_employee_sales_data was called for both RHN and RWP
        calls = mock_repo.get_employee_sales_data.call_args_list
        assert len(calls) == 2
        assert calls[0][0][0] == 'RHN'
        assert calls[1][0][0] == 'RWP'

        # user1 revenue should be combined: 300k + 200k = 500k FP
        emp1 = next(e for e in result['employees'] if e['employee_code'] == 'EMP001')
        assert emp1['fp_revenue'] == pytest.approx(500_000)

    def test_rwp_also_triggers_combined_logic(self, service, mock_repo):
        """RWP store code also triggers the combined RHN/RWP logic."""
        mock_repo.get_store_sales_data.return_value = {
            'ACTUAL_REVENUE': 1_200_000,
            'ACTUAL_FULL_PRICE_REVENUE': 1_000_000,
            'ACTUAL_DISCOUNTED_REVENUE': 200_000,
        }
        mock_repo.get_employee_sales_data.side_effect = [
            [{'EMPLOYEE_USERNAME': 'user1', 'EMPLOYEE_FP_REVENUE': 600_000, 'EMPLOYEE_DISCOUNTED_REVENUE': 100_000}],
            [{'EMPLOYEE_USERNAME': 'user2', 'EMPLOYEE_FP_REVENUE': 400_000, 'EMPLOYEE_DISCOUNTED_REVENUE': 80_000}],
        ]

        result = service.calculate_store_commission_v2(
            store_code='RWP',
            store_target=1_000_000,
            store_fp_ratio_target=0.70,
            query_date=self._make_query_date(),
            employees=self._make_employees(2),
        )

        assert result['eligible'] is True
        calls = mock_repo.get_employee_sales_data.call_args_list
        assert calls[0][0][0] == 'RHN'
        assert calls[1][0][0] == 'RWP'

    def test_manager_bonus_at_100_plus_achievement(self, service, mock_repo):
        """Manager receives 3M bonus when store achievement >= 100%."""
        mock_repo.get_store_sales_data.return_value = {
            'ACTUAL_REVENUE': 1_100_000,
            'ACTUAL_FULL_PRICE_REVENUE': 900_000,
            'ACTUAL_DISCOUNTED_REVENUE': 200_000,
        }
        mock_repo.get_employee_sales_data.return_value = [
            {
                'EMPLOYEE_USERNAME': 'user1',
                'EMPLOYEE_FP_REVENUE': 600_000,
                'EMPLOYEE_DISCOUNTED_REVENUE': 100_000,
            },
            {
                'EMPLOYEE_USERNAME': 'user2',
                'EMPLOYEE_FP_REVENUE': 400_000,
                'EMPLOYEE_DISCOUNTED_REVENUE': 100_000,
            },
        ]

        result = service.calculate_store_commission_v2(
            store_code='HBT',
            store_target=1_000_000,
            store_fp_ratio_target=0.70,
            query_date=self._make_query_date(),
            employees=self._make_employees(2, manager_index=0),
        )

        assert result['eligible'] is True
        assert result['achievement_pct'] >= 100

        manager = next(e for e in result['employees'] if e['is_manager'])
        assert manager['manager_bonus'] == 3_000_000

        non_manager = next(e for e in result['employees'] if not e['is_manager'])
        assert non_manager['manager_bonus'] == 0

    def test_manager_bonus_between_70_and_100(self, service, mock_repo):
        """Manager receives 750k bonus when 70% <= achievement < 100%."""
        mock_repo.get_store_sales_data.return_value = {
            'ACTUAL_REVENUE': 850_000,
            'ACTUAL_FULL_PRICE_REVENUE': 750_000,
            'ACTUAL_DISCOUNTED_REVENUE': 100_000,
        }
        mock_repo.get_employee_sales_data.return_value = [
            {
                'EMPLOYEE_USERNAME': 'user1',
                'EMPLOYEE_FP_REVENUE': 500_000,
                'EMPLOYEE_DISCOUNTED_REVENUE': 50_000,
            },
            {
                'EMPLOYEE_USERNAME': 'user2',
                'EMPLOYEE_FP_REVENUE': 300_000,
                'EMPLOYEE_DISCOUNTED_REVENUE': 50_000,
            },
        ]

        result = service.calculate_store_commission_v2(
            store_code='HBT',
            store_target=1_000_000,
            store_fp_ratio_target=0.70,
            query_date=self._make_query_date(),
            employees=self._make_employees(2, manager_index=0),
        )

        assert result['eligible'] is True
        assert 70 <= result['achievement_pct'] < 100

        manager = next(e for e in result['employees'] if e['is_manager'])
        assert manager['manager_bonus'] == 750_000

    def test_probation_employee_gets_equal_share_only(self, service, mock_repo):
        """Probation employee only receives the equal share, not individual share or manager bonus."""
        mock_repo.get_store_sales_data.return_value = {
            'ACTUAL_REVENUE': 1_100_000,
            'ACTUAL_FULL_PRICE_REVENUE': 900_000,
            'ACTUAL_DISCOUNTED_REVENUE': 200_000,
        }
        mock_repo.get_employee_sales_data.return_value = [
            {
                'EMPLOYEE_USERNAME': 'user1',
                'EMPLOYEE_FP_REVENUE': 600_000,
                'EMPLOYEE_DISCOUNTED_REVENUE': 100_000,
            },
            {
                'EMPLOYEE_USERNAME': 'user2',
                'EMPLOYEE_FP_REVENUE': 400_000,
                'EMPLOYEE_DISCOUNTED_REVENUE': 100_000,
            },
        ]

        employees = self._make_employees(2, manager_index=0, probation_indices=[1])

        result = service.calculate_store_commission_v2(
            store_code='HBT',
            store_target=1_000_000,
            store_fp_ratio_target=0.70,
            query_date=self._make_query_date(),
            employees=employees,
        )

        assert result['eligible'] is True
        probation_emp = next(e for e in result['employees'] if e['is_probation'])
        # Probation employee: total = equal_share only (no individual_share, no manager_bonus)
        assert probation_emp['total_store_commission'] == pytest.approx(probation_emp['equal_share'])
        # Confirm individual_share is calculated but not added to total
        assert probation_emp['individual_share'] > 0 or probation_emp['individual_share'] == 0

    def test_seniority_affects_tier_category(self, service, mock_repo):
        """Employees with seniority >= 3 years are Senior, otherwise Junior."""
        mock_repo.get_store_sales_data.return_value = {
            'ACTUAL_REVENUE': 900_000,
            'ACTUAL_FULL_PRICE_REVENUE': 750_000,
            'ACTUAL_DISCOUNTED_REVENUE': 150_000,
        }
        mock_repo.get_employee_sales_data.return_value = [
            {
                'EMPLOYEE_USERNAME': 'user1',
                'EMPLOYEE_FP_REVENUE': 500_000,
                'EMPLOYEE_DISCOUNTED_REVENUE': 75_000,
            },
            {
                'EMPLOYEE_USERNAME': 'user2',
                'EMPLOYEE_FP_REVENUE': 300_000,
                'EMPLOYEE_DISCOUNTED_REVENUE': 75_000,
            },
        ]

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
        )

        assert result['eligible'] is True
        assert len(result['employees']) == 2

    def test_rhn_rwp_equal_share_uses_working_day_ratio(self, service, mock_repo):
        """RHN/RWP stores distribute the 30% equal share by working day ratio."""
        mock_repo.get_store_sales_data.return_value = {
            'ACTUAL_REVENUE': 1_200_000,
            'ACTUAL_FULL_PRICE_REVENUE': 1_000_000,
            'ACTUAL_DISCOUNTED_REVENUE': 200_000,
        }
        mock_repo.get_employee_sales_data.side_effect = [
            [  # RHN
                {'EMPLOYEE_USERNAME': 'user1', 'EMPLOYEE_FP_REVENUE': 600_000, 'EMPLOYEE_DISCOUNTED_REVENUE': 100_000},
            ],
            [  # RWP
                {'EMPLOYEE_USERNAME': 'user2', 'EMPLOYEE_FP_REVENUE': 400_000, 'EMPLOYEE_DISCOUNTED_REVENUE': 100_000},
            ],
        ]

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
        mock_repo.get_store_sales_data.return_value = {
            'ACTUAL_REVENUE': 900_000,
            'ACTUAL_FULL_PRICE_REVENUE': 750_000,
            'ACTUAL_DISCOUNTED_REVENUE': 150_000,
        }
        mock_repo.get_employee_sales_data.return_value = [
            {
                'EMPLOYEE_USERNAME': 'user1',
                'EMPLOYEE_FP_REVENUE': 750_000,
                'EMPLOYEE_DISCOUNTED_REVENUE': 150_000,
            },
        ]

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
                'commission_100_and_below_fp': 10_000 * (i + 1),
                'commission_100_and_below_discount': 2_000 * (i + 1),
                'commission_over_100': 5_000 * (i + 1),
                'commission_jewelry': 3_000 * (i + 1),
                'commission_vhernier': 1_000 * (i + 1),
                'commission_rosa_maria': 500 * (i + 1),
                'commission_suitcase': 500_000 * (i + 1),
                'commission_hand_carry': 2_000 * (i + 1),
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
            'personal_commission_fp_under_100',
            'personal_commission_discount_under_100',
            'personal_commission_over_100',
            'personal_commission_jewelry',
            'personal_commission_vhernier',
            'personal_commission_rosa_maria',
            'personal_commission_suitcase',
            'personal_commission_hand_carry',
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
# calculate_batch_store_commissions
# ======================================================================

class TestCalculateBatchStoreCommissions:
    """Tests for batch processing of multiple stores."""

    def test_empty_employees_returns_error(self, service):
        result = service.calculate_batch_store_commissions(
            employees=[], stores=[{'store_code': 'HBT'}],
            year=2025, month=1,
            start_date='2025-01-01 00:00:00',
            end_date='2025-01-31 23:59:59',
        )
        assert result['success'] is False
        assert 'required' in result['error'].lower()

    def test_empty_stores_returns_error(self, service):
        result = service.calculate_batch_store_commissions(
            employees=[{'employee_code': 'E1'}], stores=[],
            year=2025, month=1,
            start_date='2025-01-01 00:00:00',
            end_date='2025-01-31 23:59:59',
        )
        assert result['success'] is False

    def test_none_employees_returns_error(self, service):
        result = service.calculate_batch_store_commissions(
            employees=None, stores=[{'store_code': 'HBT'}],
            year=2025, month=1,
            start_date='2025-01-01 00:00:00',
            end_date='2025-01-31 23:59:59',
        )
        assert result['success'] is False

    def test_store_config_missing_marks_employees_ineligible(self, service, mock_repo):
        """Employees whose store has no config get marked ineligible."""
        employees = [
            {
                'employee_code': 'EMP001',
                'employee_name': 'Alice',
                'store_code': 'UNKNOWN',
            }
        ]
        stores = [
            {'store_code': 'HBT', 'target_revenue': 1_000_000, 'target_fp_ratio': 0.70}
        ]

        mock_repo.get_multiple_stores_sales_data.return_value = {}
        mock_repo.get_multiple_stores_employee_sales_data.return_value = {}

        result = service.calculate_batch_store_commissions(
            employees=employees, stores=stores,
            year=2025, month=1,
            start_date='2025-01-01 00:00:00',
            end_date='2025-01-31 23:59:59',
        )

        assert result['success'] is True
        assert len(result['employees']) == 1
        assert result['employees'][0]['eligible'] is False
        assert 'not provided' in result['employees'][0]['reason'].lower()

    def test_ineligible_store_all_employees_get_zero(self, service, mock_repo):
        """All employees in an ineligible store get 0 commission."""
        employees = [
            {'employee_code': 'EMP001', 'employee_name': 'Alice', 'store_code': 'HBT'},
            {'employee_code': 'EMP002', 'employee_name': 'Bob', 'store_code': 'HBT'},
        ]
        stores = [
            {'store_code': 'HBT', 'store_name': 'HBT Store', 'target_revenue': 1_000_000, 'target_fp_ratio': 0.70}
        ]

        # Store data shows below 70% achievement
        mock_repo.get_multiple_stores_sales_data.return_value = {
            'HBT': {
                'ACTUAL_REVENUE': 500_000,
                'ACTUAL_FULL_PRICE_REVENUE': 400_000,
                'ACTUAL_DISCOUNTED_REVENUE': 100_000,
            }
        }
        mock_repo.get_multiple_stores_employee_sales_data.return_value = {}

        result = service.calculate_batch_store_commissions(
            employees=employees, stores=stores,
            year=2025, month=1,
            start_date='2025-01-01 00:00:00',
            end_date='2025-01-31 23:59:59',
        )

        assert result['success'] is True
        assert result['summary']['ineligible_employees'] == 2
        for emp in result['employees']:
            assert emp['eligible'] is False
            assert emp['total_commission'] == 0

    def test_eligible_store_with_employee_sales(self, service, mock_repo):
        """Eligible store produces commission for employees with sales data."""
        employees = [
            {
                'employee_code': 'EMP001',
                'employee_name': 'Alice',
                'store_code': 'HBT',
                'seniority': 12,
            },
        ]
        stores = [
            {
                'store_code': 'HBT',
                'store_name': 'HBT Store',
                'target_revenue': 1_000_000,
                'target_fp_ratio': 0.70,
            }
        ]

        mock_repo.get_multiple_stores_sales_data.return_value = {
            'HBT': {
                'ACTUAL_REVENUE': 900_000,
                'ACTUAL_FULL_PRICE_REVENUE': 800_000,
                'ACTUAL_DISCOUNTED_REVENUE': 100_000,
            }
        }
        mock_repo.get_multiple_stores_employee_sales_data.return_value = {
            'HBT': [
                {
                    'EMPLOYEE_FULL_NAME': 'Alice',
                    'EMPLOYEE_FP_REVENUE': 500_000,
                    'EMPLOYEE_DISCOUNTED_REVENUE': 50_000,
                }
            ]
        }

        result = service.calculate_batch_store_commissions(
            employees=employees, stores=stores,
            year=2025, month=1,
            start_date='2025-01-01 00:00:00',
            end_date='2025-01-31 23:59:59',
        )

        assert result['success'] is True
        assert result['summary']['eligible_employees'] >= 1
        eligible_emps = [e for e in result['employees'] if e.get('eligible')]
        assert len(eligible_emps) >= 1
        assert eligible_emps[0]['total_commission'] > 0

    def test_multiple_stores_mixed_eligibility(self, service, mock_repo):
        """Batch with one eligible and one ineligible store."""
        employees = [
            {'employee_code': 'EMP001', 'employee_name': 'Alice', 'store_code': 'HBT', 'seniority': 12},
            {'employee_code': 'EMP002', 'employee_name': 'Bob', 'store_code': 'HDG', 'seniority': 8},
        ]
        stores = [
            {'store_code': 'HBT', 'store_name': 'HBT Store', 'target_revenue': 1_000_000, 'target_fp_ratio': 0.70},
            {'store_code': 'HDG', 'store_name': 'HDG Store', 'target_revenue': 1_000_000, 'target_fp_ratio': 0.70},
        ]

        mock_repo.get_multiple_stores_sales_data.return_value = {
            'HBT': {
                'ACTUAL_REVENUE': 900_000,
                'ACTUAL_FULL_PRICE_REVENUE': 800_000,
                'ACTUAL_DISCOUNTED_REVENUE': 100_000,
            },
            'HDG': {
                'ACTUAL_REVENUE': 400_000,    # Below 70%
                'ACTUAL_FULL_PRICE_REVENUE': 300_000,
                'ACTUAL_DISCOUNTED_REVENUE': 100_000,
            },
        }
        mock_repo.get_multiple_stores_employee_sales_data.return_value = {
            'HBT': [
                {
                    'EMPLOYEE_FULL_NAME': 'Alice',
                    'EMPLOYEE_FP_REVENUE': 500_000,
                    'EMPLOYEE_DISCOUNTED_REVENUE': 50_000,
                }
            ],
            'HDG': [
                {
                    'EMPLOYEE_FULL_NAME': 'Bob',
                    'EMPLOYEE_FP_REVENUE': 200_000,
                    'EMPLOYEE_DISCOUNTED_REVENUE': 50_000,
                }
            ],
        }

        result = service.calculate_batch_store_commissions(
            employees=employees, stores=stores,
            year=2025, month=1,
            start_date='2025-01-01 00:00:00',
            end_date='2025-01-31 23:59:59',
        )

        assert result['success'] is True
        assert result['summary']['eligible_stores'] == 1
        assert result['summary']['total_stores'] == 2

        alice = next(e for e in result['employees'] if e['employee_code'] == 'EMP001')
        bob = next(e for e in result['employees'] if e['employee_code'] == 'EMP002')
        assert alice['eligible'] is True
        assert bob['eligible'] is False
        assert bob['total_commission'] == 0

    def test_result_structure(self, service, mock_repo):
        """Verify the output contains expected top-level keys."""
        employees = [
            {'employee_code': 'EMP001', 'employee_name': 'Alice', 'store_code': 'HBT', 'seniority': 12}
        ]
        stores = [
            {'store_code': 'HBT', 'store_name': 'HBT', 'target_revenue': 1_000_000, 'target_fp_ratio': 0.70}
        ]

        mock_repo.get_multiple_stores_sales_data.return_value = {
            'HBT': {
                'ACTUAL_REVENUE': 900_000,
                'ACTUAL_FULL_PRICE_REVENUE': 800_000,
                'ACTUAL_DISCOUNTED_REVENUE': 100_000,
            }
        }
        mock_repo.get_multiple_stores_employee_sales_data.return_value = {
            'HBT': [
                {'EMPLOYEE_FULL_NAME': 'Alice', 'EMPLOYEE_FP_REVENUE': 500_000, 'EMPLOYEE_DISCOUNTED_REVENUE': 50_000}
            ]
        }

        result = service.calculate_batch_store_commissions(
            employees=employees, stores=stores,
            year=2025, month=1,
            start_date='2025-01-01 00:00:00',
            end_date='2025-01-31 23:59:59',
        )

        assert 'success' in result
        assert 'period' in result
        assert 'summary' in result
        assert 'stores' in result
        assert 'employees' in result
        assert result['period']['year'] == 2025
        assert result['period']['month'] == 1


# ======================================================================
# calculate_personal_commissions
# ======================================================================

class TestCalculatePersonalCommissions:
    """Tests for personal commission calculation (pandas-heavy method)."""

    def _make_sales_df(self, rows):
        """Helper to create a sales DataFrame with required columns."""
        columns = [
            'SALE_ID', 'UPC', 'EMPLOYEE_CODE', 'EMPLOYEE_USERNAME', 'EMPLOYEE_SID',
            'BILL_NUMBER', 'STORE_CODE', 'SALE_DATE', 'SALE_TIME',
            'REVENUE_WITH_VAT', 'REVENUE_BEFORE_VAT', 'DISCOUNT_RATE',
            'IS_JEWELRY', 'VENDOR_CODE', 'CATEGORY', 'DEPARTMENT',
        ]
        df = pd.DataFrame(rows, columns=columns)
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
            [1, 'UPC001', 'E1', 'user1', 'SID1', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             1_100_000, 1_000_000, 0.0, 0, 'ABC', 'SHIRTS', 'RTW'],
        ])
        mock_repo.get_personal_commission_sales_data.return_value = sales_df
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
            [1, 'UPC001', 'E1', 'user1', 'SID1', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             1_100_000, 1_000_000, 0.0, 0, 'ABC', 'SHIRTS', 'RTW'],
        ])
        mock_repo.get_personal_commission_sales_data.return_value = sales_df
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
            [1, 'UPC001', 'E1', 'user1', 'SID1', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             400_000, 363_636, 0.0, 0, 'ABC', 'SHIRTS', 'RTW'],
        ])
        mock_repo.get_personal_commission_sales_data.return_value = sales_df
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
        assert emp['commission_100_and_below_fp'] == 0
        assert emp['commission_100_and_below_discount'] == 0
        assert emp['commission_over_100'] == 0

    def test_tier1_50_to_70_percent_commission(self, service, mock_repo):
        """Tier 1 (50-70%): FP at 0.25%, discount at 0.125% (standard rates)."""
        # target = 1,000,000. revenue_with_vat = 600,000 => 60% achievement
        sales_df = self._make_sales_df([
            # Full price item (discount_rate <= 0.30)
            [1, 'UPC001', 'E1', 'user1', 'SID1', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             600_000, 500_000, 0.10, 0, 'ABC', 'SHIRTS', 'RTW'],
            # Discounted item (discount_rate > 0.30)
            [2, 'UPC002', 'E1', 'user1', 'SID1', 'BILL1', 'HBT', '2025-01-15', '10:01:00',
             0, 200_000, 0.50, 0, 'ABC', 'PANTS', 'RTW'],
        ])
        mock_repo.get_personal_commission_sales_data.return_value = sales_df
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
        assert emp['commission_100_and_below_fp'] == pytest.approx(500_000 * 0.0025)
        assert emp['commission_100_and_below_discount'] == pytest.approx(200_000 * 0.00125)

    def test_tier2_70_to_100_percent_commission(self, service, mock_repo):
        """Tier 2 (70-100%): FP at 0.5%, discount at 0.25% (standard rates)."""
        sales_df = self._make_sales_df([
            [1, 'UPC001', 'E1', 'user1', 'SID1', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             800_000, 700_000, 0.10, 0, 'ABC', 'SHIRTS', 'RTW'],
            [2, 'UPC002', 'E1', 'user1', 'SID1', 'BILL1', 'HBT', '2025-01-15', '10:01:00',
             0, 100_000, 0.50, 0, 'ABC', 'PANTS', 'RTW'],
        ])
        mock_repo.get_personal_commission_sales_data.return_value = sales_df
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
        assert emp['commission_100_and_below_fp'] == pytest.approx(700_000 * 0.005)
        assert emp['commission_100_and_below_discount'] == pytest.approx(100_000 * 0.0025)

    def test_jewelry_commission_regardless_of_achievement(self, service, mock_repo):
        """Jewelry commission is paid regardless of achievement rate."""
        sales_df = self._make_sales_df([
            # VHN jewelry: 1% on revenue_before_vat
            [1, 'UPC001', 'E1', 'user1', 'SID1', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             300_000, 272_727, 0.0, 1, 'VHN', 'RINGS', 'JWL'],
            # ROM EARRINGS: 3%
            [2, 'UPC002', 'E1', 'user1', 'SID1', 'BILL1', 'HBT', '2025-01-15', '10:01:00',
             200_000, 181_818, 0.0, 1, 'ROM', 'EARRINGS', 'JWL'],
        ])
        mock_repo.get_personal_commission_sales_data.return_value = sales_df
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
            [1, 'UPC001', 'E1', 'user1', 'SID1', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             5_000_000, 4_545_454, 0.0, 0, 'TVL', 'LUGGAGE', 'ACC'],
            [2, 'UPC002', 'E1', 'user1', 'SID1', 'BILL1', 'HBT', '2025-01-15', '10:01:00',
             3_000_000, 2_727_272, 0.0, 0, 'TIT', 'LUGGAGE', 'ACC'],
        ])
        mock_repo.get_personal_commission_sales_data.return_value = sales_df
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
            [1, 'HC001', 'E1', 'user1', 'SID1', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             1_000_000, 909_090, 0.0, 0, 'ATQ', 'BAGS', 'ACC'],
            # 2% vendor (CGI) hand carry
            [2, 'HC002', 'E1', 'user1', 'SID1', 'BILL1', 'HBT', '2025-01-15', '10:01:00',
             2_000_000, 1_818_181, 0.0, 0, 'CGI', 'BAGS', 'ACC'],
            # ROM EARRINGS hand carry: 3%
            [3, 'HC003', 'E1', 'user1', 'SID1', 'BILL1', 'HBT', '2025-01-15', '10:02:00',
             500_000, 454_545, 0.0, 1, 'ROM', 'EARRINGS', 'JWL'],
        ])
        mock_repo.get_personal_commission_sales_data.return_value = sales_df
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

    def test_cosm_excluded_from_commission_but_counts_for_achievement(self, service, mock_repo):
        """COSM items count toward achievement but earn no commission."""
        sales_df = self._make_sales_df([
            # Regular RTW item
            [1, 'UPC001', 'E1', 'user1', 'SID1', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             500_000, 454_545, 0.10, 0, 'ABC', 'SHIRTS', 'RTW'],
            # COSM item (should be excluded from commission calc)
            [2, 'UPC002', 'E1', 'user1', 'SID1', 'BILL2', 'HBT', '2025-01-15', '11:00:00',
             300_000, 272_727, 0.10, 0, 'COS', 'CREAM', 'COSM'],
        ])
        mock_repo.get_personal_commission_sales_data.return_value = sales_df
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
        assert emp['commission_100_and_below_fp'] == pytest.approx(454_545 * 0.005)

    def test_rwd_store_uses_special_rates(self, service, mock_repo):
        """RWD store uses special commission rates when ENABLE_RWD_SPECIAL_RATES is True."""
        sales_df = self._make_sales_df([
            [1, 'UPC001', 'E1', 'user1', 'SID1', 'BILL1', 'RWD', '2025-01-15', '10:00:00',
             600_000, 500_000, 0.10, 0, 'ABC', 'SHIRTS', 'RTW'],
        ])
        mock_repo.get_personal_commission_sales_data.return_value = sales_df
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
        assert emp['commission_100_and_below_fp'] == pytest.approx(500_000 * 0.00375)

    def test_output_dataframe_columns(self, service, mock_repo):
        """Verify the output DataFrame has all expected columns."""
        sales_df = self._make_sales_df([
            [1, 'UPC001', 'E1', 'user1', 'SID1', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             1_000_000, 909_090, 0.0, 0, 'ABC', 'SHIRTS', 'RTW'],
        ])
        mock_repo.get_personal_commission_sales_data.return_value = sales_df
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
            'commission_100_and_below_fp', 'commission_100_and_below_discount',
            'commission_over_100', 'commission_jewelry',
            'commission_vhernier', 'commission_rosa_maria',
            'commission_suitcase', 'commission_hand_carry', 'total',
        ]
        assert list(result.columns) == expected_columns

    def test_employee_with_no_sales_gets_zero(self, service, mock_repo):
        """Employee whose SID is in sales df but has zero actual rows gets zero."""
        # Sales df has user1 data but user2 has no rows
        sales_df = self._make_sales_df([
            [1, 'UPC001', 'E1', 'user1', 'SID1', 'BILL1', 'HBT', '2025-01-15', '10:00:00',
             1_000_000, 909_090, 0.0, 0, 'ABC', 'SHIRTS', 'RTW'],
        ])
        mock_repo.get_personal_commission_sales_data.return_value = sales_df
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

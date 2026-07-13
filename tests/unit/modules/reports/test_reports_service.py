"""
Unit tests for app.modules.reports.service.ReportsService (CR #78).

The Oracle repository is mocked — these cover date validation, period
assembly, avg-bill rounding, the returning-revenue derivation, and the
new+returning == total_revenue invariant.
"""
from datetime import date
from unittest.mock import MagicMock

import pytest

from app.modules.reports.service import ReportsService


def _metrics(total_revenue, bill_count, new_customers,
             returning_customers, new_customer_revenue):
    return {
        'total_revenue':        total_revenue,
        'bill_count':           bill_count,
        'new_customers':        new_customers,
        'returning_customers':  returning_customers,
        'new_customer_revenue': new_customer_revenue,
    }


@pytest.fixture
def service():
    svc = ReportsService()
    svc.repo = MagicMock()
    return svc


# ---------------------------------------------------------------------------
# Happy path + assembly
# ---------------------------------------------------------------------------

class TestGetSaleComparison:

    def test_assembles_both_periods(self, service):
        service.repo.fetch_period_metrics.side_effect = [
            _metrics(1_200_000_000, 3450, 120, 890, 180_000_000),
            _metrics(1_000_000_000, 3000, 100, 800, 150_000_000),
        ]
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31')
        assert res['success'] is True

        a = res['period_a']
        assert a['from'] == '2026-06-01' and a['to'] == '2026-06-30'
        assert a['total_revenue'] == 1_200_000_000
        assert a['bill_count'] == 3450
        # avg_bill = round(1_200_000_000 / 3450)
        assert a['avg_bill'] == round(1_200_000_000 / 3450)
        assert a['new_customers'] == 120
        assert a['returning_customers'] == 890
        assert a['new_customer_revenue'] == 180_000_000
        # returning revenue = total - new (walk-ins fold into returning)
        assert a['returning_customer_revenue'] == 1_200_000_000 - 180_000_000
        # Invariant the frontend relies on.
        assert a['new_customer_revenue'] + a['returning_customer_revenue'] == a['total_revenue']

        assert res['period_b']['total_revenue'] == 1_000_000_000

    def test_to_is_inclusive_via_exclusive_bind(self, service):
        """`to` day is included: repo is called with to_exclusive = to + 1 day."""
        service.repo.fetch_period_metrics.side_effect = [
            _metrics(0, 0, 0, 0, 0), _metrics(0, 0, 0, 0, 0)]
        service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-01-01', '2026-01-31')
        first_call = service.repo.fetch_period_metrics.call_args_list[0]
        from_d, to_exclusive = first_call.args
        assert from_d == date(2026, 6, 1)
        assert to_exclusive == date(2026, 7, 1)   # 2026-06-30 + 1 day

    def test_zero_bill_period_returns_null_avg(self, service):
        service.repo.fetch_period_metrics.side_effect = [
            _metrics(0, 0, 0, 0, 0),
            _metrics(500, 5, 1, 4, 100),
        ]
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31')
        a = res['period_a']
        assert a['avg_bill'] is None
        assert a['total_revenue'] == 0
        assert a['new_customers'] == 0
        assert a['returning_customer_revenue'] == 0

    def test_periods_are_independent_order(self, service):
        """B before A chronologically is fine — no ordering constraint."""
        service.repo.fetch_period_metrics.side_effect = [
            _metrics(10, 1, 1, 0, 10), _metrics(20, 2, 0, 2, 0)]
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-01-01', '2026-01-31')
        assert res['success'] is True


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:

    def test_unparseable_date_rejected(self, service):
        res = service.get_sale_comparison(
            '2026-13-01', '2026-06-30', '2026-05-01', '2026-05-31')
        assert res['success'] is False
        assert res['code'] == 'INVALID_INPUT'
        service.repo.fetch_period_metrics.assert_not_called()

    def test_from_after_to_rejected(self, service):
        res = service.get_sale_comparison(
            '2026-06-30', '2026-06-01', '2026-05-01', '2026-05-31')
        assert res['success'] is False
        assert res['code'] == 'INVALID_INPUT'
        assert 'after' in res['error']

    def test_range_exceeding_366_days_rejected(self, service):
        # 2025-01-01 .. 2026-01-02 inclusive = 367 days
        res = service.get_sale_comparison(
            '2025-01-01', '2026-01-02', '2026-05-01', '2026-05-31')
        assert res['success'] is False
        assert res['code'] == 'INVALID_INPUT'
        assert '366' in res['error']

    def test_range_exactly_366_days_allowed(self, service):
        service.repo.fetch_period_metrics.side_effect = [
            _metrics(1, 1, 1, 0, 1), _metrics(1, 1, 1, 0, 1)]
        # 2025-01-01 .. 2026-01-01 inclusive = 366 days (leap year 2024 not in range)
        res = service.get_sale_comparison(
            '2025-01-01', '2026-01-01', '2026-05-01', '2026-05-31')
        assert res['success'] is True

    def test_period_b_validated_too(self, service):
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', 'not-a-date', '2026-05-31')
        assert res['success'] is False
        assert res['code'] == 'INVALID_INPUT'
        assert 'B' in res['error']

    def test_repo_failure_returns_server_error(self, service):
        service.repo.fetch_period_metrics.side_effect = RuntimeError('oracle down')
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31')
        assert res['success'] is False
        assert res['code'] == 'SERVER_ERROR'

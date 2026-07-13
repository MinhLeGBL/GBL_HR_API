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
             returning_customers, new_customer_revenue,
             tourist_customers=0, tourist_customer_revenue=0):
    return {
        'total_revenue':            total_revenue,
        'bill_count':               bill_count,
        'tourist_customers':        tourist_customers,
        'tourist_customer_revenue': tourist_customer_revenue,
        'new_customers':            new_customers,
        'returning_customers':      returning_customers,
        'new_customer_revenue':     new_customer_revenue,
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
            _metrics(1_200_000_000, 3450, 120, 890, 180_000_000,
                     tourist_customers=45, tourist_customer_revenue=100_000_000),
            _metrics(1_000_000_000, 3000, 100, 800, 150_000_000,
                     tourist_customers=30, tourist_customer_revenue=80_000_000),
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
        assert a['tourist_customers'] == 45
        assert a['new_customer_revenue'] == 180_000_000
        assert a['tourist_customer_revenue'] == 100_000_000
        # CR #80: returning = total - new - tourist (tourist broken out).
        assert a['returning_customer_revenue'] == 1_200_000_000 - 180_000_000 - 100_000_000
        # Updated invariant: new + returning + tourist == total_revenue.
        assert (a['new_customer_revenue'] + a['returning_customer_revenue']
                + a['tourist_customer_revenue']) == a['total_revenue']

        assert res['period_b']['total_revenue'] == 1_000_000_000
        assert res['period_b']['tourist_customers'] == 30

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


# ---------------------------------------------------------------------------
# CR #79 — pins
# ---------------------------------------------------------------------------

class TestGetPins:

    def test_no_row_returns_nulls(self, service):
        service.repo.get_pins.return_value = None
        res = service.get_pins(user_id=42)
        assert res == {'success': True, 'period_a': None, 'period_b': None}

    def test_row_maps_to_periods(self, service):
        service.repo.get_pins.return_value = {
            'period_a_from': date(2026, 6, 1), 'period_a_to': date(2026, 6, 30),
            'period_b_from': None, 'period_b_to': None,
        }
        res = service.get_pins(user_id=42)
        assert res['period_a'] == {'from': '2026-06-01', 'to': '2026-06-30'}
        assert res['period_b'] is None

    def test_db_failure_is_server_error(self, service):
        service.repo.get_pins.side_effect = RuntimeError('pg down')
        res = service.get_pins(user_id=42)
        assert res['success'] is False and res['code'] == 'SERVER_ERROR'


class TestSetPins:

    def test_persists_and_echoes(self, service):
        res = service.set_pins(user_id=7, body={
            'period_a': {'from': '2026-06-01', 'to': '2026-06-30'},
            'period_b': None,
        })
        assert res['success'] is True
        assert res['period_a'] == {'from': '2026-06-01', 'to': '2026-06-30'}
        assert res['period_b'] is None
        service.repo.upsert_pins.assert_called_once_with(
            7, date(2026, 6, 1), date(2026, 6, 30), None, None)

    def test_clear_both_sides(self, service):
        res = service.set_pins(user_id=7, body={'period_a': None, 'period_b': None})
        assert res['success'] is True
        assert res['period_a'] is None and res['period_b'] is None
        service.repo.upsert_pins.assert_called_once_with(7, None, None, None, None)

    def test_missing_top_level_key_rejected(self, service):
        res = service.set_pins(user_id=7, body={'period_a': None})
        assert res['success'] is False and res['code'] == 'INVALID_INPUT'
        service.repo.upsert_pins.assert_not_called()

    def test_non_dict_body_rejected(self, service):
        res = service.set_pins(user_id=7, body=None)
        assert res['success'] is False and res['code'] == 'INVALID_INPUT'

    def test_pin_missing_from_or_to_rejected(self, service):
        res = service.set_pins(user_id=7, body={
            'period_a': {'from': '2026-06-01'}, 'period_b': None})
        assert res['success'] is False and res['code'] == 'INVALID_INPUT'
        assert 'A' in res['error']

    def test_invalid_date_rejected(self, service):
        res = service.set_pins(user_id=7, body={
            'period_a': None,
            'period_b': {'from': '2026-13-01', 'to': '2026-06-30'}})
        assert res['success'] is False and res['code'] == 'INVALID_INPUT'

    def test_from_after_to_rejected(self, service):
        res = service.set_pins(user_id=7, body={
            'period_a': {'from': '2026-06-30', 'to': '2026-06-01'},
            'period_b': None})
        assert res['success'] is False and res['code'] == 'INVALID_INPUT'

    def test_range_over_366_days_rejected(self, service):
        res = service.set_pins(user_id=7, body={
            'period_a': {'from': '2025-01-01', 'to': '2026-01-02'},
            'period_b': None})
        assert res['success'] is False and res['code'] == 'INVALID_INPUT'

    def test_repo_failure_is_server_error(self, service):
        service.repo.upsert_pins.side_effect = RuntimeError('pg down')
        res = service.set_pins(user_id=7, body={'period_a': None, 'period_b': None})
        assert res['success'] is False and res['code'] == 'SERVER_ERROR'

    def test_non_string_date_is_400_not_500(self, service):
        """A JSON number/bool for `from`/`to` must be rejected as INVALID_INPUT
        (400), not raise AttributeError from .strip() → 500."""
        for bad in (20260601, True, ['2026-06-01'], {'x': 1}):
            res = service.set_pins(user_id=7, body={
                'period_a': {'from': bad, 'to': '2026-06-30'},
                'period_b': None})
            assert res['success'] is False, f'{bad!r} should be rejected'
            assert res['code'] == 'INVALID_INPUT'
        service.repo.upsert_pins.assert_not_called()

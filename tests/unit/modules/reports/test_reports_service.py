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
             tourist_customers=0, tourist_customer_revenue=0,
             gross_sales_revenue=0, net_sales_revenue=0, items_sold=0,
             fp_items_sold=0, fp_revenue=0):
    return {
        'total_revenue':            total_revenue,
        'bill_count':               bill_count,
        'tourist_customers':        tourist_customers,
        'tourist_customer_revenue': tourist_customer_revenue,
        'new_customers':            new_customers,
        'returning_customers':      returning_customers,
        'new_customer_revenue':     new_customer_revenue,
        'gross_sales_revenue':      gross_sales_revenue,
        'net_sales_revenue':        net_sales_revenue,
        'items_sold':               items_sold,
        'fp_items_sold':            fp_items_sold,
        'fp_revenue':               fp_revenue,
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
        from_d, to_exclusive, store_sids = first_call.args
        assert from_d == date(2026, 6, 1)
        assert to_exclusive == date(2026, 7, 1)   # 2026-06-30 + 1 day
        assert store_sids is None                 # no store scope by default

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
# CR #81 — per-period store scope on sale-comparison
# ---------------------------------------------------------------------------

class TestStoreScope:

    def test_no_store_scope_passes_none(self, service):
        """Omitted store params → each period built with store_sids=None."""
        service.repo.fetch_period_metrics.side_effect = [
            _metrics(1, 1, 1, 0, 1), _metrics(1, 1, 1, 0, 1)]
        service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31')
        service.repo.get_store_sids.assert_not_called()
        for call in service.repo.fetch_period_metrics.call_args_list:
            assert call.args[2] is None   # store_sids

    def test_resolves_and_scopes_per_side(self, service):
        """store_a='3,7' → period A scoped to those stores' Oracle SIDs; B (no
        param) stays all-stores."""
        service.repo.get_store_sids.return_value = {3: 501, 7: 502}
        service.repo.fetch_period_metrics.side_effect = [
            _metrics(1, 1, 1, 0, 1), _metrics(1, 1, 1, 0, 1)]
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31',
            store_a='3,7')
        assert res['success'] is True
        calls = service.repo.fetch_period_metrics.call_args_list
        assert calls[0].args[2] == [501, 502]   # period A scoped
        assert calls[1].args[2] is None          # period B all stores

    def test_unknown_store_id_is_400(self, service):
        """An id with no row in the resolver map → INVALID_INPUT ('unknown'),
        no Oracle call."""
        service.repo.get_store_sids.return_value = {3: 501}   # 7 has no row
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31',
            store_a='3,7')
        assert res['success'] is False and res['code'] == 'INVALID_INPUT'
        assert '7' in res['error'] and 'unknown' in res['error'].lower()
        service.repo.fetch_period_metrics.assert_not_called()

    def test_unmapped_store_id_is_400_distinct_message(self, service):
        """A store that exists but has no Retail Pro mapping (sid None) → 400 with
        a 'not linked' message, distinct from the 'unknown' case (finding #2)."""
        service.repo.get_store_sids.return_value = {3: 501, 7: None}   # 7 unmapped
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31',
            store_a='3,7')
        assert res['success'] is False and res['code'] == 'INVALID_INPUT'
        assert '7' in res['error'] and 'not linked' in res['error'].lower()
        assert 'unknown' not in res['error'].lower()
        service.repo.fetch_period_metrics.assert_not_called()

    def test_non_integer_store_token_is_400(self, service):
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31',
            store_b='3,abc')
        assert res['success'] is False and res['code'] == 'INVALID_INPUT'
        service.repo.get_store_sids.assert_not_called()
        service.repo.fetch_period_metrics.assert_not_called()

    def test_blank_store_param_means_all_stores(self, service):
        """A present-but-empty value (e.g. store_a=) → all stores, not an error."""
        service.repo.fetch_period_metrics.side_effect = [
            _metrics(1, 1, 1, 0, 1), _metrics(1, 1, 1, 0, 1)]
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31',
            store_a='   ', store_b=',')
        assert res['success'] is True
        service.repo.get_store_sids.assert_not_called()
        for call in service.repo.fetch_period_metrics.call_args_list:
            assert call.args[2] is None

    def test_duplicate_ids_deduped_to_distinct_sids(self, service):
        service.repo.get_store_sids.return_value = {3: 501, 7: 501, 9: 502}
        service.repo.fetch_period_metrics.side_effect = [
            _metrics(1, 1, 1, 0, 1), _metrics(1, 1, 1, 0, 1)]
        service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31',
            store_a='3,7,9')
        # 3 and 7 both map to SID 501 → deduped, order preserved.
        assert service.repo.fetch_period_metrics.call_args_list[0].args[2] == [501, 502]

    def test_store_resolution_db_fault_is_500(self, service):
        service.repo.get_store_sids.side_effect = RuntimeError('pg down')
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31',
            store_a='3')
        assert res['success'] is False and res['code'] == 'SERVER_ERROR'


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
            'period_a_store_ids': [3, 7],
            'period_b_from': None, 'period_b_to': None, 'period_b_store_ids': None,
        }
        res = service.get_pins(user_id=42)
        assert res['period_a'] == {
            'from': '2026-06-01', 'to': '2026-06-30', 'store_ids': [3, 7]}
        assert res['period_b'] is None

    def test_legacy_null_store_ids_read_as_empty(self, service):
        """CR #81: a pin saved before store arrays existed reads back with
        store_ids == [] (all stores), no migration break."""
        service.repo.get_pins.return_value = {
            'period_a_from': date(2026, 6, 1), 'period_a_to': date(2026, 6, 30),
            'period_a_store_ids': None,
            'period_b_from': None, 'period_b_to': None, 'period_b_store_ids': None,
        }
        res = service.get_pins(user_id=42)
        assert res['period_a'] == {
            'from': '2026-06-01', 'to': '2026-06-30', 'store_ids': []}

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
        # store_ids omitted → [] echoed, None persisted (all stores).
        assert res['period_a'] == {
            'from': '2026-06-01', 'to': '2026-06-30', 'store_ids': []}
        assert res['period_b'] is None
        service.repo.upsert_pins.assert_called_once_with(
            7, date(2026, 6, 1), date(2026, 6, 30), None, None, None, None)

    def test_persists_store_ids(self, service):
        """CR #81: a store_ids array is validated, persisted, and echoed back."""
        res = service.set_pins(user_id=7, body={
            'period_a': {'from': '2026-06-01', 'to': '2026-06-30', 'store_ids': [3, 7]},
            'period_b': {'from': '2026-07-01', 'to': '2026-07-31', 'store_ids': []},
        })
        assert res['success'] is True
        assert res['period_a']['store_ids'] == [3, 7]
        assert res['period_b']['store_ids'] == []   # [] → all stores
        service.repo.upsert_pins.assert_called_once_with(
            7, date(2026, 6, 1), date(2026, 6, 30), [3, 7],
            date(2026, 7, 1), date(2026, 7, 31), None)

    def test_non_list_store_ids_rejected(self, service):
        res = service.set_pins(user_id=7, body={
            'period_a': {'from': '2026-06-01', 'to': '2026-06-30', 'store_ids': 3},
            'period_b': None})
        assert res['success'] is False and res['code'] == 'INVALID_INPUT'
        service.repo.upsert_pins.assert_not_called()

    def test_non_int_store_id_rejected(self, service):
        for bad in ('3', 3.5, True, None):
            res = service.set_pins(user_id=7, body={
                'period_a': {'from': '2026-06-01', 'to': '2026-06-30',
                             'store_ids': [bad]},
                'period_b': None})
            assert res['success'] is False, f'{bad!r} should be rejected'
            assert res['code'] == 'INVALID_INPUT'
        service.repo.upsert_pins.assert_not_called()

    def test_out_of_range_store_id_is_400_not_500(self, service):
        """A store id beyond Postgres BIGINT range must be rejected as 400, not
        escape as a 500 at INSERT (finding #3)."""
        for bad in (2 ** 63, -(2 ** 63) - 1):
            res = service.set_pins(user_id=7, body={
                'period_a': {'from': '2026-06-01', 'to': '2026-06-30',
                             'store_ids': [bad]},
                'period_b': None})
            assert res['success'] is False, f'{bad!r} should be rejected'
            assert res['code'] == 'INVALID_INPUT'
        service.repo.upsert_pins.assert_not_called()

    def test_clear_both_sides(self, service):
        res = service.set_pins(user_id=7, body={'period_a': None, 'period_b': None})
        assert res['success'] is True
        assert res['period_a'] is None and res['period_b'] is None
        service.repo.upsert_pins.assert_called_once_with(
            7, None, None, None, None, None, None)

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


# ---------------------------------------------------------------------------
# CR #83 — avg_discount_rate (value-weighted, percent)
# ---------------------------------------------------------------------------

class TestAvgDiscountRate:

    def test_value_weighted_rate(self, service):
        # gross 250M, net 200M -> 100 * (250-200)/250 = 20.0 (worked example)
        service.repo.fetch_period_metrics.side_effect = [
            _metrics(200_000_000, 10, 1, 9, 0,
                     gross_sales_revenue=250_000_000, net_sales_revenue=200_000_000),
            _metrics(0, 0, 0, 0, 0),
        ]
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31')
        assert res['period_a']['avg_discount_rate'] == 20.0

    def test_rounds_to_one_decimal(self, service):
        # gross 300M, net 200M -> 33.333... -> 33.3
        service.repo.fetch_period_metrics.side_effect = [
            _metrics(200_000_000, 5, 1, 4, 0,
                     gross_sales_revenue=300_000_000, net_sales_revenue=200_000_000),
            _metrics(0, 0, 0, 0, 0),
        ]
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31')
        assert res['period_a']['avg_discount_rate'] == 33.3

    def test_null_when_no_gross(self, service):
        # Empty period (no gross) -> null, not a ZeroDivisionError.
        service.repo.fetch_period_metrics.side_effect = [
            _metrics(0, 0, 0, 0, 0, gross_sales_revenue=0, net_sales_revenue=0),
            _metrics(0, 0, 0, 0, 0),
        ]
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31')
        assert res['period_a']['avg_discount_rate'] is None

    def test_zero_discount_period(self, service):
        # Nothing discounted -> gross == net -> 0.0 (not null).
        service.repo.fetch_period_metrics.side_effect = [
            _metrics(500_000, 3, 1, 2, 0,
                     gross_sales_revenue=500_000, net_sales_revenue=500_000),
            _metrics(0, 0, 0, 0, 0),
        ]
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31')
        assert res['period_a']['avg_discount_rate'] == 0.0


# ---------------------------------------------------------------------------
# CR #84 — items_sold (total units)
# ---------------------------------------------------------------------------

class TestItemsSold:

    def test_passed_through_per_period(self, service):
        service.repo.fetch_period_metrics.side_effect = [
            _metrics(1_000, 2, 1, 1, 0, items_sold=15),
            _metrics(500, 1, 1, 0, 0, items_sold=3),
        ]
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31')
        assert res['period_a']['items_sold'] == 15
        assert res['period_b']['items_sold'] == 3

    def test_zero_for_empty_period(self, service):
        # Empty period -> 0 (integer), never null.
        service.repo.fetch_period_metrics.side_effect = [
            _metrics(0, 0, 0, 0, 0, items_sold=0),
            _metrics(0, 0, 0, 0, 0),
        ]
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31')
        assert res['period_a']['items_sold'] == 0


# ---------------------------------------------------------------------------
# CR #85 — FP/MD split (Markdown derived as residual)
# ---------------------------------------------------------------------------

class TestFpMdSplit:

    def test_md_derived_and_reconciles(self, service):
        # items_sold=15 (fp 5), net_sales=200 (fp 150) -> md items 10, md rev 50.
        service.repo.fetch_period_metrics.side_effect = [
            _metrics(200, 3, 1, 2, 0,
                     net_sales_revenue=200, items_sold=15,
                     fp_items_sold=5, fp_revenue=150),
            _metrics(0, 0, 0, 0, 0),
        ]
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31')
        a = res['period_a']
        assert a['fp_items_sold'] == 5 and a['md_items_sold'] == 10
        assert a['fp_revenue'] == 150 and a['md_revenue'] == 50
        # Invariants the FE relies on for the pies.
        assert a['fp_items_sold'] + a['md_items_sold'] == a['items_sold']
        assert a['fp_revenue'] + a['md_revenue'] == 200   # == net_sales_revenue

    def test_all_full_price(self, service):
        service.repo.fetch_period_metrics.side_effect = [
            _metrics(500, 2, 1, 1, 0,
                     net_sales_revenue=500, items_sold=8,
                     fp_items_sold=8, fp_revenue=500),
            _metrics(0, 0, 0, 0, 0),
        ]
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31')
        a = res['period_a']
        assert a['md_items_sold'] == 0 and a['md_revenue'] == 0

    def test_empty_period_all_zero(self, service):
        service.repo.fetch_period_metrics.side_effect = [
            _metrics(0, 0, 0, 0, 0, net_sales_revenue=0, items_sold=0,
                     fp_items_sold=0, fp_revenue=0),
            _metrics(0, 0, 0, 0, 0),
        ]
        res = service.get_sale_comparison(
            '2026-06-01', '2026-06-30', '2026-05-01', '2026-05-31')
        a = res['period_a']
        assert (a['fp_items_sold'], a['md_items_sold'],
                a['fp_revenue'], a['md_revenue']) == (0, 0, 0, 0)

"""
Unit tests for AccountPayableService.compute_released_for_month (CR #66).

The accumulator pulls bills via `_load_bills`, filters to those with any
payment in the target month, and multiplies each line item by the bill's
allocation factor × the effective release rate to bucket released amounts
per (employee_code, category_key).

Mocking strategy — patch the four data sources used by the method:
  - `_load_bills` (Oracle ledger replay + manual reconciles)
  - `repository.get_bill_items` (Oracle line items)
  - `_load_auto_rates` (Postgres `payable_bill_rates` from CR #61)
  - `_load_custom_rates` (Postgres `payable_item_custom_rates` from CR #60)

Each test focuses on one branch of the algorithm so we can quickly
identify regressions.
"""
from unittest.mock import patch, MagicMock

from app.modules.account_payable.service import AccountPayableService


def _bill(sid, original_charge, payment_dates_and_amounts):
    """Build a bill dict matching `_load_bills`'s output shape.

    `payment_dates_and_amounts` is a list of (YYYY-MM-DD, int) tuples.
    """
    return {
        'doc_no':           f'BILL-{sid}',
        'doc_store_code':   'RWR',
        'customer_sid':     'C001',
        'customer_name':    'Test Customer',
        'original_charge':  original_charge,
        'total_paid':       sum(a for _, a in payment_dates_and_amounts),
        'remaining_unpaid': original_charge - sum(a for _, a in payment_dates_and_amounts),
        'status':           'partial',
        'created_date':     '2026-03-15',
        'last_payment_date': (payment_dates_and_amounts[-1][0]
                              if payment_dates_and_amounts else None),
        'sale_total_amt':   original_charge,
        'post_month':       '2026-03',
        'payments': [
            {
                'payment_doc_sid': f'P{sid}_{i}',
                'payment_doc_no':  f'PMT-{sid}-{i}',
                'payment_date':    d,
                'amount_applied':  a,
                'source':          'fifo',
            }
            for i, (d, a) in enumerate(payment_dates_and_amounts)
        ],
    }


def _item(bill_sid, upc, *, vendor, qty, revenue_with_vat, employee_code='GL001'):
    return {
        'bill_sid':         bill_sid,
        'sale_id':          f'S-{bill_sid}-{upc}',
        'upc':              upc,
        'description':      f'Item {upc}',
        'qty':              qty,
        'vendor_code':      vendor,
        'is_jewelry':       0,
        'category':         '',
        'department':       '',
        'discount_rate':    0,
        'revenue_with_vat': revenue_with_vat,
        'employee_sid':     f'SID-{employee_code}',
        'employee_code':    employee_code,
        'employee_name':    'Test Employee',
    }


class TestComputeReleasedForMonth:

    @patch.object(AccountPayableService, '_load_bills')
    def test_empty_bills_returns_empty(self, mock_load_bills):
        mock_load_bills.return_value = {}
        assert AccountPayableService().compute_released_for_month(2026, 5) == {}

    @patch.object(AccountPayableService, '_load_custom_rates')
    @patch.object(AccountPayableService, '_load_auto_rates')
    @patch.object(AccountPayableService, '_load_bills')
    def test_skips_bills_with_no_payment_in_target_month(
        self, mock_load_bills, mock_auto, mock_custom,
    ):
        # Bill paid in April only — May calculation should ignore it.
        mock_load_bills.return_value = {
            '100': _bill('100', 11_000_000, [('2026-04-15', 11_000_000)]),
        }
        mock_auto.return_value = {}
        mock_custom.return_value = {}
        result = AccountPayableService().compute_released_for_month(2026, 5)
        assert result == {}

    @patch.object(AccountPayableService, '_load_custom_rates')
    @patch.object(AccountPayableService, '_load_auto_rates')
    @patch.object(AccountPayableService, '_load_bills')
    def test_fashion_fp_full_payment_uses_auto_rate(
        self, mock_load_bills, mock_auto, mock_custom,
    ):
        # 11M VND bill, fully paid in May → allocation factor = 1.
        # Item revenue_with_vat = 11M → before VAT = 10M.
        # auto_release_rate = 0.005 (Tier 2 FP) → released = 10M × 0.005 = 50_000.
        mock_load_bills.return_value = {
            '100': _bill('100', 11_000_000, [('2026-05-10', 11_000_000)]),
        }
        svc = AccountPayableService()
        svc.repository = MagicMock()
        svc.repository.get_bill_items.return_value = [
            _item('100', '888001', vendor='XYZ', qty=1,
                  revenue_with_vat=11_000_000, employee_code='GL005'),
        ]
        mock_auto.return_value = {
            ('100', '888001'): {
                'revenue_type':   'fashion',
                'fp_or_md':       'fp',
                'effective_rate': 0.005,
            },
        }
        mock_custom.return_value = {}

        result = svc.compute_released_for_month(2026, 5)
        assert result == {'GL005': {'fashion_fp': 50_000}}

    @patch.object(AccountPayableService, '_load_custom_rates')
    @patch.object(AccountPayableService, '_load_auto_rates')
    @patch.object(AccountPayableService, '_load_bills')
    def test_custom_rate_overrides_auto_rate(
        self, mock_load_bills, mock_auto, mock_custom,
    ):
        # Same item as above but with a custom rate of 0.01.
        # released = 10M × 0.01 = 100_000.
        mock_load_bills.return_value = {
            '100': _bill('100', 11_000_000, [('2026-05-10', 11_000_000)]),
        }
        svc = AccountPayableService()
        svc.repository = MagicMock()
        svc.repository.get_bill_items.return_value = [
            _item('100', '888001', vendor='XYZ', qty=1,
                  revenue_with_vat=11_000_000, employee_code='GL005'),
        ]
        mock_auto.return_value = {
            ('100', '888001'): {
                'revenue_type':   'fashion',
                'fp_or_md':       'fp',
                'effective_rate': 0.005,
            },
        }
        mock_custom.return_value = {('100', '888001'): 0.01}

        result = svc.compute_released_for_month(2026, 5)
        assert result == {'GL005': {'fashion_fp': 100_000}}

    @patch.object(AccountPayableService, '_load_custom_rates')
    @patch.object(AccountPayableService, '_load_auto_rates')
    @patch.object(AccountPayableService, '_load_bills')
    def test_partial_payment_prorates_release(
        self, mock_load_bills, mock_auto, mock_custom,
    ):
        # 11M bill, only 5.5M paid in May → allocation_factor = 0.5.
        # Item before-VAT = 10M; rate 0.01 → release = 10M × 0.01 × 0.5 = 50_000.
        mock_load_bills.return_value = {
            '100': _bill('100', 11_000_000, [('2026-05-10', 5_500_000)]),
        }
        svc = AccountPayableService()
        svc.repository = MagicMock()
        svc.repository.get_bill_items.return_value = [
            _item('100', '888001', vendor='XYZ', qty=1,
                  revenue_with_vat=11_000_000, employee_code='GL005'),
        ]
        mock_auto.return_value = {
            ('100', '888001'): {
                'revenue_type':   'fashion',
                'fp_or_md':       'fp',
                'effective_rate': 0.01,
            },
        }
        mock_custom.return_value = {}

        result = svc.compute_released_for_month(2026, 5)
        assert result == {'GL005': {'fashion_fp': 50_000}}

    @patch.object(AccountPayableService, '_load_custom_rates')
    @patch.object(AccountPayableService, '_load_auto_rates')
    @patch.object(AccountPayableService, '_load_bills')
    def test_hand_carry_uses_with_vat_basis(
        self, mock_load_bills, mock_auto, mock_custom,
    ):
        # Hand carry: basis = revenue_with_vat (no /1.1).
        # 11M × 0.01 × 1.0 = 110_000 (vs 100_000 if VAT was stripped).
        mock_load_bills.return_value = {
            '100': _bill('100', 11_000_000, [('2026-05-10', 11_000_000)]),
        }
        svc = AccountPayableService()
        svc.repository = MagicMock()
        svc.repository.get_bill_items.return_value = [
            _item('100', '888001', vendor='HC', qty=1,
                  revenue_with_vat=11_000_000, employee_code='GL005'),
        ]
        mock_auto.return_value = {
            ('100', '888001'): {
                'revenue_type':   'hand_carry',
                'fp_or_md':       None,
                'effective_rate': 0.01,
            },
        }
        mock_custom.return_value = {}

        result = svc.compute_released_for_month(2026, 5)
        assert result == {'GL005': {'hand_carry': 110_000}}

    @patch.object(AccountPayableService, '_load_custom_rates')
    @patch.object(AccountPayableService, '_load_auto_rates')
    @patch.object(AccountPayableService, '_load_bills')
    def test_suitcase_uses_flat_per_qty_not_rate(
        self, mock_load_bills, mock_auto, mock_custom,
    ):
        # Suitcase vendor TVL: SUITCASE_FLAT (500k) × qty × allocation_factor.
        # 3 items, full payment → 500k × 3 = 1_500_000. NOT in payable_bill_rates.
        mock_load_bills.return_value = {
            '100': _bill('100', 30_000_000, [('2026-05-10', 30_000_000)]),
        }
        svc = AccountPayableService()
        svc.repository = MagicMock()
        svc.repository.get_bill_items.return_value = [
            _item('100', '888001', vendor='TVL', qty=3,
                  revenue_with_vat=30_000_000, employee_code='GL005'),
        ]
        mock_auto.return_value = {}     # suitcase deliberately absent
        mock_custom.return_value = {}

        result = svc.compute_released_for_month(2026, 5)
        assert result == {'GL005': {'suitcase': 1_500_000}}

    @patch.object(AccountPayableService, '_load_custom_rates')
    @patch.object(AccountPayableService, '_load_auto_rates')
    @patch.object(AccountPayableService, '_load_bills')
    def test_aggregates_multiple_categories_per_employee(
        self, mock_load_bills, mock_auto, mock_custom,
    ):
        # Single bill, one employee, items spanning fashion_fp + jewelry.
        mock_load_bills.return_value = {
            '100': _bill('100', 22_000_000, [('2026-05-10', 22_000_000)]),
        }
        svc = AccountPayableService()
        svc.repository = MagicMock()
        svc.repository.get_bill_items.return_value = [
            _item('100', '888001', vendor='XYZ', qty=1,
                  revenue_with_vat=11_000_000, employee_code='GL005'),
            _item('100', '888002', vendor='ATS', qty=1,
                  revenue_with_vat=11_000_000, employee_code='GL005'),
        ]
        mock_auto.return_value = {
            ('100', '888001'): {
                'revenue_type': 'fashion', 'fp_or_md': 'fp',
                'effective_rate': 0.01,
            },
            ('100', '888002'): {
                'revenue_type': 'jewelry', 'fp_or_md': None,
                'effective_rate': 0.02,
            },
        }
        mock_custom.return_value = {}

        result = svc.compute_released_for_month(2026, 5)
        # fashion_fp = 10M × 0.01 = 100k; jewelry = 10M × 0.02 = 200k
        assert result == {'GL005': {'fashion_fp': 100_000, 'jewelry': 200_000}}

    @patch.object(AccountPayableService, '_load_custom_rates')
    @patch.object(AccountPayableService, '_load_auto_rates')
    @patch.object(AccountPayableService, '_load_bills')
    def test_missing_rate_snapshot_skips_item_silently(
        self, mock_load_bills, mock_auto, mock_custom,
    ):
        # Legacy bill with no payable_bill_rates row → item drops to 0.
        # Warning is printed but the result is empty (no other items).
        mock_load_bills.return_value = {
            '100': _bill('100', 11_000_000, [('2026-05-10', 11_000_000)]),
        }
        svc = AccountPayableService()
        svc.repository = MagicMock()
        svc.repository.get_bill_items.return_value = [
            _item('100', '888001', vendor='XYZ', qty=1,
                  revenue_with_vat=11_000_000, employee_code='GL005'),
        ]
        mock_auto.return_value = {}
        mock_custom.return_value = {}

        result = svc.compute_released_for_month(2026, 5)
        assert result == {}

    @patch.object(AccountPayableService, '_load_custom_rates')
    @patch.object(AccountPayableService, '_load_auto_rates')
    @patch.object(AccountPayableService, '_load_bills')
    def test_payments_in_other_months_do_not_count(
        self, mock_load_bills, mock_auto, mock_custom,
    ):
        # Bill has 2 payments: April (3.3M) and May (7.7M) of an 11M bill.
        # For May calc, allocation = 7.7M/11M = 0.7. Release = 10M × 0.01 × 0.7 = 70k.
        mock_load_bills.return_value = {
            '100': _bill('100', 11_000_000, [
                ('2026-04-15', 3_300_000),
                ('2026-05-10', 7_700_000),
            ]),
        }
        svc = AccountPayableService()
        svc.repository = MagicMock()
        svc.repository.get_bill_items.return_value = [
            _item('100', '888001', vendor='XYZ', qty=1,
                  revenue_with_vat=11_000_000, employee_code='GL005'),
        ]
        mock_auto.return_value = {
            ('100', '888001'): {
                'revenue_type': 'fashion', 'fp_or_md': 'fp',
                'effective_rate': 0.01,
            },
        }
        mock_custom.return_value = {}

        result = svc.compute_released_for_month(2026, 5)
        assert result == {'GL005': {'fashion_fp': 70_000}}

    @patch.object(AccountPayableService, '_load_custom_rates')
    @patch.object(AccountPayableService, '_load_auto_rates')
    @patch.object(AccountPayableService, '_load_bills')
    def test_items_without_employee_code_are_skipped(
        self, mock_load_bills, mock_auto, mock_custom,
    ):
        # SYSADMIN / orphan items lack employee_code → must not appear in
        # the result (otherwise we'd attribute payouts to a non-employee).
        mock_load_bills.return_value = {
            '100': _bill('100', 11_000_000, [('2026-05-10', 11_000_000)]),
        }
        item = _item('100', '888001', vendor='XYZ', qty=1,
                     revenue_with_vat=11_000_000, employee_code='GL005')
        item['employee_code'] = None
        svc = AccountPayableService()
        svc.repository = MagicMock()
        svc.repository.get_bill_items.return_value = [item]
        mock_auto.return_value = {
            ('100', '888001'): {
                'revenue_type': 'fashion', 'fp_or_md': 'fp',
                'effective_rate': 0.01,
            },
        }
        mock_custom.return_value = {}

        result = svc.compute_released_for_month(2026, 5)
        assert result == {}

"""
Cross-endpoint + cross-module consistency tests (CR #62/63 canaries).

These are designed to catch the specific class of bug that CR #62 was —
two endpoints disagreeing on the same source data — and CR #63's
sibling class — AP's classifier drifting from commission's. Either
condition would have surfaced these bugs before v1.0.0 shipped.

If either of these tests starts failing, somebody added a code path
that breaks the single-source-of-truth invariant.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.modules.account_payable.service import AccountPayableService


FROZEN_TODAY = date(2026, 6, 2)


def _bill(bill_sid, doc_no, customer_sid, customer_name,
          original, remaining, status, created_date, post_month,
          payments=()):
    return {
        'doc_no': doc_no, 'doc_store_code': 'HBT',
        'customer_sid': customer_sid, 'customer_name': customer_name,
        'original_charge': original,
        'total_paid': original - remaining,
        'remaining_unpaid': remaining,
        'status': status,
        'created_date': created_date, 'last_payment_date': None,
        'sale_total_amt': int(original * 1.1),
        'post_month': post_month,
        'payments': list(payments),
    }


def _chrono(payment_doc_sid, amount, source, payment_date):
    return {
        'payment_doc_sid': payment_doc_sid,
        'payment_doc_no':  f'P-{payment_doc_sid}',
        'payment_date':    payment_date,
        'amount_applied':  amount,
        'source':          source,
    }


@pytest.fixture(autouse=True)
def freeze_today():
    with patch('app.modules.account_payable.service._today', return_value=FROZEN_TODAY):
        yield


# ======================================================================
# CR #62 canary: bill view and queue must agree on "matched" status.
# ======================================================================

class TestBillViewAndQueueAgree:
    """Every payment in a bill's `payments[]` chronology MUST be classified
    consistently by `get_pending_payments`:

      - bill chronology shows `source ∈ {ref_sale_sid, manual, fifo}` AND
        payment is past-month → released → MUST NOT appear in queue
      - bill chronology shows the payment AND payment is current month →
        MUST appear in queue with `match_source` matching the chronology source

    The pre-CR-#62 bug was: queue showed 72 FIFO-applied payments as
    `unmatched + is_overdue` while bills showed them as `fully_paid +
    source='fifo'`. This test would have caught that.
    """

    def _setup(self, sample_bills, sample_payments):
        repo = MagicMock()
        repo.get_charge_payments.return_value = sample_payments
        repo.get_bill_items.return_value = []   # bill_detail joins items separately
        svc = AccountPayableService(repository=repo)
        svc._load_bills = MagicMock(return_value=sample_bills)
        svc._load_auto_rates = MagicMock(return_value={})
        svc._load_custom_rates = MagicMock(return_value={})
        return svc

    def test_fifo_past_month_payment_drops_from_queue_and_shows_in_bill(self):
        """The exact CR #62 scenario: a past-month FIFO-applied payment.
        Bill view must show it; queue must hide it (released)."""
        bills = {
            '1001': _bill(
                '1001', 'D-1001', '999', 'Cust',
                original=100_000, remaining=0, status='fully_paid',
                created_date='2026-04-01', post_month='2026-04',
                payments=[_chrono('P_PAST', 100_000, 'fifo', '2026-04-20')],
            ),
        }
        payments = [{
            'payment_doc_sid': 'P_PAST', 'payment_doc_no': 'P-P_PAST',
            'doc_store_code': 'HBT', 'customer_sid': '999',
            'customer_name': 'Cust', 'notes_lostdoc': None,
            'ref_sale_sid': None, 'amount': 100_000,
            'payment_date': '2026-04-20',
        }]
        svc = self._setup(bills, payments)

        queue = svc.get_pending_payments()['data']
        bill = svc.get_bill_detail('1001')['data']

        # Bill view: shows the FIFO payment in chronology.
        bill_payment_sids = {p['payment_doc_sid'] for p in bill['payments']}
        assert 'P_PAST' in bill_payment_sids

        # Queue: must NOT show it (released — past month + applied via replay).
        queue_payment_sids = {p['payment_doc_sid'] for p in queue}
        assert 'P_PAST' not in queue_payment_sids

    def test_fifo_current_month_payment_appears_in_both_with_source_fifo(self):
        bills = {
            '1001': _bill(
                '1001', 'D-1001', '999', 'Cust',
                original=100_000, remaining=0, status='fully_paid',
                created_date='2026-06-01', post_month='2026-06',
                payments=[_chrono('P_NOW', 100_000, 'fifo', '2026-06-02')],
            ),
        }
        payments = [{
            'payment_doc_sid': 'P_NOW', 'payment_doc_no': 'P-P_NOW',
            'doc_store_code': 'HBT', 'customer_sid': '999',
            'customer_name': 'Cust', 'notes_lostdoc': None,
            'ref_sale_sid': None, 'amount': 100_000,
            'payment_date': '2026-06-02',
        }]
        svc = self._setup(bills, payments)

        queue = svc.get_pending_payments()['data']
        bill = svc.get_bill_detail('1001')['data']

        row = next((p for p in queue if p['payment_doc_sid'] == 'P_NOW'), None)
        assert row is not None, 'current-month payment must appear in queue'
        # The status must match the bill chronology's source — fifo on both sides.
        bill_entry = next(p for p in bill['payments'] if p['payment_doc_sid'] == 'P_NOW')
        assert bill_entry['source'] == 'fifo'
        assert row['match_source'] == 'fifo'
        assert row['status'] == 'matched_pending'

    def test_no_chronology_means_no_match_in_either_endpoint(self):
        """A payment that the replay couldn't apply: bill view doesn't mention
        it; queue shows it as unmatched (with is_overdue for past month)."""
        bills = {
            '1001': _bill(
                '1001', 'D-1001', '999', 'Cust',
                original=100_000, remaining=100_000, status='open',
                created_date='2026-06-01', post_month='2026-06',
                payments=[],
            ),
        }
        payments = [{
            'payment_doc_sid': 'P_ORPHAN', 'payment_doc_no': 'P-P_ORPHAN',
            'doc_store_code': 'HBT', 'customer_sid': '888',   # different customer
            'customer_name': 'Other', 'notes_lostdoc': None,
            'ref_sale_sid': None, 'amount': 50_000,
            'payment_date': '2026-04-15',
        }]
        svc = self._setup(bills, payments)

        queue = svc.get_pending_payments()['data']
        bill = svc.get_bill_detail('1001')['data']

        # Bill view doesn't reference P_ORPHAN.
        assert all(p['payment_doc_sid'] != 'P_ORPHAN' for p in bill['payments'])
        # Queue has it as unmatched + overdue.
        row = next((p for p in queue if p['payment_doc_sid'] == 'P_ORPHAN'), None)
        assert row is not None
        assert row['status'] == 'unmatched'
        assert row['match_source'] is None
        assert row['is_overdue'] is True


# ======================================================================
# CR #63 sibling canary: AP's classifier must agree with commission's.
# ======================================================================

class TestClassifierAgreesWithCommission:
    """AP duplicated commission's CR #61 classifier as a private bucket-only
    helper because CLAUDE.md forbids reaching into another module's privates.
    If commission's bucket logic shifts and AP doesn't follow, legacy-item
    propagation will silently target the wrong items.

    This test imports BOTH classifiers and asserts they produce the same
    `(revenue_type, fp_or_md)` for a representative set of rows.
    """

    @pytest.mark.parametrize('row, hand_carry_upcs, creation_month, expected', [
        # fashion FP
        ({'vendor_code': 'GUC', 'upc': 'X', 'department': 'WRTW',
          'is_jewelry': 0, 'category': 'BAG', 'discount_rate': 0.0},
         set(), '2026-05', ('fashion', 'fp')),
        # fashion MD (over the discount threshold)
        ({'vendor_code': 'GUC', 'upc': 'X', 'department': 'WRTW',
          'is_jewelry': 0, 'category': 'BAG', 'discount_rate': 0.50},
         set(), '2026-05', ('fashion', 'md')),
        # hand carry by UPC
        ({'vendor_code': 'CGI', 'upc': 'HC1', 'department': 'WRTW',
          'is_jewelry': 0, 'category': 'BAG', 'discount_rate': 0.0},
         {'HC1'}, '2026-05', ('hand_carry', None)),
        # HOME
        ({'vendor_code': 'IKE', 'upc': 'X', 'department': 'HOME',
          'is_jewelry': 0, 'category': 'DECOR', 'discount_rate': 0.0},
         set(), '2026-05', ('home_decor', None)),
        # COSM+HEA pre-cutoff
        ({'vendor_code': 'HEA', 'upc': 'X', 'department': 'COSM',
          'is_jewelry': 0, 'category': 'CREAM', 'discount_rate': 0.0},
         set(), '2026-03', ('other', None)),
        # COSM+HEA post-cutoff
        ({'vendor_code': 'HEA', 'upc': 'X', 'department': 'COSM',
          'is_jewelry': 0, 'category': 'CREAM', 'discount_rate': 0.0},
         set(), '2026-04', ('fashion', 'fp')),
        # vhernier
        ({'vendor_code': 'VHN', 'upc': 'J1', 'department': 'JWLY',
          'is_jewelry': 1, 'category': 'NECKLACE', 'discount_rate': 0.0},
         set(), '2026-05', ('vhernier', None)),
        # rosa_maria
        ({'vendor_code': 'ROM', 'upc': 'J2', 'department': 'JWLY',
          'is_jewelry': 1, 'category': 'EARRINGS', 'discount_rate': 0.0},
         set(), '2026-05', ('rosa_maria', None)),
        # generic jewelry
        ({'vendor_code': 'OTH', 'upc': 'J3', 'department': 'JWLY',
          'is_jewelry': 1, 'category': 'RING', 'discount_rate': 0.0},
         set(), '2026-05', ('jewelry', None)),
    ])
    def test_ap_and_commission_classifiers_agree(
        self, row, hand_carry_upcs, creation_month, expected,
    ):
        from app.modules.commission.service import (
            CommissionService, STANDARD_RATES,
        )
        # AP's classifier — bucket-only.
        ap_result = AccountPayableService._classify_item_bucket(
            row, hand_carry_upcs=hand_carry_upcs, creation_month=creation_month,
        )

        # Commission's classifier — returns (type, fp_or_md, rate); we
        # compare only the first two. It expects `upc_clean` (not `upc`)
        # and the period as a hea_as_fashion bool, so adapt the row.
        comm_row = dict(row)
        comm_row['upc_clean'] = comm_row.pop('upc', None)
        hea_as_fashion = creation_month >= '2026-04'
        comm_result = CommissionService._classify_item_rate(
            row=comm_row, rates=STANDARD_RATES, achievement_rate=80,
            hand_carry_upcs=hand_carry_upcs,
            ot_qualifying_sale_ids=set(), ot_blended_rates_by_sale_id={},
            hea_as_fashion_period=hea_as_fashion,
        )
        comm_bucket = (comm_result[0], comm_result[1]) if comm_result else None

        # All three must agree.
        assert ap_result == expected, f'AP says {ap_result} for {row}'
        assert comm_bucket == expected, f'Commission says {comm_bucket} for {row}'

    def test_suitcase_returns_none_in_both(self):
        """Suitcase items have no rate concept — both classifiers must skip them
        (AP returns None, commission returns None)."""
        from app.modules.commission.service import (
            CommissionService, STANDARD_RATES,
        )
        ap_row = {
            'vendor_code': 'TVL', 'upc': 'X', 'department': 'BAGS',
            'is_jewelry': 0, 'category': 'TRAVEL', 'discount_rate': 0.0,
        }
        comm_row = dict(ap_row)
        comm_row['upc_clean'] = comm_row.pop('upc')

        assert AccountPayableService._classify_item_bucket(
            ap_row, hand_carry_upcs=set(), creation_month='2026-05',
        ) is None
        assert CommissionService._classify_item_rate(
            row=comm_row, rates=STANDARD_RATES, achievement_rate=80,
            hand_carry_upcs=set(),
            ot_qualifying_sale_ids=set(), ot_blended_rates_by_sale_id={},
            hea_as_fashion_period=False,
        ) is None

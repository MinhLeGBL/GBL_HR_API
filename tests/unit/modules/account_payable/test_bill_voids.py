"""
Unit tests for AccountPayableService — CR #68 bill voids.

Covers:
- `void_remaining` validation + happy path + edge cases
- `_apply_voids_to_bills` derived `total_voided` / `remaining_unpaid` / `status`
- Bill shape includes `total_voided` (BillRow) and `voids[]` (BillDetail)
- Reconcile cumulative cap incorporates voids
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.modules.account_payable.service import AccountPayableService


FROZEN_TODAY = date(2026, 6, 4)


# ----------------------------------------------------------------------
# Fixtures — bare Oracle replay output (pre-_apply_voids_to_bills)
# ----------------------------------------------------------------------

def _raw_bill(
    bill_sid, doc_no, customer_sid, customer_name,
    original, paid, status, created_date, post_month,
    sale_total_amt=None, payments=(),
):
    """Bill shape as `repository.get_all_bills` returns it (BEFORE the
    service-layer `_apply_voids_to_bills` pass). Tests can let the
    service compute `remaining_unpaid`/`status` from these numbers."""
    return {
        'doc_no': doc_no, 'doc_store_code': 'HBT',
        'customer_sid': customer_sid, 'customer_name': customer_name,
        'original_charge': original,
        'total_paid': paid,
        'remaining_unpaid': max(0, original - paid),
        'status': status,
        'created_date': created_date,
        'last_payment_date': payments[-1]['payment_date'] if payments else None,
        'sale_total_amt': sale_total_amt if sale_total_amt is not None else int(original * 1.1),
        'post_month': post_month,
        'payments': list(payments),
    }


@pytest.fixture
def sample_bills():
    return {
        '1001': _raw_bill(
            '1001', 'D-1001', '111', 'Alice',
            original=100_000_000, paid=60_000_000, status='partial',
            created_date='2026-04-10', post_month='2026-04',
        ),
        '1002': _raw_bill(
            '1002', 'D-1002', '111', 'Alice',
            original=50_000_000, paid=0, status='open',
            created_date='2026-04-15', post_month='2026-04',
        ),
        '1003': _raw_bill(
            '1003', 'D-1003', '222', 'Bob',
            original=30_000_000, paid=30_000_000, status='fully_paid',
            created_date='2026-03-01', post_month='2026-03',
        ),
    }


@pytest.fixture
def mock_repo(sample_bills):
    repo = MagicMock()
    repo.get_all_bills.return_value = sample_bills
    repo.get_bill_items.return_value = []
    repo.get_charge_payments.return_value = []
    return repo


@pytest.fixture(autouse=True)
def freeze_today():
    with patch(
        'app.modules.account_payable.service._today', return_value=FROZEN_TODAY,
    ):
        yield


# ----------------------------------------------------------------------
# _apply_voids_to_bills — derived shape
# ----------------------------------------------------------------------

class TestApplyVoids:

    def _service_with_voids(self, mock_repo, voids_by_bill):
        svc = AccountPayableService(repository=mock_repo)
        # Patch the rate + manual loaders so _load_bills doesn't touch Postgres.
        # `_load_voids_by_bill_sid` is the one we want to control directly.
        svc._load_manual_allocations = MagicMock(return_value={})
        svc._load_voids_by_bill_sid = MagicMock(return_value=voids_by_bill)
        return svc

    def test_no_voids_leaves_bill_unchanged(self, mock_repo):
        svc = self._service_with_voids(mock_repo, voids_by_bill={})
        bills = svc._load_bills()
        # Status preserved from repo output; total_voided=0 attached.
        assert bills['1001']['total_voided'] == 0
        assert bills['1001']['remaining_unpaid'] == 40_000_000
        assert bills['1001']['status'] == 'partial'
        assert bills['1001']['voids_internal'] == []

    def test_partial_void_keeps_status_partial(self, mock_repo):
        # 100M bill, 60M paid → 40M remaining. Void 10M → 30M remaining.
        svc = self._service_with_voids(mock_repo, voids_by_bill={
            '1001': [{
                'void_id': '1', 'amount': 10_000_000, 'reason': None,
                'voided_at': '2026-06-01', 'voided_by': 'finance@example.com',
            }],
        })
        bills = svc._load_bills()
        assert bills['1001']['total_voided'] == 10_000_000
        assert bills['1001']['remaining_unpaid'] == 30_000_000
        assert bills['1001']['status'] == 'partial'

    def test_full_void_of_remaining_flips_to_fully_paid(self, mock_repo):
        # 100M bill, 60M paid → 40M remaining. Void exactly 40M → 0 remaining.
        svc = self._service_with_voids(mock_repo, voids_by_bill={
            '1001': [{
                'void_id': '1', 'amount': 40_000_000, 'reason': 'VIP discount',
                'voided_at': '2026-06-01', 'voided_by': 'finance@example.com',
            }],
        })
        bills = svc._load_bills()
        assert bills['1001']['total_voided'] == 40_000_000
        assert bills['1001']['remaining_unpaid'] == 0
        assert bills['1001']['status'] == 'fully_paid'

    def test_void_only_open_bill_becomes_partial(self, mock_repo):
        # 50M bill, 0 paid. Void 20M (operator marks part as gift before
        # any payment). 30M remaining; status flips open → partial because
        # SOME write-off exists.
        svc = self._service_with_voids(mock_repo, voids_by_bill={
            '1002': [{
                'void_id': '1', 'amount': 20_000_000, 'reason': 'goodwill',
                'voided_at': '2026-06-02', 'voided_by': 'finance@example.com',
            }],
        })
        bills = svc._load_bills()
        assert bills['1002']['total_voided'] == 20_000_000
        assert bills['1002']['remaining_unpaid'] == 30_000_000
        assert bills['1002']['status'] == 'partial'

    def test_full_writeoff_of_open_bill_becomes_fully_paid(self, mock_repo):
        # 50M bill, 0 paid. Void the entire 50M → 0 remaining, status fully_paid.
        svc = self._service_with_voids(mock_repo, voids_by_bill={
            '1002': [{
                'void_id': '1', 'amount': 50_000_000, 'reason': 'full write-off',
                'voided_at': '2026-06-02', 'voided_by': 'finance@example.com',
            }],
        })
        bills = svc._load_bills()
        assert bills['1002']['total_voided'] == 50_000_000
        assert bills['1002']['remaining_unpaid'] == 0
        assert bills['1002']['status'] == 'fully_paid'

    def test_multiple_voids_sum(self, mock_repo):
        # 100M bill, 60M paid → 40M remaining. Two partial voids 10M + 15M.
        # total_voided = 25M, remaining_unpaid = 15M.
        svc = self._service_with_voids(mock_repo, voids_by_bill={
            '1001': [
                {'void_id': '2', 'amount': 15_000_000, 'reason': 'second void',
                 'voided_at': '2026-06-03', 'voided_by': 'finance@example.com'},
                {'void_id': '1', 'amount': 10_000_000, 'reason': 'first void',
                 'voided_at': '2026-06-01', 'voided_by': 'finance@example.com'},
            ],
        })
        bills = svc._load_bills()
        assert bills['1001']['total_voided'] == 25_000_000
        assert bills['1001']['remaining_unpaid'] == 15_000_000
        assert bills['1001']['status'] == 'partial'
        # Voids list preserved order from _load_voids_by_bill_sid (newest-first).
        assert [v['void_id'] for v in bills['1001']['voids_internal']] == ['2', '1']


# ----------------------------------------------------------------------
# void_remaining — validation
# ----------------------------------------------------------------------

class TestVoidRemainingValidation:

    @pytest.fixture
    def svc(self, mock_repo):
        s = AccountPayableService(repository=mock_repo)
        s._load_manual_allocations = MagicMock(return_value={})
        s._load_voids_by_bill_sid = MagicMock(return_value={})
        return s

    def test_non_digit_bill_sid_rejected(self, svc):
        r = svc.void_remaining('abc', 1_000, None, 'op@example.com')
        assert r['success'] is False
        assert 'numeric' in r['error']

    def test_non_positive_amount_rejected(self, svc):
        for bad in (0, -1, 'oops', None, 1.5):
            r = svc.void_remaining('1001', bad, None, 'op@example.com')  # type: ignore[arg-type]
            assert r['success'] is False, f'should reject {bad!r}'
            assert 'positive' in r['error'] or 'int' in r['error']

    def test_non_string_reason_rejected(self, svc):
        r = svc.void_remaining('1001', 1_000, 42, 'op@example.com')  # type: ignore[arg-type]
        assert r['success'] is False
        assert 'reason' in r['error']

    def test_empty_voided_by_rejected(self, svc):
        r = svc.void_remaining('1001', 1_000, None, '')
        assert r['success'] is False
        assert 'voided_by' in r['error']

    def test_unknown_bill_returns_not_found(self, svc):
        r = svc.void_remaining('99999', 1_000, None, 'op@example.com')
        assert r['success'] is False
        assert r.get('not_found') is True

    def test_amount_exceeds_remaining_rejected(self, svc):
        # 1001 has 100M bill, 60M paid → 40M remaining. 50M void should fail.
        r = svc.void_remaining('1001', 50_000_000, None, 'op@example.com')
        assert r['success'] is False
        assert '50,000,000' in r['error']
        assert '40,000,000' in r['error']

    def test_void_on_fully_paid_bill_rejected(self, svc):
        # 1003 is fully paid → remaining = 0. Any void should fail.
        r = svc.void_remaining('1003', 1_000, None, 'op@example.com')
        assert r['success'] is False
        assert 'no remaining' in r['error']


# ----------------------------------------------------------------------
# void_remaining — happy path
# ----------------------------------------------------------------------

class TestVoidRemainingHappyPath:

    @pytest.fixture
    def svc(self, mock_repo):
        s = AccountPayableService(repository=mock_repo)
        s._load_manual_allocations = MagicMock(return_value={})
        # First call: empty (pre-void). Second call (inside get_bill_detail
        # after INSERT): one void row.
        s._load_voids_by_bill_sid = MagicMock(side_effect=[
            {},
            {'1001': [{
                'void_id': '1', 'amount': 20_000_000,
                'reason': 'VIP discount',
                'voided_at': '2026-06-04',
                'voided_by': 'finance@example.com',
            }]},
        ])
        s._load_auto_rates = MagicMock(return_value={})
        s._load_custom_rates = MagicMock(return_value={})
        return s

    @patch('app.modules.account_payable.service.get_postgres_connection')
    def test_writes_row_and_returns_updated_bill(self, mock_conn, svc):
        cur = MagicMock()
        cur.fetchone.return_value = (1,)   # void_id returned by RETURNING
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        mock_conn.return_value = conn

        r = svc.void_remaining(
            '1001', 20_000_000, 'VIP discount', 'finance@example.com',
        )
        assert r['success'] is True
        # INSERT executed with the 4-tuple shape we expect.
        insert_args = cur.execute.call_args.args
        assert 'INSERT INTO payable_bill_voids' in insert_args[0]
        assert insert_args[1] == ('1001', 20_000_000, 'VIP discount',
                                  'finance@example.com')
        conn.commit.assert_called_once()

        # Response embeds the post-void BillDetail + void_id.
        assert r['data']['void_id'] == '1'
        bill = r['data']['bill']
        # The second _load_voids_by_bill_sid call returned the inserted row,
        # so the bill detail reflects total_voided=20M, remaining=20M.
        assert bill['total_voided'] == 20_000_000
        assert bill['remaining_unpaid'] == 20_000_000
        assert bill['status'] == 'partial'
        # voids array surfaced in BillDetail (CR #68).
        assert len(bill['voids']) == 1
        assert bill['voids'][0]['amount'] == 20_000_000
        assert bill['voids'][0]['reason'] == 'VIP discount'
        assert bill['voids'][0]['voided_by'] == 'finance@example.com'


# ----------------------------------------------------------------------
# Bill shape — `total_voided` is always present, `voids` only on detail
# ----------------------------------------------------------------------

class TestBillShape:

    def test_total_voided_default_zero_on_bill_row(self, mock_repo):
        svc = AccountPayableService(repository=mock_repo)
        svc._load_manual_allocations = MagicMock(return_value={})
        svc._load_voids_by_bill_sid = MagicMock(return_value={})
        result = svc.get_bills()
        assert result['success']
        for row in result['data']:
            assert 'total_voided' in row
            assert row['total_voided'] == 0
            # `voids` is detail-only, not on BillRow.
            assert 'voids' not in row

    def test_bill_detail_includes_voids_array(self, mock_repo):
        svc = AccountPayableService(repository=mock_repo)
        svc._load_manual_allocations = MagicMock(return_value={})
        svc._load_voids_by_bill_sid = MagicMock(return_value={
            '1001': [{
                'void_id': '7', 'amount': 5_000_000, 'reason': 'gift',
                'voided_at': '2026-06-01', 'voided_by': 'finance@example.com',
            }],
        })
        svc._load_auto_rates = MagicMock(return_value={})
        svc._load_custom_rates = MagicMock(return_value={})
        result = svc.get_bill_detail('1001')
        assert result['success']
        detail = result['data']
        assert detail['total_voided'] == 5_000_000
        assert detail['remaining_unpaid'] == 35_000_000   # 40M - 5M
        assert len(detail['voids']) == 1
        assert detail['voids'][0]['void_id'] == '7'


# ----------------------------------------------------------------------
# Reconcile cumulative cap — must include voids
# ----------------------------------------------------------------------

class TestReconcileCapWithVoids:

    @pytest.fixture
    def svc(self, mock_repo):
        s = AccountPayableService(repository=mock_repo)
        s._load_manual_allocations = MagicMock(return_value={})
        # 5M voided on bill 1001 → effective cap = 100M - 5M = 95M.
        s._load_voids_by_bill_sid = MagicMock(return_value={
            '1001': [{
                'void_id': '1', 'amount': 5_000_000, 'reason': 'goodwill',
                'voided_at': '2026-06-01', 'voided_by': 'finance@example.com',
            }],
        })
        return s

    @patch('app.modules.account_payable.service.get_postgres_connection')
    def test_cap_check_subtracts_voids(self, mock_conn, svc, mock_repo):
        cur = MagicMock()
        cur.fetchall.return_value = []
        # Other-payments baseline = 90M against this bill.
        cur.fetchone.return_value = (90_000_000,)
        cur.rowcount = 0
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        mock_conn.return_value = conn
        mock_repo.get_payment_meta.return_value = {
            'doc_sid': '9001', 'doc_no': 'P-1', 'customer_sid': '111',
            'customer_name': 'Alice', 'doc_store_code': 'HBT',
            'amount': 10_000_000, 'payment_date': '2026-04-30',
            'notes_lostdoc': None, 'ref_sale_sid': None,
        }
        # Existing baseline: 90M from other payments. Voided: 5M. Effective
        # cap: 100M - 5M = 95M. New 10M would push to 100M → overflow by 5M.
        r = svc.reconcile('9001', [{'bill_sid': '1001', 'amount': 10_000_000}])
        assert r['success'] is False
        assert 'available cap' in r['error']
        assert '5,000,000' in r['error']

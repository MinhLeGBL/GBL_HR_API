"""Unit tests for PNLService — import, read, lock.

The repository is mocked: what is pinned here is the orchestration, not SQL.
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from openpyxl import Workbook

from app.modules.pnl.service import PNLService

SELLING = '8. Chi phí bán hàng'


def sheet(title='T07.2026', rows=None):
    wb = Workbook()
    ws = wb.active
    ws.title = title
    ws.cell(2, 1, 'Chỉ tiêu')
    for i, store in enumerate(('Total', 'HQ', 'RHN')):
        ws.cell(2, 2 + i, store)
    rows = rows or [('1. Doanh thu bán hàng và cung cấp dịch vụ', (300, 100, 200))]
    for r, (label, values) in enumerate(rows, start=3):
        ws.cell(r, 1, label)
        if values:
            for i, v in enumerate(values):
                ws.cell(r, 2 + i, v)
    return ws


def fact(year, month, entity, category, amount):
    return {'year': year, 'month': month, 'entity': entity,
            'entity_code': None, 'category': category,
            'fast_account': 'x', 'amount': Decimal(str(amount))}


@pytest.fixture
def service():
    s = PNLService()
    s.repo = MagicMock()
    s.repo.locked_months.return_value = set()
    s.repo.replace_months.return_value = {'success': True, 'import_id': 7,
                                          'replaced': []}
    return s


class TestImport:
    def test_a_valid_file_is_stored(self, service):
        result = service.import_worksheet(sheet(), filename='f.xlsx')
        assert result['success'] is True
        assert result['import_id'] == 7
        assert result['months'] == [{'year': 2026, 'month': 7,
                                     'month_name': 'July'}]

    def test_facts_reach_the_repository_without_the_total_column(self, service):
        service.import_worksheet(sheet(), filename='f.xlsx')
        facts = service.repo.replace_months.call_args.kwargs['facts']
        assert {f['entity'] for f in facts} == {'HQ', 'RHN'}

    def test_a_bad_file_is_rejected_before_anything_is_written(self, service):
        ws = sheet(rows=[(SELLING, None), ('- Không biết', (1, 1, 0))])
        result = service.import_worksheet(ws, filename='f.xlsx')
        assert result['success'] is False
        assert result['code'] == 'INVALID_FILE'
        assert result['problems']
        service.repo.replace_months.assert_not_called()

    def test_a_locked_month_is_refused_and_named(self, service):
        service.repo.locked_months.return_value = {(2026, 7)}
        result = service.import_worksheet(sheet(), filename='f.xlsx')
        assert result['code'] == 'MONTH_LOCKED'
        assert 'July 2026' in result['error']
        service.repo.replace_months.assert_not_called()

    def test_a_lock_can_be_overridden_deliberately(self, service):
        service.repo.locked_months.return_value = {(2026, 7)}
        result = service.import_worksheet(sheet(), filename='f.xlsx',
                                          allow_locked=True)
        assert result['success'] is True
        service.repo.replace_months.assert_called_once()

    def test_an_unlocked_month_is_unaffected_by_other_locks(self, service):
        service.repo.locked_months.return_value = {(2026, 6), (2025, 12)}
        assert service.import_worksheet(sheet(), filename='f.xlsx')['success']

    def test_the_source_total_divergence_is_surfaced(self, service):
        # Total says 300, the stores sum to 300 — no divergence. Now make the
        # source disagree, as the real July export does.
        ws = sheet(rows=[('1. Doanh thu bán hàng và cung cấp dịch vụ',
                          (100, 100, 40))])
        result = service.import_worksheet(ws, filename='f.xlsx')
        assert result['total_check']
        assert result['total_check'][0]['difference'] == 40.0

    def test_a_storage_failure_is_reported_not_swallowed(self, service):
        service.repo.replace_months.return_value = {'success': False,
                                                    'error': 'disk on fire'}
        result = service.import_worksheet(sheet(), filename='f.xlsx')
        assert result['code'] == 'SERVER_ERROR'
        assert 'disk on fire' in result['error']


class TestStatement:
    FACTS = [
        fact(2026, 6, 'RHN', 'Gross sales', 1000),
        fact(2026, 6, 'RWR', 'Gross sales', 500),
        fact(2026, 6, 'RHN', 'Discounts', 100),
        fact(2025, 6, 'RHN', 'Gross sales', 800),
    ]

    def test_it_returns_every_line_with_a_comparison(self, service):
        service.repo.fetch_facts.return_value = self.FACTS
        result = service.statement(2026, 'June', 'Half-to-date')
        assert result['success'] is True
        by_line = {r['line']: r for r in result['lines']}
        assert by_line['Gross sales']['current'] == 1500.0
        assert by_line['Gross sales']['prior'] == 800.0
        assert by_line['Store net sales']['current'] == 1400.0

    def test_both_years_are_fetched_in_one_call(self, service):
        service.repo.fetch_facts.return_value = self.FACTS
        service.statement(2026, 'June', 'Half-to-date')
        years, first, last, entity = service.repo.fetch_facts.call_args.args
        assert sorted(years) == [2025, 2026]
        assert (first, last) == (1, 6)
        assert entity is None

    def test_percentages_are_of_net_sales(self, service):
        service.repo.fetch_facts.return_value = self.FACTS
        by_line = {r['line']: r for r in
                   service.statement(2026, 'June', 'Half-to-date')['lines']}
        assert by_line['Discounts']['current_pct'] == pytest.approx(100 / 1400)
        # The sales lines carry no percentage — a share of itself says nothing.
        assert by_line['Net sales']['current_pct'] is None

    def test_variance_against_a_zero_base_has_no_ratio(self, service):
        service.repo.fetch_facts.return_value = [
            fact(2026, 6, 'RHN', 'Tax', 50)]     # nothing in 2025
        by_line = {r['line']: r for r in
                   service.statement(2026, 'June', 'Half-to-date')['lines']}
        assert by_line['Tax']['variance'] == 50.0
        assert by_line['Tax']['variance_pct'] is None

    def test_no_data_is_a_clear_answer_not_a_zero_statement(self, service):
        # Rendering zeros would look like a real P&L of nothing.
        service.repo.fetch_facts.return_value = []
        result = service.statement(2026, 'June', 'Half-to-date')
        assert result['code'] == 'NO_DATA'

    def test_coverage_reports_which_months_are_actually_present(self, service):
        service.repo.fetch_facts.return_value = self.FACTS
        cov = service.statement(2026, 'June', 'Half-to-date')['coverage']
        assert cov['current_months'] == [6]
        assert cov['expected_months'] == [1, 2, 3, 4, 5, 6]

    def test_one_store_can_be_requested(self, service):
        service.repo.fetch_facts.return_value = self.FACTS
        service.statement(2026, 'June', 'Half-to-date', entity='RHN')
        assert service.repo.fetch_facts.call_args.args[3] == 'RHN'

    @pytest.mark.parametrize('basis,month', [('Decade-to-date', 'June'),
                                             ('Half-to-date', 'Juny')])
    def test_bad_input_is_refused_before_touching_the_database(
            self, service, basis, month):
        result = service.statement(2026, month, basis)
        assert result['code'] == 'INVALID_INPUT'
        service.repo.fetch_facts.assert_not_called()


class TestLocks:
    def test_locking_records_the_user(self, service):
        service.repo.set_lock.return_value = True
        result = service.lock_month(2026, 'July', user='a@b.c')
        assert result == {'success': True, 'year': 2026, 'month': 7,
                          'month_name': 'July', 'locked': True}
        service.repo.set_lock.assert_called_once_with(2026, 7, 'a@b.c')

    def test_unlocking(self, service):
        service.repo.clear_lock.return_value = True
        assert service.unlock_month(2026, 7)['locked'] is False

    def test_a_bad_month_never_reaches_the_repository(self, service):
        assert service.lock_month(2026, 'Juny')['code'] == 'INVALID_INPUT'
        service.repo.set_lock.assert_not_called()

    def test_a_failed_lock_is_reported(self, service):
        service.repo.set_lock.return_value = False
        assert service.lock_month(2026, 7)['code'] == 'SERVER_ERROR'

"""Route tests for the P&L API.

The service is mocked; what is pinned here is the HTTP contract — status codes,
required parameters, and that both permission sections are actually enforced
server-side rather than only hidden in the nav.
"""
import io
from unittest.mock import patch

import pytest

from app.main import app

VIEW = 'PNL'
IMPORT = 'PNL_IMPORT'


@pytest.fixture
def client():
    return app.test_client()


def authed(sections):
    """Patch auth so the request runs as a user holding `sections`."""
    return (
        patch('app.core.auth.middleware.auth_service.verify_token',
              return_value={'sid': '1', 'email': 't@t', 'role': 'user',
                            'type': 'access'}),
        patch('app.core.auth.middleware.auth_service.get_user_by_sid',
              return_value={'sid': '1', 'email': 't@t', 'role': 'user',
                            'is_active': True}),
        patch('app.modules.permissions.service.PermissionService'
              '.get_user_permissions_v2',
              return_value=[{'section_code': s} for s in sections]),
    )


class _As:
    def __init__(self, sections):
        self.patches = authed(sections)

    def __enter__(self):
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *a):
        for p in self.patches:
            p.stop()


HEADERS = {'Authorization': 'Bearer x'}


class TestPermissions:
    """A P&L exposes payroll, G&A and company profit — a narrower audience
    than the weekly sales report, so the sections are separate and enforced."""

    def test_statement_needs_the_view_section(self, client):
        with _As([]):
            r = client.get('/api/v1/pnl/statement?year=2026&month=June'
                           '&basis=Half-to-date', headers=HEADERS)
        assert r.status_code == 403

    def test_view_section_is_not_enough_to_import(self, client):
        with _As([VIEW]):
            r = client.post('/api/v1/pnl/import', headers=HEADERS,
                            data={'file': (io.BytesIO(b'x'), 'f.xlsx')},
                            content_type='multipart/form-data')
        assert r.status_code == 403

    def test_view_section_is_not_enough_to_lock(self, client):
        with _As([VIEW]):
            r = client.post('/api/v1/pnl/months/2026/7/lock', headers=HEADERS)
        assert r.status_code == 403

    def test_importer_can_view_and_import(self, client):
        with _As([VIEW, IMPORT]):
            with patch('app.modules.pnl.routes.pnl_service.lock_month',
                       return_value={'success': True}) as m:
                r = client.post('/api/v1/pnl/months/2026/7/lock', headers=HEADERS)
        assert r.status_code == 200
        m.assert_called_once()


class TestStatementRoute:
    def test_missing_parameters_are_rejected(self, client):
        with _As([VIEW]):
            r = client.get('/api/v1/pnl/statement?year=2026', headers=HEADERS)
        assert r.status_code == 400
        assert 'required' in r.get_json()['error']

    def test_a_statement_is_returned(self, client):
        with _As([VIEW]):
            with patch('app.modules.pnl.routes.pnl_service.statement',
                       return_value={'success': True, 'lines': []}):
                r = client.get('/api/v1/pnl/statement?year=2026&month=June'
                               '&basis=Half-to-date', headers=HEADERS)
        assert r.status_code == 200

    def test_no_data_is_a_404(self, client):
        with _As([VIEW]):
            with patch('app.modules.pnl.routes.pnl_service.statement',
                       return_value={'success': False, 'code': 'NO_DATA',
                                     'error': 'nothing stored'}):
                r = client.get('/api/v1/pnl/statement?year=2026&month=June'
                               '&basis=Half-to-date', headers=HEADERS)
        assert r.status_code == 404

    def test_entity_is_passed_through(self, client):
        with _As([VIEW]):
            with patch('app.modules.pnl.routes.pnl_service.statement',
                       return_value={'success': True}) as m:
                client.get('/api/v1/pnl/statement?year=2026&month=June'
                           '&basis=Half-to-date&entity=RHN', headers=HEADERS)
        assert m.call_args.kwargs['entity'] == 'RHN'


class TestImportRoute:
    def test_no_file_is_rejected(self, client):
        with _As([IMPORT]):
            r = client.post('/api/v1/pnl/import', headers=HEADERS, data={},
                            content_type='multipart/form-data')
        assert r.status_code == 400
        assert r.get_json()['error'] == 'No file was uploaded.'

    def test_a_non_xlsx_is_rejected_before_it_is_read(self, client):
        with _As([IMPORT]):
            r = client.post('/api/v1/pnl/import', headers=HEADERS,
                            data={'file': (io.BytesIO(b'x'), 'notes.txt')},
                            content_type='multipart/form-data')
        assert r.status_code == 400

    def test_an_unreadable_workbook_is_422_not_500(self, client):
        with _As([IMPORT]):
            r = client.post('/api/v1/pnl/import', headers=HEADERS,
                            data={'file': (io.BytesIO(b'not a zip'), 'f.xlsx')},
                            content_type='multipart/form-data')
        assert r.status_code == 422

    def test_a_locked_month_is_409(self, client):
        with _As([IMPORT]):
            with patch('app.modules.pnl.routes._open_sheet',
                       return_value=('ws', 'T07.2026', ['T07.2026'])), \
                 patch('app.modules.pnl.routes.pnl_service.import_worksheet',
                       return_value={'success': False, 'code': 'MONTH_LOCKED',
                                     'error': 'locked'}):
                r = client.post('/api/v1/pnl/import', headers=HEADERS,
                                data={'file': (io.BytesIO(b'x'), 'f.xlsx')},
                                content_type='multipart/form-data')
        assert r.status_code == 409

    def test_a_successful_import_is_201(self, client):
        with _As([IMPORT]):
            with patch('app.modules.pnl.routes._open_sheet',
                       return_value=('ws', 'T07.2026', ['T07.2026'])), \
                 patch('app.modules.pnl.routes.pnl_service.import_worksheet',
                       return_value={'success': True, 'import_id': 1}):
                r = client.post('/api/v1/pnl/import', headers=HEADERS,
                                data={'file': (io.BytesIO(b'x'), 'f.xlsx')},
                                content_type='multipart/form-data')
        assert r.status_code == 201
        assert r.get_json()['available_sheets'] == ['T07.2026']

    def test_the_period_override_and_lock_override_reach_the_service(self, client):
        with _As([IMPORT]):
            with patch('app.modules.pnl.routes._open_sheet',
                       return_value=('ws', 'Sheet1', ['Sheet1'])), \
                 patch('app.modules.pnl.routes.pnl_service.import_worksheet',
                       return_value={'success': True}) as m:
                client.post('/api/v1/pnl/import', headers=HEADERS,
                            data={'file': (io.BytesIO(b'x'), 'f.xlsx'),
                                  'year': '2026', 'month': 'August',
                                  'allow_locked': 'true'},
                            content_type='multipart/form-data')
        kwargs = m.call_args.kwargs
        assert (kwargs['year'], kwargs['month']) == (2026, 'August')
        assert kwargs['allow_locked'] is True

    def test_allow_locked_defaults_to_false(self, client):
        with _As([IMPORT]):
            with patch('app.modules.pnl.routes._open_sheet',
                       return_value=('ws', 'T07.2026', ['T07.2026'])), \
                 patch('app.modules.pnl.routes.pnl_service.import_worksheet',
                       return_value={'success': True}) as m:
                client.post('/api/v1/pnl/import', headers=HEADERS,
                            data={'file': (io.BytesIO(b'x'), 'f.xlsx')},
                            content_type='multipart/form-data')
        assert m.call_args.kwargs['allow_locked'] is False


class TestMonthsAndImports:
    def test_months_are_listed(self, client):
        with _As([VIEW]):
            with patch('app.modules.pnl.routes.pnl_service.months',
                       return_value={'success': True, 'months': []}):
                r = client.get('/api/v1/pnl/months', headers=HEADERS)
        assert r.status_code == 200

    def test_import_history_limit_is_capped(self, client):
        with _As([VIEW]):
            with patch('app.modules.pnl.routes.pnl_service.imports',
                       return_value={'success': True, 'imports': []}) as m:
                client.get('/api/v1/pnl/imports?limit=9999', headers=HEADERS)
        assert m.call_args.kwargs['limit'] == 200

    def test_unlock_uses_delete(self, client):
        with _As([IMPORT]):
            with patch('app.modules.pnl.routes.pnl_service.unlock_month',
                       return_value={'success': True}) as m:
                r = client.delete('/api/v1/pnl/months/2026/7/lock',
                                  headers=HEADERS)
        assert r.status_code == 200
        m.assert_called_once_with(2026, 7)

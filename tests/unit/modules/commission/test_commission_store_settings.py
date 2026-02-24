"""
Unit tests for commission store settings endpoints and service

Tests:
- GET  /api/v1/commission/stores?month=M&year=Y
- PUT  /api/v1/commission/stores/<store_code>
- CommissionStoreSettingsService.init_database()
- CommissionStoreSettingsService.get_commission_stores()
- CommissionStoreSettingsService.update_commission_store_settings()
"""
import pytest
from unittest.mock import patch, MagicMock


def _auth_headers():
    return {'Authorization': 'Bearer testtoken'}


# ===========================================================================
# 1. GET /api/v1/commission/stores
# ===========================================================================

class TestGetCommissionStores:
    URL = '/api/v1/commission/stores'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionStoreSettingsService')
    def test_success(self, MockService, mock_get_user, mock_verify, client):
        mock_verify.return_value = {'sid': 1, 'email': 'x@x.com', 'role': 'admin', 'type': 'access'}
        mock_get_user.return_value = None

        mock_svc = MockService.return_value
        mock_svc.get_commission_stores.return_value = {
            'success': True,
            'month': 2,
            'year': 2026,
            'stores': [
                {'store_code': 'RWT', 'store_name': 'Retail West T', 'store_target': 5000000000, 'fp_ratio_target': 60},
                {'store_code': 'RWR', 'store_name': 'Retail West R', 'store_target': None, 'fp_ratio_target': None},
            ]
        }

        resp = client.get(self.URL + '?month=2&year=2026', headers=_auth_headers())

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['month'] == 2
        assert body['year'] == 2026
        assert len(body['stores']) == 2
        assert body['stores'][0]['store_code'] == 'RWT'
        assert body['stores'][0]['fp_ratio_target'] == 60
        assert body['stores'][1]['store_target'] is None
        mock_svc.get_commission_stores.assert_called_once_with(2, 2026)

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_month(self, mock_get_user, mock_verify, client):
        mock_verify.return_value = {'sid': 1, 'email': 'x@x.com', 'role': 'admin', 'type': 'access'}
        mock_get_user.return_value = None

        resp = client.get(self.URL + '?year=2026', headers=_auth_headers())

        assert resp.status_code == 400
        assert 'month' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_year(self, mock_get_user, mock_verify, client):
        mock_verify.return_value = {'sid': 1, 'email': 'x@x.com', 'role': 'admin', 'type': 'access'}
        mock_get_user.return_value = None

        resp = client.get(self.URL + '?month=2', headers=_auth_headers())

        assert resp.status_code == 400
        assert 'year' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_invalid_month(self, mock_get_user, mock_verify, client):
        mock_verify.return_value = {'sid': 1, 'email': 'x@x.com', 'role': 'admin', 'type': 'access'}
        mock_get_user.return_value = None

        resp = client.get(self.URL + '?month=13&year=2026', headers=_auth_headers())

        assert resp.status_code == 400
        assert 'month must be between 1 and 12' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_non_integer_params(self, mock_get_user, mock_verify, client):
        mock_verify.return_value = {'sid': 1, 'email': 'x@x.com', 'role': 'admin', 'type': 'access'}
        mock_get_user.return_value = None

        resp = client.get(self.URL + '?month=abc&year=2026', headers=_auth_headers())

        assert resp.status_code == 400
        assert 'integer' in resp.get_json()['error']

    def test_no_auth_token(self, client):
        resp = client.get(self.URL + '?month=2&year=2026')
        assert resp.status_code == 401

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionStoreSettingsService')
    def test_service_failure_returns_500(self, MockService, mock_get_user, mock_verify, client):
        mock_verify.return_value = {'sid': 1, 'email': 'x@x.com', 'role': 'admin', 'type': 'access'}
        mock_get_user.return_value = None

        mock_svc = MockService.return_value
        mock_svc.get_commission_stores.return_value = {
            'success': False,
            'error': 'DB connection failed'
        }

        resp = client.get(self.URL + '?month=2&year=2026', headers=_auth_headers())

        assert resp.status_code == 500
        assert resp.get_json()['success'] is False


# ===========================================================================
# 2. PUT /api/v1/commission/stores/<store_code>
# ===========================================================================

def _put_payload(**overrides):
    payload = {
        'month': 2,
        'year': 2026,
        'store_target': 5000000000,
        'fp_ratio_target': 60
    }
    payload.update(overrides)
    return payload


class TestUpdateCommissionStore:
    URL = '/api/v1/commission/stores/RWT'

    def _mock_manager(self, mock_verify, mock_get_user):
        mock_verify.return_value = {'sid': 1, 'email': 'x@x.com', 'role': 'manager', 'type': 'access'}
        mock_get_user.return_value = None

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionStoreSettingsService')
    def test_success(self, MockService, mock_get_user, mock_verify, client):
        self._mock_manager(mock_verify, mock_get_user)

        mock_svc = MockService.return_value
        mock_svc.update_commission_store_settings.return_value = {
            'success': True,
            'data': {
                'store_code': 'RWT',
                'month': 2,
                'year': 2026,
                'store_target': 5000000000,
                'fp_ratio_target': 60
            }
        }

        resp = client.put(self.URL, json=_put_payload(), headers=_auth_headers())

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['data']['store_code'] == 'RWT'
        assert body['data']['fp_ratio_target'] == 60
        mock_svc.update_commission_store_settings.assert_called_once_with(
            store_code='RWT',
            month=2,
            year=2026,
            store_target=5000000000,
            fp_ratio_target=60
        )

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionStoreSettingsService')
    def test_nullable_fields_accepted(self, MockService, mock_get_user, mock_verify, client):
        self._mock_manager(mock_verify, mock_get_user)

        mock_svc = MockService.return_value
        mock_svc.update_commission_store_settings.return_value = {
            'success': True,
            'data': {
                'store_code': 'RWT',
                'month': 2,
                'year': 2026,
                'store_target': None,
                'fp_ratio_target': None
            }
        }

        resp = client.put(self.URL, json={'month': 2, 'year': 2026}, headers=_auth_headers())

        assert resp.status_code == 200
        mock_svc.update_commission_store_settings.assert_called_once_with(
            store_code='RWT',
            month=2,
            year=2026,
            store_target=None,
            fp_ratio_target=None
        )

    def test_no_auth_token(self, client):
        resp = client.put(self.URL, json=_put_payload())
        assert resp.status_code == 401

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_staff_role_forbidden(self, mock_get_user, mock_verify, client):
        mock_verify.return_value = {'sid': 1, 'email': 'x@x.com', 'role': 'staff', 'type': 'access'}
        mock_get_user.return_value = None

        resp = client.put(self.URL, json=_put_payload(), headers=_auth_headers())

        assert resp.status_code == 403

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_month(self, mock_get_user, mock_verify, client):
        self._mock_manager(mock_verify, mock_get_user)

        payload = _put_payload()
        del payload['month']
        resp = client.put(self.URL, json=payload, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'month' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_year(self, mock_get_user, mock_verify, client):
        self._mock_manager(mock_verify, mock_get_user)

        payload = _put_payload()
        del payload['year']
        resp = client.put(self.URL, json=payload, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'year' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_invalid_month(self, mock_get_user, mock_verify, client):
        self._mock_manager(mock_verify, mock_get_user)

        resp = client.put(self.URL, json=_put_payload(month=0), headers=_auth_headers())
        assert resp.status_code == 400
        assert 'month must be between 1 and 12' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_no_body(self, mock_get_user, mock_verify, client):
        self._mock_manager(mock_verify, mock_get_user)

        resp = client.put(self.URL, json={}, headers=_auth_headers())
        assert resp.status_code == 400
        assert 'required' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionStoreSettingsService')
    def test_store_not_found_returns_404(self, MockService, mock_get_user, mock_verify, client):
        self._mock_manager(mock_verify, mock_get_user)

        mock_svc = MockService.return_value
        mock_svc.update_commission_store_settings.return_value = {
            'success': False,
            'error': 'Store not found: UNKNOWN'
        }

        resp = client.put(self.URL, json=_put_payload(), headers=_auth_headers())

        assert resp.status_code == 404
        assert resp.get_json()['success'] is False

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionStoreSettingsService')
    def test_service_failure_returns_500(self, MockService, mock_get_user, mock_verify, client):
        self._mock_manager(mock_verify, mock_get_user)

        mock_svc = MockService.return_value
        mock_svc.update_commission_store_settings.return_value = {
            'success': False,
            'error': 'DB error'
        }

        resp = client.put(self.URL, json=_put_payload(), headers=_auth_headers())

        assert resp.status_code == 500
        assert resp.get_json()['success'] is False


# ===========================================================================
# 3. CommissionStoreSettingsService unit tests
# ===========================================================================

class TestCommissionStoreSettingsServiceInitDatabase:

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_success(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        from app.modules.commission.service import CommissionStoreSettingsService
        result = CommissionStoreSettingsService().init_database()

        assert result['success'] is True
        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_no_connection(self, mock_get_conn):
        mock_get_conn.return_value = None

        from app.modules.commission.service import CommissionStoreSettingsService
        result = CommissionStoreSettingsService().init_database()

        assert result['success'] is False
        assert 'PostgreSQL' in result['error']

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_db_exception_rolls_back(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception('syntax error')
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        from app.modules.commission.service import CommissionStoreSettingsService
        result = CommissionStoreSettingsService().init_database()

        assert result['success'] is False
        mock_conn.rollback.assert_called_once()
        mock_conn.close.assert_called_once()


class TestCommissionStoreSettingsServiceGetStores:

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_success_returns_all_stores(self, mock_get_conn):
        rows = [
            ('RWT', 'Retail West T', 5000000000, 60),
            ('RWR', 'Retail West R', None, None),
        ]
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = rows
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        from app.modules.commission.service import CommissionStoreSettingsService
        result = CommissionStoreSettingsService().get_commission_stores(2, 2026)

        assert result['success'] is True
        assert result['month'] == 2
        assert result['year'] == 2026
        assert len(result['stores']) == 2
        assert result['stores'][0] == {
            'store_code': 'RWT',
            'store_name': 'Retail West T',
            'store_target': 5000000000,
            'fp_ratio_target': 60
        }
        assert result['stores'][1]['store_target'] is None
        assert result['stores'][1]['fp_ratio_target'] is None

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_no_connection(self, mock_get_conn):
        mock_get_conn.return_value = None

        from app.modules.commission.service import CommissionStoreSettingsService
        result = CommissionStoreSettingsService().get_commission_stores(2, 2026)

        assert result['success'] is False

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_empty_stores(self, mock_get_conn):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        from app.modules.commission.service import CommissionStoreSettingsService
        result = CommissionStoreSettingsService().get_commission_stores(2, 2026)

        assert result['success'] is True
        assert result['stores'] == []


class TestCommissionStoreSettingsServiceUpdate:

    def _make_mock_conn(self, fetchone_side_effect=None, fetchone_return=None):
        mock_cursor = MagicMock()
        if fetchone_side_effect:
            mock_cursor.fetchone.side_effect = fetchone_side_effect
        elif fetchone_return is not None:
            mock_cursor.fetchone.return_value = fetchone_return
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        return mock_conn, mock_cursor

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_success(self, mock_get_conn):
        # First fetchone (store validation) returns a row; second (RETURNING) returns saved data
        returned_row = ('RWT', 2, 2026, 5000000000, 60)
        columns = ['store_code', 'month', 'year', 'store_target', 'fp_ratio_target']

        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [(1,), returned_row]
        mock_cursor.description = [(col,) for col in columns]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        from app.modules.commission.service import CommissionStoreSettingsService
        result = CommissionStoreSettingsService().update_commission_store_settings(
            store_code='RWT', month=2, year=2026,
            store_target=5000000000, fp_ratio_target=60
        )

        assert result['success'] is True
        assert result['data']['store_code'] == 'RWT'
        assert result['data']['fp_ratio_target'] == 60
        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_store_not_found(self, mock_get_conn):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None  # store validation fails
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        from app.modules.commission.service import CommissionStoreSettingsService
        result = CommissionStoreSettingsService().update_commission_store_settings(
            store_code='UNKNOWN', month=2, year=2026,
            store_target=None, fp_ratio_target=None
        )

        assert result['success'] is False
        assert 'not found' in result['error'].lower()

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_no_connection(self, mock_get_conn):
        mock_get_conn.return_value = None

        from app.modules.commission.service import CommissionStoreSettingsService
        result = CommissionStoreSettingsService().update_commission_store_settings(
            store_code='RWT', month=2, year=2026,
            store_target=5000000000, fp_ratio_target=60
        )

        assert result['success'] is False

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_db_exception_rolls_back(self, mock_get_conn):
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception('unique violation')
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        from app.modules.commission.service import CommissionStoreSettingsService
        result = CommissionStoreSettingsService().update_commission_store_settings(
            store_code='RWT', month=2, year=2026,
            store_target=5000000000, fp_ratio_target=60
        )

        assert result['success'] is False
        mock_conn.rollback.assert_called_once()
        mock_conn.close.assert_called_once()

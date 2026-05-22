"""Unit tests for handcarry routes."""
from unittest.mock import patch


def _auth_headers():
    return {'Authorization': 'Bearer testtoken'}


def _mock_auth(mock_verify, mock_get_user, role='admin', department_code='IT'):
    mock_verify.return_value = {
        'sid': 1, 'email': 'admin@test.com', 'role': role,
        'role_code': role.upper(), 'type': 'access'
    }
    mock_get_user.return_value = {
        'sid': 1, 'role': role, 'role_code': role.upper(),
        'department_id': 400000001,
        'department_code': department_code,
        'department': {'id': 400000001, 'code': department_code, 'name': department_code}
    }


class TestList:
    URL = '/api/v1/handcarry'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.handcarry.routes.handcarry_service.list_items')
    def test_success(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {
            'success': True,
            'items': [{'id': 1, 'upc': 12345}],
            'count': 1,
        }

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.get_json()['count'] == 1
        mock_svc.assert_called_once_with(search=None)

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.handcarry.routes.handcarry_service.list_items')
    def test_with_search(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': True, 'items': [], 'count': 0}

        resp = client.get(self.URL + '?search=999', headers=_auth_headers())

        assert resp.status_code == 200
        mock_svc.assert_called_once_with(search='999')

    def test_unauthenticated(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 401


class TestImport:
    URL = '/api/v1/handcarry/import'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.handcarry.routes.handcarry_service.import_records')
    def test_records_body_uses_import_records(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='manager')
        mock_svc.return_value = {
            'success': True,
            'inserted': 2, 'updated': 1, 'skipped': 0,
            'errors': [], 'total_received': 3,
        }
        records = [
            {'upc': 100, 'quantity_imported': 5,
             'price_before_vat': 1000, 'price_after_vat': 1100},
            {'upc': 200, 'quantity_imported': 5,
             'price_before_vat': 1000, 'price_after_vat': 1100},
            {'upc': 300, 'quantity_imported': 5,
             'price_before_vat': 1000, 'price_after_vat': 1100},
        ]

        resp = client.post(self.URL, json={'records': records}, headers=_auth_headers())

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['inserted'] == 2
        assert data['updated'] == 1
        mock_svc.assert_called_once_with(records)

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.handcarry.routes.handcarry_service.import_upcs')
    def test_legacy_upcs_body_still_supported(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='manager')
        mock_svc.return_value = {
            'success': True,
            'inserted': 2, 'skipped': 1, 'errors': [], 'total_received': 3,
        }

        resp = client.post(self.URL, json={'upcs': [100, 200, 300]}, headers=_auth_headers())

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['inserted'] == 2
        assert data['skipped'] == 1
        mock_svc.assert_called_once_with([100, 200, 300])

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_body_returns_400(self, mock_get_user, mock_verify, client):
        """Empty body (no `records` or `upcs`) → 400."""
        _mock_auth(mock_verify, mock_get_user, role='manager')
        resp = client.post(self.URL, json={}, headers=_auth_headers())
        assert resp.status_code == 400
        assert "Missing 'records'" in resp.get_json()['error']


class TestUpdate:
    URL = '/api/v1/handcarry/5'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.handcarry.routes.handcarry_service.update_item')
    def test_success(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='manager')
        mock_svc.return_value = {'success': True, 'item': {'id': 5, 'upc': 99999}}

        resp = client.put(self.URL, json={'upc': 99999}, headers=_auth_headers())

        assert resp.status_code == 200
        mock_svc.assert_called_once_with(5, 99999)

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.handcarry.routes.handcarry_service.update_item')
    def test_duplicate_upc_returns_409(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='manager')
        mock_svc.return_value = {'success': False, 'error': 'UPC already exists', 'status': 409}

        resp = client.put(self.URL, json={'upc': 1}, headers=_auth_headers())

        assert resp.status_code == 409

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.handcarry.routes.handcarry_service.update_item')
    def test_missing_id_returns_404(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='manager')
        mock_svc.return_value = {'success': False, 'error': 'not found', 'status': 404}

        resp = client.put(self.URL, json={'upc': 1}, headers=_auth_headers())

        assert resp.status_code == 404


class TestDelete:
    URL = '/api/v1/handcarry/5'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.handcarry.routes.handcarry_service.delete_item')
    def test_success(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='manager')
        mock_svc.return_value = {'success': True, 'id': 5}

        resp = client.delete(self.URL, headers=_auth_headers())

        assert resp.status_code == 200
        mock_svc.assert_called_once_with(5)

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.handcarry.routes.handcarry_service.delete_item')
    def test_missing_id_returns_404(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='manager')
        mock_svc.return_value = {'success': False, 'error': 'not found', 'status': 404}

        resp = client.delete(self.URL, headers=_auth_headers())

        assert resp.status_code == 404

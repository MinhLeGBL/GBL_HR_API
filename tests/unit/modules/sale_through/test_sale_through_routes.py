"""Unit tests for sale_through routes."""
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


class TestByBrandSeasonCategory:
    URL = '/api/v1/sale-through/by-brand-season-category'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.sale_through.routes.sale_through_service.get_brand_season_category_report')
    def test_success_no_filters(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {
            'success': True,
            'rows': [
                {
                    'brand': 'PROENZA SCHOULER', 'season': 'SS25', 'category': 'DRESS',
                    'sku_count': 12, 'imported_qty': 100, 'sold_qty': 75,
                    'on_hand_qty': 25, 'in_transit_qty': 0, 'actual_on_hand_qty': 25,
                    'transferred_out_qty': 0, 'adjustment_qty': 0,
                    'sell_through_pct': 75.0,
                }
            ],
            'count': 1,
        }

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['count'] == 1
        assert data['rows'][0]['sell_through_pct'] == 75.0
        mock_svc.assert_called_once_with(brand=None, season=None, category=None)

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.sale_through.routes.sale_through_service.get_brand_season_category_report')
    def test_success_with_filters(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': True, 'rows': [], 'count': 0}

        resp = client.get(
            self.URL + '?brand=PROENZA+SCHOULER&season=SS25&category=DRESS',
            headers=_auth_headers(),
        )

        assert resp.status_code == 200
        mock_svc.assert_called_once_with(
            brand='PROENZA SCHOULER', season='SS25', category='DRESS',
        )

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.sale_through.routes.sale_through_service.get_brand_season_category_report')
    def test_oracle_failure_returns_500(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': False, 'error': 'Failed to connect to Oracle'}

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 500
        assert resp.get_json()['success'] is False

    def test_unauthenticated_returns_401(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 401

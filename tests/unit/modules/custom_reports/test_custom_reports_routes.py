"""Route-level tests for custom_reports — service is mocked."""
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


class TestSizesEndpoint:
    URL = '/api/v1/custom-reports/sizes'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.custom_reports.routes.custom_reports_service.get_size_by_brand_season')
    def test_success_defaults(self, mock_svc, mock_get_user, mock_verify, client):
        """No params → seasons=None (service applies SS25/SS26 default), brands=None
        (service applies FOCUS_BRANDS default), size=None."""
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {
            'success': True,
            'seasons': ['SS25', 'SS26'],
            'brands': ['ALEXANDER MCQUEEN'],  # service-side default
            'rows': [],
            'count': 0,
        }

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 200
        mock_svc.assert_called_once_with(
            seasons=None, brands=None, size=None, include_never_received=False,
        )

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.custom_reports.routes.custom_reports_service.get_size_by_brand_season')
    def test_success_with_filters(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': True, 'seasons': ['SS25'], 'brands': ['AKRIS'], 'rows': [], 'count': 0}

        resp = client.get(
            self.URL + '?seasons=SS25&brands=AKRIS,AKRIS%20PUNTO&size=M',
            headers=_auth_headers(),
        )

        assert resp.status_code == 200
        mock_svc.assert_called_once_with(
            seasons=['SS25'], brands=['AKRIS', 'AKRIS PUNTO'], size='M',
            include_never_received=False,
        )

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.custom_reports.routes.custom_reports_service.get_size_by_brand_season')
    def test_include_never_received_flag(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': True, 'seasons': ['SS25', 'SS26'], 'brands': None, 'rows': [], 'count': 0}

        resp = client.get(self.URL + '?include_never_received=true', headers=_auth_headers())

        assert resp.status_code == 200
        mock_svc.assert_called_once_with(
            seasons=None, brands=None, size=None, include_never_received=True,
        )

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.custom_reports.routes.custom_reports_service.get_size_by_brand_season')
    def test_brands_star_disables_filter(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': True, 'seasons': ['SS25', 'SS26'], 'brands': None, 'rows': [], 'count': 0}

        resp = client.get(self.URL + '?brands=*', headers=_auth_headers())

        assert resp.status_code == 200
        mock_svc.assert_called_once_with(
            seasons=None, brands=['*'], size=None, include_never_received=False,
        )

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.custom_reports.routes.custom_reports_service.get_size_by_brand_season')
    def test_invalid_season_returns_400(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': False, 'error': "invalid season code: 'SS25;DROP'"}

        resp = client.get(self.URL + '?seasons=SS25;DROP', headers=_auth_headers())

        assert resp.status_code == 400

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.custom_reports.routes.custom_reports_service.get_size_by_brand_season')
    def test_oracle_failure_returns_500(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': False, 'error': 'Failed to fetch data from Oracle'}

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 500

    def test_unauthenticated_returns_401(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 401

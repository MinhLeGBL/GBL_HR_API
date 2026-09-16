"""Unit tests for app.modules.periodic_report.routes.

Mocks the service and the auth middleware. Covers the HTTP layer: permission
gating, status-code mapping, and the token-spend guard on regeneration.
"""
from unittest.mock import patch

import pytest

from app.main import create_app

SERVICE = 'app.modules.periodic_report.routes.periodic_report_service'
VERIFY = 'app.core.auth.middleware.auth_service.verify_token'
GET_USER = 'app.core.auth.middleware.auth_service.get_user_by_sid'
BASE = '/api/v1/periodic-report'


PERMS = ('app.modules.permissions.service.PermissionService'
         '.get_user_permissions_v2')


@pytest.fixture(autouse=True)
def grant_sections():
    """`section_required` resolves permissions from Postgres, which these unit
    tests do not have. Grant both sections by default; the test that checks
    refusal overrides this with its own patch."""
    with patch(PERMS, return_value=[{'section_code': 'PERIODIC_REPORT'},
                                    {'section_code': 'PERIODIC_REPORT_APPROVE'}]):
        yield


@pytest.fixture
def client():
    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _mock_auth(mock_verify, mock_get_user, role='admin'):
    mock_verify.return_value = {'sid': 1, 'email': 'a@t.com', 'role': role,
                                'role_code': role.upper(), 'type': 'access'}
    mock_get_user.return_value = {'sid': 1, 'role': role,
                                  'role_code': role.upper(),
                                  'department_id': 1, 'department_code': 'BOD',
                                  'department': {'id': 1, 'code': 'BOD',
                                                 'name': 'BOD'}}


def _headers():
    return {'Authorization': 'Bearer t'}


class TestNoGenerationOverHTTP:
    """There is deliberately no endpoint that generates a report.

    The analysis costs money and is written once, by the Monday 03:00 job. A
    button that spends tokens on every press would buy nothing anyway: a sale
    posted late carries the later post date, so it lands in the NEXT week's
    window and re-running today cannot pull it back.
    """

    def test_the_generate_endpoint_does_not_exist(self, client):
        app = create_app()
        rules = {str(r) for r in app.url_map.iter_rules()}
        assert f'{BASE}/runs/generate' not in rules

    @patch(GET_USER)
    @patch(VERIFY)
    def test_posting_to_it_is_a_404(self, mock_verify, mock_get_user, client):
        _mock_auth(mock_verify, mock_get_user)
        res = client.post(f'{BASE}/runs/generate', json={}, headers=_headers())
        assert res.status_code == 404

    def test_no_route_reaches_the_commentary_generator(self):
        # Belt and braces: the module must not even import it.
        import inspect
        from app.modules.periodic_report import routes
        assert 'generate_commentary' not in inspect.getsource(routes)
        assert 'draft_email' not in inspect.getsource(routes)


class TestAuth:
    def test_unauthenticated_is_rejected(self, client):
        assert client.get(f'{BASE}/runs/latest').status_code == 401

    @patch(GET_USER)
    @patch(VERIFY)
    def test_a_staff_user_without_the_section_is_refused(
            self, mock_verify, mock_get_user, client):
        # Approval emails the management board, so it is enforced server-side
        # rather than only hidden in the nav.
        _mock_auth(mock_verify, mock_get_user, role='staff')
        with patch(PERMS, return_value=[]):
            res = client.post(f'{BASE}/runs/1/approve', json={},
                              headers=_headers())
        assert res.status_code == 403


class TestStatusMapping:
    @patch(GET_USER)
    @patch(VERIFY)
    def test_an_invalid_state_maps_to_409_not_400(
            self, mock_verify, mock_get_user, client):
        _mock_auth(mock_verify, mock_get_user)
        with patch(SERVICE) as svc:
            svc.approve.return_value = {
                'success': False, 'error': 'not pending', 'code': 'INVALID_STATE'}
            res = client.post(f'{BASE}/runs/1/approve', json={}, headers=_headers())
        assert res.status_code == 409

    @patch(GET_USER)
    @patch(VERIFY)
    def test_a_missing_run_maps_to_404(self, mock_verify, mock_get_user, client):
        _mock_auth(mock_verify, mock_get_user)
        with patch(SERVICE) as svc:
            svc.get_run.return_value = {'success': False, 'error': 'nope',
                                        'code': 'NOT_FOUND'}
            res = client.get(f'{BASE}/runs/99', headers=_headers())
        assert res.status_code == 404

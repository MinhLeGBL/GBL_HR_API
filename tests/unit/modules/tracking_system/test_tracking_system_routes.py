"""Unit tests for the tracking-system routes — HTTP layer only, service mocked."""
from unittest.mock import patch

import pytest

from app.main import create_app

SERVICE = 'app.modules.tracking_system.routes.tracking_system_service'
VERIFY = 'app.core.auth.middleware.auth_service.verify_token'
GET_USER = 'app.core.auth.middleware.auth_service.get_user_by_sid'


@pytest.fixture
def client():
    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def auth():
    with patch(VERIFY) as verify, patch(GET_USER) as get_user:
        verify.return_value = {'sid': 7, 'email': 'u@test.com', 'role': 'staff',
                               'type': 'access'}
        get_user.return_value = {'sid': 7, 'role': 'staff', 'department_id': None}
        yield


H = {'Authorization': 'Bearer t'}


class TestUnlockRoute:

    def test_requires_login(self, client):
        assert client.post('/api/v1/tracking-system/unlock', json={'code': 'X'}).status_code == 401

    def test_success_passes_caller_sid_and_code(self, client, auth):
        with patch(SERVICE) as svc:
            svc.unlock.return_value = {'success': True, 'unlock_token': 'u', 'expires_at': 'e'}
            r = client.post('/api/v1/tracking-system/unlock', json={'code': 'GH135'}, headers=H)
        assert r.status_code == 200
        assert r.get_json()['unlock_token'] == 'u'
        svc.unlock.assert_called_once_with(7, 'GH135')

    @pytest.mark.parametrize('code,status', [
        ('INVALID_INPUT', 400), ('INVALID_CODE', 403),
        ('RATE_LIMITED', 429), ('SERVER_ERROR', 500),
    ])
    def test_error_status_mapping(self, client, auth, code, status):
        with patch(SERVICE) as svc:
            svc.unlock.return_value = {'success': False, 'code': code, 'error': 'x'}
            r = client.post('/api/v1/tracking-system/unlock', json={'code': 'GH135'}, headers=H)
        assert r.status_code == status

    def test_missing_body_passes_none(self, client, auth):
        with patch(SERVICE) as svc:
            svc.unlock.return_value = {'success': False, 'code': 'INVALID_INPUT', 'error': 'x'}
            client.post('/api/v1/tracking-system/unlock', headers=H)
        svc.unlock.assert_called_once_with(7, None)


class TestSessionRoute:

    def test_requires_login(self, client):
        assert client.get('/api/v1/tracking-system/session').status_code == 401

    def test_unlocked(self, client, auth):
        with patch(SERVICE) as svc:
            svc.verify_unlock.return_value = True
            r = client.get('/api/v1/tracking-system/session',
                           headers={**H, 'X-Unlock-Token': 'u'})
        assert r.status_code == 200
        svc.verify_unlock.assert_called_once_with(7, 'u')

    def test_locked(self, client, auth):
        with patch(SERVICE) as svc:
            svc.verify_unlock.return_value = False
            r = client.get('/api/v1/tracking-system/session', headers=H)
        assert r.status_code == 403
        assert r.get_json()['code'] == 'LOCKED'


DATA_ROUTES = [
    ('get', '/api/v1/tracking-system/in'),
    ('get', '/api/v1/tracking-system/in/search?q=ab'),
    ('post', '/api/v1/tracking-system/in'),
    ('post', '/api/v1/tracking-system/in/lookup'),
    ('delete', '/api/v1/tracking-system/in/1'),
    ('get', '/api/v1/tracking-system/out'),
    ('get', '/api/v1/tracking-system/out/search?q=ab'),
    ('post', '/api/v1/tracking-system/out'),
    ('delete', '/api/v1/tracking-system/out/1'),
]


class TestDataRoutes:

    @pytest.mark.parametrize('method,url', DATA_ROUTES)
    def test_every_data_route_requires_login(self, client, method, url):
        assert getattr(client, method)(url).status_code == 401

    @pytest.mark.parametrize('method,url', DATA_ROUTES)
    def test_every_data_route_requires_an_unlock(self, client, auth, method, url):
        with patch(SERVICE) as svc:
            svc.verify_unlock.return_value = False
            r = getattr(client, method)(url, headers=H)
        assert r.status_code == 403 and r.get_json()['code'] == 'LOCKED'
        for name in ('list_in', 'list_out', 'search_items', 'search_lines',
                     'import_in', 'import_out', 'delete', 'lookup_upcs'):
            getattr(svc, name).assert_not_called()

    def test_wiring(self, client, auth):
        UH = {**H, 'X-Unlock-Token': 'u'}
        with patch(SERVICE) as svc:
            svc.verify_unlock.return_value = True
            for name in ('list_in', 'list_out', 'search_items', 'search_lines',
                         'import_in', 'import_out', 'delete', 'lookup_upcs'):
                getattr(svc, name).return_value = {'success': True}
            client.post('/api/v1/tracking-system/in/lookup', json={'upcs': ['157820']}, headers=UH)
            client.get('/api/v1/tracking-system/in/search?q=CGC', headers=UH)
            client.get('/api/v1/tracking-system/out/search?q=157', headers=UH)
            client.post('/api/v1/tracking-system/in', json={'items': [1]}, headers=UH)
            client.post('/api/v1/tracking-system/out', json={'lines': [2]}, headers=UH)
            client.delete('/api/v1/tracking-system/in/5', headers=UH)
            client.delete('/api/v1/tracking-system/out/6', headers=UH)
        svc.search_items.assert_called_once_with('CGC')
        svc.search_lines.assert_called_once_with('157')
        svc.import_in.assert_called_once_with(7, [1])
        svc.lookup_upcs.assert_called_once_with(['157820'])
        svc.import_out.assert_called_once_with(7, [2])
        assert [c.args for c in svc.delete.call_args_list] == [('in', 5), ('out', 6)]

    @pytest.mark.parametrize('code,status', [('INVALID_INPUT', 400), ('NOT_FOUND', 404),
                                             ('SERVER_ERROR', 500)])
    def test_error_status(self, client, auth, code, status):
        with patch(SERVICE) as svc:
            svc.verify_unlock.return_value = True
            svc.delete.return_value = {'success': False, 'code': code}
            r = client.delete('/api/v1/tracking-system/in/5',
                              headers={**H, 'X-Unlock-Token': 'u'})
        assert r.status_code == status


class TestCors:
    """A browser preflight must allow the unlock header, or every gated call
    fails in the browser while passing in curl and in these unit tests."""

    @pytest.mark.parametrize('url', ['/api/v1/tracking-system/in', '/api/v1/tracking-system/session'])
    def test_preflight_allows_the_unlock_header(self, client, url):
        r = client.options(url, headers={
            'Origin': 'http://localhost:5173',
            'Access-Control-Request-Method': 'GET',
            'Access-Control-Request-Headers': 'authorization,x-unlock-token',
        })
        allowed = r.headers.get('Access-Control-Allow-Headers', '').lower()
        assert 'x-unlock-token' in allowed
        assert 'authorization' in allowed

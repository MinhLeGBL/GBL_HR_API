"""Route-level tests for custom_reports — service is mocked."""
from unittest.mock import patch

import pytest

PERMS = ('app.modules.permissions.service.PermissionService'
         '.get_user_permissions_v2')


@pytest.fixture(autouse=True)
def grant_section():
    """Every route here is gated on the CUSTOM_REPORTS section (v0.7.0), and
    `section_required` resolves permissions from Postgres, which these unit
    tests do not have. Grant it by default; the refusal test overrides this
    with its own patch.

    Same fixture as `weekly_report`'s route tests, for the same reason.
    """
    with patch(PERMS, return_value=[{'section_code': 'CUSTOM_REPORTS'}]):
        yield


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


class TestSectionGate:
    """A valid token is NOT enough.

    These endpoints return company-wide net revenue, the margin split,
    customer names and membership tiers. The sidebar hides the page from
    users without the section, but a hidden nav entry is not an access
    control — so the refusal is asserted here, per route.
    """

    @pytest.mark.parametrize('method, url, body', [
        ('get',  '/api/v1/custom-reports/sizes', None),
        ('post', '/api/v1/custom-reports/query',
         {'subject': 'sales', 'from': '2026-01-01', 'to': '2026-01-31',
          'groupBy': ['brand']}),
        ('get',  '/api/v1/custom-reports/dimensions/brand', None),
    ])
    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_a_user_without_the_section_is_refused(
        self, mock_get_user, mock_verify, client, method, url, body,
    ):
        _mock_auth(mock_verify, mock_get_user, role='staff', department_code='ACC')
        with patch(PERMS, return_value=[]):
            call = getattr(client, method)
            resp = call(url, headers=_auth_headers(), json=body) if body \
                else call(url, headers=_auth_headers())
        assert resp.status_code == 403
        assert resp.get_json()['success'] is False

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_holding_a_DIFFERENT_section_does_not_admit(
        self, mock_get_user, mock_verify, client,
    ):
        """Access to the weekly report is not access to the query engine."""
        _mock_auth(mock_verify, mock_get_user)
        with patch(PERMS, return_value=[{'section_code': 'WEEKLY_REPORT'}]):
            resp = client.get('/api/v1/custom-reports/dimensions/brand',
                              headers=_auth_headers())
        assert resp.status_code == 403


class TestQueryFailureShapes:
    """The two failures the FRONTEND renders in the user's own language.

    Both carry a stable `code` ALONGSIDE the English `error`. The page
    translates a code it knows and falls back to the prose otherwise, so
    losing either field loses a real message — which is why both are
    asserted rather than just the status.
    """

    URL = '/api/v1/custom-reports/query'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_a_period_before_the_data_is_400_with_a_code(
            self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        r = client.post(self.URL, json={
            'subject': 'sales', 'from': '2023-12-31', 'to': '2024-06-30',
            'groupBy': ['brand'],
        }, headers=_auth_headers())
        assert r.status_code == 400
        body = r.get_json()
        assert body['success'] is False
        assert body['code'] == 'rangeTooEarly'
        assert '2024-01-01' in body['error']

    # Oracle is mocked to fail loudly: validation must not touch it. Until
    # 0.9.0 a long period opened a connection to judge the filters.
    @patch('app.modules.custom_reports.engine.get_oracle_connection',
           side_effect=AssertionError('validation opened a connection'))
    @patch('app.modules.custom_reports.routes.engine.run_query')
    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_the_whole_history_unfiltered_runs(
            self, mock_get_user, mock_verify, mock_run, _mock_oracle, client):
        """The 0.9.0 change: longer than a year, no filter, accepted."""
        _mock_auth(mock_verify, mock_get_user)
        mock_run.return_value = {'success': True, 'result': {'rows': []}}
        r = client.post(self.URL, json={
            'subject': 'sales', 'from': '2024-01-01', 'to': '2026-11-22',
            'groupBy': ['brand'],
        }, headers=_auth_headers())
        assert r.status_code == 200
        assert mock_run.called

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_an_ordinary_validation_error_carries_no_code(
            self, mock_get_user, mock_verify, client):
        """Only the codes the page can translate are emitted; the rest stay
        prose, so an unknown code can never reach the UI as a raw key."""
        _mock_auth(mock_verify, mock_get_user)
        r = client.post(self.URL, json={
            'subject': 'sales', 'from': '2026-01-01', 'to': '2026-01-31',
            'groupBy': ['nope'],
        }, headers=_auth_headers())
        assert r.status_code == 400
        assert 'code' not in r.get_json()

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.custom_reports.routes.engine.run_query')
    def test_cap_refuses_the_third_concurrent_query_with_503(
            self, mock_run, mock_get_user, mock_verify, client):
        """Two slots per process. The third caller is refused rather than
        queued — blocking would hold the very worker the cap protects."""
        from app.modules.custom_reports import routes

        mock_run.return_value = {'success': True, 'result': {}}
        _mock_auth(mock_verify, mock_get_user)
        body = {'subject': 'sales', 'from': '2026-01-01',
                'to': '2026-01-31', 'groupBy': ['brand']}

        # Hold both slots, as two in-flight queries would.
        assert routes._slots.acquire(blocking=False)
        assert routes._slots.acquire(blocking=False)
        try:
            r = client.post(self.URL, json=body, headers=_auth_headers())
            assert r.status_code == 503
            assert r.get_json()['code'] == 'reportsBusy'
            assert r.headers['Retry-After'] == '10'
        finally:
            routes._slots.release()
            routes._slots.release()

        # ...and the slots are given back, so the next caller succeeds.
        assert client.post(self.URL, json=body,
                           headers=_auth_headers()).status_code == 200

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.custom_reports.routes.engine.run_query')
    def test_a_failing_query_still_releases_its_slot(
            self, mock_run, mock_get_user, mock_verify, client):
        """Without the `finally`, one exception would permanently burn a
        slot and the cap would ratchet down to zero."""
        from app.modules.custom_reports import routes

        _mock_auth(mock_verify, mock_get_user)
        body = {'subject': 'sales', 'from': '2026-01-01',
                'to': '2026-01-31', 'groupBy': ['brand']}

        mock_run.side_effect = RuntimeError('oracle fell over')
        with pytest.raises(RuntimeError):
            client.post(self.URL, json=body, headers=_auth_headers())

        mock_run.side_effect = None
        mock_run.return_value = {'success': True, 'result': {}}
        assert client.post(self.URL, json=body,
                           headers=_auth_headers()).status_code == 200


TRACKING = 'app.modules.tracking_system.TrackingSystemService'


class TestReconciliation:
    """Actual values, with the tracking system's differences added. Only a
    user on that system's allowlist gets them; for anyone else the request
    is the ordinary report and the check is the app's ordinary 404."""

    URL = '/api/v1/custom-reports/query'
    CHECK = '/api/v1/custom-reports/reconcile'
    BODY = {'subject': 'sales', 'from': '2026-01-01', 'to': '2026-01-31',
            'groupBy': ['brand']}
    DELTAS = {'out': {11: 5.0}, 'in': {}}

    def _post(self, client, run, tracking, allowed, body, deltas=DELTAS):
        tracking.return_value.is_user_allowed.return_value = allowed
        tracking.return_value.reconciliation_deltas.return_value = deltas
        run.return_value = {'success': True, 'result': {'rows': []}}
        return client.post(self.URL, json=body, headers=_auth_headers())

    @patch(TRACKING)
    @patch('app.modules.custom_reports.routes.engine.run_query')
    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_an_allowed_user_gets_the_differences(
            self, mock_get_user, mock_verify, run, tracking, client):
        _mock_auth(mock_verify, mock_get_user)
        r = self._post(client, run, tracking, True, dict(self.BODY, reconcile=True))
        assert r.status_code == 200
        assert run.call_args.args[0]['reconcile'] == self.DELTAS
        tracking.return_value.is_user_allowed.assert_called_once_with(1)

    @patch(TRACKING)
    @patch('app.modules.custom_reports.routes.engine.run_query')
    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_anyone_else_gets_the_ordinary_report_silently(
            self, mock_get_user, mock_verify, run, tracking, client):
        _mock_auth(mock_verify, mock_get_user)
        r = self._post(client, run, tracking, False, dict(self.BODY, reconcile=True))
        assert r.status_code == 200
        assert 'reconcile' not in run.call_args.args[0]
        tracking.return_value.reconciliation_deltas.assert_not_called()

    @patch(TRACKING)
    @patch('app.modules.custom_reports.routes.engine.run_query')
    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_without_the_flag_the_allowlist_is_not_even_asked(
            self, mock_get_user, mock_verify, run, tracking, client):
        _mock_auth(mock_verify, mock_get_user)
        self._post(client, run, tracking, True, self.BODY)
        tracking.assert_not_called()
        assert 'reconcile' not in run.call_args.args[0]

    @pytest.mark.parametrize('flag', ['true', 1, 'yes', {'a': 1}])
    @patch(TRACKING)
    @patch('app.modules.custom_reports.routes.engine.run_query')
    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_only_a_literal_true_counts(
            self, mock_get_user, mock_verify, run, tracking, client, flag):
        _mock_auth(mock_verify, mock_get_user)
        self._post(client, run, tracking, True, dict(self.BODY, reconcile=flag))
        assert 'reconcile' not in run.call_args.args[0]

    @patch(TRACKING)
    @patch('app.modules.custom_reports.routes.engine.run_query')
    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_unreadable_differences_are_refused_not_shown_as_actual(
            self, mock_get_user, mock_verify, run, tracking, client):
        _mock_auth(mock_verify, mock_get_user)
        r = self._post(client, run, tracking, True, dict(self.BODY, reconcile=True), deltas=None)
        assert r.status_code == 503
        run.assert_not_called()

    @patch(TRACKING)
    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_the_check_answers_204_for_an_allowed_user(
            self, mock_get_user, mock_verify, tracking, client):
        _mock_auth(mock_verify, mock_get_user)
        tracking.return_value.is_user_allowed.return_value = True
        r = client.get(self.CHECK, headers=_auth_headers())
        assert r.status_code == 204
        assert r.data == b''

    @patch(TRACKING)
    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_the_check_looks_like_a_missing_url_for_anyone_else(
            self, mock_get_user, mock_verify, tracking, client):
        _mock_auth(mock_verify, mock_get_user)
        tracking.return_value.is_user_allowed.return_value = False
        refused = client.get(self.CHECK, headers=_auth_headers())
        missing = client.get('/api/v1/custom-reports/no-such-thing', headers=_auth_headers())
        assert refused.status_code == missing.status_code == 404
        assert refused.get_json() == missing.get_json()

"""Unit tests for TrackingSystemService — the employee-number gate."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest

from app.modules.tracking_system.service import (
    INVALID_CODE_MESSAGE, MAX_FAILED_ATTEMPTS, UNLOCK_TOKEN_TYPE, TrackingSystemService,
)

CONN = 'app.modules.tracking_system.service.get_postgres_connection'


def _conn(fetchone_results):
    """A mock connection whose cursor returns `fetchone_results` in order."""
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.side_effect = list(fetchone_results)
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


def _inserted_success(cur):
    """The `success` value written to the attempt log."""
    for call in cur.execute.call_args_list:
        if 'INSERT INTO tracking_system_unlock_attempts' in call.args[0]:
            return call.args[1][1]
    raise AssertionError('no attempt was logged')


@pytest.fixture
def svc():
    return TrackingSystemService()


class TestUnlock:

    def test_own_allowed_code_unlocks(self, svc):
        conn, cur = _conn([(0,), ('GH135',), (1,)])
        with patch(CONN, return_value=conn):
            r = svc.unlock(7, '  gh135 ')
        assert r['success'] is True
        payload = jwt.decode(r['unlock_token'], svc.secret_key, algorithms=[svc.algorithm])
        assert payload['sid'] == 7 and payload['type'] == UNLOCK_TOKEN_TYPE
        assert _inserted_success(cur) is True
        conn.commit.assert_called_once()

    def test_someone_elses_code_is_refused(self, svc):
        # The user's own code is GH135; typing an allowed colleague's code fails.
        conn, cur = _conn([(0,), ('GH135',), (1,)])
        with patch(CONN, return_value=conn):
            r = svc.unlock(7, 'GL314')
        assert r == {'success': False, 'code': 'INVALID_CODE', 'error': INVALID_CODE_MESSAGE}
        assert _inserted_success(cur) is False

    def test_own_code_not_on_allowlist_is_refused_identically(self, svc):
        conn, cur = _conn([(0,), ('GH135',), None])
        with patch(CONN, return_value=conn):
            r = svc.unlock(7, 'GH135')
        assert r == {'success': False, 'code': 'INVALID_CODE', 'error': INVALID_CODE_MESSAGE}
        assert _inserted_success(cur) is False

    def test_user_without_employee_record_is_refused(self, svc):
        conn, cur = _conn([(0,), None])
        with patch(CONN, return_value=conn):
            r = svc.unlock(7, 'GH135')
        assert r['code'] == 'INVALID_CODE'
        assert _inserted_success(cur) is False

    def test_rate_limited_after_too_many_failures(self, svc):
        conn, cur = _conn([(MAX_FAILED_ATTEMPTS,)])
        with patch(CONN, return_value=conn):
            r = svc.unlock(7, 'GH135')
        assert r['code'] == 'RATE_LIMITED'
        # Refused before the code is even looked at, and not logged as an attempt:
        # only the per-user lock and the count ran.
        assert cur.execute.call_count == 2
        assert 'pg_advisory_xact_lock' in cur.execute.call_args_list[0].args[0]

    @pytest.mark.parametrize('code', [None, '', '   ', 123, ['GH135'], {'a': 1}, 'X' * 51])
    def test_blank_code_is_invalid_input_without_touching_db(self, svc, code):
        with patch(CONN) as get_conn:
            r = svc.unlock(7, code)
        assert r['code'] == 'INVALID_INPUT'
        get_conn.assert_not_called()

    def test_attempts_are_serialised_per_user(self, svc):
        conn, cur = _conn([(0,), ('GH135',), (1,)])
        with patch(CONN, return_value=conn):
            svc.unlock(7, 'GH135')
        sql, args = cur.execute.call_args_list[0].args
        assert 'pg_advisory_xact_lock' in sql and args[1] == 7

    def test_allowlist_is_checked_even_when_the_code_is_wrong(self, svc):
        # Same queries either way, so timing does not tell "wrong code" from
        # "right code, not on the list".
        conn, cur = _conn([(0,), ('GH135',), (1,)])
        with patch(CONN, return_value=conn):
            svc.unlock(7, 'WRONG')
        assert any('tracking_system_access' in c.args[0] for c in cur.execute.call_args_list)

    def test_non_ascii_code_is_a_logged_failure_not_a_crash(self, svc):
        conn, cur = _conn([(0,), ('GH135',), (1,)])
        with patch(CONN, return_value=conn):
            r = svc.unlock(7, 'GHÉ35')
        assert r['code'] == 'INVALID_CODE'
        assert _inserted_success(cur) is False

    def test_db_unavailable(self, svc):
        with patch(CONN, return_value=None):
            assert svc.unlock(7, 'GH135')['code'] == 'SERVER_ERROR'

    def test_db_error_rolls_back(self, svc):
        conn, cur = _conn([])
        cur.execute.side_effect = RuntimeError('boom')
        with patch(CONN, return_value=conn):
            r = svc.unlock(7, 'GH135')
        assert r['code'] == 'SERVER_ERROR'
        conn.rollback.assert_called_once()


class TestVerifyUnlock:

    def _token(self, svc, sid=7, type_=UNLOCK_TOKEN_TYPE, delta=timedelta(hours=1)):
        return jwt.encode({'sid': sid, 'type': type_,
                           'exp': datetime.now(timezone.utc) + delta},
                          svc.secret_key, algorithm=svc.algorithm)

    def test_valid_token_and_still_allowed(self, svc):
        conn, _ = _conn([('GH135',), (1,)])
        with patch(CONN, return_value=conn):
            assert svc.verify_unlock(7, self._token(svc)) is True

    def test_revoked_when_removed_from_allowlist(self, svc):
        conn, _ = _conn([('GH135',), None])
        with patch(CONN, return_value=conn):
            assert svc.verify_unlock(7, self._token(svc)) is False

    @pytest.mark.parametrize('kwargs', [
        {'sid': 8},                          # another user's unlock
        {'type_': 'access'},                 # a login token is not an unlock
        {'delta': timedelta(seconds=-1)},    # expired
    ])
    def test_rejected_tokens_never_reach_the_db(self, svc, kwargs):
        with patch(CONN) as get_conn:
            assert svc.verify_unlock(7, self._token(svc, **kwargs)) is False
        get_conn.assert_not_called()

    @pytest.mark.parametrize('token', [None, '', 'not-a-jwt'])
    def test_missing_or_garbage_token(self, svc, token):
        assert svc.verify_unlock(7, token) is False


class TestInitDatabase:

    def test_creates_both_tables_and_commits(self, svc):
        conn, cur = _conn([])
        with patch(CONN, return_value=conn):
            assert svc.init_database() == {'success': True}
        sql = ' '.join(c.args[0] for c in cur.execute.call_args_list)
        assert 'tracking_system_access' in sql
        assert 'tracking_system_unlock_attempts' in sql
        conn.commit.assert_called_once()

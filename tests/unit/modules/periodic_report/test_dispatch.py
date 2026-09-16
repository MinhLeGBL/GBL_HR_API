"""Unit tests for the send path — message assembly, dry-run safety, dispatch.

Nothing here opens a socket. `DryRunMailer` stands in for SMTP, which is also
what a development box gets by default.
"""
import os
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.core.mail.mailer import (DryRunMailer, MailError, build_message,
                                  get_mailer, mail_identity)
from app.modules.periodic_report.service import PeriodicReportService

XLSX = ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


class TestBuildMessage:
    def _msg(self, **kw):
        defaults = dict(subject='Weekly report', body='Hello',
                        to=['a@example.com'], from_addr='reports@example.com')
        return build_message(**{**defaults, **kw})

    def test_sets_the_basic_headers(self):
        msg = self._msg()
        assert msg['Subject'] == 'Weekly report'
        assert msg['To'] == 'a@example.com'
        assert msg['From'] == 'reports@example.com'

    def test_display_name_is_applied_when_given(self):
        msg = self._msg(from_name='GBL Master')
        assert msg['From'] == 'GBL Master <reports@example.com>'

    def test_cc_is_a_visible_header(self):
        msg = self._msg(cc=['c@example.com'])
        assert msg['Cc'] == 'c@example.com'

    def test_bcc_is_NEVER_written_as_a_header(self):
        # A Bcc header would disclose every hidden recipient to everyone — the
        # one thing bcc exists to prevent. It belongs in the envelope only.
        msg = self._msg(bcc=['hidden@example.com'])
        assert msg['Bcc'] is None
        assert 'hidden@example.com' not in msg.as_string()

    def test_attaches_the_workbook_with_the_right_type(self):
        msg = self._msg(attachments=[('report.xlsx', XLSX, b'PK\x03\x04data')])
        parts = list(msg.iter_attachments())
        assert len(parts) == 1
        assert parts[0].get_filename() == 'report.xlsx'
        assert parts[0].get_content_type() == XLSX
        assert parts[0].get_payload(decode=True) == b'PK\x03\x04data'

    def test_body_survives_round_trip(self):
        msg = self._msg(body='Line one\nLine two')
        assert 'Line one' in msg.get_content()


class TestGetMailer:
    def test_defaults_to_dry_run_outside_production(self):
        # A developer running the dispatcher against a copy of the database must
        # not be able to email the management board because their .env happened
        # to carry live SMTP credentials.
        with patch.dict(os.environ, {'FLASK_ENV': 'development',
                                     'SMTP_HOST': 'mail.example.com'},
                        clear=False):
            os.environ.pop('MAIL_BACKEND', None)
            assert isinstance(get_mailer(), DryRunMailer)

    def test_explicit_dryrun_wins_even_in_production(self):
        with patch.dict(os.environ, {'FLASK_ENV': 'production',
                                     'MAIL_BACKEND': 'dryrun'}, clear=False):
            assert isinstance(get_mailer(), DryRunMailer)

    def test_smtp_without_a_host_is_a_clear_error(self):
        with patch.dict(os.environ, {'MAIL_BACKEND': 'smtp'}, clear=False):
            os.environ.pop('SMTP_HOST', None)
            with pytest.raises(MailError, match='SMTP_HOST'):
                get_mailer()

    def test_smtp_without_a_from_address_is_a_clear_error(self):
        with patch.dict(os.environ, {'MAIL_BACKEND': 'smtp',
                                     'SMTP_HOST': 'mail.example.com'},
                        clear=False):
            for key in ('SMTP_FROM', 'SMTP_USER'):
                os.environ.pop(key, None)
            with pytest.raises(MailError, match='SMTP_FROM'):
                get_mailer()

    def test_rejects_an_unknown_backend(self):
        with patch.dict(os.environ, {'MAIL_BACKEND': 'carrier-pigeon'},
                        clear=False):
            with pytest.raises(MailError, match='carrier-pigeon'):
                get_mailer()

    @pytest.mark.parametrize('port,expected', [
        (465, 'ssl'), (587, 'starttls'), (25, 'none'),
    ])
    def test_security_is_inferred_from_the_port(self, port, expected):
        env = {'MAIL_BACKEND': 'smtp', 'SMTP_HOST': 'mail.example.com',
               'SMTP_PORT': str(port), 'SMTP_FROM': 'a@b.c'}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop('SMTP_SECURITY', None)
            assert get_mailer().security == expected

    def test_rejects_an_unknown_security_mode(self):
        env = {'MAIL_BACKEND': 'smtp', 'SMTP_HOST': 'mail.example.com',
               'SMTP_FROM': 'a@b.c', 'SMTP_SECURITY': 'magic'}
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(MailError, match='SMTP_SECURITY'):
                get_mailer()

    def test_identity_falls_back_from_from_to_user(self):
        with patch.dict(os.environ, {'SMTP_USER': 'box@example.com'},
                        clear=False):
            os.environ.pop('SMTP_FROM', None)
            assert mail_identity()[0] == 'box@example.com'


@pytest.fixture
def service():
    svc = PeriodicReportService()
    svc.repo = MagicMock()
    svc.repo.get_run.return_value = {
        'id': 1, 'as_of': date(2026, 9, 14), 'status': 'sending',
        'email_subject': 'Weekly Sales Report — Week 37 2026',
        'email_body': 'Body text', 'workbook': b'PK\x03\x04xlsxbytes',
    }
    svc.repo.list_recipients.return_value = [
        {'email': 'board@example.com', 'kind': 'to'},
        {'email': 'cc@example.com', 'kind': 'cc'},
        {'email': 'archive@example.com', 'kind': 'bcc'},
    ]
    return svc


class TestSendRun:
    def test_sends_to_every_bucket_in_the_envelope(self, service):
        mailer = DryRunMailer()
        with patch('app.core.mail.get_mailer', return_value=mailer):
            result = service.send_run(1)
        assert result['success'] is True
        _msg, envelope = mailer.sent[0]
        assert set(envelope) == {'board@example.com', 'cc@example.com',
                                 'archive@example.com'}

    def test_attaches_the_stored_workbook(self, service):
        mailer = DryRunMailer()
        with patch('app.core.mail.get_mailer', return_value=mailer):
            service.send_run(1)
        msg, _ = mailer.sent[0]
        attachment = next(msg.iter_attachments())
        assert attachment.get_filename() == 'periodic_report_2026-09-14.xlsx'
        assert attachment.get_payload(decode=True) == b'PK\x03\x04xlsxbytes'

    def test_marks_the_run_sent_and_logs_it(self, service):
        with patch('app.core.mail.get_mailer', return_value=DryRunMailer()):
            service.send_run(1)
        service.repo.mark_sent.assert_called_once_with(1)
        assert service.repo.log_send.call_args[0][2] is True

    def test_refuses_to_send_with_no_TO_recipient(self, service):
        # cc/bcc alone is not a valid send — there would be no visible addressee.
        service.repo.list_recipients.return_value = [
            {'email': 'cc@example.com', 'kind': 'cc'}]
        result = service.send_run(1)
        assert result['success'] is False
        service.repo.mark_send_failed.assert_called_once()
        service.repo.mark_sent.assert_not_called()

    def test_a_failed_send_returns_the_run_to_approved_for_retry(self, service):
        failing = MagicMock()
        failing.send.side_effect = MailError('connection refused')
        with patch('app.core.mail.get_mailer', return_value=failing):
            result = service.send_run(1)
        assert result['success'] is False
        assert 'connection refused' in result['error']
        # Returned to the queue, not marked sent and not lost.
        service.repo.mark_send_failed.assert_called_once()
        service.repo.mark_sent.assert_not_called()
        assert service.repo.log_send.call_args[0][2] is False

    def test_a_missing_run_is_reported_not_raised(self, service):
        service.repo.get_run.return_value = None
        assert service.send_run(99)['success'] is False


class TestDispatchDue:
    def test_sends_each_claimed_run(self, service):
        service.repo.claim_due_runs.return_value = [1]
        with patch('app.core.mail.get_mailer', return_value=DryRunMailer()):
            result = service.dispatch_due(datetime(2026, 9, 14, 9, 0))
        assert len(result['dispatched']) == 1
        assert result['dispatched'][0]['success'] is True

    def test_claims_before_sending_so_a_slow_send_is_not_sent_twice(self, service):
        # The claim is what makes double-sending impossible; assert the
        # dispatcher actually goes through it rather than querying directly.
        service.repo.claim_due_runs.return_value = []
        service.dispatch_due(datetime(2026, 9, 14, 9, 0))
        service.repo.claim_due_runs.assert_called_once()

    def test_nothing_due_is_not_an_error(self, service):
        service.repo.claim_due_runs.return_value = []
        result = service.dispatch_due()
        assert result['success'] is True
        assert result['dispatched'] == []


class TestMultipartBody:
    """Bold and bullets need HTML; the plain part is never dropped, because
    some clients and most archiving tools refuse HTML outright."""

    def _sent(self, service):
        mailer = DryRunMailer()
        with patch('app.core.mail.get_mailer', return_value=mailer):
            service.send_run(1)
        return mailer.sent[0][0]

    def test_sends_both_a_plain_and_an_html_part(self, service):
        service.repo.get_run.return_value = {
            **service.repo.get_run.return_value,
            'email_body': '**1. Tuần gần nhất**\n\n- Doanh thu **giảm 4.4%**.',
        }
        msg = self._sent(service)
        types = {p.get_content_type() for p in msg.walk()}
        assert 'text/plain' in types
        assert 'text/html' in types

    def test_html_part_carries_real_markup_not_asterisks(self, service):
        service.repo.get_run.return_value = {
            **service.repo.get_run.return_value,
            'email_body': '- Doanh thu **giảm 4.4%**.',
        }
        msg = self._sent(service)
        html = next(p.get_content() for p in msg.walk()
                    if p.get_content_type() == 'text/html')
        assert '<strong>giảm 4.4%</strong>' in html
        assert '<li' in html
        assert '**' not in html

    def test_plain_part_drops_the_markers_but_keeps_the_bullets(self, service):
        service.repo.get_run.return_value = {
            **service.repo.get_run.return_value,
            'email_body': '- Doanh thu **giảm 4.4%**.',
        }
        msg = self._sent(service)
        plain = next(p.get_content() for p in msg.walk()
                     if p.get_content_type() == 'text/plain')
        assert '- Doanh thu giảm 4.4%.' in plain
        assert '**' not in plain
        assert '<strong>' not in plain

    def test_the_workbook_still_attaches_alongside_both_parts(self, service):
        msg = self._sent(service)
        names = [p.get_filename() for p in msg.iter_attachments()
                 if p.get_filename()]
        assert 'periodic_report_2026-09-14.xlsx' in names


"""The Sent-folder copy: discovery, the APPEND, and never breaking a send."""
import imaplib
from email.message import EmailMessage
from unittest.mock import patch

import pytest

from app.core.mail import mailer
from app.core.mail.sent_folder import file_as_sent, find_sent_folder


class FakeIMAP:
    """Just enough IMAP to exercise discovery and APPEND."""

    def __init__(self, lines, append_status='OK', list_status='OK'):
        self._lines = lines
        self._list_status = list_status
        self._append_status = append_status
        self.appended = []
        self.logged_out = False

    def list(self):
        if self._list_status != 'OK':
            raise imaplib.IMAP4.error('LIST failed')
        return self._list_status, self._lines

    def append(self, folder, flags, date, payload):
        self.appended.append((folder, flags, payload))
        return self._append_status, [b'APPEND completed']

    def login(self, user, password):
        return 'OK', [b'logged in']

    def logout(self):
        self.logged_out = True
        return 'BYE', [b'bye']


def msg(subject='Weekly report'):
    m = EmailMessage()
    m['Subject'] = subject
    m['From'] = 'reports@globallink.vn'
    m['To'] = 'board@globallink.vn'
    m['Date'] = 'Mon, 22 Sep 2026 08:00:00 +0700'
    m.set_content('body')
    return m


@pytest.fixture
def imap_env(monkeypatch):
    monkeypatch.setenv('SMTP_HOST', 'mail.globallink.vn')
    monkeypatch.setenv('SMTP_USER', 'reports@globallink.vn')
    monkeypatch.setenv('SMTP_PASSWORD', 'secret')
    monkeypatch.delenv('IMAP_HOST', raising=False)
    monkeypatch.delenv('IMAP_SENT_FOLDER', raising=False)
    monkeypatch.delenv('MAIL_FILE_SENT', raising=False)


# ── discovery ────────────────────────────────────────────────────────────────

def test_special_use_flag_wins_over_the_name():
    # The whole point of \Sent: the folder can be called anything, including a
    # localised name no guess would ever produce.
    fake = FakeIMAP([
        rb'(\HasNoChildren) "." "INBOX"',
        rb'(\HasNoChildren \Sent) "." "INBOX.Elementos enviados"',
        rb'(\HasNoChildren) "." "Sent"',
    ])
    assert find_sent_folder(fake) == 'INBOX.Elementos enviados'


def test_falls_back_to_a_known_name_when_no_flag_is_advertised():
    fake = FakeIMAP([
        rb'(\HasNoChildren) "." "INBOX"',
        rb'(\HasNoChildren) "." "INBOX.Sent"',
        rb'(\HasNoChildren) "." "INBOX.Trash"',
    ])
    assert find_sent_folder(fake) == 'INBOX.Sent'


def test_prefers_plain_sent_over_other_fallbacks():
    fake = FakeIMAP([
        rb'(\HasNoChildren) "/" "Sent Items"',
        rb'(\HasNoChildren) "/" "Sent"',
    ])
    assert find_sent_folder(fake) == 'Sent'


def test_unquoted_folder_names_are_read():
    # Servers may return the name as an atom rather than a quoted string.
    fake = FakeIMAP([rb'(\HasNoChildren \Sent) "." INBOX.Sent'])
    assert find_sent_folder(fake) == 'INBOX.Sent'


def test_no_sent_folder_anywhere_is_none_not_a_guess():
    # Returning a guess here would file the copy into a folder that does not
    # exist, or worse, create one.
    fake = FakeIMAP([rb'(\HasNoChildren) "." "INBOX"'])
    assert find_sent_folder(fake) is None


def test_a_failed_list_is_not_an_exception():
    fake = FakeIMAP([], list_status='NO')
    assert find_sent_folder(fake) is None


# ── filing ───────────────────────────────────────────────────────────────────

def _with_fake(fake):
    return patch('imaplib.IMAP4_SSL', return_value=fake)


def test_files_the_message_seen_and_logs_out(imap_env):
    fake = FakeIMAP([rb'(\HasNoChildren \Sent) "." "INBOX.Sent"'])
    with _with_fake(fake):
        filed, reason = file_as_sent(msg())

    assert filed is True
    assert 'INBOX.Sent' in reason
    folder, flags, payload = fake.appended[0]
    assert folder == 'INBOX.Sent'
    # Our own outgoing mail: an unread badge on Sent is pure noise.
    assert flags == r'(\Seen)'
    assert b'Weekly report' in payload
    assert fake.logged_out is True


def test_a_folder_name_with_a_space_is_quoted(imap_env):
    fake = FakeIMAP([rb'(\HasNoChildren) "/" "Sent Items"'])
    with _with_fake(fake):
        filed, _ = file_as_sent(msg())

    assert filed is True
    assert fake.appended[0][0] == '"Sent Items"'


def test_an_explicit_folder_skips_discovery(imap_env, monkeypatch):
    monkeypatch.setenv('IMAP_SENT_FOLDER', 'Archive/Outgoing')
    fake = FakeIMAP([rb'(\HasNoChildren \Sent) "." "INBOX.Sent"'])
    with _with_fake(fake):
        filed, _ = file_as_sent(msg())

    assert filed is True
    assert fake.appended[0][0] == 'Archive/Outgoing'


def test_imap_host_defaults_to_the_smtp_host(imap_env):
    fake = FakeIMAP([rb'(\HasNoChildren \Sent) "." "INBOX.Sent"'])
    with patch('imaplib.IMAP4_SSL', return_value=fake) as ctor:
        file_as_sent(msg())
    # No IMAP_HOST on the server's .env — reusing SMTP_HOST is what makes this
    # work without a config change.
    assert ctor.call_args[0][0] == 'mail.globallink.vn'
    assert ctor.call_args[0][1] == 993
    # And it must not be able to hang the dispatch job forever.
    assert ctor.call_args.kwargs['timeout'] == 30


def test_a_refused_append_reports_rather_than_raises(imap_env):
    fake = FakeIMAP([rb'(\HasNoChildren \Sent) "." "INBOX.Sent"'],
                    append_status='NO')
    with _with_fake(fake):
        filed, reason = file_as_sent(msg())

    assert filed is False
    assert 'refused' in reason


def test_an_unreachable_server_reports_rather_than_raises(imap_env):
    with patch('imaplib.IMAP4_SSL', side_effect=OSError('connection refused')):
        filed, reason = file_as_sent(msg())

    assert filed is False
    assert 'could not reach' in reason


def test_a_rejected_certificate_is_not_reported_as_unreachable(imap_env):
    # These are different faults with different fixes, and the usual cause of
    # the second is an empty CA bundle on the CLIENT — calling that "could not
    # reach the mail server" sends the operator to the wrong machine.
    import ssl as _ssl
    err = _ssl.SSLCertVerificationError(
        '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed')
    with patch('imaplib.IMAP4_SSL', side_effect=err):
        filed, reason = file_as_sent(msg())

    assert filed is False
    assert 'certificate' in reason.lower()
    assert 'could not reach' not in reason
    assert 'CA bundle' in reason


def test_a_login_failure_reports_rather_than_raises(imap_env):
    with patch('imaplib.IMAP4_SSL',
               side_effect=imaplib.IMAP4.error('AUTHENTICATIONFAILED')):
        filed, reason = file_as_sent(msg())

    assert filed is False
    assert 'IMAP error' in reason


def test_no_sent_folder_names_the_setting_that_fixes_it(imap_env):
    fake = FakeIMAP([rb'(\HasNoChildren) "." "INBOX"'])
    with _with_fake(fake):
        filed, reason = file_as_sent(msg())

    assert filed is False
    assert 'IMAP_SENT_FOLDER' in reason


def test_a_hung_mailbox_times_out_rather_than_stalling_the_job(imap_env,
                                                               monkeypatch):
    import socket
    monkeypatch.setenv('IMAP_TIMEOUT', '5')
    with patch('imaplib.IMAP4_SSL', side_effect=socket.timeout('timed out')) \
            as ctor:
        filed, reason = file_as_sent(msg())

    assert filed is False
    assert 'could not reach' in reason
    assert ctor.call_args.kwargs['timeout'] == 5


def test_can_be_switched_off(imap_env, monkeypatch):
    monkeypatch.setenv('MAIL_FILE_SENT', 'false')
    with patch('imaplib.IMAP4_SSL') as ctor:
        filed, reason = file_as_sent(msg())

    assert (filed, 'disabled' in reason) == (False, True)
    ctor.assert_not_called()


def test_no_credentials_means_no_connection_attempt(monkeypatch):
    for key in ('SMTP_HOST', 'SMTP_USER', 'IMAP_HOST', 'IMAP_USER'):
        monkeypatch.delenv(key, raising=False)
    with patch('imaplib.IMAP4_SSL') as ctor:
        filed, reason = file_as_sent(msg())

    assert filed is False
    assert 'not configured' in reason or 'no IMAP host' in reason
    ctor.assert_not_called()


# ── the send path ────────────────────────────────────────────────────────────

class FakeSMTP:
    def __init__(self, *a, **kw):
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def send_message(self, message, to_addrs=None):
        self.sent.append((message, to_addrs))


def _smtp_mailer():
    return mailer.SMTPMailer(host='mail.globallink.vn', port=587,
                             security='starttls', user='reports@globallink.vn',
                             password='secret')


def test_a_successful_send_files_a_copy():
    smtp = FakeSMTP()
    with patch('smtplib.SMTP', return_value=smtp), \
         patch.object(mailer, 'file_as_sent',
                      return_value=(True, 'filed in INBOX.Sent')) as filer:
        detail = _smtp_mailer().send(msg(), ['board@globallink.vn'])

    filer.assert_called_once()
    assert 'sent via' in detail
    assert 'no Sent copy' not in detail


def test_a_failed_copy_never_fails_the_send():
    # The email is already delivered by this point. Raising here would make the
    # caller retry and send the report to the board TWICE.
    smtp = FakeSMTP()
    with patch('smtplib.SMTP', return_value=smtp), \
         patch.object(mailer, 'file_as_sent',
                      return_value=(False, 'could not reach mail:993')):
        detail = _smtp_mailer().send(msg(), ['board@globallink.vn'])

    assert smtp.sent, 'the message must still have been sent'
    assert 'sent via' in detail
    # Silent would be worse than useless — the operator needs to know the copy
    # is missing and why.
    assert 'no Sent copy' in detail
    assert 'could not reach' in detail

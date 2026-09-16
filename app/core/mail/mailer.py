"""Outbound email.

The first mail capability in this codebase. Deliberately small and entirely
configured from the environment, so pointing it at a different provider is an
`.env` change rather than a code change — nothing here is specific to any host.

Environment
-----------
  MAIL_BACKEND      'smtp' or 'dryrun'. Defaults to 'smtp' in production and
                    'dryrun' everywhere else, so a development box cannot email
                    the management board by accident.
  SMTP_HOST         mail server hostname (the OUTGOING/SMTP host — an IMAP host
                    is for reading mail and will not send)
  SMTP_PORT         465 for ssl, 587 for starttls, 25 for none
  SMTP_SECURITY     'ssl' | 'starttls' | 'none'   (default: inferred from port)
  SMTP_USER         login, usually the full mailbox address
  SMTP_PASSWORD     password or app password
  SMTP_FROM         envelope/from address (default: SMTP_USER)
  SMTP_FROM_NAME    display name (default: 'GBL Master')
  SMTP_TIMEOUT      socket timeout in seconds (default: 30)
"""
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Iterable, List, Optional, Sequence


class MailError(RuntimeError):
    """Sending failed. Carries a message safe to show an operator."""


def build_message(*, subject: str, body: str, to: Sequence[str],
                  cc: Sequence[str] = (), bcc: Sequence[str] = (),
                  from_addr: str, from_name: str = '',
                  attachments: Iterable[tuple] = (),
                  html: Optional[str] = None) -> EmailMessage:
    """Assemble a message with optional binary attachments.

    `attachments` is an iterable of (filename, mime_type, bytes).

    When `html` is given the message is multipart/alternative: `body` is the
    plain-text part and `html` the rich one, and the reader's client picks. The
    plain part is never omitted — some clients and most archiving tools refuse
    HTML, and a recipient should never receive an empty message.

    Bcc is deliberately NOT written as a header — it goes in the envelope only,
    at send time. Putting it in the headers would show every hidden recipient to
    everyone, which is the one thing bcc exists to prevent.
    """
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = formataddr((from_name, from_addr)) if from_name else from_addr
    msg['To'] = ', '.join(to)
    if cc:
        msg['Cc'] = ', '.join(cc)
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid()
    msg.set_content(body)
    if html:
        # add_alternative must come AFTER set_content: it upgrades the message
        # to multipart/alternative with the plain part first, which is the
        # order clients expect (last part wins, so HTML is preferred).
        msg.add_alternative(html, subtype='html')

    for filename, mime_type, payload in attachments:
        maintype, _, subtype = mime_type.partition('/')
        msg.add_attachment(payload, maintype=maintype, subtype=subtype,
                           filename=filename)
    return msg


class Mailer:
    """Interface. `send` returns a short description of what happened."""

    def send(self, message: EmailMessage,
             envelope_to: Sequence[str]) -> str:   # pragma: no cover
        raise NotImplementedError


class DryRunMailer(Mailer):
    """Logs instead of sending. The default outside production.

    Used by development and by the tests, so the whole approve-and-dispatch path
    can be exercised end to end with nothing leaving the machine.
    """

    def __init__(self):
        self.sent: List[tuple] = []

    def send(self, message: EmailMessage, envelope_to: Sequence[str]) -> str:
        self.sent.append((message, list(envelope_to)))
        attachments = [p.get_filename() for p in message.iter_attachments()]
        detail = (f'DRY RUN — not sent. to={list(envelope_to)} '
                  f'subject={message["Subject"]!r} attachments={attachments}')
        print(f'[MAIL] {detail}')
        return detail


class SMTPMailer(Mailer):

    def __init__(self, host: str, port: int, security: str,
                 user: Optional[str], password: Optional[str],
                 timeout: int = 30):
        self.host = host
        self.port = port
        self.security = security
        self.user = user
        self.password = password
        self.timeout = timeout

    def send(self, message: EmailMessage, envelope_to: Sequence[str]) -> str:
        if not envelope_to:
            raise MailError('No recipients to send to')
        context = ssl.create_default_context()
        try:
            if self.security == 'ssl':
                server = smtplib.SMTP_SSL(self.host, self.port,
                                          timeout=self.timeout, context=context)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            with server:
                server.ehlo()
                if self.security == 'starttls':
                    server.starttls(context=context)
                    server.ehlo()
                if self.user:
                    server.login(self.user, self.password or '')
                server.send_message(message, to_addrs=list(envelope_to))
        except smtplib.SMTPAuthenticationError as e:
            raise MailError(f'SMTP authentication failed for {self.user}: {e}')
        except smtplib.SMTPRecipientsRefused as e:
            raise MailError(f'Recipients refused: {e.recipients}')
        except smtplib.SMTPException as e:
            raise MailError(f'SMTP error talking to {self.host}:{self.port}: {e}')
        except OSError as e:
            raise MailError(f'Could not reach {self.host}:{self.port}: {e}')
        return f'sent via {self.host}:{self.port} to {len(envelope_to)} recipient(s)'


def _infer_security(port: int) -> str:
    if port == 465:
        return 'ssl'
    if port == 25:
        return 'none'
    return 'starttls'


def mail_identity() -> tuple:
    """(from address, display name) — needed by callers building a message."""
    from_addr = os.getenv('SMTP_FROM') or os.getenv('SMTP_USER') or ''
    return from_addr, os.getenv('SMTP_FROM_NAME', 'GBL Master')


def get_mailer() -> Mailer:
    """Build the configured mailer.

    Defaults to dry-run outside production: a developer running the dispatcher
    against a copy of the database should not be able to email real recipients
    because their `.env` happened to carry live SMTP credentials.
    """
    backend = os.getenv('MAIL_BACKEND')
    if backend is None:
        backend = ('smtp' if os.getenv('FLASK_ENV') == 'production'
                   else 'dryrun')
    backend = backend.strip().lower()

    if backend in ('dryrun', 'dry-run', 'none'):
        return DryRunMailer()
    if backend != 'smtp':
        raise MailError(f'Unknown MAIL_BACKEND: {backend!r}')

    host = os.getenv('SMTP_HOST')
    if not host:
        raise MailError('SMTP_HOST is not configured')
    port = int(os.getenv('SMTP_PORT', 587))
    security = (os.getenv('SMTP_SECURITY') or _infer_security(port)).lower()
    if security not in ('ssl', 'starttls', 'none'):
        raise MailError(f'SMTP_SECURITY must be ssl, starttls or none — '
                        f'got {security!r}')
    if not mail_identity()[0]:
        raise MailError('SMTP_FROM (or SMTP_USER) is not configured')

    return SMTPMailer(host=host, port=port, security=security,
                      user=os.getenv('SMTP_USER'),
                      password=os.getenv('SMTP_PASSWORD'),
                      timeout=int(os.getenv('SMTP_TIMEOUT', 30)))

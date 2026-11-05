"""File a copy of an outgoing message in the mailbox's Sent folder.

SMTP delivers a message; it does not file a copy anywhere. The Sent folder
lives on the IMAP server, and the only thing that ever puts a message there is
a client doing a SECOND step after sending: an IMAP APPEND. Outlook,
Thunderbird and Apple Mail all do both halves, which is why hand-sent mail
appears in Sent and mail from this app did not.

(Gmail is the exception people remember: it files SMTP sends itself, because
the send passes through Google's own servers. A standard IMAP server does not.)

BEST EFFORT, NEVER FATAL
------------------------
By the time this runs the message has already been delivered. A failure here
means the copy is missing, not that the send failed — and raising would be
actively harmful, because the caller's retry would deliver the email a SECOND
time. Every failure returns a reason instead.

Environment
-----------
  IMAP_HOST       defaults to SMTP_HOST — the same mailbox, usually the same
                  server
  IMAP_PORT       default 993
  IMAP_SECURITY   'ssl' | 'starttls'   (default: ssl on 993, else starttls)
  IMAP_USER       defaults to SMTP_USER
  IMAP_PASSWORD   defaults to SMTP_PASSWORD
  IMAP_SENT_FOLDER  skip discovery and use this folder name
  IMAP_TIMEOUT    socket timeout in seconds (default: SMTP_TIMEOUT, else 30)
  MAIL_FILE_SENT  'false' to switch the whole thing off

A note on Bcc: the copy is the message as built, and bcc lives in the envelope
only — never in the headers — so the Sent copy shows To and Cc but not the bcc
recipients. That is the same thing every mail client does, and writing bcc into
the headers to make the copy fuller would expose the hidden recipients to
everyone. `weekly_report_sends` keeps the full envelope if you need it.
"""
import imaplib
import os
import re
import ssl
import time
from email.message import EmailMessage
from email.utils import parsedate_tz
from typing import Optional, Tuple

# Tried in order when the server advertises no \Sent folder. Ordered by how
# common they are across the servers this is likely to meet.
_FALLBACK_NAMES = ('Sent', 'INBOX.Sent', 'Sent Items', 'INBOX.Sent Items',
                   'Sent Messages', 'INBOX.Sent Messages')

# RFC 6154 special-use. The reliable way to find the folder — a server that
# advertises it is telling us outright, and the name can be anything.
_SENT_FLAG = rb'\\Sent'

# `* LIST (\HasNoChildren \Sent) "." "INBOX.Sent"` — the quoted name at the end.
_LIST_LINE = re.compile(rb'\(([^)]*)\)\s+"[^"]*"\s+(?:"([^"]*)"|(\S+))\s*$')


def _enabled() -> bool:
    return os.getenv('MAIL_FILE_SENT', 'true').strip().lower() not in (
        'false', '0', 'no', 'off')


def imap_config() -> Tuple[str, int, str, Optional[str], Optional[str], int]:
    host = os.getenv('IMAP_HOST') or os.getenv('SMTP_HOST') or ''
    port = int(os.getenv('IMAP_PORT') or 993)
    security = (os.getenv('IMAP_SECURITY')
                or ('ssl' if port == 993 else 'starttls')).lower()
    user = os.getenv('IMAP_USER') or os.getenv('SMTP_USER')
    password = os.getenv('IMAP_PASSWORD') or os.getenv('SMTP_PASSWORD')
    # A timeout matters MORE here than on the send. The mail has already gone
    # out; a mailbox that accepts the connection and then stops talking would
    # otherwise hang the dispatch job indefinitely over a cosmetic copy.
    timeout = int(os.getenv('IMAP_TIMEOUT') or os.getenv('SMTP_TIMEOUT') or 30)
    return host, port, security, user, password, timeout


def find_sent_folder(imap: imaplib.IMAP4) -> Optional[str]:
    """The Sent folder's name, by special-use flag first, then by name.

    Discovery rather than a guess: it is `Sent` on some servers, `INBOX.Sent`
    or `Sent Items` on others, and can be localised. A wrong guess would file
    the copy into a folder nobody reads, or create one.
    """
    try:
        status, lines = imap.list()
    except imaplib.IMAP4.error:
        status, lines = 'NO', []

    existing = set()
    if status == 'OK':
        for raw in lines or []:
            if not isinstance(raw, bytes):
                continue
            m = _LIST_LINE.search(raw)
            if not m:
                continue
            flags, quoted, bare = m.group(1), m.group(2), m.group(3)
            name = (quoted or bare or b'').decode('utf-8', 'replace')
            if re.search(_SENT_FLAG, flags, re.IGNORECASE):
                return name
            existing.add(name)

    for candidate in _FALLBACK_NAMES:
        if candidate in existing:
            return candidate
    return None


def file_as_sent(message: EmailMessage) -> Tuple[bool, str]:
    """APPEND `message` to the Sent folder. Returns (filed, reason).

    Never raises: see the module docstring.
    """
    if not _enabled():
        return False, 'disabled by MAIL_FILE_SENT'

    host, port, security, user, password, timeout = imap_config()
    if not host or not user:
        return False, 'no IMAP host or user configured'

    context = ssl.create_default_context()
    imap = None
    try:
        if security == 'ssl':
            imap = imaplib.IMAP4_SSL(host, port, ssl_context=context,
                                     timeout=timeout)
        else:
            imap = imaplib.IMAP4(host, port, timeout=timeout)
            imap.starttls(ssl_context=context)
        imap.login(user, password or '')

        folder = os.getenv('IMAP_SENT_FOLDER') or find_sent_folder(imap)
        if not folder:
            return False, (f'no Sent folder found on {host} — set '
                           'IMAP_SENT_FOLDER to name it explicitly')

        # \Seen because it is our own outgoing mail: a Sent folder that shows
        # unread counts is just noise.
        stamp = _sent_time(message)
        # A folder name with a space must be quoted for APPEND.
        target = f'"{folder}"' if ' ' in folder else folder
        status, detail = imap.append(target, r'(\Seen)', stamp,
                                     message.as_bytes())
        if status != 'OK':
            return False, f'APPEND to {folder} refused: {detail!r}'
        return True, f'filed in {folder}'
    except imaplib.IMAP4.error as e:
        return False, f'IMAP error on {host}:{port}: {e}'
    except ssl.SSLCertVerificationError as e:
        # Called out separately because "could not reach" would be a LIE here —
        # the connection succeeded and the certificate was rejected, which is a
        # completely different thing to fix. Worth the extra clause: the usual
        # cause is not the mail server at all but a Python whose trust store is
        # empty, which fails IDENTICALLY for every host on the internet.
        return False, (f'TLS certificate rejected for {host}:{port}: {e} — '
                       'check this machine\'s CA bundle before suspecting the '
                       'mail server')
    except (OSError, ssl.SSLError) as e:
        return False, f'could not reach {host}:{port}: {e}'
    finally:
        if imap is not None:
            try:
                imap.logout()
            except Exception:                                  # noqa: BLE001
                pass


def _sent_time(message: EmailMessage):
    """The message's own Date as an IMAP internaldate, falling back to now.

    Keeps the Sent copy in step with the header the recipient sees, rather than
    stamping it with whenever the APPEND happened to run.
    """
    parsed = parsedate_tz(message.get('Date', '')) if message.get('Date') else None
    if parsed:
        try:
            return imaplib.Time2Internaldate(time.mktime(parsed[:9])
                                             - (parsed[9] or 0)
                                             + time.timezone)
        except (ValueError, OverflowError):
            pass
    return imaplib.Time2Internaldate(time.time())

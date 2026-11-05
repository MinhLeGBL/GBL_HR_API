"""
Sent-folder check — does the mailbox let us file a copy, and where?

SMTP delivers a message but never files a copy; the Sent folder is an IMAP
thing, and putting a message there is a second conversation (an APPEND). This
script proves that second conversation works BEFORE the weekly report relies on
it.

**It sends no email.** By default it only logs in and lists folders, so it is
safe to run against the live mailbox at any time.

Usage (on the server, where the mail credentials live):

    cd /home/gbladmin/GBL_HR_API
    PYTHONPATH=. .venv/bin/python scripts/jobs/mail_sent_folder_check.py

    # also file one harmless test message into Sent, to prove APPEND works:
    PYTHONPATH=. .venv/bin/python scripts/jobs/mail_sent_folder_check.py --append-test

`--append-test` writes a message titled "GBL Master — Sent folder test" into the
Sent folder and nothing else. It is not delivered to anyone; delete it from the
mailbox afterwards if you like.

Exit codes:
    0 — logged in and found a Sent folder (and filed the test, if asked)
    1 — could not connect, log in, or find a Sent folder
"""
import argparse
import os
import sys
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dotenv import load_dotenv

env = os.getenv('FLASK_ENV', 'production')
env_file = f'.env.{env}'
if os.path.exists(env_file):
    load_dotenv(env_file)
else:
    load_dotenv()

from app.core.mail.sent_folder import (file_as_sent,  # noqa: E402
                                       find_sent_folder, imap_config)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--append-test', action='store_true',
                        help='Also file one test message in Sent. Sends '
                             'nothing to anyone.')
    args = parser.parse_args()

    import imaplib
    import ssl

    host, port, security, user, password, timeout = imap_config()
    if not host or not user:
        print('No IMAP host or user configured. IMAP_HOST falls back to '
              'SMTP_HOST and IMAP_USER to SMTP_USER — set at least those.')
        return 1

    print(f'Connecting to {host}:{port} ({security}) as {user}')
    context = ssl.create_default_context()
    try:
        if security == 'ssl':
            imap = imaplib.IMAP4_SSL(host, port, ssl_context=context,
                                     timeout=timeout)
        else:
            imap = imaplib.IMAP4(host, port, timeout=timeout)
            imap.starttls(ssl_context=context)
        imap.login(user, password or '')
    except ssl.SSLCertVerificationError as e:
        print(f'FAILED: TLS certificate rejected — {e}')
        print('\nThe connection got through; it is the certificate that was '
              'refused. Before suspecting the mail server, check this '
              'machine\'s CA bundle:')
        print('    python -c "import ssl; '
              'print(len(ssl.create_default_context().get_ca_certs()))"')
        print('0 means an empty trust store — EVERY https host fails the same '
              'way, and the mail server is innocent.')
        return 1
    except Exception as e:                                     # noqa: BLE001
        print(f'FAILED: {e}')
        return 1

    try:
        status, lines = imap.list()
        print(f'\nFolders ({status}):')
        for raw in lines or []:
            print(f'  {raw.decode("utf-8", "replace")}')

        override = os.getenv('IMAP_SENT_FOLDER')
        chosen = override or find_sent_folder(imap)
        print()
        if override:
            print(f'Sent folder: {chosen!r}  (forced by IMAP_SENT_FOLDER)')
        elif chosen:
            print(f'Sent folder: {chosen!r}  (discovered)')
        else:
            print('No Sent folder found. Pick one from the list above and set '
                  'IMAP_SENT_FOLDER to it.')
            return 1
    finally:
        try:
            imap.logout()
        except Exception:                                      # noqa: BLE001
            pass

    if not args.append_test:
        print('\nOK — no test message filed. Re-run with --append-test to '
              'prove the APPEND itself.')
        return 0

    probe = EmailMessage()
    probe['Subject'] = 'GBL Master — Sent folder test'
    probe['From'] = os.getenv('SMTP_FROM') or user
    probe['To'] = os.getenv('SMTP_FROM') or user
    probe['Date'] = formatdate(localtime=True)
    probe['Message-ID'] = make_msgid()
    probe.set_content(
        'Written directly into the Sent folder by '
        'scripts/jobs/mail_sent_folder_check.py at '
        f'{datetime.now():%Y-%m-%d %H:%M:%S}.\n\n'
        'It was not delivered to anyone. Safe to delete.\n')

    filed, reason = file_as_sent(probe)
    print(f'\nAPPEND: {"OK" if filed else "FAILED"} — {reason}')
    return 0 if filed else 1


if __name__ == '__main__':
    sys.exit(main())

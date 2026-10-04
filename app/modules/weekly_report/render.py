"""Turn the stored email draft into what actually gets sent.

The draft is held as lightly-marked text — `**bold**` and `- ` bullets — because
that is the form a person can edit in a textarea without fighting markup, and
the form the model produces most reliably. This module renders it two ways:

  markdown_to_html()  the HTML part, where bold and bullets actually render
  to_plain_text()     the plain-text alternative, for clients that refuse HTML

Both are attached to the same message (multipart/alternative), so the reader's
client picks. That is why the draft is NOT stored as HTML: one source of truth
that is editable, with both wire formats derived from it.

The sender's signature is appended by signature_html() / signature_text() rather
than living in the draft. The app speaks SMTP directly, so it never passes
through the webmail client that would otherwise append it, and keeping it out of
the draft means it cannot be edited away by accident or re-written by the model.

The supported subset is deliberately tiny — bold, bullets, paragraphs. It is not
a Markdown implementation and does not try to be. Everything is HTML-escaped
BEFORE any markup is inserted, so a stray `<` in an edited draft cannot break
the email or inject anything.
"""
import re
from html import escape
from pathlib import Path

_BOLD = re.compile(r'\*\*(.+?)\*\*', re.S)
_BULLET = re.compile(r'^[-*•]\s+(.*)$')
_HEADING_HASH = re.compile(r'^\s{0,3}#{1,6}\s*')

# Inline styles, not a stylesheet: mail clients strip <style> blocks
# unpredictably, and Gmail in particular ignores anything but inline CSS.
_BODY_STYLE = (
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
    "Helvetica,Arial,sans-serif;font-size:14px;line-height:1.55;color:#1a1a1a"
)
_P_STYLE = 'margin:0 0 12px 0'
_UL_STYLE = 'margin:0 0 14px 0;padding-left:22px'
_LI_STYLE = 'margin:0 0 7px 0'


def _inline(text: str) -> str:
    """Escape, then apply inline marks. Order matters — escaping after would
    destroy the tags this inserts."""
    return _BOLD.sub(r'<strong>\1</strong>', escape(text))


def markdown_to_html(text: str) -> str:
    """Render the draft as an HTML fragment (no <html>/<body> wrapper)."""
    if not text:
        return ''

    blocks: list = []
    para: list = []
    items: list = []

    def flush_para():
        if para:
            blocks.append(f'<p style="{_P_STYLE}">' + '<br>'.join(para) + '</p>')
            para.clear()

    def flush_items():
        if items:
            lis = ''.join(f'<li style="{_LI_STYLE}">{i}</li>' for i in items)
            blocks.append(f'<ul style="{_UL_STYLE}">{lis}</ul>')
            items.clear()

    for raw in text.split('\n'):
        line = _HEADING_HASH.sub('', raw.strip())
        if not line:
            flush_items()
            flush_para()
            continue
        bullet = _BULLET.match(line)
        if bullet:
            # A bullet ends any open paragraph but continues an open list.
            flush_para()
            items.append(_inline(bullet.group(1)))
        else:
            flush_items()
            para.append(_inline(line))

    flush_items()
    flush_para()
    return '\n'.join(blocks)


# The sender's mail-server signature, reproduced so the SMTP path matches what
# the webmail client appends. One sender identity for this report, so these are
# literals rather than configuration.
#
# Follows the company-wide template issued 2026-10-02: company line, then name,
# then the contact rows. That template carries no job title, so neither does
# this. "Corporation" is spelt correctly here; the issued template misspells it
# "Coporation" — a deliberate departure, agreed with the sender.
SIGNATURE = {
    'company': 'Global Link Corporation',
    'name': 'Lê Minh',
    'email': 'minhle@globallink.vn',
    'phone': '0903951092',
    'home': 'www.runwayvietnam.com',
    'address': '20 Dang Tat street, Tan Dinh District, Ho Chi Minh City, '
               'Vietnam.',
}

# The logo sits to the LEFT of the text block, with a vertical rule between
# them. The rule is part of the image rather than a CSS border: borders on
# table cells are the first thing Outlook's renderer disagrees about, and a
# one-pixel line that lands in the wrong place looks like a defect.
LOGO_PATH = Path(__file__).resolve().parent / 'assets' / 'runway_logo.png'
LOGO_CID = 'runway-logo'
LOGO_MIME = 'image/png'
LOGO_WIDTH = 150          # display px; the file is 330 wide, so it stays sharp
LOGO_HEIGHT = 117         # 330x257 held to ratio — stated so clients reserve
                          # the space before the image loads


def signature_logo():
    """(cid, mime, bytes) for the logo, or None when the file is missing.

    Missing art must not stop a report going out: the figures are the point of
    the email. `signature_html` drops the image column in that case and the
    text block simply starts at the left margin.
    """
    try:
        return (LOGO_CID, LOGO_MIME, LOGO_PATH.read_bytes())
    except OSError:
        return None


_SIG_COMPANY = ('margin:0 0 2px 0;font-size:14px;font-weight:700;'
                'color:#5f6368')
_SIG_NAME = 'margin:0 0 8px 0;font-size:15px;font-weight:700;color:#1a1a1a'
_SIG_TABLE = 'border-collapse:collapse;font-size:13px;line-height:1.5'
_SIG_LABEL = ('padding:0 6px 2px 0;font-weight:700;color:#5f6368;'
              'vertical-align:top;white-space:nowrap')
_SIG_VALUE = 'padding:0 22px 2px 0;color:#1a1a1a;vertical-align:top'
_SIG_LOGO_CELL = 'padding:0 18px 0 0;vertical-align:middle'
_SIG_LINK = 'color:#1155cc;text-decoration:underline'

# The EMAIL ADDRESS is plain text, not a mailto: anchor. Clients style a real
# <a> blue and underlined, which reads as "click me" on an address nobody needs
# to click — anyone replying uses Reply. The WEBSITE is a genuine link: it is
# the company's own site and following it is the point of putting it there.
HOMEPAGE_URL = 'https://www.runwayvietnam.com'


def signature_html(with_logo=True):
    """The signature as an HTML fragment: logo, rule, then the text block.

    Laid out with tables rather than flex or float — Outlook renders Word's
    HTML engine, which supports neither, and a signature that collapses into a
    stack in the one client the board reads mail in is worse than no logo.
    """
    s = SIGNATURE
    email = escape(s['email'])
    rows = (
        f'<tr>'
        f'<td style="{_SIG_LABEL}">Email:</td>'
        f'<td style="{_SIG_VALUE}">{email}</td>'
        f'<td style="{_SIG_LABEL}">Phone:</td>'
        f'<td style="{_SIG_VALUE}">{escape(s["phone"])}</td>'
        f'</tr>'
        f'<tr>'
        f'<td style="{_SIG_LABEL}">Home:</td>'
        f'<td style="{_SIG_VALUE}" colspan="3">'
        f'<a href="{HOMEPAGE_URL}" style="{_SIG_LINK}">{escape(s["home"])}</a>'
        f'</td>'
        f'</tr>'
        f'<tr>'
        f'<td style="{_SIG_LABEL}">Address:</td>'
        f'<td style="{_SIG_VALUE}" colspan="3">{escape(s["address"])}</td>'
        f'</tr>'
    )
    text_block = (
        f'<p style="{_SIG_COMPANY}">{escape(s["company"])}</p>'
        f'<p style="{_SIG_NAME}">{escape(s["name"])}</p>'
        f'<table style="{_SIG_TABLE}" cellpadding="0" cellspacing="0">{rows}</table>'
    )

    logo_cell = ''
    if with_logo and LOGO_PATH.exists():
        logo_cell = (
            f'<td style="{_SIG_LOGO_CELL}">'
            f'<img src="cid:{LOGO_CID}" width="{LOGO_WIDTH}" '
            f'height="{LOGO_HEIGHT}" alt="Runway" '
            f'style="display:block;border:0;outline:none;text-decoration:none">'
            f'</td>'
        )
    return (
        f'<table style="margin:18px 0 0 0;border-collapse:collapse" '
        f'cellpadding="0" cellspacing="0"><tr>{logo_cell}'
        f'<td style="vertical-align:middle">{text_block}</td></tr></table>'
    )


def signature_text():
    """The signature as plain text. No logo — plain text cannot carry one, and
    an "[image]" placeholder tells the reader nothing."""
    s = SIGNATURE
    return (
        '\n--\n'
        f'{s["company"]}\n'
        f'{s["name"]}\n\n'
        f'Email:   {s["email"]}\n'
        f'Phone:   {s["phone"]}\n'
        f'Home:    {s["home"]}\n'
        f'Address: {s["address"]}'
    )


def html_document(fragment: str) -> str:
    """Wrap a fragment in the minimal document mail clients expect."""
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'</head><body style="{_BODY_STYLE}">{fragment}</body></html>'
    )


def to_plain_text(text: str) -> str:
    """The plain-text alternative: marks removed, bullets kept as '- '.

    Bullets survive because they read perfectly well in plain text and carry the
    structure; only the bold markers go, since `**` renders literally and adds
    nothing without styling.
    """
    if not text:
        return text or ''
    out = []
    for raw in text.split('\n'):
        line = _HEADING_HASH.sub('', raw.rstrip())
        bullet = _BULLET.match(line.strip())
        if bullet:
            # Concatenation, not an f-string: a backslash inside an f-string
            # EXPRESSION is a SyntaxError before Python 3.12 (PEP 701 relaxed
            # it). Production runs 3.10, so the f-string form took the whole
            # app down at import — this module is reached from `app.main`.
            out.append('- ' + _BOLD.sub(r'\1', bullet.group(1)))
        else:
            out.append(_BOLD.sub(r'\1', line))
    return '\n'.join(out)

"""Turn the stored email draft into what actually gets sent.

The draft is held as lightly-marked text — `**bold**` and `- ` bullets — because
that is the form a person can edit in a textarea without fighting markup, and
the form the model produces most reliably. This module renders it two ways:

  markdown_to_html()  the HTML part, where bold and bullets actually render
  to_plain_text()     the plain-text alternative, for clients that refuse HTML

Both are attached to the same message (multipart/alternative), so the reader's
client picks. That is why the draft is NOT stored as HTML: one source of truth
that is editable, with both wire formats derived from it.

The supported subset is deliberately tiny — bold, bullets, paragraphs. It is not
a Markdown implementation and does not try to be. Everything is HTML-escaped
BEFORE any markup is inserted, so a stray `<` in an edited draft cannot break
the email or inject anything.
"""
import re
from html import escape

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

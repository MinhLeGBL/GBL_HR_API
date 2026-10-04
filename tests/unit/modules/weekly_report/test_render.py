"""Unit tests for app.modules.weekly_report.render.

The draft is stored as lightly-marked text and rendered two ways at send time:
HTML (where bold and bullets actually show) and a plain-text alternative.
"""
from app.modules.weekly_report.render import (SIGNATURE, html_document,
                                                markdown_to_html,
                                                signature_html,
                                                signature_text,
                                                to_plain_text)


class TestBold:
    def test_converts_bold_to_strong(self):
        assert '<strong>giảm 4.4%</strong>' in markdown_to_html('Doanh thu **giảm 4.4%**.')

    def test_leaves_parenthetical_detail_plain(self):
        # The house style bolds the claim but not the supporting numbers.
        html = markdown_to_html('Doanh thu **giảm 4.4%** (113 so với 106).')
        assert '(113 so với 106)' in html
        assert '<strong>(113' not in html

    def test_handles_several_bold_runs_on_one_line(self):
        html = markdown_to_html('**a** giữa **b**')
        assert html.count('<strong>') == 2

    def test_a_lone_asterisk_is_not_markup(self):
        assert '5*3' in markdown_to_html('Giá 5*3')


class TestBullets:
    def test_builds_a_list(self):
        html = markdown_to_html('- một\n- hai')
        assert html.count('<li') == 2
        assert html.count('<ul') == 1

    def test_a_blank_line_closes_the_list(self):
        html = markdown_to_html('- một\n\n- hai')
        assert html.count('<ul') == 2

    def test_accepts_the_bullet_characters_the_model_might_use(self):
        for ch in ('-', '*', '•'):
            assert '<li' in markdown_to_html(f'{ch} một')

    def test_a_heading_line_before_bullets_becomes_its_own_paragraph(self):
        html = markdown_to_html('**1. Tuần gần nhất**\n- một')
        assert '<p' in html and '<ul' in html
        assert html.index('<p') < html.index('<ul')


class TestEscaping:
    """The draft is editable in a textarea, so anything can end up in it."""

    def test_escapes_angle_brackets(self):
        html = markdown_to_html('Doanh thu < 5 và a > b')
        assert '&lt;' in html and '&gt;' in html

    def test_a_typed_html_tag_is_shown_not_executed(self):
        html = markdown_to_html('<script>alert(1)</script>')
        assert '<script>' not in html
        assert '&lt;script&gt;' in html

    def test_escapes_ampersands(self):
        assert '&amp;' in markdown_to_html('R&D')

    def test_bold_still_works_around_escaped_content(self):
        html = markdown_to_html('**a < b**')
        assert '<strong>a &lt; b</strong>' in html


class TestPlainTextAlternative:
    def test_removes_bold_markers(self):
        assert to_plain_text('Doanh thu **giảm 4.4%**.') == 'Doanh thu giảm 4.4%.'

    def test_KEEPS_bullets_because_they_read_fine_in_plain_text(self):
        # Bullets carry the structure; only the bold markers are noise without
        # styling.
        assert to_plain_text('- một\n- hai') == '- một\n- hai'

    def test_normalises_other_bullet_characters_to_a_dash(self):
        assert to_plain_text('• một') == '- một'

    def test_does_not_escape_anything(self):
        # It is text, not HTML — escaping would show &lt; to the reader.
        assert to_plain_text('a < b') == 'a < b'


class TestHtmlDocument:
    def test_wraps_with_charset_for_vietnamese(self):
        doc = html_document('<p>Tuần</p>')
        assert 'charset="utf-8"' in doc
        assert '<p>Tuần</p>' in doc

    def test_styles_are_inline_not_a_stylesheet(self):
        # Mail clients strip <style> blocks unpredictably; Gmail ignores
        # everything but inline CSS.
        doc = html_document(markdown_to_html('- một'))
        assert '<style' not in doc
        assert 'style="' in doc


class TestEmptyInput:
    def test_empty_renders_empty_rather_than_raising(self):
        assert markdown_to_html('') == ''
        assert to_plain_text('') == ''
        assert markdown_to_html(None) == ''


class TestSignature:
    """The sender's mail-server signature, appended at send time.

    It lives in code rather than in the draft or the stored body_template, so a
    deploy ships it and neither an edit in the textarea nor a model-written
    draft can drop it.
    """

    def test_html_carries_every_field(self):
        html = signature_html()
        for key in ('name', 'title', 'company', 'phone', 'address'):
            assert SIGNATURE[key] in html

    def test_email_is_a_mailto_link(self):
        html = signature_html()
        assert f'href="mailto:{SIGNATURE["email"]}"' in html

    def test_name_is_bold_and_title_is_muted(self):
        html = signature_html()
        assert 'font-weight:700' in html.split(SIGNATURE['name'])[0]
        assert '#5f6368' in html.split(SIGNATURE['title'])[0]

    def test_styles_stay_inline_like_the_rest_of_the_email(self):
        assert '<style' not in signature_html()

    def test_plain_text_keeps_the_separator_and_the_fields(self):
        text = signature_text()
        assert text.lstrip().startswith('---')
        for key in ('name', 'title', 'company', 'email', 'phone', 'address'):
            assert SIGNATURE[key] in text

    def test_vietnamese_survives_escaping(self):
        # escape() must not mangle the diacritics in the name or address.
        html = signature_html()
        assert 'Lê Minh' in html
        assert 'Phó giám đốc' in html
        assert 'Đặng Tất' in html

    def test_appends_after_the_draft_without_swallowing_it(self):
        draft = 'Dear all,\n\nBest regards,\n'
        combined = html_document(markdown_to_html(draft) + signature_html())
        assert 'Dear all,' in combined
        assert SIGNATURE['name'] in combined
        assert combined.index('Dear all,') < combined.index(SIGNATURE['name'])

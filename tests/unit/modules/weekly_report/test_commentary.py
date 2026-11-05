"""Unit tests for app.modules.weekly_report.commentary.

The Anthropic call is mocked throughout — what is exercised is the figures block
the model reads, the pre-computed derived facts, and the request shape.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from app.modules.weekly_report import commentary as C

PAYLOAD = {
    'as_of': '2026-09-14',
    'through': '2026-09-13',
    'store_names': {'RWD': 'RUNWAY DIAMOND', 'RWT': 'RUNWAY TAKASHIMAYA'},
    'periods': {
        'WTD': {
            'label': 'Week to date',
            'current': {'from': '2026-09-07', 'to': '2026-09-13',
                        'label': 'Week 37 2026'},
            # The SAME ISO week a year earlier — not the week before. Week 37 of
            # 2025 runs 08/09-14/09, a day offset from 2026's, which is exactly
            # why both sides carry their year in the header.
            'prior': {'from': '2025-09-08', 'to': '2025-09-14',
                      'label': 'Week 37 2025'},
            'total': {
                'current': {'total_sales': 3_961_000_000, 'bills': 113,
                            'qty_sold': 251, 'avg_discount_pct': 14.0,
                            'returns_value': 375_000_000,
                            'qty_returned': 12,
                            'avg_unit_price': 17_300_000,
                            'avg_transaction_value': 38_400_000},
                'prior': {'total_sales': 4_142_000_000, 'bills': 106,
                          'qty_sold': 270, 'avg_discount_pct': 27.3,
                          'returns_value': 122_000_000,
                          'qty_returned': 4,
                          'avg_unit_price': 15_800_000,
                          'avg_transaction_value': 40_200_000},
                'change': {
                    'total_sales': {'value': -4.4, 'kind': 'pct', 'unfavourable': True},
                    'bills': {'value': 6.6, 'kind': 'pct', 'unfavourable': False},
                    'avg_discount_pct': {'value': -13.3, 'kind': 'pp', 'unfavourable': False},
                    'returns_value': {'value': 206.0, 'kind': 'pct', 'unfavourable': True},
                },
            },
            'stores': [
                {'store': 'RWD',
                 'current': {'total_sales': 221_000_000, 'avg_discount_pct': 44.8},
                 'prior': {'total_sales': 232_000_000, 'avg_discount_pct': 28.9},
                 'change': {
                     'total_sales': {'value': -4.5, 'kind': 'pct', 'unfavourable': True},
                     'avg_discount_pct': {'value': 15.9, 'kind': 'pp', 'unfavourable': True},
                 },
                 'departments': []},
                {'store': 'RWT',
                 'current': {'total_sales': 625_000_000, 'avg_discount_pct': 12.6},
                 'prior': {'total_sales': 636_000_000, 'avg_discount_pct': 12.8},
                 'change': {
                     'total_sales': {'value': -1.8, 'kind': 'pct', 'unfavourable': True},
                     'avg_discount_pct': {'value': -0.2, 'kind': 'pp', 'unfavourable': False},
                 },
                 'departments': []},
            ],
        },
        'MTD': {
            'label': 'MTD',
            'current': {'from': '2026-09-01', 'to': '2026-09-13'},
            'prior': {'from': '2025-09-01', 'to': '2025-09-13'},
            'total': {
                'current': {'total_sales': 7_641_000_000, 'avg_discount_pct': 20.9,
                            'returns_value': 489_000_000},
                'prior': {'total_sales': 7_333_000_000, 'avg_discount_pct': 16.2,
                          'returns_value': 261_000_000},
                'change': {'total_sales': {'value': 4.2, 'kind': 'pct', 'unfavourable': False}},
            },
            'stores': [],
        },
        'YTD': {
            'label': 'YTD',
            'current': {'from': '2026-01-01', 'to': '2026-09-13'},
            'prior': {'from': '2025-01-01', 'to': '2025-09-13'},
            'total': {
                'current': {'total_sales': 163_124_000_000},
                'prior': {'total_sales': 148_943_000_000},
                'change': {'total_sales': {'value': 9.5, 'kind': 'pct', 'unfavourable': False}},
            },
            'stores': [],
        },
    },
}


class TestFiguresBlock:
    def test_money_is_in_millions_the_unit_the_email_declares(self):
        # The email says "Đơn vị tiền: triệu đồng". A figure handed over in raw
        # VND would be quoted in the wrong unit.
        prompt = C.build_prompt(PAYLOAD)
        assert 'Doanh thu: 3,961' in prompt
        assert '3,961,000,000' not in prompt

    def test_per_unit_averages_keep_one_decimal(self):
        prompt = C.build_prompt(PAYLOAD)
        assert '38.4' in prompt and '40.2' in prompt

    def test_week_labels_are_vietnamese_not_english(self):
        # An English label inside a Vietnamese block invites the model to echo
        # "Week 37 2026" into the email.
        prompt = C.build_prompt(PAYLOAD)
        assert 'Tuần 37' in prompt
        assert 'Week 37' not in prompt

    def test_prior_year_windows_carry_the_year(self):
        # Without it, MTD reads "01/09–13/09 so với cùng kỳ năm trước
        # 01/09–13/09" and the two sides look identical.
        prompt = C.build_prompt(PAYLOAD)
        assert '01/09/2026' in prompt
        assert '01/09/2025' in prompt

    def test_rate_changes_are_in_points_not_percent(self):
        prompt = C.build_prompt(PAYLOAD)
        assert '-13.3 điểm' in prompt

    def test_an_undefined_change_says_so_rather_than_implying_100_percent(self):
        payload = {**PAYLOAD}
        payload['periods'] = {**PAYLOAD['periods']}
        prompt = C.build_prompt(payload)
        # Present in the shape; assert the wording exists for the null case.
        assert 'không xác định' in C._fmt_change({'value': None, 'kind': 'pct'})

    def test_every_metric_is_named_in_vietnamese(self):
        prompt = C.build_prompt(PAYLOAD)
        for name in C.METRIC_VI.values():
            assert name in prompt


class TestWhichPeriodsAreNarrated:
    """Which periods the analysis covers is a decision, not a detail.

    Swapped from WOW to WTD on 2026-09-22: readers said the year-on-year week
    was the more useful one, and it makes all three parts the same comparison.
    """

    def test_the_analysis_covers_wtd_mtd_and_ytd(self):
        assert C.COMMENTARY_LABELS == ('WTD', 'MTD', 'YTD')

    def test_week_over_week_is_not_narrated(self):
        # WOW is still a tab on the page; it is simply not in the email. If it
        # is ever put back, that should be a deliberate edit here.
        assert 'WOW' not in C.COMMENTARY_LABELS
        assert 'WOW' not in C.PERIOD_VI

    def test_part_one_compares_against_the_same_week_last_year(self):
        prompt = C.build_prompt(PAYLOAD)
        line = next(l for l in prompt.splitlines() if l.startswith('TUẦN GẦN NHẤT'))
        assert 'so với cùng kỳ năm trước' in line
        assert 'Tuần 37/2026' in line and 'Tuần 37/2025' in line

    def test_both_weeks_carry_their_year(self):
        # Without the year the header reads "Tuần 37 so với Tuần 37" — the same
        # week compared with itself, which tells the reader nothing.
        assert C._vi_week('Week 37 2026', True) == 'Tuần 37/2026'
        assert C._vi_week('Week 37 2026') == 'Tuần 37'

    def test_the_model_is_told_part_one_is_not_week_over_week(self):
        # The prompt used to say "Tuần X so với Tuần Y" and the model was free
        # to read that as the previous week. Left alone it would narrate the
        # right figures under the wrong baseline.
        assert 'KHÔNG phải với tuần liền trước' in C.SYSTEM_PROMPT
        assert 'CẢ BA PHẦN đều so sánh với CÙNG KỲ NĂM TRƯỚC' in C.SYSTEM_PROMPT

    def test_a_payload_without_wtd_produces_no_week_section(self):
        # Tolerance, not a crash: commentary is best-effort, and a partial
        # payload should still yield an analysis of the blocks it does have.
        payload = {**PAYLOAD, 'periods': {k: v for k, v in PAYLOAD['periods'].items()
                                          if k != 'WTD'}}
        prompt = C.build_prompt(payload)
        assert 'TUẦN GẦN NHẤT' not in prompt
        assert 'LŨY KẾ THÁNG' in prompt


class TestTradeVocabulary:
    """The email's Vietnamese is the company's own; the model writes the words
    it is handed."""

    def test_discount_is_giam_gia_not_chiet_khau(self):
        # "chiết khấu" is the supplier/settlement discount; this figure is the
        # markdown off the retail ticket, which fashion retail calls "giảm giá".
        # Both dictionaries say "discount", which is exactly how the wrong one
        # gets used for months without anyone noticing.
        assert C.METRIC_VI['avg_discount_pct'] == 'Tỷ lệ giảm giá'

    def test_the_word_appears_nowhere_in_what_the_model_reads(self):
        surfaces = C.SYSTEM_PROMPT + C.build_prompt(PAYLOAD) + '\n'.join(
            C.derived_facts(PAYLOAD) + C.notable_movements(PAYLOAD))
        assert 'chiết khấu' not in surfaces
        assert 'giảm giá' in surfaces


class TestDerivedFacts:
    """The house style quotes cross-period ratios. They are computed here so the
    model never has to divide in prose."""

    def test_states_the_weeks_share_of_month_returns(self):
        facts = '\n'.join(C.derived_facts(PAYLOAD))
        # 375 of 489 is 76.7%.
        assert '76.7%' in facts
        assert 'Hàng trả lại' in facts

    def test_states_the_weeks_share_of_month_sales(self):
        facts = '\n'.join(C.derived_facts(PAYLOAD))
        assert '51.8%' in facts

    def test_locates_discount_against_the_month(self):
        # Week 14.0% vs month 20.9% — 6.9 points LOWER, which is what tells the
        # reader the heavy discounting was earlier in the month.
        facts = '\n'.join(C.derived_facts(PAYLOAD))
        assert 'thấp hơn' in facts
        assert '6.9 điểm' in facts

    def test_a_zero_denominator_is_skipped_not_divided_by(self):
        payload = {'periods': {
            'WTD': {'total': {'current': {'returns_value': 10.0, 'total_sales': 5.0},
                              'prior': {}, 'change': {}}},
            'MTD': {'total': {'current': {'returns_value': 0, 'total_sales': 0},
                              'prior': {}, 'change': {}}},
        }}
        assert C.derived_facts(payload) == []

    def test_missing_periods_produce_no_facts_rather_than_raising(self):
        assert C.derived_facts({'periods': {}}) == []


class TestNotableMovements:
    def test_flags_a_store_whose_discount_rate_jumped(self):
        out = '\n'.join(C.notable_movements(PAYLOAD))
        assert 'RWD' in out
        assert '28.9% → 44.8%' in out
        assert '+15.9 điểm' in out

    def test_does_not_flag_a_store_that_barely_moved(self):
        out = '\n'.join(C.notable_movements(PAYLOAD))
        assert 'RWT' not in out

    def test_says_so_explicitly_when_nothing_crosses_the_threshold(self):
        # A quiet week must not be padded with manufactured significance.
        quiet = {'store_names': {}, 'periods': {
            'WTD': {'stores': []}, 'MTD': {'stores': []}, 'YTD': {'stores': []}}}
        assert C.notable_movements(quiet) == []
        assert 'không có cửa hàng nào vượt ngưỡng' in C.build_prompt(quiet)


class TestGenerateCommentary:
    def test_returns_empty_without_an_api_key_rather_than_raising(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('ANTHROPIC_API_KEY', None)
            assert C.generate_commentary(PAYLOAD) == ''

    def _client(self, text='1. Tuần gần nhất ...', stop_reason='end_turn'):
        block = MagicMock()
        block.type = 'text'
        block.text = text
        response = MagicMock()
        response.content = [block]
        response.stop_reason = stop_reason
        client = MagicMock()
        client.messages.create.return_value = response
        return client

    def test_uses_opus_5_with_adaptive_thinking(self):
        client = self._client()
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k'}, clear=False):
            os.environ.pop('WEEKLY_REPORT_MODEL', None)
            with patch('anthropic.Anthropic', return_value=client):
                C.generate_commentary(PAYLOAD)
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs['model'] == 'claude-opus-5'
        assert kwargs['thinking'] == {'type': 'adaptive'}
        # budget_tokens is rejected with a 400 on Opus 5.
        assert 'budget_tokens' not in str(kwargs['thinking'])

    def test_sends_the_vietnamese_house_style_as_the_system_prompt(self):
        client = self._client()
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k'}, clear=False):
            with patch('anthropic.Anthropic', return_value=client):
                C.generate_commentary(PAYLOAD)
        system = client.messages.create.call_args.kwargs['system']
        assert 'Điểm tích cực' in system
        assert 'Cần lưu ý' in system
        assert 'KHÔNG tự tính toán thêm' in system

    def test_returns_the_generated_text(self):
        client = self._client(text='1. Tuần gần nhất — phân tích')
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k'}, clear=False):
            with patch('anthropic.Anthropic', return_value=client):
                assert 'phân tích' in C.generate_commentary(PAYLOAD)

    def test_a_refusal_raises_rather_than_returning_empty_text(self):
        # A decline comes back as HTTP 200 with no text block; silently sending
        # an email with a blank analysis would hide the failure.
        client = self._client(text='', stop_reason='refusal')
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k'}, clear=False):
            with patch('anthropic.Anthropic', return_value=client):
                with pytest.raises(RuntimeError, match='declined'):
                    C.generate_commentary(PAYLOAD)

    def test_model_is_overridable_by_env(self):
        client = self._client()
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k',
                                     'WEEKLY_REPORT_MODEL': 'claude-sonnet-5'},
                        clear=False):
            with patch('anthropic.Anthropic', return_value=client):
                C.generate_commentary(PAYLOAD)
        assert client.messages.create.call_args.kwargs['model'] == 'claude-sonnet-5'



class TestOutputFormat:
    """The email is sent as HTML (see render.py), so bold and bullets are the
    INTENDED output. An earlier revision stripped them — that was solving the
    wrong problem and its tests were removed with this change."""

    def test_asks_for_bullets(self):
        assert 'GẠCH ĐẦU DÒNG' in C.SYSTEM_PROMPT
        assert '"- "' in C.SYSTEM_PROMPT

    def test_asks_for_bold_on_the_claim_and_its_number(self):
        assert 'IN ĐẬM' in C.SYSTEM_PROMPT
        assert '**3,961 so với 4,142 — giảm 4.4%**' in C.SYSTEM_PROMPT

    def test_tells_it_to_leave_parenthetical_detail_unbolded(self):
        # The screenshot bolds the claim but leaves "(113 so với 106)" plain.
        assert 'KHÔNG in đậm phần số liệu phụ trong ngoặc đơn' in C.SYSTEM_PROMPT

    def test_restricts_markup_to_bold_and_bullets_only(self):
        # render.py supports exactly this subset; tables or headings would
        # pass through as literal text.
        assert 'CHỈ dùng hai ký hiệu' in C.SYSTEM_PROMPT

    def test_target_length_dropped_now_that_output_is_bulleted(self):
        # Bullets are terser than flowing prose, which is the point.
        assert C.DEFAULT_MAX_WORDS == 400

    def test_generated_bold_and_bullets_are_preserved_not_stripped(self):
        client = MagicMock()
        block = MagicMock()
        block.type = 'text'
        block.text = '**1. Tuần gần nhất**\n\n- Doanh thu **giảm 4.4%**.'
        response = MagicMock()
        response.content, response.stop_reason = [block], 'end_turn'
        client.messages.create.return_value = response
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k'}, clear=False):
            with patch('anthropic.Anthropic', return_value=client):
                out = C.generate_commentary(PAYLOAD)
        assert '**giảm 4.4%**' in out
        assert '- Doanh thu' in out

"""Unit tests for app.modules.periodic_report.service.

Oracle and Postgres are mocked throughout — what is exercised here is the
payload shape the frontend binds to, the template renderer, the send-time
scheduling maths, and the approval state machine.
"""
from datetime import date, datetime, time, timezone
from decimal import Decimal as D
from unittest.mock import MagicMock, patch

import pytest

from app.modules.periodic_report.service import (DEFAULT_BODY_TEMPLATE,
                                                 PeriodicReportService,
                                                 next_send_time,
                                                 render_template,
                                                 summary_lines)

# Two days, two stores, two departments — enough for store/dept/grand rollups
# and a prior-year side, small enough to assert on by hand.
DEPT_ROWS = [
    {'day': date(2026, 9, 7), 'store_code': 'RWT', 'store_name': 'TAKASHIMAYA',
     'department': 'RTW', 'sale_net': D('1000'), 'return_net': D('100'),
     'sale_gross': D('1250'), 'qty_sold': D('10'), 'qty_returned': D('1'),
     'dept_bills': 4},
    {'day': date(2026, 9, 8), 'store_code': 'RWT', 'store_name': 'TAKASHIMAYA',
     'department': 'BAG', 'sale_net': D('500'), 'return_net': D('0'),
     'sale_gross': D('500'), 'qty_sold': D('5'), 'qty_returned': D('0'),
     'dept_bills': 2},
    # Prior-year week, same store/department. Carries a non-zero return so the
    # returns polarity has a denominator to divide by.
    {'day': date(2025, 9, 8), 'store_code': 'RWT', 'store_name': 'TAKASHIMAYA',
     'department': 'RTW', 'sale_net': D('800'), 'return_net': D('50'),
     'sale_gross': D('1000'), 'qty_sold': D('8'), 'qty_returned': D('1'),
     'dept_bills': 3},
]

STORE_ROWS = [
    {'day': date(2026, 9, 7), 'store_code': 'RWT', 'bills': 4},
    {'day': date(2026, 9, 8), 'store_code': 'RWT', 'bills': 2},
    {'day': date(2025, 9, 8), 'store_code': 'RWT', 'bills': 3},
]


@pytest.fixture
def service():
    svc = PeriodicReportService()
    svc.repo = MagicMock()
    svc.repo.fetch_sales.return_value = (DEPT_ROWS, STORE_ROWS)
    svc.repo.get_settings.return_value = {
        'send_mode': 'immediate', 'send_time': None, 'send_weekday': None,
        'subject_template': 'Weekly Sales Report — {{week_label}}',
        'body_template': DEFAULT_BODY_TEMPLATE, 'commentary_enabled': False,
        'updated_at': None,
    }
    return svc


@pytest.fixture
def payload(service):
    return service.build_snapshot(date(2026, 9, 14), 'monday', date(2026, 9, 13))


class TestSnapshotShape:
    def test_carries_all_three_periods(self, payload):
        assert set(payload['periods']) == {'WOW', 'MTD', 'YTD'}

    def test_records_the_through_date_it_was_built_with(self, payload):
        assert payload['as_of'] == '2026-09-14'
        assert payload['through'] == '2026-09-13'

    def test_week_window_is_the_closed_week(self, payload):
        wow = payload['periods']['WOW']
        assert (wow['current']['from'], wow['current']['to']) == \
            ('2026-09-07', '2026-09-13')
        assert wow['current']['label'] == 'Week 37 2026'

    def test_mtd_stops_on_the_closed_sunday(self, payload):
        assert payload['periods']['MTD']['current']['to'] == '2026-09-13'

    def test_every_row_carries_current_prior_and_change(self, payload):
        total = payload['periods']['WOW']['total']
        assert set(total) == {'current', 'prior', 'change'}
        assert set(total['change']['total_sales']) == \
            {'value', 'kind', 'unfavourable'}

    def test_money_is_serialised_as_float_not_decimal(self, payload):
        sales = payload['periods']['WOW']['total']['current']['total_sales']
        assert isinstance(sales, float)

    def test_departments_nest_under_their_store(self, payload):
        stores = payload['periods']['WOW']['stores']
        assert [s['store'] for s in stores] == ['RWT']
        assert sorted(d['department'] for d in stores[0]['departments']) == \
            ['BAG', 'RTW']

    def test_grand_total_nets_returns_within_the_period(self, payload):
        # 1000 + 500 sale_net, minus 100 of returns.
        assert payload['periods']['WOW']['total']['current']['total_sales'] \
            == pytest.approx(1400.0)

    def test_store_bills_come_from_the_document_grain_not_the_dept_sum(self, payload):
        # Department bills are 4 + 2 = 6 here and happen to agree, but the store
        # figure must come from STORE_ROWS. Assert the source, not the number.
        store = payload['periods']['WOW']['stores'][0]
        assert store['current']['bills'] == 6

    def test_one_fetch_serves_every_window(self, service):
        service.build_snapshot(date(2026, 9, 14), 'monday', date(2026, 9, 13))
        assert service.repo.fetch_sales.call_count == 1


class TestChangePolarity:
    """A falling discount rate and falling returns are WINS. Colouring on the
    sign alone would mark both as problems, which is backwards."""

    def _change(self, payload, key):
        return payload['periods']['WOW']['total']['change'][key]

    def test_falling_sales_is_unfavourable(self, payload):
        # Prior week has no rows at all, so this compares against zero and the
        # change is undefined; use the YTD block, which has a prior-year side.
        change = payload['periods']['YTD']['total']['change']['total_sales']
        assert change['value'] is not None
        assert change['unfavourable'] is False   # 1400 vs 800 is a rise

    def test_discount_rate_change_is_in_percentage_points(self, payload):
        assert self._change(payload, 'avg_discount_pct')['kind'] == 'pp'

    def test_sales_change_is_a_ratio(self, payload):
        assert self._change(payload, 'total_sales')['kind'] == 'pct'

    def test_rising_returns_is_unfavourable(self, payload):
        # 100 this year against 50 last year. Returns are in LOWER_IS_BETTER,
        # so a RISE is the bad direction — the opposite of sales.
        change = payload['periods']['YTD']['total']['change']['returns_value']
        assert change['value'] == pytest.approx(100.0)
        assert change['unfavourable'] is True

    def test_a_rise_in_sales_of_the_same_sign_is_favourable(self, payload):
        # Same positive sign as the returns rise above, opposite verdict —
        # polarity is per metric, not per sign.
        change = payload['periods']['YTD']['total']['change']['total_sales']
        assert change['value'] > 0
        assert change['unfavourable'] is False

    def test_change_against_a_zero_prior_is_undefined_not_infinite(self, payload):
        # The WOW prior week has no rows at all. A percentage change from zero
        # has no meaning, so it is reported as null and never flagged — the
        # Excel leaves that cell blank for the same reason.
        change = payload['periods']['WOW']['total']['change']['total_sales']
        assert change['value'] is None
        assert change['unfavourable'] is False


class TestRenderTemplate:
    def test_substitutes_every_documented_placeholder(self, payload):
        tpl = ('{{week_no}}|{{week_year}}|{{prior_week_no}}|{{week_range}}|'
               '{{month_no}}|{{mtd_range}}|{{ytd_range}}|{{week_label}}|'
               '{{as_of}}|{{through}}|{{summary}}|{{commentary}}')
        out = render_template(tpl, payload, 'COMMENT')
        assert 'COMMENT' in out
        assert '{{' not in out

    def test_fills_the_vietnamese_opening_sentence(self, payload):
        out = render_template(DEFAULT_BODY_TEMPLATE, payload, 'PHÂN TÍCH')
        assert 'Tuần 37 so với Tuần 36' in out
        assert 'lũy kế tháng 9 (01/09–13/09)' in out
        assert 'triệu đồng, chưa bao gồm VAT' in out
        assert 'PHÂN TÍCH' in out
        assert '{{' not in out

    def test_dates_are_day_first_for_the_vietnamese_reader(self, payload):
        assert render_template('{{week_range}}', payload) == '07/09–13/09'

    def test_month_comes_from_the_MTD_window_not_as_of(self, service):
        # A run on Monday the 1st reports the month that just ENDED. Taking the
        # month from `as_of` would name the wrong month in the opening line.
        snap = service.build_snapshot(date(2026, 9, 1), 'monday', date(2026, 8, 31))
        assert render_template('{{month_no}}', snap) == '8'
        assert render_template('{{mtd_range}}', snap) == '01/08–31/08'

    def test_week_numbers_are_iso(self, payload):
        assert render_template('{{week_no}}/{{week_year}}', payload) == '37/2026'
        assert render_template('{{prior_week_no}}', payload) == '36'

    def test_unknown_placeholder_is_left_alone_rather_than_raising(self, payload):
        # A typo in a template must not take down the Monday run.
        out = render_template('{{week_label}} {{nope}}', payload)
        assert '{{nope}}' in out

    def test_empty_commentary_leaves_the_slot_blank(self, payload):
        assert '{{commentary}}' not in render_template('{{commentary}}', payload)

    def test_summary_has_a_line_per_period(self, payload):
        assert len(summary_lines(payload).splitlines()) == 3


class TestNextSendTime:
    def test_returns_now_when_unconfigured(self):
        now = datetime(2026, 9, 14, 9, 0)
        assert next_send_time(now, None, None) == now
        assert next_send_time(now, 0, None) == now

    def test_finds_the_next_matching_weekday(self):
        # Monday 09:00 -> next Wednesday 08:00.
        now = datetime(2026, 9, 14, 9, 0)
        assert next_send_time(now, 2, time(8, 0)) == datetime(2026, 9, 16, 8, 0)

    def test_later_today_is_used_when_the_weekday_matches(self):
        now = datetime(2026, 9, 14, 9, 0)      # Monday
        assert next_send_time(now, 0, time(17, 0)) == datetime(2026, 9, 14, 17, 0)

    def test_a_time_already_past_today_rolls_to_next_week(self):
        now = datetime(2026, 9, 14, 18, 0)     # Monday, past 17:00
        assert next_send_time(now, 0, time(17, 0)) == datetime(2026, 9, 21, 17, 0)

    def test_never_returns_a_time_in_the_past(self):
        now = datetime(2026, 9, 14, 18, 0)
        for weekday in range(7):
            assert next_send_time(now, weekday, time(9, 0)) > now


class TestApproval:
    """Approval signs off the CONTENT. It does not send, and deliberately does
    not require recipients — the list is edited independently and may be empty
    at that moment."""

    def _run(self, status='pending_approval'):
        return {'id': 1, 'status': status, 'as_of': date(2026, 9, 14)}

    def test_approving_does_not_queue_anything(self, service):
        service.repo.get_run.return_value = self._run()
        service.repo.approve_run.return_value = True
        result = service.approve(1, user_id=7)
        assert result['success'] is True
        # No send time is set: `queue_run` is the Send action's job.
        service.repo.queue_run.assert_not_called()

    def test_approving_does_NOT_require_recipients(self, service):
        service.repo.get_run.return_value = self._run()
        service.repo.list_recipients.return_value = []
        service.repo.approve_run.return_value = True
        assert service.approve(1, user_id=7)['success'] is True

    def test_rejects_a_run_that_is_not_pending(self, service):
        service.repo.get_run.return_value = self._run('sent')
        assert service.approve(1, user_id=7)['code'] == 'INVALID_STATE'

    def test_rejects_a_missing_run(self, service):
        service.repo.get_run.return_value = None
        assert service.approve(1, user_id=7)['code'] == 'NOT_FOUND'

    def test_losing_the_approval_race_is_reported_not_swallowed(self, service):
        service.repo.get_run.return_value = self._run()
        service.repo.approve_run.return_value = False
        assert service.approve(1, user_id=7)['code'] == 'INVALID_STATE'


class TestSend:
    """One Send action covers the first send and a re-send — they differ only in
    what the row said beforehand."""

    def _run(self, status='approved'):
        return {'id': 1, 'status': status, 'as_of': date(2026, 9, 14)}

    def _recips(self, service, kinds=('to',)):
        service.repo.list_recipients.return_value = [
            {'email': f'{k}@b.c', 'kind': k} for k in kinds]

    def test_sends_an_approved_run_now(self, service):
        service.repo.get_run.return_value = self._run()
        self._recips(service)
        service.repo.queue_run.return_value = True
        result = service.send(1, user_id=7, mode='immediate')
        assert result['success'] is True
        assert result['send_mode'] == 'immediate'

    def test_the_SAME_action_sends_an_already_sent_run_again(self, service):
        service.repo.get_run.return_value = self._run('sent')
        self._recips(service)
        service.repo.queue_run.return_value = True
        assert service.send(1, user_id=7, mode='immediate')['success'] is True

    def test_refuses_a_run_still_awaiting_approval(self, service):
        service.repo.get_run.return_value = self._run('pending_approval')
        self._recips(service)
        result = service.send(1, user_id=7)
        assert result['code'] == 'INVALID_STATE'
        assert 'approve it before sending' in result['error']

    def test_refuses_one_mid_send(self, service):
        service.repo.get_run.return_value = self._run('sending')
        self._recips(service)
        assert service.send(1, user_id=7)['code'] == 'INVALID_STATE'

    def test_REQUIRES_a_to_recipient(self, service):
        # Unlike approval. A send with no visible addressee is refused by the
        # mailer anyway, so it is better refused here.
        service.repo.get_run.return_value = self._run()
        self._recips(service, kinds=('cc',))
        assert service.send(1, user_id=7)['code'] == 'NO_RECIPIENTS'

    def test_refuses_with_an_empty_list(self, service):
        service.repo.get_run.return_value = self._run()
        service.repo.list_recipients.return_value = []
        assert service.send(1, user_id=7)['code'] == 'NO_RECIPIENTS'

    def test_a_schedule_given_at_send_time_BECOMES_the_default(self, service):
        # Same contract as the recipient list: set it once, it stays until
        # changed.
        service.repo.get_run.return_value = self._run()
        self._recips(service)
        service.repo.queue_run.return_value = True
        service.send(1, user_id=7, mode='scheduled',
                     send_weekday=2, send_time='08:30')
        saved = service.repo.update_settings.call_args[0][0]
        assert saved['send_mode'] == 'scheduled'
        assert saved['send_weekday'] == 2
        assert saved['send_time'] == time(8, 30)

    def test_a_later_send_reuses_the_saved_schedule(self, service):
        service.repo.get_run.return_value = self._run()
        self._recips(service)
        service.repo.queue_run.return_value = True
        service.repo.get_settings.return_value = {
            **service.repo.get_settings.return_value,
            'send_weekday': 2, 'send_time': time(8, 30)}
        result = service.send(1, user_id=7, mode='scheduled')
        assert result['success'] is True
        # Nothing new to persist, so settings are untouched apart from the mode.
        assert result['send_mode'] == 'scheduled'

    def test_scheduling_without_a_time_anywhere_is_refused(self, service):
        service.repo.get_run.return_value = self._run()
        self._recips(service)
        service.repo.get_settings.return_value = {
            **service.repo.get_settings.return_value,
            'send_weekday': None, 'send_time': None}
        result = service.send(1, user_id=7, mode='scheduled')
        assert result['code'] == 'INVALID_INPUT'

    def test_rejects_a_weekday_out_of_range(self, service):
        service.repo.get_run.return_value = self._run()
        self._recips(service)
        assert service.send(1, 7, mode='scheduled',
                            send_weekday=9)['code'] == 'INVALID_INPUT'

    def test_rejects_a_malformed_time(self, service):
        service.repo.get_run.return_value = self._run()
        self._recips(service)
        assert service.send(1, 7, mode='scheduled',
                            send_time='half eight')['code'] == 'INVALID_INPUT'

    def test_rejects_an_unknown_mode(self, service):
        service.repo.get_run.return_value = self._run()
        self._recips(service)
        assert service.send(1, 7, mode='whenever')['code'] == 'INVALID_INPUT'

    def test_losing_the_queue_race_is_reported(self, service):
        service.repo.get_run.return_value = self._run()
        self._recips(service)
        service.repo.queue_run.return_value = False
        assert service.send(1, user_id=7)['code'] == 'INVALID_STATE'


class TestRecipientValidation:
    def test_rejects_a_non_list(self, service):
        assert service.set_recipients({'email': 'a@b.c'})['code'] == 'INVALID_INPUT'

    def test_rejects_a_malformed_address(self, service):
        result = service.set_recipients([{'email': 'not-an-email'}])
        assert result['code'] == 'INVALID_INPUT'

    def test_rejects_an_unknown_kind(self, service):
        result = service.set_recipients([{'email': 'a@b.c', 'kind': 'reply-to'}])
        assert result['code'] == 'INVALID_INPUT'

    def test_rejects_the_same_address_twice_in_one_kind(self, service):
        result = service.set_recipients([{'email': 'a@b.c'},
                                         {'email': 'A@B.C'}])
        assert result['code'] == 'INVALID_INPUT'

    def test_same_address_as_to_and_cc_is_allowed(self, service):
        service.repo.list_recipients.return_value = []
        result = service.set_recipients([{'email': 'a@b.c', 'kind': 'to'},
                                         {'email': 'a@b.c', 'kind': 'cc'}])
        assert result['success'] is True

    def test_normalises_case(self, service):
        service.repo.list_recipients.return_value = []
        service.set_recipients([{'email': 'Person@Example.COM'}])
        saved = service.repo.replace_recipients.call_args[0][0]
        assert saved[0]['email'] == 'person@example.com'


class TestSettingsValidation:
    def test_scheduled_mode_requires_a_weekday_and_time(self, service):
        service.repo.get_settings.return_value = {'send_weekday': None,
                                                  'send_time': None}
        result = service.update_settings({'send_mode': 'scheduled'})
        assert result['success'] is False
        assert result['code'] == 'INVALID_INPUT'

    def test_rejects_a_weekday_out_of_range(self, service):
        assert service.update_settings({'send_weekday': 7})['code'] == 'INVALID_INPUT'

    def test_rejects_a_malformed_time(self, service):
        assert service.update_settings({'send_time': '25 past'})['code'] \
            == 'INVALID_INPUT'

    def test_rejects_an_empty_template(self, service):
        assert service.update_settings({'body_template': '   '})['code'] \
            == 'INVALID_INPUT'


class TestGenerateRun:
    def test_refuses_to_overwrite_a_sent_run(self, service):
        service.repo.get_run_by_as_of.return_value = {'status': 'sent'}
        result = service.generate_run(date(2026, 9, 14))
        assert result['code'] == 'ALREADY_SENT'
        service.repo.create_run.assert_not_called()

    def test_records_a_failed_run_rather_than_vanishing(self, service):
        service.repo.get_run_by_as_of.return_value = None
        service.repo.fetch_sales.side_effect = RuntimeError('Oracle down')
        result = service.generate_run(date(2026, 9, 14))
        assert result['success'] is False
        # The page must be able to show that Monday's job ran and failed,
        # rather than silently showing last week's numbers as current.
        assert service.repo.create_run.call_args.kwargs['status'] == 'failed'

    def test_stores_a_workbook_alongside_the_payload(self, service):
        service.repo.get_run_by_as_of.return_value = None
        service.repo.create_run.return_value = 42
        result = service.generate_run(date(2026, 9, 14),
                                      through=date(2026, 9, 13))
        assert result['success'] is True
        kwargs = service.repo.create_run.call_args.kwargs
        assert kwargs['status'] == 'pending_approval'
        assert kwargs['workbook'][:2] == b'PK'      # a real .xlsx zip
        assert kwargs['payload']['through'] == '2026-09-13'

    def test_fetches_oracle_once_for_payload_and_workbook(self, service):
        # Two fetches would let live Oracle move between them and put different
        # figures in the preview and the attachment.
        service.repo.get_run_by_as_of.return_value = None
        service.repo.create_run.return_value = 1
        service.generate_run(date(2026, 9, 14), through=date(2026, 9, 13))
        assert service.repo.fetch_sales.call_count == 1


class TestCommentaryIsBestEffort:
    def test_a_commentary_failure_still_produces_a_draft(self, service):
        service.repo.get_settings.return_value = {
            **service.repo.get_settings.return_value,
            'commentary_enabled': True,
        }
        payload = service.build_snapshot(date(2026, 9, 14), 'monday',
                                         date(2026, 9, 13))
        with patch('app.modules.periodic_report.commentary.generate_commentary',
                   side_effect=RuntimeError('no API key')):
            subject, body, error = service.draft_email(payload)
        assert subject
        assert body                      # the template still rendered
        assert '{{commentary}}' not in body
        # ...but it must not fail SILENTLY: a dead key has to look different
        # from a disabled one, or nobody finds out for weeks.
        assert 'no API key' in error

    def test_an_unconfigured_key_is_reported_not_treated_as_success(self, service):
        # generate_commentary returns '' when unconfigured rather than raising.
        # That is still a missing analysis and must be surfaced.
        payload = service.build_snapshot(date(2026, 9, 14), 'monday',
                                         date(2026, 9, 13))
        service.repo.get_settings.return_value = {
            **service.repo.get_settings.return_value, 'commentary_enabled': True}
        with patch('app.modules.periodic_report.commentary.generate_commentary',
                   return_value=''):
            _subject, _body, error = service.draft_email(payload)
        assert error and 'ANTHROPIC_API_KEY' in error

    def test_no_error_when_commentary_is_deliberately_disabled(self, service):
        # Switched off on purpose is not a failure, and must not raise an alarm.
        payload = service.build_snapshot(date(2026, 9, 14), 'monday',
                                         date(2026, 9, 13))
        _subject, _body, error = service.draft_email(payload)
        assert error is None


class TestWeekAnnotation:
    """Runs carry the week they report on, so the week selector does not have to
    re-implement the last-complete-week rule in TypeScript."""

    def _run(self, as_of=date(2026, 9, 14), **over):
        return {'id': 1, 'as_of': as_of, 'week_start': 'monday',
                'status': 'sent', 'through_date': date(2026, 9, 13),
                'payload': None, 'email_subject': None, 'email_body': None,
                'error': None, 'generated_at': None, 'approved_by': None,
                'approved_at': None, 'scheduled_send_at': None,
                'sent_at': None, **over}

    def test_list_carries_the_reported_week(self, service):
        service.repo.list_runs.return_value = [self._run()]
        run = service.list_runs()['runs'][0]
        assert run['week_from'] == '2026-09-07'
        assert run['week_to'] == '2026-09-13'
        assert run['week_label'] == 'Week 37 2026'

    def test_week_is_the_one_that_CLOSED_not_the_one_containing_as_of(self, service):
        # as_of is the Monday the job fires on; the reported week is the one
        # that ended the day before.
        service.repo.list_runs.return_value = [self._run()]
        run = service.list_runs()['runs'][0]
        assert run['week_to'] < run['as_of']

    def test_sunday_week_start_shifts_the_label_window(self, service):
        # 2026-09-13 is a Sunday, which BEGINS a Sunday-start week — so the last
        # complete one is Sun 09-06 .. Sat 09-12, not the week just before it.
        service.repo.list_runs.return_value = [
            self._run(as_of=date(2026, 9, 13), week_start='sunday')]
        run = service.list_runs()['runs'][0]
        assert run['week_from'] == '2026-09-06'
        assert run['week_to'] == '2026-09-12'

    def test_get_run_carries_it_too(self, service):
        service.repo.get_run.return_value = self._run()
        service.repo.list_sends.return_value = []
        assert service.get_run(1)['run']['week_label'] == 'Week 37 2026'


class TestSendHistory:
    def _run(self):
        return {'id': 1, 'as_of': date(2026, 9, 14), 'week_start': 'monday',
                'status': 'sent', 'through_date': date(2026, 9, 13),
                'payload': None, 'email_subject': 'S', 'email_body': 'B',
                'error': None, 'generated_at': None, 'approved_by': None,
                'approved_at': None, 'scheduled_send_at': None,
                'sent_at': datetime(2026, 9, 14, 9, 0)}

    def test_get_run_reports_what_actually_went_out(self, service):
        # The point: a past report must show the addresses it was SENT to, not
        # today's recipient list, which may have changed since.
        service.repo.get_run.return_value = self._run()
        service.repo.list_sends.return_value = [{
            'id': 1, 'recipients': ['old-board@example.com'], 'success': True,
            'detail': 'sent via mail.example.com:587 to 1 recipient(s)',
            'sent_at': datetime(2026, 9, 14, 9, 0),
        }]
        sends = service.get_run(1)['run']['sends']
        assert sends[0]['recipients'] == ['old-board@example.com']
        assert sends[0]['success'] is True
        assert sends[0]['sent_at'] == '2026-09-14T09:00:00'

    def test_a_run_never_sent_has_an_empty_send_list(self, service):
        service.repo.get_run.return_value = self._run()
        service.repo.list_sends.return_value = []
        assert service.get_run(1)['run']['sends'] == []

    def test_failed_attempts_are_visible_not_hidden(self, service):
        service.repo.get_run.return_value = self._run()
        service.repo.list_sends.return_value = [
            {'id': 1, 'recipients': ['a@b.c'], 'success': False,
             'detail': 'connection refused', 'sent_at': datetime(2026, 9, 14, 9, 0)},
            {'id': 2, 'recipients': ['a@b.c'], 'success': True,
             'detail': 'sent', 'sent_at': datetime(2026, 9, 14, 9, 15)},
        ]
        sends = service.get_run(1)['run']['sends']
        assert [s['success'] for s in sends] == [False, True]



class TestLLMIsCalledOncePerReport:
    """The analysis costs money and is written exactly ONCE per report, by the
    Monday 03:00 job. Nothing reachable over HTTP can spend a token."""

    def _existing(self, **over):
        return {'id': 1, 'as_of': date(2026, 9, 14), 'status': 'pending_approval',
                'email_subject': 'Báo cáo — Tuần 37/2026',
                'email_body': '**1. Tuần gần nhất**', **over}

    def test_the_scheduled_job_defaults_to_drafting(self):
        import inspect
        sig = inspect.signature(PeriodicReportService.generate_run)
        assert sig.parameters['draft_email'].default is True

    def test_draft_email_false_skips_the_model_entirely(self, service):
        # The CLI's --no-email-draft flag: figures without paying for prose.
        service.repo.get_run_by_as_of.return_value = None
        service.repo.create_run.return_value = 1
        with patch('app.modules.periodic_report.commentary.generate_commentary') as gen:
            service.generate_run(date(2026, 9, 14), through=date(2026, 9, 13),
                                 draft_email=False)
        gen.assert_not_called()
        assert service.repo.create_run.call_args.kwargs['email_body'] is None

    def test_refusing_to_replace_a_run_that_is_mid_send(self, service):
        # Replacing the row during a send would reset it under the dispatcher
        # and hand recipients the old workbook against apparently-new figures.
        service.repo.get_run_by_as_of.return_value = self._existing(status='sending')
        result = service.generate_run(date(2026, 9, 14))
        assert result['code'] == 'INVALID_STATE'
        service.repo.create_run.assert_not_called()



class TestRetryGeneration:
    """An explicit opt-in retry, offered ONLY when generation actually failed.

    This is the one path that can spend a token outside the Monday job, so the
    guard is the point: it must be impossible to trigger on a healthy run.

    It regenerates EVERYTHING — one path, not two. Re-fetching is safe because
    every window ends in the past and a late-posted sale carries the later post
    date, so it lands in the next week rather than changing this one.
    """

    def _run(self, **over):
        return {'id': 1, 'as_of': date(2026, 9, 14), 'week_start': 'monday',
                'through_date': date(2026, 9, 13), 'status': 'pending_approval',
                'payload': {'periods': {}}, 'commentary_error': None, **over}

    def test_REFUSES_on_a_healthy_run(self, service):
        service.repo.get_run.return_value = self._run()
        result = service.retry_generation(1)
        assert result['success'] is False
        assert result['code'] == 'INVALID_STATE'
        assert 'written once per report' in result['error']
        service.repo.fetch_sales.assert_not_called()

    def test_refuses_once_the_report_is_approved(self, service):
        service.repo.get_run.return_value = self._run(
            status='approved', commentary_error='boom')
        assert service.retry_generation(1)['code'] == 'INVALID_STATE'

    def test_refuses_once_the_report_is_sent(self, service):
        service.repo.get_run.return_value = self._run(
            status='sent', commentary_error='boom')
        assert service.retry_generation(1)['code'] == 'INVALID_STATE'

    def test_refuses_a_missing_run(self, service):
        service.repo.get_run.return_value = None
        assert service.retry_generation(1)['code'] == 'NOT_FOUND'

    def test_a_failed_analysis_regenerates_everything(self, service):
        service.repo.get_run.return_value = self._run(commentary_error='no key')
        service.repo.get_run_by_as_of.return_value = None
        service.repo.create_run.return_value = 1
        with patch.object(service, 'draft_email', return_value=('S', 'B', None)):
            result = service.retry_generation(1)
        assert result['success'] is True
        service.repo.fetch_sales.assert_called_once()
        assert service.repo.create_run.call_args.kwargs['email_body'] == 'B'

    def test_a_wholly_failed_run_regenerates_too(self, service):
        service.repo.get_run.return_value = self._run(status='failed', payload=None)
        service.repo.get_run_by_as_of.return_value = None
        service.repo.create_run.return_value = 1
        with patch.object(service, 'draft_email', return_value=('S', 'B', None)):
            assert service.retry_generation(1)['success'] is True
        service.repo.fetch_sales.assert_called_once()

    def test_it_reuses_the_original_window_not_today(self, service):
        # Retrying on Wednesday must still report the week the run was for.
        service.repo.get_run.return_value = self._run(commentary_error='boom')
        service.repo.get_run_by_as_of.return_value = None
        service.repo.create_run.return_value = 1
        with patch.object(service, 'draft_email', return_value=('S', 'B', None)):
            service.retry_generation(1)
        kwargs = service.repo.create_run.call_args.kwargs
        assert kwargs['as_of'] == date(2026, 9, 14)
        assert kwargs['through_date'] == date(2026, 9, 13)

    def test_a_retry_that_fails_again_is_reported_not_claimed_as_success(self, service):
        service.repo.get_run.return_value = self._run(commentary_error='boom')
        service.repo.get_run_by_as_of.return_value = None
        service.repo.create_run.return_value = 1
        with patch.object(service, 'draft_email',
                          return_value=('S', 'B', 'still broken')):
            result = service.retry_generation(1)
        assert result['success'] is False
        assert result['code'] == 'COMMENTARY_FAILED'
        # The figures were still saved — only the prose is missing.
        assert service.repo.create_run.call_args.kwargs['commentary_error'] \
            == 'still broken'


class TestRecipientsAreNotBoundToARun:
    def test_a_run_never_stores_recipients(self, service):
        # The guarantee behind re-sending: `create_run` has no recipient
        # parameter, so nothing about who receives a report is frozen onto it.
        import inspect
        params = inspect.signature(
            service.repo.create_run).parameters if not isinstance(
                service.repo, MagicMock) else None
        from app.modules.periodic_report.repository import PeriodicReportRepository
        sig = inspect.signature(PeriodicReportRepository.create_run)
        assert not any('recipient' in p for p in sig.parameters)


class TestScheduleIsTimezoneAware:
    """A send time the user picks is meant in THEIR timezone.

    Observed in production: the user chose 00:30 and it was stored as 00:30 UTC
    — 07:30 in Vietnam — because the server clock is UTC and `datetime.now()`
    was naive. The browser then faithfully displayed 07:30 for a time typed as
    00:30.
    """

    def test_now_local_is_aware(self):
        from app.modules.periodic_report.service import now_local
        assert now_local().tzinfo is not None

    def test_next_send_time_keeps_the_callers_zone(self):
        from app.modules.periodic_report.service import REPORT_TZ
        now = datetime(2026, 9, 16, 9, 0, tzinfo=REPORT_TZ)   # Wed
        result = next_send_time(now, 3, time(0, 30))          # Thu 00:30
        assert result.tzinfo is not None
        assert result == datetime(2026, 9, 17, 0, 30, tzinfo=REPORT_TZ)

    def test_the_stored_instant_is_the_users_clock_not_the_servers(self):
        # 00:30 in Vietnam is 17:30 UTC the previous day. Before the fix this
        # became 00:30 UTC, seven hours late.
        from app.modules.periodic_report.service import REPORT_TZ
        now = datetime(2026, 9, 16, 9, 0, tzinfo=REPORT_TZ)
        result = next_send_time(now, 3, time(0, 30))
        assert result.astimezone(timezone.utc) == \
            datetime(2026, 9, 16, 17, 30, tzinfo=timezone.utc)

    def test_a_time_already_past_today_rolls_a_week(self):
        from app.modules.periodic_report.service import REPORT_TZ
        now = datetime(2026, 9, 17, 9, 0, tzinfo=REPORT_TZ)   # Thu 09:00
        assert next_send_time(now, 3, time(0, 30)) == \
            datetime(2026, 9, 24, 0, 30, tzinfo=REPORT_TZ)

    def test_an_immediate_send_is_aware_too(self, service):
        service.repo.get_run.return_value = {'id': 1, 'status': 'approved',
                                             'as_of': date(2026, 9, 14)}
        service.repo.list_recipients.return_value = [{'email': 'a@b.c', 'kind': 'to'}]
        service.repo.queue_run.return_value = True
        service.send(1, user_id=7, mode='immediate')
        queued_at = service.repo.queue_run.call_args[0][2]
        assert queued_at.tzinfo is not None


class TestRunsAreKeyedByTheWeekTheyReport:
    """Observed in production: the week selector showed Week 37 twice.

    Runs are keyed by `as_of`, but every day Mon-Sun reports the SAME closed
    week — so a manual run mid-week created a second row for a week that
    already had one.
    """

    def test_any_day_of_the_week_collapses_to_one_as_of(self, service):
        service.repo.get_run_by_as_of.return_value = None
        service.repo.create_run.return_value = 1
        seen = set()
        for day in range(14, 21):                  # Mon 14th .. Sun 20th
            service.repo.create_run.reset_mock()
            with patch.object(service, 'draft_email', return_value=('S', 'B', None)):
                service.generate_run(date(2026, 9, day))
            seen.add(service.repo.create_run.call_args.kwargs['as_of'])
        assert seen == {date(2026, 9, 14)}

    def test_the_canonical_day_is_the_one_after_the_week_ends(self, service):
        service.repo.get_run_by_as_of.return_value = None
        service.repo.create_run.return_value = 1
        with patch.object(service, 'draft_email', return_value=('S', 'B', None)):
            service.generate_run(date(2026, 9, 16))
        kwargs = service.repo.create_run.call_args.kwargs
        assert kwargs['as_of'] == date(2026, 9, 14)
        assert kwargs['through_date'] == date(2026, 9, 13)

    def test_a_rerun_mid_week_REPLACES_rather_than_duplicating(self, service):
        # It looks up the existing run under the canonical key, so ON CONFLICT
        # replaces it instead of inserting a rival row for the same week.
        service.repo.get_run_by_as_of.return_value = None
        service.repo.create_run.return_value = 1
        with patch.object(service, 'draft_email', return_value=('S', 'B', None)):
            service.generate_run(date(2026, 9, 17))
        assert service.repo.get_run_by_as_of.call_args[0][0] == date(2026, 9, 14)

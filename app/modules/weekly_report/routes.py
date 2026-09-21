"""Weekly report API routes.

The analysis costs money and is written exactly once per report, by the Monday
03:00 job. There is deliberately no general "regenerate" endpoint: it would
spend tokens on every press and buy nothing, because a sale posted late carries
the later post date and falls into the NEXT week's window.

The one exception is `/runs/<id>/retry`, which re-runs a generation that FAILED
— the whole thing if there are no figures, the analysis alone if the figures are
fine. The service refuses it on a healthy run, so it cannot become the casual
spend path.

One permission section gates this module: WEEKLY_REPORT. Access to the page is
access to the page — the same design as every other feature.

It was briefly split into view and approve/send sections while the email was
new and its blast radius unknown. That bought nothing: nobody was granted the
view without also needing to act on it, and the split only made the access
model inconsistent with the rest of the app.

Still enforced here rather than only hidden in the nav — approving sends email
to the management board, and a hidden button is not an access control.
"""
from io import BytesIO

from flask import Blueprint, g, jsonify, request, send_file

from app.core.auth.middleware import section_required
from .service import WeeklyReportService

weekly_report_bp = Blueprint('weekly_report', __name__,
                               url_prefix='/api/v1/weekly-report')
weekly_report_service = WeeklyReportService()

# ONE section, not two. Access to the page is access to the page — the same
# design as every other feature. Approving and sending were gated separately
# while the email was new and the blast radius unknown; splitting them turned
# out to buy nothing, because nobody was ever granted the view without the
# ability to act on it.
SECTION = 'WEEKLY_REPORT'

# Service error code → HTTP status.
_ERROR_STATUS = {
    'INVALID_INPUT':  400,
    'INVALID_STATE':  409,
    'ALREADY_SENT':   409,
    'NO_RECIPIENTS':  409,
    'COMMENTARY_FAILED': 502,
    'NOT_FOUND':      404,
    'SERVER_ERROR':   500,
}


def _respond(result, ok=200):
    if not result.get('success'):
        return jsonify(result), _ERROR_STATUS.get(result.get('code'), 400)
    return jsonify(result), ok


@weekly_report_bp.route('/runs/latest', methods=['GET'])
@section_required(SECTION)
def get_latest_run():
    """The most recent run — the Monday report awaiting approval, normally.
    Returns `{run: null}` (not 404) before the first run has ever happened."""
    return _respond(weekly_report_service.get_latest())


@weekly_report_bp.route('/runs', methods=['GET'])
@section_required(SECTION)
def list_runs():
    """Recent runs, newest first, without payloads — a history list."""
    try:
        limit = min(int(request.args.get('limit', 26)), 200)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'limit must be an integer',
                        'code': 'INVALID_INPUT'}), 400
    return _respond(weekly_report_service.list_runs(limit))


@weekly_report_bp.route('/runs/<int:run_id>', methods=['GET'])
@section_required(SECTION)
def get_run(run_id):
    """One run in full, including the snapshot payload and the email draft."""
    return _respond(weekly_report_service.get_run(run_id))


@weekly_report_bp.route('/runs/<int:run_id>/workbook', methods=['GET'])
@section_required(SECTION)
def download_workbook(run_id):
    """The stored .xlsx for a run — the same file the email attaches."""
    run = weekly_report_service.get_workbook(run_id)
    if run is None:
        return jsonify({'success': False, 'error': 'No workbook for this run',
                        'code': 'NOT_FOUND'}), 404
    return send_file(
        BytesIO(run['workbook']),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f"weekly_report_{run['as_of']}.xlsx",
    )


@weekly_report_bp.route('/runs/<int:run_id>/email', methods=['PUT'])
@section_required(SECTION)
def update_draft(run_id):
    """Save edits to the draft. Only while the run awaits approval."""
    body = request.get_json(silent=True) or {}
    return _respond(weekly_report_service.update_draft(
        run_id, body.get('subject', ''), body.get('body', '')))


@weekly_report_bp.route('/runs/<int:run_id>/approve', methods=['POST'])
@section_required(SECTION)
def approve_run(run_id):
    """Sign off the content. Does NOT send.

    Approval and sending are separate decisions, so this takes no body and does
    not require recipients — the list is edited independently and may be empty
    at this moment.
    """
    return _respond(weekly_report_service.approve(run_id, user_id=g.sid))


@weekly_report_bp.route('/runs/<int:run_id>/send', methods=['POST'])
@section_required(SECTION)
def send_run(run_id):
    """Queue an approved — or already sent — report for delivery.

    One endpoint for both: sending the first time and sending again differ only
    in what the row said beforehand.

    Body (optional): {send_mode: 'immediate'|'scheduled', send_weekday: 0-6,
    send_time: 'HH:MM'}. A schedule given here is saved as the new default, the
    same way the recipient list is.
    """
    body = request.get_json(silent=True) or {}
    return _respond(weekly_report_service.send(
        run_id, user_id=g.sid, mode=body.get('send_mode'),
        send_weekday=body.get('send_weekday'), send_time=body.get('send_time')))


@weekly_report_bp.route('/runs/<int:run_id>/retry', methods=['POST'])
@section_required(SECTION)
def retry_run(run_id):
    """Re-run a generation that FAILED. One action; the service picks the scope.

    If the whole run failed there are no figures, so it regenerates from Oracle
    and drafts. If only the analysis failed the figures are fine, so it re-drafts
    from the stored snapshot and Oracle is not touched.

    NOT a general regenerate button — refused unless something actually went
    wrong, so it cannot become a way to casually re-roll a healthy report.
    """
    return _respond(weekly_report_service.retry_generation(run_id))


@weekly_report_bp.route('/recipients', methods=['GET'])
@section_required(SECTION)
def get_recipients():
    return _respond(weekly_report_service.get_recipients())


@weekly_report_bp.route('/recipients', methods=['PUT'])
@section_required(SECTION)
def set_recipients():
    """Replace the recipient list. Body: {recipients: [{email, name?, kind?}]}."""
    body = request.get_json(silent=True) or {}
    return _respond(weekly_report_service.set_recipients(
        body.get('recipients')))


@weekly_report_bp.route('/settings', methods=['GET'])
@section_required(SECTION)
def get_settings():
    return _respond(weekly_report_service.get_settings())


@weekly_report_bp.route('/settings', methods=['PUT'])
@section_required(SECTION)
def update_settings():
    """Body: any of {send_mode, send_weekday, send_time, subject_template,
    body_template, commentary_enabled}."""
    return _respond(weekly_report_service.update_settings(
        request.get_json(silent=True)))

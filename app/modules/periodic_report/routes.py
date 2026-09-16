"""Periodic report API routes.

The analysis costs money and is written exactly once per report, by the Monday
03:00 job. There is deliberately no general "regenerate" endpoint: it would
spend tokens on every press and buy nothing, because a sale posted late carries
the later post date and falls into the NEXT week's window.

The one exception is `/runs/<id>/retry`, which re-runs a generation that FAILED
— the whole thing if there are no figures, the analysis alone if the figures are
fine. The service refuses it on a healthy run, so it cannot become the casual
spend path.

Two permission sections gate this module:
  PERIODIC_REPORT          see the page, the figures and the draft
  PERIODIC_REPORT_APPROVE  edit the draft, approve, and change who receives it

Approval sends email to the management board, so it is enforced here rather
than only hidden in the frontend nav.
"""
from io import BytesIO

from flask import Blueprint, g, jsonify, request, send_file

from app.core.auth.middleware import section_required
from .service import PeriodicReportService

periodic_report_bp = Blueprint('periodic_report', __name__,
                               url_prefix='/api/v1/periodic-report')
periodic_report_service = PeriodicReportService()

VIEW = 'PERIODIC_REPORT'
APPROVE = 'PERIODIC_REPORT_APPROVE'

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


@periodic_report_bp.route('/runs/latest', methods=['GET'])
@section_required(VIEW)
def get_latest_run():
    """The most recent run — the Monday report awaiting approval, normally.
    Returns `{run: null}` (not 404) before the first run has ever happened."""
    return _respond(periodic_report_service.get_latest())


@periodic_report_bp.route('/runs', methods=['GET'])
@section_required(VIEW)
def list_runs():
    """Recent runs, newest first, without payloads — a history list."""
    try:
        limit = min(int(request.args.get('limit', 26)), 200)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'limit must be an integer',
                        'code': 'INVALID_INPUT'}), 400
    return _respond(periodic_report_service.list_runs(limit))


@periodic_report_bp.route('/runs/<int:run_id>', methods=['GET'])
@section_required(VIEW)
def get_run(run_id):
    """One run in full, including the snapshot payload and the email draft."""
    return _respond(periodic_report_service.get_run(run_id))


@periodic_report_bp.route('/runs/<int:run_id>/workbook', methods=['GET'])
@section_required(VIEW)
def download_workbook(run_id):
    """The stored .xlsx for a run — the same file the email attaches."""
    run = periodic_report_service.get_workbook(run_id)
    if run is None:
        return jsonify({'success': False, 'error': 'No workbook for this run',
                        'code': 'NOT_FOUND'}), 404
    return send_file(
        BytesIO(run['workbook']),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f"periodic_report_{run['as_of']}.xlsx",
    )


@periodic_report_bp.route('/runs/<int:run_id>/email', methods=['PUT'])
@section_required(APPROVE)
def update_draft(run_id):
    """Save edits to the draft. Only while the run awaits approval."""
    body = request.get_json(silent=True) or {}
    return _respond(periodic_report_service.update_draft(
        run_id, body.get('subject', ''), body.get('body', '')))


@periodic_report_bp.route('/runs/<int:run_id>/approve', methods=['POST'])
@section_required(APPROVE)
def approve_run(run_id):
    """Approve a run and queue it for sending.

    Body (optional): {send_mode: 'immediate' | 'scheduled'} — overrides the
    configured default for this run only.
    """
    body = request.get_json(silent=True) or {}
    return _respond(periodic_report_service.approve(
        run_id, user_id=g.sid, send_mode=body.get('send_mode')))


@periodic_report_bp.route('/runs/<int:run_id>/retry', methods=['POST'])
@section_required(APPROVE)
def retry_run(run_id):
    """Re-run a generation that FAILED. One action; the service picks the scope.

    If the whole run failed there are no figures, so it regenerates from Oracle
    and drafts. If only the analysis failed the figures are fine, so it re-drafts
    from the stored snapshot and Oracle is not touched.

    NOT a general regenerate button — refused unless something actually went
    wrong, so it cannot become a way to casually re-roll a healthy report.
    """
    return _respond(periodic_report_service.retry_generation(run_id))


@periodic_report_bp.route('/recipients', methods=['GET'])
@section_required(VIEW)
def get_recipients():
    return _respond(periodic_report_service.get_recipients())


@periodic_report_bp.route('/recipients', methods=['PUT'])
@section_required(APPROVE)
def set_recipients():
    """Replace the recipient list. Body: {recipients: [{email, name?, kind?}]}."""
    body = request.get_json(silent=True) or {}
    return _respond(periodic_report_service.set_recipients(
        body.get('recipients')))


@periodic_report_bp.route('/settings', methods=['GET'])
@section_required(VIEW)
def get_settings():
    return _respond(periodic_report_service.get_settings())


@periodic_report_bp.route('/settings', methods=['PUT'])
@section_required(APPROVE)
def update_settings():
    """Body: any of {send_mode, send_weekday, send_time, subject_template,
    body_template, commentary_enabled}."""
    return _respond(periodic_report_service.update_settings(
        request.get_json(silent=True)))

"""P&L API routes.

Two permission sections gate this module:
  PNL          see the statement, the stored months and the import history
  PNL_IMPORT   upload a FAST export, and lock or unlock a month

They are separate from the weekly report's sections on purpose. A P&L exposes
company profit, payroll and G&A, which is a narrower audience than the sales
report — so whoever can see weekly sales does NOT automatically see this.

`PNL_IMPORT` covers locking as well as uploading, because the two are the same
authority: deciding what the stored figures for a month are.
"""
from io import BytesIO

from flask import Blueprint, g, jsonify, request

from app.core.auth.middleware import section_required
from .service import PNLService

pnl_bp = Blueprint('pnl', __name__, url_prefix='/api/v1/pnl')
pnl_service = PNLService()

VIEW = 'PNL'
IMPORT = 'PNL_IMPORT'

_ERROR_STATUS = {
    'INVALID_INPUT': 400,
    'INVALID_FILE': 422,
    'MONTH_LOCKED': 409,
    'NO_DATA': 404,
    'SERVER_ERROR': 500,
}

# openpyxl reads a whole workbook into memory. The bulk template with 21 months
# is well under a megabyte; 25 MB is far above any real export and far below
# anything that would trouble the API.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _respond(result, ok=200):
    if not result.get('success'):
        return jsonify(result), _ERROR_STATUS.get(result.get('code'), 400)
    return jsonify(result), ok


@pnl_bp.route('/statement', methods=['GET'])
@section_required(VIEW)
def get_statement():
    """The P&L for one period.

    Query: year, month ('June' or 6), basis, and optionally entity for a single
    store. Omitting `entity` gives the company — summed from the stores, never
    read from the source's Total column.
    """
    year = request.args.get('year', type=int)
    month = request.args.get('month')
    basis = request.args.get('basis')
    if year is None or not month or not basis:
        return jsonify({'success': False, 'code': 'INVALID_INPUT',
                        'error': 'year, month and basis are all required.'}), 400
    return _respond(pnl_service.statement(
        year=year, month=month, basis=basis,
        entity=request.args.get('entity') or None))


@pnl_bp.route('/months', methods=['GET'])
@section_required(VIEW)
def get_months():
    """Which months are stored, how complete each is, and which are locked."""
    return _respond(pnl_service.months())


@pnl_bp.route('/imports', methods=['GET'])
@section_required(VIEW)
def get_imports():
    """Upload history: who loaded what, when, and what it replaced."""
    return _respond(pnl_service.imports(
        limit=min(request.args.get('limit', default=50, type=int), 200)))


@pnl_bp.route('/import', methods=['POST'])
@section_required(IMPORT)
def import_file():
    """Upload a FAST export and store it.

    multipart/form-data:
        file          the .xlsx                          (required)
        sheet         worksheet name                     (optional)
        year, month   period override for a monthly file
                      whose sheet is not named like T07.2026
        allow_locked  'true' to restate a locked month   (deliberate override)

    Replaces every month the file covers; months it does not mention are left
    alone. All or nothing — see PNLService.import_worksheet.
    """
    upload = request.files.get('file')
    if upload is None or not upload.filename:
        return jsonify({'success': False, 'code': 'INVALID_INPUT',
                        'error': 'No file was uploaded.'}), 400
    if not upload.filename.lower().endswith(('.xlsx', '.xlsm')):
        return jsonify({'success': False, 'code': 'INVALID_INPUT',
                        'error': 'Expected an .xlsx file exported from FAST.'}), 400

    blob = upload.read(MAX_UPLOAD_BYTES + 1)
    if len(blob) > MAX_UPLOAD_BYTES:
        return jsonify({
            'success': False, 'code': 'INVALID_INPUT',
            'error': f'File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.'
        }), 400

    sheet_name = request.form.get('sheet') or None
    try:
        worksheet, resolved, available = _open_sheet(blob, sheet_name)
    except _SheetError as e:
        return jsonify({'success': False, 'code': 'INVALID_FILE',
                        'error': str(e), 'sheets': e.sheets}), 422

    result = pnl_service.import_worksheet(
        worksheet,
        filename=upload.filename,
        sheet_name=resolved,
        uploaded_by=getattr(g, 'email', None),
        year=request.form.get('year', type=int),
        month=request.form.get('month') or None,
        allow_locked=str(request.form.get('allow_locked', '')).lower() == 'true',
    )
    if result.get('success'):
        result['available_sheets'] = available
    return _respond(result, ok=201)


@pnl_bp.route('/months/<int:year>/<int:month>/lock', methods=['POST'])
@section_required(IMPORT)
def lock_month(year, month):
    """Close a month. Later uploads covering it are refused."""
    return _respond(pnl_service.lock_month(year, month,
                                           user=getattr(g, 'email', None)))


@pnl_bp.route('/months/<int:year>/<int:month>/lock', methods=['DELETE'])
@section_required(IMPORT)
def unlock_month(year, month):
    """Reopen a month so a correction can be imported."""
    return _respond(pnl_service.unlock_month(year, month))


class _SheetError(Exception):
    def __init__(self, message, sheets):
        self.sheets = sheets
        super().__init__(message)


def _open_sheet(blob, sheet_name):
    """(worksheet, resolved name, all sheet names).

    `data_only=True` so a file carrying formulas yields the numbers Excel last
    computed. `read_only` is deliberately NOT used: it makes `ws.cell()` access
    quadratic, and the importer addresses cells directly.

    With no sheet named, the first one is used — a FAST export has exactly one,
    and its name (`T07.2026`) is where the period comes from.
    """
    from zipfile import BadZipFile

    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException

    try:
        wb = load_workbook(BytesIO(blob), data_only=True)
    # BadZipFile subclasses neither OSError nor ValueError, so it has to be
    # named: an .xlsx IS a zip, and anything that is not one reaches here.
    # Without it a corrupt upload answered 500 rather than 422.
    except (InvalidFileException, BadZipFile, KeyError, OSError, ValueError) as e:
        raise _SheetError(f'The file could not be opened as an .xlsx: {e}', [])
    if sheet_name is None:
        return wb[wb.sheetnames[0]], wb.sheetnames[0], wb.sheetnames
    if sheet_name not in wb.sheetnames:
        raise _SheetError(
            f'The workbook has no sheet named {sheet_name!r}.', wb.sheetnames)
    return wb[sheet_name], sheet_name, wb.sheetnames

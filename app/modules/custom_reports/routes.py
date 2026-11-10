"""
Custom Reports API Routes.

This module hosts cross-domain on-demand reports that don't fit cleanly
into a single feature module (commission, crm, sale_through, etc.).

One permission section gates the whole module: CUSTOM_REPORTS. Access to
the page is access to the page — the same design as every other feature,
on the user's instruction (2026-11-05): an admin or IT manager grants it
on the access page.

⚠️ ENFORCED HERE, not only hidden in the nav. These endpoints return
company-wide net revenue, the full-price/markdown margin split, CUSTOMER
NAMES and membership tiers. The sidebar already hides the page from users
without the section, but a hidden nav entry is not an access control — an
authenticated employee could POST a query directly. A bare token check,
which is what these routes shipped with while the module was one narrow
size report, is not enough for the query engine.
"""
import os
import threading

from flask import Blueprint, jsonify, request

from app.core.auth.middleware import section_required
from . import engine
from .service import CustomReportsService

# The permission section gating every route below. Matches SECTION_CODE in
# the frontend's `src/features/custom-reports/constants.ts`.
SECTION = 'CUSTOM_REPORTS'

# ----------------------------------------------------------------------
# Concurrency guard
# ----------------------------------------------------------------------
# ⚠️ WHY A CAP AT ALL: gunicorn runs `--workers 3` (see
# `deployment/gbl-hr-api.service`) with the default SYNC worker, so one
# in-flight query occupies one of three processes for its whole duration.
# An unfiltered multi-year query is ~28s. Three of those at once and the
# API has no worker left for ANYTHING — `/auth/login` included. The
# failure is not a slow report, it is the whole tool looking dead.
#
# Two is deliberate rather than three: it leaves one worker free by
# construction, so logins and every other feature keep answering however
# many people pile onto this page. The cost is that a third concurrent
# report is refused while a worker is technically idle — the right trade,
# since a clear "try again" beats a login timeout for someone else.
#
# ⚠️ PER PROCESS, NOT PER CLUSTER. Each worker holds its own semaphore,
# so the real ceiling is `min(2, workers)` per process rather than a
# global 2 — with sync workers that still means a free process, which is
# the property being protected. A cluster-wide limit would need shared
# state (Postgres or Redis), which is not worth it for 9 users.
_MAX_CONCURRENT = int(os.environ.get('CUSTOM_REPORTS_MAX_CONCURRENT', '2'))
_slots = threading.BoundedSemaphore(_MAX_CONCURRENT)

custom_reports_bp = Blueprint(
    'custom_reports', __name__, url_prefix='/api/v1/custom-reports'
)
custom_reports_service = CustomReportsService()


@custom_reports_bp.route('/sizes', methods=['GET'])
@section_required(SECTION)
def sizes_by_brand_season():
    """
    Size-by-brand-season report.

    Per (brand, department, normalized_size): imported / sold / on-hand
    quantities, sell-through %, FIFO unit-matched average days-to-sell,
    plus a `raw_sizes` audit list of the source `item_size` labels.

    Items are filtered to sized departments (WRTW, MRTW, WSHO, MSHO);
    items with NULL / empty / one-size labels (jewelry, bags, accessories)
    are dropped.

    Query params:
        seasons   — comma-separated season codes (default: SS25,SS26)
        brands    — comma-separated vendor_name values to keep.
                    Default: all vendors (no filter). Pass `brands=*` for
                    the same behaviour explicitly. To restrict to the
                    13-brand curated list, pass them by name.
        size      — optional item_size filter (case-insensitive exact)
        include_never_received — `true` to keep catalog ghost SKUs
                    (first_rcvd_date IS NULL) in the report. Default:
                    false — ghosts are dropped to keep totals meaningful.
    """
    seasons_raw = request.args.get('seasons')
    seasons = [s.strip() for s in seasons_raw.split(',')] if seasons_raw else None

    brands_raw = request.args.get('brands')
    if brands_raw is None:
        brands = None  # service treats None as "all vendors"
    else:
        brands = [b.strip() for b in brands_raw.split(',') if b.strip()]

    size = request.args.get('size')
    include_never_received = request.args.get('include_never_received', '').lower() == 'true'

    result = custom_reports_service.get_size_by_brand_season(
        seasons=seasons, brands=brands, size=size,
        include_never_received=include_never_received,
    )
    if not result['success']:
        # 400 for input validation errors, 500 for backend failures
        status = 400 if 'invalid season' in result.get('error', '') else 500
        return jsonify(result), status
    return jsonify(result), 200


@custom_reports_bp.route('/query', methods=['POST'])
@section_required(SECTION)
def run_query():
    """The query engine (CR #1).

    POST, not GET, deliberately: the filter body is nested and long, and
    it keeps customer names out of URLs and access logs.

    Body:
        subject  — "sales" | "imports" | "sellThrough"
        from, to — inclusive ISO yyyy-mm-dd. At most 366 days apart, or
                   up to 1830 (five years) when at least one filter can
                   be pushed into SQL — see `MAX_RANGE_DAYS_FILTERED`.
        filters  — { dimensionId: string[] }, empty array = no filter
        groupBy  — up to 3 dimension ids; [] means line detail

    Returns `{ success, result: { subject, groupBy, columns, rows,
    totals, rowCount, queryMs, notes } }`. The contract is recorded in
    GBL_MASTER_FRONTEND/docs/cr/custom_reports.md and specified by
    src/features/custom-reports/mocks/engine.ts.

    400 on a bad request — including a dimension that does not apply to
    the subject, which is REJECTED rather than ignored: silently dropping
    the clause would answer a different question under the label of the
    one that was asked.
    """
    try:
        query = engine.parse_query(request.get_json(silent=True))
    except engine.QueryError as e:
        # `code` only where the frontend has a translation for it; the
        # English `error` remains the contract and the fallback.
        body = {'success': False, 'error': str(e)}
        if e.code:
            body['code'] = e.code
        return jsonify(body), 400

    # Refuse rather than queue. Flask is already holding a worker to read
    # this request; blocking here would hold it for the length of someone
    # else's query too, which is the exact starvation the cap exists to
    # prevent. 503 + Retry-After is the honest answer, and the frontend
    # turns it into a message that says to try again.
    if not _slots.acquire(blocking=False):
        return jsonify({
            'success': False,
            'error': 'another report is still running — try again shortly',
            'code': 'reportsBusy',
        }), 503, {'Retry-After': '10'}

    try:
        # Labels pass through as ids. The frontend holds the EN/VI strings
        # and relabels every column itself, so translating here would be
        # work thrown away — but `run_query` takes them as arguments so a
        # CSV export or a scheduled job can supply real text.
        result = engine.run_query(query, lambda d: d, lambda m: m)
    finally:
        _slots.release()
    return jsonify(result), 200 if result.get('success') else 500


@custom_reports_bp.route('/dimensions/<dim>', methods=['GET'])
@section_required(SECTION)
def dimension_values(dim):
    """Allowed values for one dimension's picker — `{ success, values }`.

    One dimension per call, fetched lazily when its picker opens. Only
    `multi` and `typeahead` dimensions call this; the `search` ones
    (bill, receiving, alu, description) are free-text CONTAINS matches
    with no option list.
    """
    try:
        result = engine.dimension_values(dim)
    except engine.QueryError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    return jsonify(result), 200 if result.get('success') else 500

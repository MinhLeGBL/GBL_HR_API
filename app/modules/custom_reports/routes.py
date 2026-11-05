"""
Custom Reports API Routes.

This module hosts cross-domain on-demand reports that don't fit cleanly
into a single feature module (commission, crm, sale_through, etc.).
"""
from flask import Blueprint, jsonify, request

from app.core.auth.middleware import token_required
from .service import CustomReportsService

custom_reports_bp = Blueprint(
    'custom_reports', __name__, url_prefix='/api/v1/custom-reports'
)
custom_reports_service = CustomReportsService()


@custom_reports_bp.route('/sizes', methods=['GET'])
@token_required
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

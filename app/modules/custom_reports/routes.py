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

    Per (brand, size, season): imported / sold / on-hand quantities,
    sell-through %, and FIFO unit-matched average days-to-sell.

    Query params:
        seasons   — comma-separated season codes (default: SS25,SS26)
        brands    — comma-separated vendor_name values to keep.
                    Defaults to FOCUS_BRANDS (the 13 brands of interest).
                    Pass `brands=*` to disable the filter (all brands).
        size      — optional item_size filter (case-insensitive exact)
    """
    seasons_raw = request.args.get('seasons')
    seasons = [s.strip() for s in seasons_raw.split(',')] if seasons_raw else None

    brands_raw = request.args.get('brands')
    if brands_raw is None:
        brands = None  # service falls back to FOCUS_BRANDS
    else:
        brands = [b.strip() for b in brands_raw.split(',') if b.strip()]

    size = request.args.get('size')

    result = custom_reports_service.get_size_by_brand_season(
        seasons=seasons, brands=brands, size=size,
    )
    if not result['success']:
        # 400 for input validation errors, 500 for backend failures
        status = 400 if 'invalid season' in result.get('error', '') else 500
        return jsonify(result), status
    return jsonify(result), 200

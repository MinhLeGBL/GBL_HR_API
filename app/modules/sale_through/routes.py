"""
Sale-Through Report API Routes.
"""
from flask import Blueprint, jsonify, request

from app.core.auth.middleware import token_required
from .service import SaleThroughService

sale_through_bp = Blueprint('sale_through', __name__, url_prefix='/api/v1/sale-through')
sale_through_service = SaleThroughService()


@sale_through_bp.route('/by-brand-season-category', methods=['GET'])
@token_required
def by_brand_season_category():
    """
    Returns imported / sold / on-hand quantities aggregated by
    brand × season × category, plus sell-through percentage.

    Optional query params:
        brand     — filter to a single vendor name (case-insensitive exact match)
        season    — filter to a season code (e.g. SS25, FW24)
        category  — filter to a category (e.g. DRESS, JACKET)
    """
    brand = request.args.get('brand')
    season = request.args.get('season')
    category = request.args.get('category')

    result = sale_through_service.get_brand_season_category_report(
        brand=brand, season=season, category=category,
    )
    if not result['success']:
        return jsonify(result), 500
    return jsonify(result), 200

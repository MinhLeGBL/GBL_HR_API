"""
Bathroom Products API Routes
"""
from flask import Blueprint, jsonify
from app.core.auth.middleware import token_required, admin_required
from .service import BathroomService

bathroom_bp = Blueprint('bathroom', __name__, url_prefix='/api/v1/bathroom')
bathroom_service = BathroomService()

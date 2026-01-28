"""
Health Check API Routes
"""
from flask import Blueprint, jsonify
from app.database.connection import get_oracle_connection

health_bp = Blueprint('health', __name__, url_prefix='/api/v1/health')


@health_bp.route('/', methods=['GET'])
def health_check():
    """
    API health check endpoint

    Returns:
        JSON response with API health status
    """
    return jsonify({
        'success': True,
        'status': 'healthy',
        'service': 'GBL HR API'
    }), 200


@health_bp.route('/database', methods=['GET'])
def database_health():
    """
    Database connection health check

    Returns:
        JSON response with database connection status
    """
    try:
        conn = get_oracle_connection()
        if conn:
            conn.close()
            return jsonify({
                'success': True,
                'status': 'connected',
                'database': 'Oracle'
            }), 200
        else:
            return jsonify({
                'success': False,
                'status': 'disconnected',
                'error': 'Failed to connect to Oracle database'
            }), 503

    except Exception as e:
        return jsonify({
            'success': False,
            'status': 'error',
            'error': str(e)
        }), 503

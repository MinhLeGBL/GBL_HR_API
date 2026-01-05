"""
GBL HR API - Main Application Entry Point
"""
from flask import Flask, jsonify
from app.api.v1.routes.employee_routes import employee_bp
from app.api.v1.routes.sales_routes import sales_bp
from app.api.v1.routes.health_routes import health_bp


def create_app():
    """
    Application factory pattern for creating Flask app

    Returns:
        Configured Flask application instance
    """
    app = Flask(__name__)

    # Load configuration
    # TODO: Add configuration loading from config files

    # Register blueprints (API routes)
    app.register_blueprint(health_bp)
    app.register_blueprint(employee_bp)
    app.register_blueprint(sales_bp)

    # Root endpoint
    @app.route('/')
    def index():
        return jsonify({
            'service': 'GBL HR API',
            'version': '1.0',
            'status': 'running',
            'endpoints': {
                'health': '/api/v1/health',
                'database_health': '/api/v1/health/database',
                'employees': '/api/v1/employees',
                'sales_reports': '/api/v1/sales/reports'
            }
        }), 200

    # Error handlers
    @app.errorhandler(404)
    def not_found(error):
        return jsonify({
            'success': False,
            'error': 'Endpoint not found',
            'status_code': 404
        }), 404

    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({
            'success': False,
            'error': 'Internal server error',
            'status_code': 500
        }), 500

    return app


# Create app instance
app = create_app()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

"""
GBL HR API - Main Application Entry Point
"""
from flask import Flask, jsonify
from flask_cors import CORS
from app.modules.employees.routes import hr_employee_bp
from app.modules.stores.routes import store_bp
from app.modules.health.routes import health_bp
from app.modules.commission.routes import commission_bp
from app.modules.auth.routes import auth_bp
from app.modules.permissions.routes import permission_bp
from app.modules.dashboard.routes import dashboard_bp
from app.modules.auth.user_routes import user_bp


def create_app():
    """
    Application factory pattern for creating Flask app

    Returns:
        Configured Flask application instance
    """
    app = Flask(__name__)

    # Enable CORS for frontend access
    CORS(app, resources={
        r"/api/*": {
            "origins": ["http://localhost:5173", "http://127.0.0.1:5173", "http://192.168.10.39:5173"],
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"]
        }
    })

    # Load configuration
    # TODO: Add configuration loading from config files

    # Register blueprints (API routes)
    app.register_blueprint(health_bp)
    app.register_blueprint(hr_employee_bp)
    app.register_blueprint(store_bp)
    app.register_blueprint(commission_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(permission_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(user_bp)

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
                'stores': '/api/v1/stores',
                'commission': '/api/v1/commission',
                'auth': '/api/v1/auth',
                'departments': '/api/v1/departments',
                'sections': '/api/v1/sections',
                'section_groups': '/api/v1/section-groups',
                'permissions': '/api/v1/permissions',
                'dashboard': '/api/v1/dashboard',
                'users': '/api/v1/users'
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
    app.run(debug=True, host='0.0.0.0', port=5120)

"""
GBL Master API - Main Application Entry Point
"""
import os
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
from app.modules.bathroom.routes import bathroom_bp
from app.modules.crm.routes import crm_bp
from app.modules.handcarry.routes import handcarry_bp
from app.modules.account_payable.routes import account_payable_bp
from app.modules.reports.routes import reports_bp
from app.modules.weekly_report.routes import weekly_report_bp


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

    # Development sign-in, registered ONLY when explicitly enabled on a local
    # database — so in production these URLs do not exist and 404 like any
    # other unknown path. See app/modules/auth/dev_auth.py for the three
    # conditions and why it still goes through bcrypt.
    from app.modules.auth.dev_auth import dev_auth_bp, dev_auth_status
    _dev_ok, _dev_why = dev_auth_status()
    if _dev_ok:
        app.register_blueprint(dev_auth_bp)
        print(f'[DEV AUTH] account picker at /api/v1/auth/dev/accounts '
              f'— {_dev_why}')
    elif (os.getenv('DEV_AUTH_BYPASS') or '').strip():
        # Asked for and refused: say so, or it looks like the flag did nothing.
        print(f'[DEV AUTH] NOT enabled — {_dev_why}')
    app.register_blueprint(permission_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(bathroom_bp)
    app.register_blueprint(crm_bp)
    app.register_blueprint(handcarry_bp)
    app.register_blueprint(account_payable_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(weekly_report_bp)
    # TEMPORARY ALIAS — remove once the frontend rename has been live a while.
    # The two repos deploy independently, so between the backend landing and
    # the frontend landing the old page would call a path that no longer
    # exists. Serving both prefixes makes the rename a non-event in either
    # order. The section gate is unchanged: both paths require WEEKLY_REPORT.
    app.register_blueprint(weekly_report_bp, name='weekly_report_legacy',
                           url_prefix='/api/v1/periodic-report')

    # Root endpoint
    @app.route('/')
    def index():
        return jsonify({
            'service': 'GBL Master API',
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
                'sections_permissions': '/api/v1/sections/permissions',
                'permissions': '/api/v1/permissions',
                'dashboard': '/api/v1/dashboard',
                'users': '/api/v1/users',
                'bathroom': '/api/v1/bathroom',
                'crm': '/api/v1/crm',
                'handcarry': '/api/v1/handcarry',
                'account_payable': '/api/v1/account-payable',
                'periodic_report': '/api/v1/weekly-report'
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
    app.run(debug=True, host='0.0.0.0', port=5200)

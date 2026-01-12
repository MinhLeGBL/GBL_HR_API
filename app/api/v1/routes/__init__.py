"""
API Routes - HTTP endpoint definitions
"""
from .employee_routes import employee_bp
from .sales_routes import sales_bp
from .health_routes import health_bp
from .commission_routes import commission_bp

__all__ = ['employee_bp', 'sales_bp', 'health_bp', 'commission_bp']

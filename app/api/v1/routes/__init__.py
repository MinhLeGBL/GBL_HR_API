"""
API Routes - HTTP endpoint definitions
"""
from .health_routes import health_bp
from .commission_routes import commission_bp

__all__ = ['health_bp', 'commission_bp']

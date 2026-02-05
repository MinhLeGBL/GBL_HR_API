"""
Application settings and configuration
"""
import os
from dotenv import load_dotenv

# Env file loading is handled by config/database.py which is imported first.
# This fallback ensures settings work even if database.py wasn't imported yet.
_env = os.getenv('FLASK_ENV', 'development')
_env_file = f'.env.{_env}'
if os.path.exists(_env_file):
    load_dotenv(_env_file)
else:
    load_dotenv()


# Flask Application Settings
class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', '')
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'

    # API Settings
    API_VERSION = os.getenv('API_VERSION', 'v1')
    API_TITLE = os.getenv('API_TITLE', 'GBL HR API')
    API_DESCRIPTION = os.getenv('API_DESCRIPTION', 'HR Salary Calculation API')

    # CORS Settings
    CORS_ORIGINS = os.getenv('CORS_ORIGINS', '*').split(',')

    # JWT Settings
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', '')
    JWT_ACCESS_TOKEN_EXPIRES = int(os.getenv('JWT_ACCESS_TOKEN_EXPIRES', 3600))  # 1 hour

    # Logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE = os.getenv('LOG_FILE', 'logs/app.log')

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False

class TestingConfig(Config):
    TESTING = True
    DEBUG = True

# Configuration mapping
config_by_name = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig
}

def get_config():
    """Get configuration based on environment"""
    env = os.getenv('FLASK_ENV', 'development')
    return config_by_name.get(env, DevelopmentConfig)
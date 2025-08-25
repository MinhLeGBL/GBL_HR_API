"""
Database configuration for Oracle HR database
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Oracle Database Configuration
DATABASE_CONFIG = {
    'host': os.getenv('DB_HOST', '118.107.78.35'),
    'port': os.getenv('DB_PORT', '1521'),
    'service_name': os.getenv('DB_SERVICE_NAME', 'rproods'),
    'username': os.getenv('DB_USERNAME', 'reportuser'),
    'password': os.getenv('DB_PASSWORD', 'report'),
    'encoding': os.getenv('DB_ENCODING', 'UTF-8')
}

# SSH Tunnel Configuration for PostgreSQL
SSH_CONFIG = {
    'ssh_host': os.getenv('SSH_HOST', '192.168.10.39'),
    'ssh_port': int(os.getenv('SSH_PORT', 22)),
    'ssh_username': os.getenv('SSH_USERNAME', 'gbladmin'),
    'ssh_password': os.getenv('SSH_PASSWORD', '123456'),
    'local_port': int(os.getenv('SSH_LOCAL_PORT', 6543)),
    'remote_port': int(os.getenv('SSH_REMOTE_PORT', 5432))
}

# PostgreSQL Database Configuration (via SSH tunnel)
POSTGRES_CONFIG = {
    'username': os.getenv('POSTGRES_USER', 'postgres'),
    'password': os.getenv('POSTGRES_PASSWORD', 'rootpass'),
    'database': os.getenv('POSTGRES_DB', 'mydb'),
    'host': os.getenv('POSTGRES_HOST', 'localhost'),
    'port': int(os.getenv('POSTGRES_PORT', 5432))
}

# Connection Pool Settings
POOL_CONFIG = {
    'min_pool_size': int(os.getenv('DB_MIN_POOL_SIZE', 2)),
    'max_pool_size': int(os.getenv('DB_MAX_POOL_SIZE', 10)),
    'increment': int(os.getenv('DB_POOL_INCREMENT', 1))
}
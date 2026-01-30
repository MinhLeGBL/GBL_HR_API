"""
GBL HR API - Application Entry Point

This file imports the Flask application from app.main and runs it.
The main application logic is in app/main.py
"""
import os
from dotenv import load_dotenv
from app.main import app

load_dotenv()

if __name__ == '__main__':
    port = int(os.getenv('API_PORT', 5000))
    debug = os.getenv('DEBUG', 'False').lower() == 'true'
    app.run(debug=debug, host='0.0.0.0', port=port)
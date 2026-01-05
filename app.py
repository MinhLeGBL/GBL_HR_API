"""
GBL HR API - Application Entry Point

This file imports the Flask application from app.main and runs it.
The main application logic is in app/main.py
"""
from app.main import app

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
#!/bin/bash

################################################################################
# GBL HR API - Quick Setup Script (No Root Required)
#
# This script sets up the application without requiring root privileges
# Use this if you don't have sudo access or want a simple local setup
#
# Usage: bash quick-setup.sh
################################################################################

set -e  # Exit on error

# Color codes
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$APP_DIR/.venv"

################################################################################
# Main Setup
################################################################################

log_info "GBL HR API - Quick Setup"
echo ""

# Check Python
log_info "Checking Python installation..."
if ! command -v python3 &> /dev/null; then
    log_error "Python 3 is not installed. Please install Python 3.8 or higher."
    exit 1
fi

PYTHON_VERSION=$(python3 --version | awk '{print $2}')
log_info "Python version: $PYTHON_VERSION"

# Create virtual environment
log_info "Creating virtual environment..."
if [ -d "$VENV_DIR" ]; then
    log_warn "Virtual environment already exists. Skipping..."
else
    python3 -m venv $VENV_DIR
    log_info "Virtual environment created"
fi

# Activate virtual environment
source $VENV_DIR/bin/activate

# Upgrade pip
log_info "Upgrading pip..."
pip install --upgrade pip --quiet

# Install dependencies
log_info "Installing Python packages (this may take a few minutes)..."
pip install -r requirements.txt --quiet

# Create directories
log_info "Creating necessary directories..."
mkdir -p logs
mkdir -p config

# Copy configuration template
if [ ! -f .env ]; then
    log_info "Creating .env file from template..."
    cp .env.example .env
    log_warn "Please edit .env with your database credentials"
else
    log_info ".env file already exists"
fi

# Check for Google credentials
if [ ! -f config/credentials.json ]; then
    log_warn "Google Sheets credentials not found"
    log_warn "Please create config/credentials.json with your service account credentials"
fi

# Success message
echo ""
echo "=========================================="
log_info "Quick setup completed!"
echo "=========================================="
echo ""
log_info "Next steps:"
echo ""
echo "1. Configure your database credentials:"
echo "   nano .env"
echo ""
echo "2. Add Google Sheets service account credentials:"
echo "   nano config/credentials.json"
echo ""
echo "3. Activate the virtual environment:"
echo "   source .venv/bin/activate"
echo ""
echo "4. Run the application:"
echo "   python app.py"
echo ""
echo "   OR use Gunicorn for production:"
echo "   gunicorn -w 4 -b 0.0.0.0:5000 'app.main:app'"
echo ""
log_warn "Note: This setup runs the app without systemd service."
log_warn "For production deployment, use the full setup.sh script with sudo."

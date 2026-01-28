#!/bin/bash

################################################################################
# GBL HR API - Server Setup Script
#
# This script sets up a Linux server to run the GBL HR Commission API
# Run this after cloning the repository from GitHub
#
# Usage: sudo bash setup.sh
################################################################################

set -e  # Exit on error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="${SUDO_USER:-$USER}"
VENV_DIR="$APP_DIR/.venv"
SERVICE_NAME="gbl-hr-api"
SERVICE_PORT=5000

################################################################################
# Helper Functions
################################################################################

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

################################################################################
# System Requirements Check
################################################################################

check_requirements() {
    log_info "Checking system requirements..."

    # Check Python version
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 --version | awk '{print $2}')
        log_info "Python version: $PYTHON_VERSION"

        # Check if Python >= 3.8
        PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
        PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

        if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 8 ]); then
            log_error "Python 3.8 or higher is required"
            exit 1
        fi
    else
        log_error "Python 3 is not installed"
        exit 1
    fi

    # Check OS
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        log_info "Operating System: $NAME $VERSION"
    fi
}

################################################################################
# Install System Dependencies
################################################################################

install_system_dependencies() {
    log_info "Installing system dependencies..."

    # Detect package manager
    if command -v apt-get &> /dev/null; then
        # Debian/Ubuntu
        apt-get update
        apt-get install -y \
            python3-pip \
            python3-venv \
            python3-dev \
            build-essential \
            libpq-dev \
            libaio1 \
            wget \
            unzip \
            curl

    elif command -v yum &> /dev/null; then
        # RHEL/CentOS
        yum update -y
        yum install -y \
            python3-pip \
            python3-devel \
            gcc \
            gcc-c++ \
            make \
            postgresql-devel \
            libaio \
            wget \
            unzip \
            curl

    else
        log_error "Unsupported package manager. Please install dependencies manually."
        exit 1
    fi

    log_info "System dependencies installed successfully"
}

################################################################################
# Install Oracle Instant Client
################################################################################

install_oracle_client() {
    log_info "Installing Oracle Instant Client..."

    ORACLE_DIR="/opt/oracle"

    # Check for different Oracle versions (19.30, 19.16, etc.)
    # Look for existing installations
    if [ -d "$ORACLE_DIR/instantclient_19_30" ]; then
        log_info "Oracle Instant Client 19.30 already installed"
        ORACLE_IC_DIR="$ORACLE_DIR/instantclient_19_30"
    elif [ -d "$ORACLE_DIR/instantclient_19_16" ]; then
        log_info "Oracle Instant Client 19.16 already installed"
        ORACLE_IC_DIR="$ORACLE_DIR/instantclient_19_16"
    else
        # Not installed, check for ZIP files to install
        ORACLE_IC_DIR=""

        # Check for version 19.30 files first
        if [ -f "/tmp/instantclient-basic-linux.x64-19.30.0.0.0dbru.zip" ]; then
            log_info "Found Oracle 19.30 files, installing..."
            ORACLE_VERSION="19.30"
            ORACLE_IC_DIR="$ORACLE_DIR/instantclient_19_30"

            mkdir -p $ORACLE_DIR
            cd $ORACLE_DIR
            unzip -q /tmp/instantclient-basic-linux.x64-19.30.0.0.0dbru.zip -d $ORACLE_DIR
            unzip -q /tmp/instantclient-sdk-linux.x64-19.30.0.0.0dbru.zip -d $ORACLE_DIR

        # Check for version 19.16 files
        elif [ -f "/tmp/instantclient-basic-linux.x64-19.16.0.0.0dbru.zip" ]; then
            log_info "Found Oracle 19.16 files, installing..."
            ORACLE_VERSION="19.16"
            ORACLE_IC_DIR="$ORACLE_DIR/instantclient_19_16"

            mkdir -p $ORACLE_DIR
            cd $ORACLE_DIR
            unzip -q /tmp/instantclient-basic-linux.x64-19.16.0.0.0dbru.zip -d $ORACLE_DIR
            unzip -q /tmp/instantclient-sdk-linux.x64-19.16.0.0.0dbru.zip -d $ORACLE_DIR
        else
            # No files found
            log_warn "Oracle Instant Client ZIP files not found in /tmp/"
            log_warn ""
            log_warn "To install Oracle Instant Client, download these files:"
            log_warn "  https://www.oracle.com/database/technologies/instant-client/linux-x86-64-downloads.html"
            log_warn ""
            log_warn "Supported versions: 19.30 or 19.16"
            log_warn "  For 19.30: instantclient-basic-linux.x64-19.30.0.0.0dbru.zip"
            log_warn "             instantclient-sdk-linux.x64-19.30.0.0.0dbru.zip"
            log_warn "  For 19.16: instantclient-basic-linux.x64-19.16.0.0.0dbru.zip"
            log_warn "             instantclient-sdk-linux.x64-19.16.0.0.0dbru.zip"
            log_warn ""
            log_warn "Place them in /tmp/ and run this script again to install Oracle Client."
            log_warn "Continuing setup without Oracle Client (you can add it later)..."
            echo ""
            sleep 2
            return 0
        fi
    fi

    # Set up library path if we have an installation
    if [ -n "$ORACLE_IC_DIR" ] && [ -d "$ORACLE_IC_DIR" ]; then
        echo "$ORACLE_IC_DIR" > /etc/ld.so.conf.d/oracle-instantclient.conf
        ldconfig
        log_info "Oracle Instant Client installed successfully at $ORACLE_IC_DIR"
    fi
}

################################################################################
# Create Virtual Environment and Install Python Packages
################################################################################

setup_python_environment() {
    log_info "Setting up Python virtual environment..."

    cd $APP_DIR

    # Create virtual environment
    sudo -u $APP_USER python3 -m venv $VENV_DIR

    # Activate and upgrade pip
    log_info "Upgrading pip..."
    sudo -u $APP_USER $VENV_DIR/bin/pip install --upgrade pip

    # Install Python dependencies
    log_info "Installing Python packages (this may take a few minutes)..."
    sudo -u $APP_USER $VENV_DIR/bin/pip install -r requirements.txt

    log_info "Python environment setup complete"
}

################################################################################
# Configure Application
################################################################################

configure_application() {
    log_info "Configuring application..."

    cd $APP_DIR

    # Create logs directory
    mkdir -p logs
    chown $APP_USER:$APP_USER logs

    # Create config directory if it doesn't exist
    mkdir -p config
    chown $APP_USER:$APP_USER config

    # Copy .env.example to .env if it doesn't exist
    if [ ! -f .env ]; then
        log_info "Creating .env file from template..."
        cp .env.example .env
        chown $APP_USER:$APP_USER .env
        log_warn "Please edit .env file with your database credentials"
    else
        log_info ".env file already exists"
    fi

    # Check for Google credentials
    if [ ! -f config/credentials.json ]; then
        log_warn "Google Sheets credentials not found at config/credentials.json"
        log_warn "Please place your service account credentials there"
    fi

    log_info "Application configuration complete"
}

################################################################################
# Create Systemd Service
################################################################################

create_systemd_service() {
    log_info "Creating systemd service..."

    cat > /etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=GBL HR Commission API
After=network.target

[Service]
Type=notify
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
Environment="PATH=$VENV_DIR/bin"
Environment="LD_LIBRARY_PATH=/opt/oracle/instantclient_19_16"
ExecStart=$VENV_DIR/bin/gunicorn \\
    --bind 0.0.0.0:$SERVICE_PORT \\
    --workers 4 \\
    --timeout 300 \\
    --access-logfile $APP_DIR/logs/access.log \\
    --error-logfile $APP_DIR/logs/error.log \\
    --log-level info \\
    "app.main:app"
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

    # Reload systemd
    systemctl daemon-reload

    log_info "Systemd service created: ${SERVICE_NAME}.service"
}

################################################################################
# Setup Firewall
################################################################################

setup_firewall() {
    log_info "Configuring firewall..."

    # Check if firewalld is running
    if systemctl is-active --quiet firewalld; then
        firewall-cmd --permanent --add-port=${SERVICE_PORT}/tcp
        firewall-cmd --reload
        log_info "Firewall configured (port $SERVICE_PORT opened)"
    elif command -v ufw &> /dev/null; then
        ufw allow ${SERVICE_PORT}/tcp
        log_info "Firewall configured (port $SERVICE_PORT opened)"
    else
        log_warn "No firewall detected. Please manually open port $SERVICE_PORT if needed"
    fi
}

################################################################################
# Display Final Instructions
################################################################################

show_instructions() {
    echo ""
    echo "=========================================="
    log_info "Setup completed successfully!"
    echo "=========================================="
    echo ""
    log_info "Next steps:"
    echo ""
    echo "1. Configure environment variables:"
    echo "   nano .env"
    echo ""
    echo "2. Add Google Sheets credentials:"
    echo "   nano config/credentials.json"
    echo ""
    echo "3. Test the application:"
    echo "   source .venv/bin/activate"
    echo "   python app.py"
    echo ""
    echo "4. Start the service:"
    echo "   sudo systemctl start ${SERVICE_NAME}"
    echo "   sudo systemctl enable ${SERVICE_NAME}"
    echo ""
    echo "5. Check service status:"
    echo "   sudo systemctl status ${SERVICE_NAME}"
    echo ""
    echo "6. View logs:"
    echo "   tail -f logs/access.log"
    echo "   tail -f logs/error.log"
    echo ""
    echo "API will be available at: http://localhost:${SERVICE_PORT}"
    echo ""
    log_warn "Don't forget to configure your .env file and Google credentials!"
}

################################################################################
# Main Execution
################################################################################

main() {
    log_info "Starting GBL HR API server setup..."
    echo ""

    check_root
    check_requirements
    install_system_dependencies
    install_oracle_client
    setup_python_environment
    configure_application
    create_systemd_service
    setup_firewall

    show_instructions
}

# Run main function
main

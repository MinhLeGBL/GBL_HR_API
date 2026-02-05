# Deployment Guide

Complete guide for deploying the GBL HR Commission API to a Linux server.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start](#quick-start)
3. [Detailed Setup](#detailed-setup)
4. [Configuration](#configuration)
5. [Running the Service](#running-the-service)
6. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### System Requirements

- **OS**: Linux (Ubuntu 20.04+, CentOS 7+, or similar)
- **Python**: 3.8 or higher
- **RAM**: Minimum 2GB, recommended 4GB
- **Storage**: Minimum 500MB free space

### Network Requirements

- Access to Oracle database (RetailPro)
- SSH access to PostgreSQL server (for hand carry data)
- Internet access for Google Sheets API
- Open port 5000 (or your chosen port) for API access

### Required Credentials

1. **Oracle Database**
   - Host, port, service name
   - Database username and password

2. **PostgreSQL Database**
   - SSH tunnel credentials (host, port, username, key file)
   - Database name, username, password

3. **Google Cloud**
   - Service account credentials JSON file
   - Google Sheets API enabled
   - Google Drive API enabled
   - Spreadsheet shared with service account

---

## Quick Start

### Option 1: Automated Setup (Requires root/sudo)

```bash
# Clone the repository
git clone https://github.com/MinhLeGBL/GBL_HR_API.git
cd GBL_HR_API

# Checkout deployment branch
git checkout deployment

# Run setup script
sudo bash setup.sh
```

### Option 2: Quick Setup (No root required)

```bash
# Clone the repository
git clone https://github.com/MinhLeGBL/GBL_HR_API.git
cd GBL_HR_API

# Checkout deployment branch
git checkout deployment

# Run quick setup
bash quick-setup.sh
```

---

## Detailed Setup

### 1. Clone Repository

```bash
git clone https://github.com/MinhLeGBL/GBL_HR_API.git
cd GBL_HR_API
git checkout deployment
```

### 2. Install System Dependencies

#### Ubuntu/Debian

```bash
sudo apt-get update
sudo apt-get install -y \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    libpq-dev \
    libaio1 \
    wget \
    unzip
```

#### CentOS/RHEL

```bash
sudo yum update -y
sudo yum install -y \
    python3-pip \
    python3-devel \
    gcc \
    gcc-c++ \
    postgresql-devel \
    libaio \
    wget \
    unzip
```

### 3. Install Oracle Instant Client

#### Download Files

Download from [Oracle Instant Client Downloads](https://www.oracle.com/database/technologies/instant-client/linux-x86-64-downloads.html):

**Version 19.16** (Required):
- `instantclient-basic-linux.x64-19.16.0.0.0dbru.zip`
- `instantclient-sdk-linux.x64-19.16.0.0.0dbru.zip`

#### Install

```bash
# Create Oracle directory
sudo mkdir -p /opt/oracle
cd /opt/oracle

# Extract files
sudo unzip /path/to/instantclient-basic-linux.x64-19.16.0.0.0dbru.zip
sudo unzip /path/to/instantclient-sdk-linux.x64-19.16.0.0.0dbru.zip

# Configure library path
echo /opt/oracle/instantclient_19_16 | sudo tee /etc/ld.so.conf.d/oracle-instantclient.conf
sudo ldconfig
```

### 4. Set Up Python Environment

```bash
# Create virtual environment
python3 -m venv .venv

# Activate virtual environment
source .venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt
```

### 5. Create Required Directories

```bash
mkdir -p logs
mkdir -p config
```

---

## Configuration

### 1. Environment Variables

Copy the example file and edit:

```bash
cp .env.example .env
nano .env
```

**Required variables:**

```bash
# Oracle Database (RetailPro)
ORACLE_HOST=your_oracle_host
ORACLE_PORT=1521
ORACLE_SERVICE_NAME=your_service_name
ORACLE_USER=your_username
ORACLE_PASSWORD=your_password

# PostgreSQL Database (Hand Carry)
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=mydb
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password

# SSH Tunnel for PostgreSQL
SSH_HOST=your_ssh_host
SSH_PORT=22
SSH_USER=your_ssh_user
SSH_KEY_PATH=/path/to/ssh/key

# Google Sheets
GOOGLE_CREDENTIALS_FILE=config/credentials.json

# Flask
FLASK_ENV=production
FLASK_DEBUG=False
```

### 2. Google Sheets Credentials

1. Create a service account in Google Cloud Console
2. Enable Google Sheets API and Google Drive API
3. Download the credentials JSON file
4. Place it in `config/credentials.json`

```bash
nano config/credentials.json
```

Paste your service account credentials JSON.

### 3. Share Google Sheet

Share your commission spreadsheet with the service account email found in the credentials file:

```json
{
  "client_email": "your-service-account@project-id.iam.gserviceaccount.com"
}
```

Share the spreadsheet with this email as **Editor**.

---

## Running the Service

### Development Mode

```bash
source .venv/bin/activate
python app.py
```

Access at: http://localhost:5000

### Production Mode with Gunicorn

```bash
source .venv/bin/activate
gunicorn -w 4 -b 0.0.0.0:5000 --timeout 300 "app.main:app"
```

### As Systemd Service (Recommended)

If you used `setup.sh`, the service is already created:

```bash
# Start service
sudo systemctl start gbl-hr-api

# Enable on boot
sudo systemctl enable gbl-hr-api

# Check status
sudo systemctl status gbl-hr-api

# View logs
sudo journalctl -u gbl-hr-api -f
```

Or view application logs:

```bash
tail -f logs/access.log
tail -f logs/error.log
```

---

## Testing the API

### Health Check

```bash
curl http://localhost:5000/api/v1/health
```

Expected response:
```json
{
  "status": "healthy",
  "timestamp": "2026-01-28T10:00:00"
}
```

### Database Health Check

```bash
curl http://localhost:5000/api/v1/health/database
```

### Commission Calculation

```bash
curl "http://localhost:5000/api/v1/commission/store/calculate-from-sheet?spreadsheet_title=Commission"
```

---

## Troubleshooting

### Oracle Connection Issues

**Error**: `DPI-1047: Cannot locate a 64-bit Oracle Client library`

**Solution**: Ensure Oracle Instant Client is installed and library path is configured:

```bash
export LD_LIBRARY_PATH=/opt/oracle/instantclient_19_16:$LD_LIBRARY_PATH
sudo ldconfig
```

### PostgreSQL SSH Tunnel Issues

**Error**: `SSH tunnel connection failed`

**Solution**:
- Verify SSH credentials and key file permissions: `chmod 600 /path/to/key`
- Test SSH connection: `ssh -i /path/to/key user@host`
- Check SSH_HOST, SSH_PORT, SSH_USER in .env

### Google Sheets Permission Denied

**Error**: `APIError: [403] Insufficient authentication scopes`

**Solution**:
- Verify Google Sheets API and Drive API are enabled
- Ensure spreadsheet is shared with service account email
- Check credentials.json file is valid

### Import Errors

**Error**: `ModuleNotFoundError: No module named 'cx_Oracle'`

**Solution**: Reinstall dependencies in virtual environment:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### Port Already in Use

**Error**: `Address already in use`

**Solution**: Kill process on port 5000 or use a different port:

```bash
# Find and kill process
sudo lsof -ti:5000 | xargs kill -9

# Or run on different port
gunicorn -w 4 -b 0.0.0.0:8000 "app.main:app"
```

### Service Won't Start

Check logs:
```bash
sudo journalctl -u gbl-hr-api -n 50
tail -50 logs/error.log
```

Common issues:
- Missing environment variables in .env
- Database connection failures
- Permission issues on directories

---

## Security Recommendations

1. **Firewall**: Restrict API access to trusted IPs
2. **Environment**: Never commit .env or credentials.json
3. **SSH Keys**: Use strong SSH keys with passphrases
4. **Database**: Use read-only database users where possible
5. **HTTPS**: Use reverse proxy (nginx/Apache) with SSL certificate
6. **Updates**: Keep system and Python packages updated

---

## Maintenance

### Update Application

```bash
cd /path/to/GBL_HR_API
git pull origin deployment
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart gbl-hr-api
```

### View Logs

```bash
# Application logs
tail -f logs/access.log
tail -f logs/error.log

# System logs
sudo journalctl -u gbl-hr-api -f
```

### Backup

Important files to backup:
- `.env` - Environment configuration
- `config/credentials.json` - Google credentials
- `logs/` - Application logs (optional)

---

## Support

For issues or questions, contact the development team or create an issue in the GitHub repository.

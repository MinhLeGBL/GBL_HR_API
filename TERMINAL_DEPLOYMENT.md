# Terminal-Only Deployment Guide

Complete guide for deploying GBL HR API to a Linux server using only terminal/SSH.

## Prerequisites

- SSH access to your Linux server
- Oracle Instant Client zip files on your local machine
- Google service account credentials JSON file
- Database credentials

---

## Step 1: Connect to Server

```bash
ssh your_username@your_server_ip
```

---

## Step 2: Install Required System Packages

### For Ubuntu/Debian:
```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-pip python3-venv unzip wget curl
```

### For CentOS/RHEL:
```bash
sudo yum update -y
sudo yum install -y git python3 python3-pip unzip wget curl
```

---

## Step 3: Clone Repository

```bash
# Navigate to your preferred directory
cd /opt  # or ~/

# Clone the repository
git clone https://github.com/MinhLeGBL/GBL_HR_API.git

# Navigate into the project
cd GBL_HR_API

# Switch to deployment branch
git checkout deployment

# Verify you're on deployment branch
git branch
```

---

## Step 4: Transfer Oracle Instant Client Files

You need to upload Oracle Instant Client files from your local machine to the server.

### Option A: Using SCP (from your local machine)

```bash
# Open a NEW terminal on your local machine (not SSH session)
# Navigate to where you downloaded the Oracle files

# Transfer files to server
scp instantclient-basic-linux.x64-19.16.0.0.0dbru.zip your_username@your_server_ip:/tmp/
scp instantclient-sdk-linux.x64-19.16.0.0.0dbru.zip your_username@your_server_ip:/tmp/
```

### Option B: Using wget (if you have a download URL)

```bash
# On the server
cd /tmp
wget YOUR_ORACLE_BASIC_DOWNLOAD_URL -O instantclient-basic-linux.x64-19.16.0.0.0dbru.zip
wget YOUR_ORACLE_SDK_DOWNLOAD_URL -O instantclient-sdk-linux.x64-19.16.0.0.0dbru.zip
```

### Verify files are in /tmp:
```bash
ls -lh /tmp/instantclient*.zip
```

---

## Step 5: Transfer Google Credentials

### Option A: Using SCP (from your local machine)

```bash
# On your local machine
scp /path/to/your/credentials.json your_username@your_server_ip:/tmp/gbl-credentials.json
```

### Option B: Create file directly on server

```bash
# On the server
nano /tmp/gbl-credentials.json
```

Paste your Google service account credentials JSON, then save:
- Press `Ctrl + X`
- Press `Y` to confirm
- Press `Enter` to save

---

## Step 6: Run Setup Script

```bash
# Navigate to project directory
cd /opt/GBL_HR_API  # or wherever you cloned it

# Run the setup script
sudo bash setup.sh
```

The script will:
1. Install system dependencies
2. Install Oracle Instant Client (from /tmp files)
3. Create Python virtual environment
4. Install Python packages
5. Create systemd service
6. Configure firewall

**Follow the prompts** - if Oracle files are not found, it will ask if you want to continue.

---

## Step 7: Configure Environment Variables

```bash
# Edit the .env file
nano .env
```

Update these values:
```bash
# Oracle Database
ORACLE_HOST=your_oracle_host
ORACLE_PORT=1521
ORACLE_SERVICE_NAME=your_service_name
ORACLE_USER=your_username
ORACLE_PASSWORD=your_password

# PostgreSQL Database
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=mydb
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password

# SSH Tunnel for PostgreSQL
SSH_HOST=your_ssh_host
SSH_PORT=22
SSH_USER=your_ssh_user
SSH_KEY_PATH=/home/your_username/.ssh/id_rsa
# Or use password:
SSH_PASSWORD=your_ssh_password

# Google Sheets
GOOGLE_CREDENTIALS_FILE=config/credentials.json

# Flask
FLASK_ENV=production
FLASK_DEBUG=False
```

Save and exit:
- Press `Ctrl + X`
- Press `Y`
- Press `Enter`

---

## Step 8: Setup Google Credentials

```bash
# Create config directory if it doesn't exist
mkdir -p config

# Copy credentials from /tmp to config
cp /tmp/gbl-credentials.json config/credentials.json

# Set proper permissions
chmod 600 config/credentials.json

# Verify the file
cat config/credentials.json
```

---

## Step 9: Setup SSH Key (if using SSH tunnel)

If you're using SSH key authentication for PostgreSQL:

### Option A: Transfer existing key from local machine

```bash
# On your local machine
scp ~/.ssh/your_postgres_key your_username@your_server_ip:~/.ssh/postgres_key
```

Then on the server:
```bash
chmod 600 ~/.ssh/postgres_key
```

### Option B: Create new SSH key on server

```bash
# Generate new key
ssh-keygen -t rsa -b 4096 -f ~/.ssh/postgres_key

# Copy public key to PostgreSQL server
ssh-copy-id -i ~/.ssh/postgres_key.pub user@postgres_server

# Update .env with key path
nano .env
# Set: SSH_KEY_PATH=/home/your_username/.ssh/postgres_key
```

---

## Step 10: Test the Application

Before starting the service, test manually:

```bash
# Activate virtual environment
source .venv/bin/activate

# Set Oracle library path
export LD_LIBRARY_PATH=/opt/oracle/instantclient_19_16:$LD_LIBRARY_PATH

# Test database connections
python -c "from app.database.connection import get_oracle_connection; conn = get_oracle_connection(); print('Oracle OK' if conn else 'Oracle FAILED')"

# Start Flask manually
python app.py
```

In another SSH session, test the API:
```bash
curl http://localhost:5000/api/v1/health
```

If working, press `Ctrl + C` to stop Flask.

---

## Step 11: Start the Systemd Service

```bash
# Enable and start the service
sudo systemctl enable gbl-hr-api
sudo systemctl start gbl-hr-api

# Check status
sudo systemctl status gbl-hr-api

# View logs
sudo journalctl -u gbl-hr-api -f
```

---

## Step 12: Test the API

```bash
# Health check
curl http://localhost:5000/api/v1/health

# Database health
curl http://localhost:5000/api/v1/health/database

# Commission calculation (replace with your spreadsheet name)
curl "http://localhost:5000/api/v1/commission/store/calculate-from-sheet?spreadsheet_title=Commission"
```

---

## Common Terminal Commands

### View logs
```bash
# Service logs
sudo journalctl -u gbl-hr-api -f

# Application logs
tail -f logs/access.log
tail -f logs/error.log

# Show last 50 lines
sudo journalctl -u gbl-hr-api -n 50
```

### Restart service
```bash
sudo systemctl restart gbl-hr-api
```

### Stop service
```bash
sudo systemctl stop gbl-hr-api
```

### Check service status
```bash
sudo systemctl status gbl-hr-api
```

### Edit files
```bash
# Using nano (easier for beginners)
nano filename.txt

# Using vim (more powerful)
vim filename.txt
```

### Check if port is open
```bash
# Check if service is listening
sudo netstat -tlnp | grep 5000

# Or using ss
sudo ss -tlnp | grep 5000

# Or using lsof
sudo lsof -i :5000
```

### Transfer files between machines
```bash
# From local to server
scp local_file.txt user@server:/remote/path/

# From server to local
scp user@server:/remote/path/file.txt ./local_path/

# Copy entire directory
scp -r local_directory/ user@server:/remote/path/
```

---

## Troubleshooting

### Oracle Client Issues

**Test Oracle installation:**
```bash
# Check if files exist
ls -la /opt/oracle/instantclient_19_16/

# Check library path
echo $LD_LIBRARY_PATH

# Test Oracle connection
export LD_LIBRARY_PATH=/opt/oracle/instantclient_19_16:$LD_LIBRARY_PATH
python3 -c "import oracledb; print('Oracle driver OK')"
```

**Fix library path:**
```bash
# Add to systemd service if not already there
sudo nano /etc/systemd/system/gbl-hr-api.service
# Add: Environment="LD_LIBRARY_PATH=/opt/oracle/instantclient_19_16"

# Reload and restart
sudo systemctl daemon-reload
sudo systemctl restart gbl-hr-api
```

### Permission Issues

```bash
# Fix ownership
sudo chown -R your_username:your_username /opt/GBL_HR_API

# Fix log permissions
sudo chown -R your_username:your_username logs/

# Fix config permissions
chmod 600 .env
chmod 600 config/credentials.json
```

### Service Not Starting

```bash
# View detailed logs
sudo journalctl -u gbl-hr-api -n 100 --no-pager

# Check for errors
sudo systemctl status gbl-hr-api -l

# Test manually
source .venv/bin/activate
export LD_LIBRARY_PATH=/opt/oracle/instantclient_19_16:$LD_LIBRARY_PATH
python app.py
```

### Firewall Issues

```bash
# Check firewall status
sudo firewall-cmd --list-all

# Or for ufw
sudo ufw status

# Open port manually
sudo firewall-cmd --permanent --add-port=5000/tcp
sudo firewall-cmd --reload

# Or for ufw
sudo ufw allow 5000/tcp
```

---

## Quick Reference Card

```bash
# Navigate to project
cd /opt/GBL_HR_API

# Edit environment
nano .env

# Restart service
sudo systemctl restart gbl-hr-api

# View logs
sudo journalctl -u gbl-hr-api -f

# Check status
sudo systemctl status gbl-hr-api

# Test API
curl http://localhost:5000/api/v1/health

# Update from git
git pull origin deployment
sudo systemctl restart gbl-hr-api
```

---

## Remote Access

If you need to access the API from outside the server:

```bash
# Option 1: Open firewall for external access
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --reload

# Option 2: Use SSH tunnel from your local machine
ssh -L 5000:localhost:5000 user@server
# Then access http://localhost:5000 on your local machine

# Option 3: Setup nginx reverse proxy (recommended for production)
sudo apt-get install nginx
sudo nano /etc/nginx/sites-available/gbl-hr-api
```

---

## Maintenance

### Update Application
```bash
cd /opt/GBL_HR_API
git pull origin deployment
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart gbl-hr-api
```

### Backup Configuration
```bash
# Backup .env and credentials
tar -czf gbl-backup-$(date +%Y%m%d).tar.gz .env config/credentials.json

# Download to local machine
scp user@server:/opt/GBL_HR_API/gbl-backup-*.tar.gz ./
```

### Monitor Logs
```bash
# Setup log rotation
sudo nano /etc/logrotate.d/gbl-hr-api

# Add:
/opt/GBL_HR_API/logs/*.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
}
```

---

## Getting Help

If you encounter issues:

1. Check service logs: `sudo journalctl -u gbl-hr-api -n 50`
2. Check application logs: `tail -50 logs/error.log`
3. Test manually: `python app.py`
4. Verify configurations: `.env` and `config/credentials.json`
5. Check Oracle path: `ls /opt/oracle/instantclient_19_16/`

For more help, see [DEPLOYMENT.md](DEPLOYMENT.md) or contact the development team.

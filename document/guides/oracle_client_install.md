# Oracle Instant Client 19.16 Installation Guide

## Step 1: Check Your Linux Distribution

On the server, run:
```bash
cat /etc/os-release
```

This will tell you which Linux you have.

---

## Step 2: Download Correct Files

### For CentOS / RHEL / Rocky / AlmaLinux / Oracle Linux (x86_64)

Download these files from: https://www.oracle.com/database/technologies/instant-client/linux-x86-64-downloads.html

**Version 19.16.0.0.0 (for Linux x86-64):**
- `oracle-instantclient19.16-basic-19.16.0.0.0-1.x86_64.rpm`
- `oracle-instantclient19.16-devel-19.16.0.0.0-1.x86_64.rpm`

**OR the ZIP files:**
- `instantclient-basic-linux.x64-19.16.0.0.0dbru.zip`
- `instantclient-sdk-linux.x64-19.16.0.0.0dbru.zip`

---

## Step 3: Installation Methods

### Method A: Using RPM (Recommended for RHEL/CentOS)

```bash
# On the server
cd /tmp

# If you have the RPM files, install with:
sudo yum install -y oracle-instantclient19.16-basic-19.16.0.0.0-1.x86_64.rpm
sudo yum install -y oracle-instantclient19.16-devel-19.16.0.0.0-1.x86_64.rpm

# OR using dnf (newer systems):
sudo dnf install -y oracle-instantclient19.16-basic-19.16.0.0.0-1.x86_64.rpm
sudo dnf install -y oracle-instantclient19.16-devel-19.16.0.0.0-1.x86_64.rpm
```

The RPM installs to: `/usr/lib/oracle/19.16/client64`

Configure library path:
```bash
echo /usr/lib/oracle/19.16/client64/lib | sudo tee /etc/ld.so.conf.d/oracle-instantclient.conf
sudo ldconfig
```

### Method B: Using ZIP Files

```bash
# Create Oracle directory
sudo mkdir -p /opt/oracle
cd /opt/oracle

# Extract ZIP files (if you transferred them to /tmp/)
sudo unzip /tmp/instantclient-basic-linux.x64-19.16.0.0.0dbru.zip
sudo unzip /tmp/instantclient-sdk-linux.x64-19.16.0.0.0dbru.zip

# This creates: /opt/oracle/instantclient_19_16

# Configure library path
echo /opt/oracle/instantclient_19_16 | sudo tee /etc/ld.so.conf.d/oracle-instantclient.conf
sudo ldconfig
```

---

## Step 4: Verify Installation

```bash
# Check if library is found
ldconfig -p | grep oracle

# Should show something like:
# libclntsh.so.19.1 (libc6,x86-64) => /opt/oracle/instantclient_19_16/libclntsh.so.19.1
```

Test with Python:
```bash
cd ~/GBL_HR_API
source .venv/bin/activate
python -c "import oracledb; print('Oracle driver loaded successfully')"
```

---

## Easy Download & Install (One-liner for RHEL/CentOS)

If you want to download directly on the server (requires Oracle account):

```bash
# Download using wget or curl (you'll need to authenticate)
# This usually requires accepting Oracle's license agreement

# Alternative: Download on your Mac and transfer
# On your Mac:
# 1. Download the RPM files
# 2. Transfer to server:
scp oracle-instantclient19.16-basic-19.16.0.0.0-1.x86_64.rpm gbladmin@192.168.10.39:/tmp/
scp oracle-instantclient19.16-devel-19.16.0.0.0-1.x86_64.rpm gbladmin@192.168.10.39:/tmp/

# Then on server:
cd /tmp
sudo yum install -y oracle-instantclient19.16-*.rpm
echo /usr/lib/oracle/19.16/client64/lib | sudo tee /etc/ld.so.conf.d/oracle-instantclient.conf
sudo ldconfig
```

---

## Common Linux Distributions Identification

**CentOS 7:**
```
NAME="CentOS Linux"
VERSION="7 (Core)"
```

**CentOS 8 / Rocky Linux / AlmaLinux:**
```
NAME="Rocky Linux" or "AlmaLinux" or "CentOS Stream"
VERSION="8.x"
```

**Red Hat Enterprise Linux:**
```
NAME="Red Hat Enterprise Linux"
VERSION="7.x" or "8.x"
```

**Oracle Linux:**
```
NAME="Oracle Linux Server"
VERSION="7.x" or "8.x"
```

All of these use the **same Oracle Instant Client RPM/ZIP files** (x86_64).

---

## Quick Reference

**For ZIP method (what we configured in the code):**
- Installation path: `/opt/oracle/instantclient_19_16`
- Library path: Add to `/etc/ld.so.conf.d/oracle-instantclient.conf`
- Then run: `sudo ldconfig`

**For RPM method:**
- Installation path: `/usr/lib/oracle/19.16/client64`
- Update code in `app/database/connection.py` line 15 to:
  ```python
  d = "/usr/lib/oracle/19.16/client64/lib"
  ```

---

## Troubleshooting

**Error: Cannot locate Oracle Client library**
```bash
# Check if files exist
ls -la /opt/oracle/instantclient_19_16/
# or
ls -la /usr/lib/oracle/19.16/client64/

# Check library path
cat /etc/ld.so.conf.d/oracle-instantclient.conf
ldconfig -p | grep oracle

# Verify in Python
export LD_LIBRARY_PATH=/opt/oracle/instantclient_19_16:$LD_LIBRARY_PATH
python -c "import oracledb; print('OK')"
```

**If still not working:**
- Ensure libaio is installed: `sudo yum install -y libaio`
- Check permissions: `sudo chmod -R 755 /opt/oracle/instantclient_19_16`

---

## What You Need to Tell Me

Run this on the server and copy the output:
```bash
cat /etc/os-release | grep -E "^NAME=|^VERSION="
uname -m
```

Then I can tell you exactly which files to download and how to install them!

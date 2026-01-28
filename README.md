# GBL HR API - Commission Service

Production deployment of the GBL HR Commission Calculation API.

## Overview

This API provides commission calculation services for GBL retail stores, including:
- Personal commission calculation for employees
- Store commission calculation with team distribution
- Google Sheets integration for input/output
- Batch processing for multiple stores

## Quick Deployment

**For Linux servers**, use one of these automated setup scripts:

### Full Setup (Requires sudo/root)
```bash
git clone https://github.com/MinhLeGBL/GBL_HR_API.git
cd GBL_HR_API
git checkout deployment
sudo bash setup.sh
```

### Quick Setup (No root required)
```bash
git clone https://github.com/MinhLeGBL/GBL_HR_API.git
cd GBL_HR_API
git checkout deployment
bash quick-setup.sh
```

📖 **For detailed deployment instructions, see [DEPLOYMENT.md](DEPLOYMENT.md)**

## Requirements

- Python 3.8+
- Oracle Database (RetailPro data)
- PostgreSQL (Hand carry item data)
- Google Cloud Service Account (for Sheets access)

## Manual Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure environment variables:
```bash
cp .env.example .env
# Edit .env with your database credentials
```

3. Set up Google Sheets credentials:
```bash
# Place your service account credentials in config/credentials.json
```

## Configuration

### Database Connections

Configure in `.env`:
- `ORACLE_HOST`, `ORACLE_PORT`, `ORACLE_SERVICE_NAME` - Oracle RetailPro database
- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB` - PostgreSQL database
- `SSH_HOST`, `SSH_PORT`, `SSH_USER`, `SSH_KEY_PATH` - SSH tunnel for PostgreSQL

### Google Sheets

1. Create a Google Cloud service account
2. Enable Google Sheets API and Google Drive API
3. Share your commission spreadsheet with the service account email
4. Place credentials JSON in `config/credentials.json`

## Running the Service

### Development Mode
```bash
python app.py
```

### Production Mode
Use a production WSGI server like Gunicorn:
```bash
gunicorn -w 4 -b 0.0.0.0:5000 "app.main:app"
```

## API Endpoints

### Commission Calculation from Google Sheets
```
GET /api/v1/commission/store/calculate-from-sheet
Query Parameters:
  - spreadsheet_title: Name of the Google Sheet (e.g., "Commission")
  - sheet_name: Sheet tab name (default: "Sheet1")
```

This endpoint:
1. Reads store and employee data from Google Sheets
2. Calculates commissions for all stores
3. Uploads results back to the sheet

### Health Check
```
GET /api/v1/health
GET /api/v1/health/database
```

## Project Structure

```
.
├── app/
│   ├── api/v1/routes/          # API route handlers
│   ├── services/               # Business logic
│   │   ├── commission_service.py
│   │   └── google_sheets_service.py
│   ├── repositories/           # Data access layer
│   ├── queries/                # SQL queries
│   ├── database/               # Database connections
│   └── utils/                  # Utilities
├── config/                     # Configuration files
│   └── credentials.json        # Google service account
├── app.py                      # Application entry point
└── requirements.txt            # Python dependencies
```

## Commission Calculation Logic

### Store Commission
- Based on store revenue achievement and full-price ratio
- Distributed as 70% individual share + 30% equal share
- Manager bonuses for eligible employees

### Personal Commission
- Full-price items: 0.3% - 3%
- Discounted items: 0.2% - 3%
- Over 100% achievement bonus
- Jewelry commission (Vhernier, Rosa Maria)
- Suitcase and hand carry items

## Support

For issues or questions, contact the development team.

## License

Proprietary - GBL Group

# Store Commission Calculation - Implementation Guide

## Overview
This implementation provides a complete store commission calculation system based on the pseudocode algorithm. It follows the existing layered architecture pattern in the GBL HR API project.

## Directory Structure Created

```
app/
├── queries/
│   └── commission_queries.py          # SQL queries for commission data
├── repositories/
│   └── commission_repository.py       # Data access layer
├── services/
│   └── commission_service.py          # Business logic with 4-step algorithm
└── api/v1/routes/
    └── commission_routes.py           # API endpoints
```

## Files Created

### 1. Commission Queries (`app/queries/commission_queries.py`)
SQL queries for fetching:
- Store sales data with revenue breakdown (full price vs discounted)
- Employee sales data by store
- Employee information (tenure, position)

**Note:** Store targets (revenue goals, FP ratio) are now provided from the frontend, not queried from the database.

### 2. Commission Repository (`app/repositories/commission_repository.py`)
Data access layer that:
- Executes SQL queries using Oracle connection
- Provides methods to fetch store sales, employee sales, and employee info
- Returns data as dictionaries and lists for service layer

### 3. Commission Service (`app/services/commission_service.py`)
Core business logic implementing the 4-step algorithm:

#### Step 1: Check Store Eligibility
- Calculates achievement percentage: `(actual_revenue / target_revenue) × 100%`
- Validates 70% full price revenue requirement
- Checks discounted goods compensation (200% rule)

#### Step 2: Calculate Employee Contributions
- Determines tier rates based on employee tenure (< 6 months vs >= 6 months)
- Allocates employee revenue to tiers based on store achievement
- Applies tier commission rates to full price revenue
- Adds 0.25% commission on discounted revenue (70-80% tier only)

#### Step 3: Calculate Store Pool
- Sums all employee contributions to get total store commission pool

#### Step 4: Distribute Store Pool
- Part A: 70% of pool distributed proportionally based on contribution
- Part B: 30% of pool distributed equally among all employees
- Manager bonus: 3,000,000 VND if achievement >= 100%

### 4. Commission Routes (`app/api/v1/routes/commission_routes.py`)
API endpoints:
- `POST /api/v1/commission/calculate` - Calculate commission (returns JSON)
- `POST /api/v1/commission/dataframe` - Get commission as DataFrame (JSON format)
- `POST /api/v1/commission/export/csv` - Export commission to CSV file
- `POST /api/v1/commission/export/excel` - Export commission to Excel file

## API Usage

### Calculate Commission

**Endpoint:** `POST /api/v1/commission/calculate`

**Request Body:**
```json
{
    "store_code": "S001",
    "store_name": "GBL Flagship Store",
    "target_revenue": 100000000,
    "target_fp_ratio": 0.75,
    "year": 2025,
    "month": 11,
    "start_date": "2025-11-01 00:00:00",
    "end_date": "2025-11-30 23:59:59"
}
```

**Field Descriptions:**
- `store_code`: Unique identifier for the store
- `store_name`: Display name of the store
- `target_revenue`: Target revenue for the period (in VND)
- `target_fp_ratio`: Target full price ratio (decimal between 0.0 and 1.0, e.g., 0.75 = 75%)
- `year`: Year of the commission period
- `month`: Month of the commission period (1-12)
- `start_date`: Start datetime of the period in 'YYYY-MM-DD HH:MI:SS' format
- `end_date`: End datetime of the period in 'YYYY-MM-DD HH:MI:SS' format

**Response (Success):**
```json
{
    "success": true,
    "data": {
        "store_code": "S001",
        "store_name": "GBL Flagship Store",
        "eligible": true,
        "achievement_pct": 105.5,
        "target_revenue": 100000000,
        "target_fp_ratio": 0.75,
        "store_pool": 15000000,
        "employee_count": 5,
        "employees": [
            {
                "employee_name": "Nguyen Van A",
                "tenure_months": 12,
                "is_manager": true,
                "fp_revenue": 50000000,
                "discounted_revenue": 10000000,
                "contribution": 5000000,
                "commission_70pct": 3500000,
                "commission_30pct": 900000,
                "manager_bonus": 3000000,
                "total_commission": 7400000
            },
            ...
        ],
        "period": {
            "start_date": "2025-11-01 00:00:00",
            "end_date": "2025-11-30 23:59:59",
            "year": 2025,
            "month": 11
        }
    }
}
```

**Response (Not Eligible):**
```json
{
    "success": true,
    "data": {
        "store_code": "S001",
        "store_name": "GBL Flagship Store",
        "eligible": false,
        "reason": "Full price revenue (30,000,000) < 70% of target (70,000,000)",
        "achievement_pct": 65.0,
        "target_revenue": 100000000,
        "target_fp_ratio": 0.75,
        "employees": []
    }
}
```

### Get Commission DataFrame

**Endpoint:** `POST /api/v1/commission/dataframe`

**Request Body:** Same as `/calculate` endpoint (see above)

Returns commission data in a structured DataFrame format with columns:
- `store_code`
- `employee_name`
- `is_manager`
- `tenure_months`
- `fp_revenue`
- `discounted_revenue`
- `contribution`
- `commission_70pct`
- `commission_30pct`
- `manager_bonus`
- `total_commission`
- `achievement_pct`
- `store_pool`
- `period_year`
- `period_month`

### Export to CSV

**Endpoint:** `POST /api/v1/commission/export/csv`

**Request Body:** Same as `/calculate` endpoint (see above)

Downloads a CSV file with commission data.

### Export to Excel

**Endpoint:** `POST /api/v1/commission/export/excel`

**Request Body:** Same as `/calculate` endpoint (see above)

Downloads an Excel file with commission data.

## Commission Tier Rates

### New Employees (< 6 months tenure)
| Achievement Range | Commission Rate |
|------------------|----------------|
| 70-80%           | 0.25%          |
| 80-90%           | 0.35%          |
| 90-100%          | 0.45%          |
| 100%+            | 0.55%          |

### Experienced Employees (>= 6 months tenure)
| Achievement Range | Commission Rate |
|------------------|----------------|
| 70-80%           | 0.30%          |
| 80-90%           | 0.40%          |
| 90-100%          | 0.50%          |
| 100%+            | 0.60%          |

## Database Requirements

The implementation expects the following database tables/views:
- `DOCUMENT` - Sales transactions
- `DOCUMENT_ITEM` - Transaction line items
- `STORE` - Store information
- `EMPLOYEE` - Employee information (tenure, position)

**Note:** Store targets (revenue and FP ratio) are no longer fetched from the database. They must be provided by the frontend in the API request.

## Dependencies

Make sure these Python packages are installed:
```bash
pip install pandas openpyxl
```

These should already be in your `requirements.txt`:
- flask
- cx_Oracle (for Oracle database connection)
- pandas
- openpyxl (for Excel export)

## Testing the Implementation

Example test using Python:
```python
from app.services.commission_service import CommissionService

service = CommissionService()
result = service.calculate_store_commission(
    store_code='S001',
    store_name='GBL Flagship Store',
    target_revenue=100000000,
    target_fp_ratio=0.75,
    year=2025,
    month=11,
    start_date='2025-11-01 00:00:00',
    end_date='2025-11-30 23:59:59'
)

# Get as DataFrame
df = service.get_commission_dataframe(
    store_code='S001',
    store_name='GBL Flagship Store',
    target_revenue=100000000,
    target_fp_ratio=0.75,
    year=2025,
    month=11,
    start_date='2025-11-01 00:00:00',
    end_date='2025-11-30 23:59:59'
)

print(df)
```

## Notes

1. **Frontend Integration:** The frontend must provide store information (store name, target revenue, target FP ratio) in each API request. This allows for flexible target management without database dependencies.

2. The tier allocation logic in `_allocate_revenue_to_tiers` uses a simplified approach. You may need to adjust this based on your actual business requirements.

3. Manager identification is based on the position name containing "MANAGER" or "QUẢN LÝ". Adjust the logic in `_calculate_employee_contributions` if your position naming is different.

4. The SQL queries assume Oracle database date format. Adjust if using a different database system.

5. All monetary values are assumed to be in VND (Vietnamese Dong).

6. The implementation returns 0 commission for stores that don't meet eligibility criteria, with a clear reason message.

7. **Validation:** The API validates that `target_fp_ratio` is between 0.0 and 1.0 (representing 0% to 100%).

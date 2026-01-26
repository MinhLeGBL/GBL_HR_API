# Batch Commission Calculation API

## Overview
The batch commission API endpoint allows you to calculate store commissions for multiple employees across multiple stores in a single request.

## Endpoint
```
POST /api/v1/commission/batch/calculate
```

## Request Body Structure

```json
{
  "year": 2025,
  "month": 11,
  "start_date": "2025-11-01 00:00:00",
  "end_date": "2025-11-30 23:59:59",
  "employees": [
    {
      "employee_code": "GL137",
      "employee_name": "Nguyen Quoc Huy",
      "store_code": "RWT",
      "personal_target": 500000000,
      "seniority": 24
    },
    {
      "employee_code": "GL013",
      "employee_name": "Vuong Que Chau",
      "store_code": "RWT",
      "personal_target": 450000000,
      "seniority": 36
    }
  ],
  "stores": [
    {
      "store_code": "RWT",
      "store_name": "RUNWAY TAKASHIMAYA",
      "target_revenue": 4000000000,
      "target_fp_ratio": 0.65
    }
  ]
}
```

### Request Fields

#### Root Level
- `year` (integer, required): Year for the commission period
- `month` (integer, required): Month for the commission period (1-12)
- `start_date` (string, required): Period start date in 'YYYY-MM-DD HH:MI:SS' format
- `end_date` (string, required): Period end date in 'YYYY-MM-DD HH:MI:SS' format
- `employees` (array, required): List of employees to calculate commissions for
- `stores` (array, required): List of store configurations

#### Employee Object
- `employee_code` (string, required): Unique employee code
- `employee_name` (string, required): Full name of the employee (must match database)
- `store_code` (string, required): Store code where employee works
- `personal_target` (number, optional): Employee's personal sales target
- `seniority` (integer, required): Employee tenure in months

#### Store Object
- `store_code` (string, required): Unique store code
- `store_name` (string, optional): Store display name
- `target_revenue` (number, required): Store's target revenue for the period
- `target_fp_ratio` (number, required): Target full-price ratio (0.0 to 1.0, e.g., 0.65 = 65%)

## Response Structure

```json
{
  "success": true,
  "period": {
    "year": 2025,
    "month": 11,
    "start_date": "2025-11-01 00:00:00",
    "end_date": "2025-11-30 23:59:59"
  },
  "summary": {
    "total_employees": 2,
    "eligible_employees": 2,
    "ineligible_employees": 0,
    "total_stores": 1,
    "eligible_stores": 1,
    "total_commission_payout": 6113701
  },
  "stores": [
    {
      "store_code": "RWT",
      "store_name": "RUNWAY TAKASHIMAYA",
      "eligible": true,
      "achievement_pct": 122.00,
      "target_revenue": 4000000000,
      "target_fp_ratio": 0.65,
      "store_pool": 6113701,
      "employee_count": 2
    }
  ],
  "employees": [
    {
      "employee_code": "GL137",
      "employee_name": "Nguyen Quoc Huy",
      "store_code": "RWT",
      "store_name": "RUNWAY TAKASHIMAYA",
      "eligible": true,
      "achievement_pct": 122.00,
      "tenure_months": 24,
      "is_manager": false,
      "fp_revenue": 631848515,
      "discounted_revenue": 192892037,
      "contribution": 3411982,
      "commission_70pct": 2388387,
      "commission_30pct": 917055,
      "manager_bonus": 0,
      "total_commission": 3305443
    }
  ]
}
```

### Response Fields

#### Summary Object
- `total_employees`: Total number of employees in the request
- `eligible_employees`: Number of employees who received commission
- `ineligible_employees`: Number of employees who didn't receive commission
- `total_stores`: Total number of stores processed
- `eligible_stores`: Number of stores that met eligibility criteria
- `total_commission_payout`: Total commission amount across all employees

#### Store Results
- `store_code`: Store identifier
- `store_name`: Store display name
- `eligible`: Whether store met commission eligibility criteria
- `achievement_pct`: Store's achievement percentage vs target
- `store_pool`: Total commission pool for the store
- `employee_count`: Number of employees receiving commission from this store

#### Employee Results
- `employee_code`: Employee identifier
- `employee_name`: Employee full name
- `eligible`: Whether employee received commission
- `fp_revenue`: Employee's full-price revenue (excludes VAT)
- `discounted_revenue`: Employee's discounted revenue (excludes VAT)
- `contribution`: Employee's contribution to store pool
- `commission_70pct`: 70% portion of pool (based on contribution ratio)
- `commission_30pct`: 30% portion of pool (distributed equally)
- `manager_bonus`: Manager bonus (if applicable and achievement >= 100%)
- `total_commission`: Total commission for the employee

## Commission Calculation Logic

### Store Eligibility
A store is eligible for commission if:
1. Full price revenue >= 70% of target revenue
2. Actual FP ratio meets target OR discounted revenue compensates (200% of FP shortage)

### Employee Commission Components
1. **Contribution Calculation** (based on tier rates and tenure):
   - New employees (< 6 months): 0.25%-0.55%
   - Experienced employees (>= 6 months): 0.30%-0.60%

2. **Pool Distribution**:
   - 70%: Distributed proportionally based on contribution
   - 30%: Distributed equally among all employees

3. **Manager Bonus**: 3,000,000 VND if achievement >= 100%

## Example Usage

### cURL
```bash
curl -X POST http://localhost:5000/api/v1/commission/batch/calculate \
  -H "Content-Type: application/json" \
  -d '{
    "year": 2025,
    "month": 11,
    "start_date": "2025-11-01 00:00:00",
    "end_date": "2025-11-30 23:59:59",
    "employees": [
      {
        "employee_code": "GL137",
        "employee_name": "Nguyen Quoc Huy",
        "store_code": "RWT",
        "seniority": 24
      }
    ],
    "stores": [
      {
        "store_code": "RWT",
        "store_name": "RUNWAY TAKASHIMAYA",
        "target_revenue": 4000000000,
        "target_fp_ratio": 0.65
      }
    ]
  }'
```

### Python
```python
import requests

url = "http://localhost:5000/api/v1/commission/batch/calculate"

payload = {
    "year": 2025,
    "month": 11,
    "start_date": "2025-11-01 00:00:00",
    "end_date": "2025-11-30 23:59:59",
    "employees": [
        {
            "employee_code": "GL137",
            "employee_name": "Nguyen Quoc Huy",
            "store_code": "RWT",
            "seniority": 24
        }
    ],
    "stores": [
        {
            "store_code": "RWT",
            "store_name": "RUNWAY TAKASHIMAYA",
            "target_revenue": 4000000000,
            "target_fp_ratio": 0.65
        }
    ]
}

response = requests.post(url, json=payload)
result = response.json()

if result['success']:
    print(f"Total Commission: {result['summary']['total_commission_payout']:,} VND")
    for emp in result['employees']:
        print(f"{emp['employee_name']}: {emp['total_commission']:,} VND")
```

## Error Responses

### Missing Required Fields
```json
{
  "success": false,
  "error": "Missing required field: employees"
}
```

### Invalid Month
```json
{
  "success": false,
  "error": "month must be between 1 and 12"
}
```

### Invalid Target FP Ratio
```json
{
  "success": false,
  "error": "Store at index 0: target_fp_ratio must be between 0.0 and 1.0"
}
```

## Notes
- Employee names in the request must **exactly match** the names in the database
- Revenue calculations include bill-level discounts
- Store eligibility is checked with VAT-inclusive revenue
- Commission calculations use VAT-exclusive revenue
- Returns are filtered to only include those where the original sale was in the same period

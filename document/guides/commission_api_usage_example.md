# Commission API - Usage Examples

## Quick Reference

All commission endpoints require the following fields in the request body:

```json
{
    "store_code": "string",
    "store_name": "string",
    "target_revenue": number,
    "target_fp_ratio": number (0.0 to 1.0),
    "year": integer,
    "month": integer,
    "start_date": "YYYY-MM-DD HH:MI:SS",
    "end_date": "YYYY-MM-DD HH:MI:SS"
}
```

## Example 1: Calculate Commission for November 2025

```bash
curl -X POST http://localhost:5000/api/v1/commission/calculate \
  -H "Content-Type: application/json" \
  -d '{
    "store_code": "HN001",
    "store_name": "Hanoi Central Store",
    "target_revenue": 150000000,
    "target_fp_ratio": 0.70,
    "year": 2025,
    "month": 11,
    "start_date": "2025-11-01 00:00:00",
    "end_date": "2025-11-30 23:59:59"
  }'
```

**Expected Response:**
```json
{
    "success": true,
    "data": {
        "store_code": "HN001",
        "store_name": "Hanoi Central Store",
        "eligible": true,
        "achievement_pct": 105.5,
        "target_revenue": 150000000,
        "target_fp_ratio": 0.70,
        "store_pool": 18500000,
        "employee_count": 8,
        "employees": [
            {
                "employee_name": "Nguyen Van A",
                "tenure_months": 18,
                "is_manager": true,
                "fp_revenue": 60000000,
                "discounted_revenue": 15000000,
                "contribution": 6200000,
                "commission_70pct": 4340000,
                "commission_30pct": 693750,
                "manager_bonus": 3000000,
                "total_commission": 8033750
            }
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

## Example 2: Store Not Eligible (Failed 70% FP Revenue Check)

```bash
curl -X POST http://localhost:5000/api/v1/commission/calculate \
  -H "Content-Type: application/json" \
  -d '{
    "store_code": "SG002",
    "store_name": "Saigon District 1",
    "target_revenue": 200000000,
    "target_fp_ratio": 0.75,
    "year": 2025,
    "month": 11,
    "start_date": "2025-11-01 00:00:00",
    "end_date": "2025-11-30 23:59:59"
  }'
```

**Expected Response:**
```json
{
    "success": true,
    "data": {
        "store_code": "SG002",
        "store_name": "Saigon District 1",
        "eligible": false,
        "reason": "Full price revenue (120,000,000) < 70% of target (140,000,000)",
        "achievement_pct": 85.5,
        "target_revenue": 200000000,
        "target_fp_ratio": 0.75,
        "employees": []
    }
}
```

## Example 3: Get DataFrame (JSON format)

```bash
curl -X POST http://localhost:5000/api/v1/commission/dataframe \
  -H "Content-Type: application/json" \
  -d '{
    "store_code": "DN003",
    "store_name": "Da Nang Center",
    "target_revenue": 120000000,
    "target_fp_ratio": 0.72,
    "year": 2025,
    "month": 11,
    "start_date": "2025-11-01 00:00:00",
    "end_date": "2025-11-30 23:59:59"
  }'
```

**Expected Response:**
```json
{
    "success": true,
    "data": {
        "records": [
            {
                "store_code": "DN003",
                "employee_name": "Tran Thi B",
                "is_manager": false,
                "tenure_months": 8,
                "fp_revenue": 45000000,
                "discounted_revenue": 12000000,
                "contribution": 4500000,
                "commission_70pct": 3150000,
                "commission_30pct": 750000,
                "manager_bonus": 0,
                "total_commission": 3900000,
                "achievement_pct": 98.5,
                "store_pool": 15000000,
                "period_year": 2025,
                "period_month": 11
            }
        ],
        "columns": [
            "store_code",
            "employee_name",
            "is_manager",
            "tenure_months",
            "fp_revenue",
            "discounted_revenue",
            "contribution",
            "commission_70pct",
            "commission_30pct",
            "manager_bonus",
            "total_commission",
            "achievement_pct",
            "store_pool",
            "period_year",
            "period_month"
        ],
        "row_count": 8
    }
}
```

## Example 4: Export to CSV

```bash
curl -X POST http://localhost:5000/api/v1/commission/export/csv \
  -H "Content-Type: application/json" \
  -d '{
    "store_code": "HN001",
    "store_name": "Hanoi Central Store",
    "target_revenue": 150000000,
    "target_fp_ratio": 0.70,
    "year": 2025,
    "month": 11,
    "start_date": "2025-11-01 00:00:00",
    "end_date": "2025-11-30 23:59:59"
  }' \
  -o commission_HN001_2025_11.csv
```

This will download a CSV file named `commission_HN001_2025_11.csv`.

## Example 5: Export to Excel

```bash
curl -X POST http://localhost:5000/api/v1/commission/export/excel \
  -H "Content-Type: application/json" \
  -d '{
    "store_code": "HN001",
    "store_name": "Hanoi Central Store",
    "target_revenue": 150000000,
    "target_fp_ratio": 0.70,
    "year": 2025,
    "month": 11,
    "start_date": "2025-11-01 00:00:00",
    "end_date": "2025-11-30 23:59:59"
  }' \
  -o commission_HN001_2025_11.xlsx
```

This will download an Excel file named `commission_HN001_2025_11.xlsx`.

## Common Error Responses

### Missing Required Field
```json
{
    "success": false,
    "error": "Missing required field: target_revenue"
}
```

### Invalid FP Ratio
```json
{
    "success": false,
    "error": "target_fp_ratio must be between 0.0 and 1.0"
}
```

### Database Connection Error
```json
{
    "success": false,
    "error": "Failed to connect to database"
}
```

## Frontend Integration Notes

1. **Store Information Management**: The frontend should maintain store target information (target revenue, target FP ratio) and pass them with each request.

2. **Date Format**: Ensure dates are in `YYYY-MM-DD HH:MI:SS` format. Example: `2025-11-01 00:00:00`

3. **FP Ratio Format**:
   - Use decimal format (0.0 to 1.0)
   - Examples:
     - 70% = 0.70
     - 75% = 0.75
     - 80.5% = 0.805

4. **Response Handling**:
   - Check `success` field first
   - If `eligible: false`, display the `reason` to users
   - If `eligible: true`, display commission breakdown

5. **Error Handling**: Always handle both HTTP errors and application-level errors from the response.

## Python Integration Example

```python
import requests
import pandas as pd

def calculate_store_commission(store_info):
    """Calculate commission for a store"""
    url = "http://localhost:5000/api/v1/commission/calculate"

    payload = {
        "store_code": store_info["code"],
        "store_name": store_info["name"],
        "target_revenue": store_info["target_revenue"],
        "target_fp_ratio": store_info["target_fp_ratio"],
        "year": 2025,
        "month": 11,
        "start_date": "2025-11-01 00:00:00",
        "end_date": "2025-11-30 23:59:59"
    }

    response = requests.post(url, json=payload)

    if response.status_code == 200:
        result = response.json()
        if result["success"]:
            return result["data"]
        else:
            print(f"Error: {result['error']}")
            return None
    else:
        print(f"HTTP Error: {response.status_code}")
        return None

# Usage
store = {
    "code": "HN001",
    "name": "Hanoi Central Store",
    "target_revenue": 150000000,
    "target_fp_ratio": 0.70
}

commission_data = calculate_store_commission(store)

if commission_data and commission_data["eligible"]:
    print(f"Store Pool: {commission_data['store_pool']:,} VND")
    print(f"Employees: {commission_data['employee_count']}")
    for emp in commission_data['employees']:
        print(f"  {emp['employee_name']}: {emp['total_commission']:,} VND")
else:
    if commission_data:
        print(f"Not eligible: {commission_data['reason']}")
```

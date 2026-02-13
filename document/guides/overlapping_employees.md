# Overlapping Employees - RHN and RWP Stores

## Business Rule

**RHN (RUNWAY HA NOI)** and **RWP (RUNWAY PHAM VAN DONG)** stores share employees who can work in both locations. This creates a special case in commission calculations.

## Commission Calculation Logic

### For RHN and RWP Stores
- Commissions are based on **where the sale was made** (`d.STORE_CODE`)
- An employee can receive commissions from both stores if they made sales in both locations
- This allows proper attribution of sales to the specific store location

### For All Other Stores
- Commissions are based on **employee's assigned store** (`e.STORE_CODE`)
- Standard filtering applies - employees only receive commissions for their home store

## Implementation

The `EMPLOYEE_SALES_DATA` query handles this with a conditional WHERE clause:

```sql
AND (
    (:store_code IN ('RHN', 'RWP') AND d.STORE_CODE = :store_code)
    OR
    (:store_code NOT IN ('RHN', 'RWP') AND e.STORE_CODE = :store_code)
)
```

## Example Scenario

### Employee Working in Both Stores

**Employee**: Nguyen Van A (Code: GH016)
- Assigned Store: RHN
- Also works at: RWP

**November 2025 Sales**:
- Sales made at RHN: 1,500,000,000 VND
- Sales made at RWP: 800,000,000 VND

**Commission Calculation**:
1. **RHN Store Commission**:
   - Includes: 1,500,000,000 VND (sales made AT RHN)
   - Employee receives RHN store pool share

2. **RWP Store Commission**:
   - Includes: 800,000,000 VND (sales made AT RWP)
   - Employee receives RWP store pool share

3. **Total Commission**:
   - Employee receives commissions from BOTH stores
   - Each store's commission is calculated independently

### Regular Store Employee

**Employee**: Nguyen Van B (Code: GL137)
- Assigned Store: RWT

**November 2025 Sales**:
- All sales attributed to RWT store
- Standard commission calculation applies
- Filtered by `e.STORE_CODE = 'RWT'`

## API Usage

When calling the batch commission API, specify the store code for each employee:

```json
{
  "employees": [
    {
      "employee_code": "GH016",
      "employee_name": "Dang Thanh Huyen",
      "store_code": "RHN",  // Will get RHN sales only
      "seniority": 16
    },
    {
      "employee_code": "GH016",
      "employee_name": "Dang Thanh Huyen",
      "store_code": "RWP",  // Same employee, RWP sales only
      "seniority": 16
    }
  ],
  "stores": [
    {
      "store_code": "RHN",
      "target_revenue": 16800000000,
      "target_fp_ratio": 0.65
    },
    {
      "store_code": "RWP",
      "target_revenue": 12000000000,
      "target_fp_ratio": 0.60
    }
  ]
}
```

## Important Notes

1. **Employee can appear multiple times** in the request with different store codes
2. **Each store calculates commissions independently** based on sales at that location
3. **Store eligibility** is checked separately for RHN and RWP
4. **Different seniority** can be specified if the employee has different tenure at each store
5. **Other stores** do NOT have this overlapping behavior

## Database Schema Context

- `EMPLOYEE_LIST_V.STORE_CODE` = Employee's assigned/home store
- `DOCUMENT.STORE_CODE` = Where the sale transaction occurred
- For RHN/RWP, these can differ for the same employee
- For other stores, these typically match

## Testing

See test examples in:
- `tests/integration/test_rhn_commission.py` - RHN with overlapping rule
- `tests/integration/test_batch_commission.py` - RWT with regular rule

Both filtering methods are tested and working correctly.

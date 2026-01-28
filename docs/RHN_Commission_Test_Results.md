# RHN Store Commission Calculation - November 2025

## Test Parameters

- **Store**: RHN (RUNWAY HA NOI)
- **Period**: November 2025 (2025-11-01 to 2025-11-30)
- **Target Revenue**: 16,800,000,000 VND (16.8 billion)
- **Target FP Ratio**: 65% (0.65)

## Store Performance

### Actual Results (with VAT)
| Metric | Amount (VND) | Percentage |
|--------|--------------|------------|
| **Actual Revenue** | 13,764,801,577 | **81.93%** of target |
| Full Price Revenue | 12,375,239,577 | 89.90% of total |
| Discounted Revenue | 1,389,562,000 | 10.10% of total |

### Eligibility Check
| Requirement | Threshold | Actual | Status |
|-------------|-----------|--------|--------|
| Minimum FP Revenue | 11,760,000,000 VND (70% of target) | 12,375,239,577 VND | ✅ **ELIGIBLE** |
| Target FP Ratio | 65.00% | 89.90% | ✅ **EXCEEDS** |
| Achievement | 70%+ | 81.93% | ✅ **QUALIFIED** |

**Result**: Store is **ELIGIBLE** for commission distribution.

## Employee Sales Data (November 2025)

### All Employees with Sales (without VAT)
| Employee Code | Name | FP Revenue | Discounted Revenue | Total Revenue |
|---------------|------|------------|-------------------|---------------|
| GH044 | Nguyen Thi Nghia | 2,783,902,478 | 57,058,981 | 2,840,961,459 |
| GH016 | Dang Thanh Huyen | 1,429,732,813 | 327,863,704 | 1,757,596,517 |
| GH109 | Tran Phan Anh | 1,518,641,326 | 19,747,685 | 1,538,389,011 |
| GH039 | Nguyen Thuy Chinh | 1,036,512,715 | 170,415,833 | 1,206,928,548 |
| GH025 | Bui Thi Hue | 910,964,720 | 48,169,722 | 959,134,443 |
| GH083 | Le Bao Ngoc | 643,402,665 | 158,764,537 | 802,167,202 |
| N/A | Nguyen Diep Thanh Lam | 540,772,824 | 60,357,963 | 601,130,787 |
| GH112 | Tran Thi Tuyet Mai | 208,500,952 | 0 | 208,500,952 |
| **TOTAL** | | **9,072,430,493** | **842,378,425** | **9,914,808,919** |

**Total Employees**: 8 employees had sales in November 2025

## Commission Calculation Test Results

### Test 1: Single Employee (GH016)
**Input:**
- Employee: GH016 (Dang Thanh Huyen)
- Seniority: 18 months (experienced employee)
- Store Achievement: 81.93%

**Results:**
| Metric | Amount (VND) |
|--------|-------------|
| FP Revenue (no VAT) | 1,429,732,813 |
| Discounted Revenue | 327,863,704 |
| Contribution to Pool | 5,575,958 |
| 70% Pool Share | 3,903,171 |
| 30% Pool Share | 1,672,787 |
| Manager Bonus | 0 |
| **Total Commission** | **5,575,958** |

**Commission Rate Breakdown** (at 81.93% achievement):
- Tier: 80-90%
- FP Revenue Commission: 0.30% (0.10% at 70-80 tier + 0.20% at 80-90 tier)
- Discounted Revenue Commission: 0% (only applies at 70-80% tier)

### Test 2: Two Employees (GH016 + GH007)
**Results:**
| Metric | Value |
|--------|-------|
| Total Employees Processed | 2 |
| Eligible Employees | 2 (GH016 and GH007 both had sales data) |
| **Total Commission Payout** | **9,128,720 VND** |
| Store Pool | 9,128,720 VND |
| Store Achievement | 81.93% |

## Commission Calculation Formula

### At 81.93% Achievement (80-90% Tier):

1. **Contribution Calculation** (experienced employee, >= 6 months):
   - FP Revenue Commission:
     - 10% of FP revenue × 0.30% (70-80 tier)
     - 90% of FP revenue × 0.40% (80-90 tier)
   - Discounted Revenue Commission: 0% (only applies at 70-80% tier)

2. **Pool Distribution**:
   - 70% of pool: Distributed proportionally based on contribution ratio
   - 30% of pool: Distributed equally among all employees

3. **Manager Bonus**:
   - 3,000,000 VND if achievement >= 100% (not applicable in this case)

## Key Findings

1. **Store Performance**: RHN achieved 81.93% of target revenue, qualifying for commission
2. **FP Ratio Excellence**: Store exceeded FP ratio target (89.90% vs 65% target)
3. **Employee Participation**: 8 employees had sales in November 2025
4. **Top Performer**: GH044 (Nguyen Thi Nghia) had the highest revenue at 2.84B VND

## API Endpoint Usage

### Request Example
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
        "employee_code": "GH016",
        "employee_name": "Dang Thanh Huyen",
        "store_code": "RHN",
        "seniority": 18
      },
      {
        "employee_code": "GH003",
        "employee_name": "Nguyen Bich Ngoc",
        "store_code": "RHN",
        "seniority": 30
      }
    ],
    "stores": [
      {
        "store_code": "RHN",
        "store_name": "RUNWAY HA NOI",
        "target_revenue": 16800000000,
        "target_fp_ratio": 0.65
      }
    ]
  }'
```

## Notes

- **VAT Handling**:
  - Store eligibility is checked using revenue WITH VAT
  - Commission calculations use revenue WITHOUT VAT

- **Return Filtering**:
  - Returns are only counted if the original sale was within the same period

- **Missing Employee Codes**:
  - One employee (Nguyen Diep Thanh Lam) doesn't have an employee code in the database
  - This employee can still be included in calculations using their full name

- **Seniority Impact**:
  - Employees with < 6 months get lower commission rates (0.25%-0.55%)
  - Employees with >= 6 months get higher rates (0.30%-0.60%)

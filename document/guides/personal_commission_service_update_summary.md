# Personal Commission Service Update Summary

## Overview
Updated the personal commission calculation service to match the pseudocode algorithm with significant improvements to jewelry commission handling, bill-level logic for over 100% tier, and proper revenue handling (WITH VAT vs BEFORE VAT).

## Key Changes

### 1. Input Parameters
**Before:**
- `employee_id`: Employee SID
- `target`: Personal sales target

**After:**
- `employee_code`: HR code (UDF4_STRING)
- `personal_target`: Personal sales target
- `store_code`: Optional store code filter

### 2. Return Type
**Before:** Dictionary with nested employee_commissions list

**After:** pandas DataFrame with 12 standardized columns

### 3. Data Architecture

#### Single Query Approach
- Retrieves ALL sales data (jewelry + non-jewelry) in ONE query
- Data ordered chronologically by: employee_code, sale_date, sale_time, bill_number
- Includes all required columns from updated PERSONAL_COMMISSION_SALES_DATA query

#### Column Handling
- Query returns UPPERCASE column names from Oracle
- Service converts to lowercase for consistent access: `df.columns = df.columns.str.lower()`

### 4. Revenue Handling - Critical Distinction

**Achievement Rate Calculation (uses revenue WITH VAT):**
```python
total_revenue_with_vat = employee_sales_df['revenue_with_vat'].sum()
achievement_rate = (total_revenue_with_vat / target) * 100
```

**Commission Calculation (uses revenue BEFORE VAT):**
```python
# Personal commission
commission_fp = non_jewelry_fp_revenue * rate  # Uses revenue_before_vat
commission_discount = non_jewelry_disc_revenue * rate  # Uses revenue_before_vat

# Jewelry commission
rom_earrings_revenue = rom_earrings_df['revenue_before_vat'].sum()
rom_earrings_commission = rom_earrings_revenue * 0.03
```

### 5. Jewelry Commission - Complete Independence

**Critical Business Rule:**
- Jewelry commission is paid to ALL employees REGARDLESS of achievement rate
- Even employees with 0% achievement receive full jewelry commission
- Jewelry commission rates are FIXED and do NOT vary by achievement tier

**Vendor-Specific Rates:**
1. **TVL/TIT**: Flat 500,000 VND per item
2. **ROM EARRINGS**: 3% on revenue_before_vat
3. **VHN (Vhernier)**: 1% on revenue_before_vat
4. **Other vendors**: 2% on revenue_before_vat (LUI, NAN, NAK, SPK, TED, BRT, ATS, VIS)

**Implementation:**
```python
# 1. TVL/TIT - Flat rate (excluded from percentage calculations)
tvl_tit_item_count = len(tvl_tit_df)
tvl_tit_commission = tvl_tit_item_count * 500000

# 2. ROM EARRINGS - Highest priority (3%)
rom_earrings_commission = rom_earrings_revenue * 0.03

# 3. VHN - 1%
vhn_commission = vhn_revenue * 0.01

# 4. Other vendors - 2%
other_vendors_commission = other_vendors_revenue * 0.02
```

### 6. Personal Commission Tiers (Non-Jewelry Only)

**NON-CUMULATIVE Tier Structure:**

**Tier 1: 50-70% Achievement**
- Full-Price: 0.25%
- Discounted: 0.125%

**Tier 2: 70-100% Achievement**
- Full-Price: 0.5%
- Discounted: 0.25%

**Tier 3: 100% Achievement**
- Full-Price: 1%
- Discounted: 0.5%

**Tier 4: Over 100% (Additional Bonus)**
- Full-Price, Non-Jewelry, NOT TIT/TVL: 2%
- Calculated at BILL level (not individual item level)

### 7. Bill-Level Logic for Over 100% Tier

**New Implementation:**
```python
# Group by bill_number and calculate bill totals
bill_totals_df = employee_sales_df.groupby('bill_number', sort=False).agg({
    'revenue_with_vat': 'sum',
    'sale_date': 'first',
    'sale_time': 'first'
}).reset_index()

# Sort chronologically
bill_totals_df = bill_totals_df.sort_values(['sale_date', 'sale_time'])

# Calculate running total by BILL
bill_totals_df['running_total'] = bill_totals_df['revenue_with_vat'].cumsum()

# Find the BILL where target was first reached
target_reached_bills = bill_totals_df[bill_totals_df['running_total'] >= target]
target_bill_number = target_reached_bills.iloc[0]['bill_number']

# Get ALL qualifying items from target-reaching bill
target_bill_qualifying = target_bill_items[
    (target_bill_items['discount_rate'] <= 0.30) &
    (target_bill_items['is_jewelry'] == 0) &
    (~target_bill_items['vendor_code'].isin(['TIT', 'TVL']))
]

# Apply 2% to ALL qualifying items in this bill
fp_non_jewelry_after_target = target_bill_qualifying['revenue_before_vat'].sum()
```

**Key Points:**
- Checks when 100% target is reached at BILL level (not item level)
- When a bill pushes achievement over 100%, ALL qualifying items in that bill get 2% bonus
- ALL items in subsequent bills (if qualifying) also get 2% bonus
- Qualifying criteria: Full-price AND Non-jewelry AND NOT TIT/TVL

### 8. Output Structure

**12-Column DataFrame:**
```python
columns = [
    'employee_code',              # HR code (UDF4_STRING)
    'fullname',                   # Employee full name
    'store_code',                 # Store identifier
    'commission_fp',              # Full-price commission (tiers 1-3)
    'commission_discount',        # Discounted commission (tiers 1-3)
    'commission_jewelry',         # Total jewelry commission
    'commission_vhernier',        # VHN jewelry commission (1%)
    'commission_rosa_maria',      # ROM EARRINGS commission (3%)
    'commission_100_and_below_fp',      # = commission_fp
    'commission_100_and_below_discount', # = commission_discount
    'commission_over_100',        # Tier 4 bonus (2%)
    'total'                       # Total commission
]
```

**Commission Relationships:**
```python
# commission_100_and_below_fp = commission_fp
# commission_100_and_below_discount = commission_discount
# commission_100_and_below = commission_fp + commission_discount + commission_jewelry
# total = commission_100_and_below + commission_over_100
```

## Testing Results

### Test Coverage
1. ✅ Returns DataFrame with correct structure
2. ✅ Has all 12 required columns
3. ✅ Calculates GL018 commissions correctly
4. ✅ Handles empty employee list
5. ✅ Handles missing employee data
6. ✅ **Jewelry commission independence verified**

### GL018 November 2025 Results
```
Employee Code: GL018
Total Transactions: 5 (all jewelry)
Total Revenue (before VAT): 301,003,935 VND

Commission Breakdown:
- Full-Price Commission: 0 VND (no non-jewelry sales)
- Discounted Commission: 0 VND (no non-jewelry sales)
- Jewelry Commission: 6,020,079 VND (301,003,935 * 2%)
  - Vendors: LUI, ROM, SPK, TED (all at 2% rate)
- Over 100% Bonus: 0 VND (didn't reach 50% target)

TOTAL COMMISSION: 6,020,079 VND
```

**Critical Verification:**
- Even with target of 10,000,000,000 VND (0% achievement), GL018 still receives full jewelry commission of 6,020,079 VND
- This confirms jewelry commission independence from achievement rate

## Files Modified

1. **[app/services/commission_service.py](../app/services/commission_service.py)**
   - Completely rewrote `calculate_personal_commissions` method (lines 462-729)
   - Changed return type from Dict to pd.DataFrame
   - Implemented bill-level logic for over 100% tier
   - Implemented jewelry commission independence
   - Added column name conversion to lowercase

2. **[tests/integration/test_personal_commission_service.py](../tests/integration/test_personal_commission_service.py)** (NEW)
   - Created comprehensive test suite with 6 test cases
   - Validates DataFrame structure and columns
   - Tests commission calculations with real data
   - Verifies jewelry commission independence

## Migration Notes

### For API Endpoints
If you have existing API endpoints using `calculate_personal_commissions`, update them:

**Before:**
```python
result = service.calculate_personal_commissions(
    month=11,
    year=2025,
    employees=[{'employee_id': 'EMP001', 'target': 500_000_000}]
)
# Returns: Dict with 'success', 'summary', 'employee_commissions'
```

**After:**
```python
result_df = service.calculate_personal_commissions(
    month=11,
    year=2025,
    employees=[{'employee_code': 'GL018', 'personal_target': 500_000_000}],
    store_code='RHN'
)
# Returns: pandas DataFrame with 12 columns
# Convert to dict if needed: result_df.to_dict('records')
```

### Column Mapping
| Old Field | New Field | Notes |
|-----------|-----------|-------|
| employee_id | employee_code | Changed to HR code |
| target | personal_target | Clearer naming |
| N/A | commission_vhernier | New - VHN jewelry |
| N/A | commission_rosa_maria | New - ROM EARRINGS |
| N/A | commission_100_and_below_fp | New - FP breakdown |
| N/A | commission_100_and_below_discount | New - Discount breakdown |

## Business Rules Verification

✅ **Jewelry Commission Independence**
- Paid to ALL employees regardless of achievement rate
- Tested with 0% achievement - still pays full commission

✅ **Revenue Distinction**
- Achievement rate: Uses revenue WITH VAT
- Commission calculation: Uses revenue BEFORE VAT

✅ **Bill-Level Logic**
- Over 100% bonus applied at bill level
- All qualifying items in target-reaching bill get bonus
- All qualifying items in subsequent bills get bonus

✅ **Non-Cumulative Tiers**
- Commission rate based on tier reached
- Not added to previous tiers

✅ **Vendor-Specific Rates**
- TVL/TIT: 500k per item
- ROM EARRINGS: 3%
- VHN: 1%
- Others: 2%

## Next Steps

1. Update API endpoints to use new DataFrame return type
2. Update frontend to consume 12-column DataFrame structure
3. Add logging for commission calculations
4. Consider adding commission calculation audit trail
5. Test with more employee data (multiple achievement tiers)

## Questions & Clarifications

### Q: Why is jewelry commission independent?
**A:** Business decision to incentivize jewelry sales regardless of overall performance. This ensures employees are motivated to sell high-value jewelry items even if they're having a challenging month with non-jewelry sales.

### Q: Why bill-level logic for over 100% tier?
**A:** To treat all items in a single transaction consistently. If a customer buys multiple items in one transaction that pushes the employee over their target, all qualifying items in that transaction should receive the bonus.

### Q: Why two different revenue values?
**A:**
- **WITH VAT** for achievement rate: Reflects total customer payment (more accurate measure of employee performance)
- **BEFORE VAT** for commission: Reflects actual revenue to company (fairer basis for commission calculation)

---

**Last Updated:** 2026-01-27
**Updated By:** Claude Sonnet 4.5
**Version:** 1.0

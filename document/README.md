# Document Directory Organization

This directory contains all documentation, reports, and reference materials for the commission calculation system.

## Directory Structure

### 📋 policies/
Commission policy documents (official policies from management)
- `14-CHÍNH SÁCH THƯỞNG DOANH SỐ (SHOP).pdf` - Store commission policy
- `16-CHÍNH SÁCH THƯỞNG DOANH SỐ (CÁ NHÂN).pdf` - Personal commission policy

### 📊 reports/
Commission reports and calculation results
- `Commission_Nov 2025.xls` - Google Sheets commission data (source of truth)
- `Commission_NOV 2025 THAO.xls` - Manual commission calculations for comparison
- `Report Sales, Inventory - 12.01.2026 - Jewelry.xlsx` - Sales reports

### 📸 screenshots/
Screenshots used for validation and comparison
- Commission calculation screenshots for test validation
- Used to verify calculation accuracy against manual reports

### 📖 guides/
Implementation guides and API documentation
- `commission_api_usage_example.md` - API endpoint usage examples
- `commission_implementation_guide.md` - Implementation guide
- `personal_commission_service_update_summary.md` - Service updates documentation

### 🧮 pseudocode/
Algorithm documentation and calculation logic
- `personal_commission_pseudocode.txt` - Personal commission calculation algorithm
- `store_commission_pseudocode.txt` - Store commission calculation algorithm

### 💾 samples/
Sample queries and code snippets
- `sample.sql` - Sample SQL queries for reference

## Notes

- **VAT Handling**: The system uses actual VAT rates from transactions (mix of 8% and 10%)
- **Calculation Method**: Tier-based approach with 70% individual / 30% equal share distribution
- **Data Source**: Commission calculations use Google Sheets data as the primary input source

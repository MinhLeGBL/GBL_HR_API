# Integration Tests Organization

This directory contains integration tests for the commission calculation system.

## Directory Structure

### 🏪 Main Integration Tests
Located in the root `tests/integration/` directory:
- `test_store_commission_service.py` - **Primary test suite** for store commission calculations
  - Tests Google Sheets integration
  - Validates commission calculations for RHN store
  - Compares results with expected values
  - **Run this regularly** to verify system accuracy

## Subdirectories

### ✅ validation/
Data validation and verification tests (used during development/investigation):
- `test_connections.py` - Validates database connections (Oracle, Google Sheets)
- `test_gl018_employee.py` - Validates employee data from GL018 table
- `test_revenue_reconciliation.py` - Validates revenue calculations and reconciliation
- `test_rhn_rwp_employee_check.py` - Validates RHN/RWP employee cross-store logic

### 🔍 queries/
Query-specific tests:
- `test_personal_commission_query.py` - Tests personal commission SQL queries
  - Validates employee sales data queries
  - Checks revenue calculations
  - Verifies discount threshold logic (30% cutoff)

### 🔧 services/
Service layer tests:
- `test_personal_commission_service.py` - Tests personal commission service logic
  - Validates service methods
  - Tests commission distribution
  - Verifies calculation formulas

## Running Tests

### Run all integration tests:
```bash
pytest tests/integration/ -v
```

### Run only main store commission test:
```bash
pytest tests/integration/test_store_commission_service.py -v
```

### Run specific test categories:
```bash
# Validation tests only
pytest tests/integration/validation/ -v

# Query tests only
pytest tests/integration/queries/ -v

# Service tests only
pytest tests/integration/services/ -v
```

### Run with output display (useful for debugging):
```bash
pytest tests/integration/test_store_commission_service.py -v -s
```

## Test Organization Guidelines

- **Main directory**: Regular tests that should run frequently
- **validation/**: Investigation and data verification tests
- **queries/**: SQL query-specific tests
- **services/**: Business logic and service layer tests

## Key Findings from Investigation

1. **VAT Calculation**: System correctly uses actual VAT from transactions (mix of 8%/10%)
2. **Discount Threshold**: Fixed inconsistent ROUND logic - now uses exact 30% threshold
3. **Formula Verification**: Tier-based calculation confirmed accurate
4. **Data Source**: Google Sheets provides commission input data

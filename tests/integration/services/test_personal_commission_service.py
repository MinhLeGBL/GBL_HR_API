"""
Integration test for personal commission service
Tests the calculate_personal_commissions method with real data
"""
import pytest
import pandas as pd
from app.services.commission_service import CommissionService


class TestPersonalCommissionService:
    """Test personal commission calculation service"""

    @pytest.fixture
    def service(self):
        """Provide commission service instance"""
        return CommissionService()

    @pytest.fixture
    def employee_list(self):
        """Sample employee list for testing"""
        return [
            {
                'employee_code': 'GL018',
                'personal_target': 500_000_000,  # 500M VND
                'full_name': 'Test Employee GL018',
                'store_code': 'RHN'
            }
        ]

    def test_calculate_personal_commissions_returns_dataframe(self, service, employee_list):
        """Test that the method returns a pandas DataFrame"""
        result = service.calculate_personal_commissions(
            month=11,
            year=2025,
            employees=employee_list,
            store_code='RHN'
        )

        assert isinstance(result, pd.DataFrame), "Result should be a pandas DataFrame"
        print(f"\n✓ Returned DataFrame with {len(result)} rows")

    def test_calculate_personal_commissions_has_correct_columns(self, service, employee_list):
        """Test that the DataFrame has all required columns"""
        result = service.calculate_personal_commissions(
            month=11,
            year=2025,
            employees=employee_list,
            store_code='RHN'
        )

        expected_columns = [
            'employee_code',
            'fullname',
            'store_code',
            'commission_fp',
            'commission_discount',
            'commission_jewelry',
            'commission_vhernier',
            'commission_rosa_maria',
            'commission_100_and_below_fp',
            'commission_100_and_below_discount',
            'commission_over_100',
            'total'
        ]

        for col in expected_columns:
            assert col in result.columns, f"Missing column: {col}"

        print("\n✓ All required columns present:")
        print(f"  Columns: {', '.join(result.columns.tolist())}")

    def test_calculate_personal_commissions_gl018(self, service, employee_list):
        """Test commission calculation for employee GL018"""
        result = service.calculate_personal_commissions(
            month=11,
            year=2025,
            employees=employee_list,
            store_code='RHN'
        )

        assert len(result) == 1, "Should return exactly 1 row"

        employee_row = result.iloc[0]

        print("\n" + "="*80)
        print("EMPLOYEE GL018 - NOVEMBER 2025 COMMISSION BREAKDOWN")
        print("="*80)
        print(f"Employee Code: {employee_row['employee_code']}")
        print(f"Full Name: {employee_row['fullname']}")
        print(f"Store Code: {employee_row['store_code']}")
        print("\n" + "-"*80)
        print("COMMISSION BREAKDOWN:")
        print("-"*80)
        print(f"Full-Price Commission: {employee_row['commission_fp']:,.0f} VND")
        print(f"Discounted Commission: {employee_row['commission_discount']:,.0f} VND")
        print(f"Jewelry Commission: {employee_row['commission_jewelry']:,.0f} VND")
        print(f"  - Vhernier (VHN): {employee_row['commission_vhernier']:,.0f} VND")
        print(f"  - Rosa Maria (ROM EARRINGS): {employee_row['commission_rosa_maria']:,.0f} VND")
        print("\n" + "-"*80)
        print("TOTALS:")
        print("-"*80)
        print(f"Commission (100% and below) - FP: {employee_row['commission_100_and_below_fp']:,.0f} VND")
        print(f"Commission (100% and below) - Discount: {employee_row['commission_100_and_below_discount']:,.0f} VND")
        print(f"Commission (Over 100% bonus): {employee_row['commission_over_100']:,.0f} VND")
        print(f"\nTOTAL COMMISSION: {employee_row['total']:,.0f} VND")
        print("="*80)

        # Verify commission components add up correctly
        commission_100_and_below = (
            employee_row['commission_fp'] +
            employee_row['commission_discount'] +
            employee_row['commission_jewelry']
        )

        total_expected = commission_100_and_below + employee_row['commission_over_100']

        assert abs(employee_row['total'] - total_expected) < 1, \
            f"Total commission mismatch: {employee_row['total']} != {total_expected}"

        print("\n✓ Commission components add up correctly")

    def test_calculate_personal_commissions_empty_employees(self, service):
        """Test with empty employee list"""
        result = service.calculate_personal_commissions(
            month=11,
            year=2025,
            employees=[],
            store_code='RHN'
        )

        assert isinstance(result, pd.DataFrame), "Should return DataFrame even with empty input"
        print("\n✓ Handles empty employee list correctly")

    def test_calculate_personal_commissions_missing_data(self, service):
        """Test with employee that has missing required fields"""
        employees = [
            {
                'employee_code': None,  # Missing
                'personal_target': 500_000_000,
                'full_name': 'Test Employee',
                'store_code': 'RHN'
            }
        ]

        result = service.calculate_personal_commissions(
            month=11,
            year=2025,
            employees=employees,
            store_code='RHN'
        )

        assert len(result) == 1, "Should return 1 row for invalid employee"
        employee_row = result.iloc[0]

        # All commissions should be 0 for invalid employee
        assert employee_row['total'] == 0, "Total should be 0 for invalid employee"

        print("\n✓ Handles missing employee data correctly (returns 0 commissions)")

    def test_jewelry_commission_independence(self, service):
        """
        Test that jewelry commission is paid regardless of achievement rate
        This is a critical business rule
        """
        # Create employee with very high target (to ensure low achievement rate)
        employees = [
            {
                'employee_code': 'GL018',
                'personal_target': 10_000_000_000,  # 10 billion VND (very high)
                'full_name': 'Test Employee GL018',
                'store_code': 'RHN'
            }
        ]

        result = service.calculate_personal_commissions(
            month=11,
            year=2025,
            employees=employees,
            store_code='RHN'
        )

        employee_row = result.iloc[0]

        # Even with very low achievement rate, jewelry commission should still be paid
        # (GL018 has jewelry sales in November 2025)
        assert employee_row['commission_jewelry'] > 0, \
            "Jewelry commission should be paid regardless of achievement rate"

        print("\n" + "="*80)
        print("JEWELRY COMMISSION INDEPENDENCE TEST")
        print("="*80)
        print(f"Employee Code: {employee_row['employee_code']}")
        print(f"Target: {10_000_000_000:,.0f} VND (Very High)")
        print(f"Jewelry Commission: {employee_row['commission_jewelry']:,.0f} VND")
        print("\n✓ Jewelry commission paid regardless of achievement rate")
        print("="*80)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

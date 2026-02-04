"""
Integration tests for Store Commission Service using Google Sheets input
"""
import pytest
from app.services.google_sheets_service import GoogleSheetsService
from app.services.commission_service import CommissionService


class TestStoreCommissionServiceIntegration:
    """Integration tests for Store Commission Service with Google Sheets"""

    @pytest.fixture
    def sheets_service(self):
        """Create GoogleSheetsService instance"""
        return GoogleSheetsService()

    @pytest.fixture
    def commission_service(self):
        """Create CommissionService instance"""
        return CommissionService()

    @pytest.fixture
    def test_spreadsheet_id(self):
        """Test spreadsheet ID"""
        return "1-L91pYnXCl646Oaob3vhvh_J1sBrv-Hl0SaF8CA1Ado"

    @pytest.fixture
    def rhn_commission_input(self, sheets_service, test_spreadsheet_id):
        """Get RHN store commission input from Google Sheets"""
        return sheets_service.get_store_commission_input(
            spreadsheet_id=test_spreadsheet_id,
            store_code="RHN",
            sheet_name="Sheet1"
        )

    def test_commission_input_structure(self, rhn_commission_input):
        """Test that commission input has correct structure"""
        assert rhn_commission_input['store_code'] == "RHN"
        assert isinstance(rhn_commission_input['store_target'], (int, float))
        assert isinstance(rhn_commission_input['store_fp_ratio_target'], (int, float))
        assert 'query_date' in rhn_commission_input
        assert 'from_date' in rhn_commission_input['query_date']
        assert 'to_date' in rhn_commission_input['query_date']
        assert isinstance(rhn_commission_input['employees'], list)
        assert len(rhn_commission_input['employees']) > 0

    def test_calculate_rhn_store_commission(self, commission_service, rhn_commission_input):
        """Test calculating commission for RHN store"""
        result = commission_service.calculate_store_commission_v2(
            store_code=rhn_commission_input['store_code'],
            store_target=rhn_commission_input['store_target'],
            store_fp_ratio_target=rhn_commission_input['store_fp_ratio_target'],
            query_date=rhn_commission_input['query_date'],
            employees=rhn_commission_input['employees']
        )

        # Check result structure
        assert result is not None, "Commission calculation returned None"
        assert 'eligible' in result, "Missing 'eligible' key"
        assert 'store_code' in result, "Missing 'store_code' key"
        assert 'achievement_pct' in result, "Missing 'achievement_pct' key"
        assert 'store_pool' in result, "Missing 'store_pool' key"
        assert 'employees' in result, "Missing 'employees' key"

    def test_commission_eligibility_check(self, commission_service, rhn_commission_input):
        """Test that eligibility is correctly determined"""
        result = commission_service.calculate_store_commission_v2(
            store_code=rhn_commission_input['store_code'],
            store_target=rhn_commission_input['store_target'],
            store_fp_ratio_target=rhn_commission_input['store_fp_ratio_target'],
            query_date=rhn_commission_input['query_date'],
            employees=rhn_commission_input['employees']
        )

        # RHN should be eligible based on the test data (November 2025)
        assert result['eligible'] is True, "RHN store should be eligible for commission"
        assert result['achievement_pct'] > 70, "Achievement should be above 70%"

    def test_commission_pool_calculation(self, commission_service, rhn_commission_input):
        """Test that store commission pool is calculated correctly"""
        result = commission_service.calculate_store_commission_v2(
            store_code=rhn_commission_input['store_code'],
            store_target=rhn_commission_input['store_target'],
            store_fp_ratio_target=rhn_commission_input['store_fp_ratio_target'],
            query_date=rhn_commission_input['query_date'],
            employees=rhn_commission_input['employees']
        )

        if result['eligible']:
            assert result['store_pool'] > 0, "Store pool should be greater than 0"
            assert isinstance(result['store_pool'], (int, float)), "Store pool should be numeric"

    def test_employee_commission_structure(self, commission_service, rhn_commission_input):
        """Test that employee commission results have correct structure"""
        result = commission_service.calculate_store_commission_v2(
            store_code=rhn_commission_input['store_code'],
            store_target=rhn_commission_input['store_target'],
            store_fp_ratio_target=rhn_commission_input['store_fp_ratio_target'],
            query_date=rhn_commission_input['query_date'],
            employees=rhn_commission_input['employees']
        )

        assert len(result['employees']) > 0, "Should have employee commission data"

        for emp in result['employees']:
            # Check required fields
            assert 'employee_code' in emp, "Missing employee_code"
            assert 'full_name' in emp, "Missing full_name"
            assert 'seniority' in emp, "Missing seniority"
            assert 'is_manager' in emp, "Missing is_manager"
            assert 'is_probation' in emp, "Missing is_probation"
            assert 'fp_revenue' in emp, "Missing fp_revenue"
            assert 'disc_revenue' in emp, "Missing disc_revenue"
            assert 'individual_share' in emp, "Missing individual_share"
            assert 'equal_share' in emp, "Missing equal_share"
            assert 'manager_bonus' in emp, "Missing manager_bonus"
            assert 'total_store_commission' in emp, "Missing total_store_commission"

            # Check data types
            assert isinstance(emp['fp_revenue'], (int, float)), "fp_revenue should be numeric"
            assert isinstance(emp['disc_revenue'], (int, float)), "disc_revenue should be numeric"
            assert isinstance(emp['individual_share'], (int, float)), "individual_share should be numeric"
            assert isinstance(emp['equal_share'], (int, float)), "equal_share should be numeric"
            assert isinstance(emp['manager_bonus'], (int, float)), "manager_bonus should be numeric"
            assert isinstance(emp['total_store_commission'], (int, float)), "total_store_commission should be numeric"

    def test_employee_commission_distribution(self, commission_service, rhn_commission_input):
        """Test that commission is distributed correctly (70% individual + 30% equal)"""
        result = commission_service.calculate_store_commission_v2(
            store_code=rhn_commission_input['store_code'],
            store_target=rhn_commission_input['store_target'],
            store_fp_ratio_target=rhn_commission_input['store_fp_ratio_target'],
            query_date=rhn_commission_input['query_date'],
            employees=rhn_commission_input['employees']
        )

        if result['eligible'] and result['store_pool'] > 0:
            total_individual = sum(emp['individual_share'] for emp in result['employees'])
            total_equal = sum(emp['equal_share'] for emp in result['employees'])
            total_manager = sum(emp['manager_bonus'] for emp in result['employees'])

            # Individual share should be approximately 70% of pool
            expected_individual = result['store_pool'] * 0.70
            assert abs(total_individual - expected_individual) < 1, \
                f"Individual share ({total_individual}) should be ~70% of pool ({expected_individual})"

            # Equal share should be approximately 30% of pool
            expected_equal = result['store_pool'] * 0.30
            assert abs(total_equal - expected_equal) < 1, \
                f"Equal share ({total_equal}) should be ~30% of pool ({expected_equal})"

    def test_probation_employee_commission(self, commission_service, rhn_commission_input):
        """Test that probation employees only receive equal share"""
        result = commission_service.calculate_store_commission_v2(
            store_code=rhn_commission_input['store_code'],
            store_target=rhn_commission_input['store_target'],
            store_fp_ratio_target=rhn_commission_input['store_fp_ratio_target'],
            query_date=rhn_commission_input['query_date'],
            employees=rhn_commission_input['employees']
        )

        for emp in result['employees']:
            if emp['is_probation']:
                # Probation employees should only receive equal share
                assert emp['total_store_commission'] == emp['equal_share'], \
                    f"Probation employee {emp['employee_code']} should only receive equal share"
                assert emp['manager_bonus'] == 0, \
                    f"Probation employee {emp['employee_code']} should not receive manager bonus"

    def test_manager_bonus_calculation(self, commission_service, rhn_commission_input):
        """Test that manager bonuses are calculated correctly"""
        result = commission_service.calculate_store_commission_v2(
            store_code=rhn_commission_input['store_code'],
            store_target=rhn_commission_input['store_target'],
            store_fp_ratio_target=rhn_commission_input['store_fp_ratio_target'],
            query_date=rhn_commission_input['query_date'],
            employees=rhn_commission_input['employees']
        )

        achievement_pct = result['achievement_pct']

        for emp in result['employees']:
            if emp['is_manager'] and not emp['is_probation']:
                if achievement_pct >= 100:
                    assert emp['manager_bonus'] == 3_000_000, \
                        f"Manager {emp['employee_code']} should receive 3M bonus for 100%+ achievement"
                elif achievement_pct >= 70:
                    assert emp['manager_bonus'] == 750_000, \
                        f"Manager {emp['employee_code']} should receive 750K bonus for 70-100% achievement"
            else:
                assert emp['manager_bonus'] == 0, \
                    f"Non-manager {emp['employee_code']} should not receive manager bonus"

    def test_working_day_distribution_for_rhn(self, commission_service, rhn_commission_input):
        """Test that RHN equal share is distributed by working day ratio"""
        result = commission_service.calculate_store_commission_v2(
            store_code=rhn_commission_input['store_code'],
            store_target=rhn_commission_input['store_target'],
            store_fp_ratio_target=rhn_commission_input['store_fp_ratio_target'],
            query_date=rhn_commission_input['query_date'],
            employees=rhn_commission_input['employees']
        )

        if result['eligible'] and result['store_pool'] > 0:
            total_working_days = sum(emp['working_day_count'] for emp in rhn_commission_input['employees'])
            expected_equal_pool = result['store_pool'] * 0.30

            # Check that equal share is proportional to working days
            for emp in result['employees']:
                # Find matching employee in input
                input_emp = next((e for e in rhn_commission_input['employees']
                                  if e['employee_code'] == emp['employee_code']), None)

                if input_emp:
                    working_day_ratio = input_emp['working_day_count'] / total_working_days
                    expected_equal = expected_equal_pool * working_day_ratio

                    # For probation, total should equal equal share
                    if emp['is_probation']:
                        assert abs(emp['total_store_commission'] - expected_equal) < 1, \
                            f"Probation employee {emp['employee_code']} equal share mismatch"

    def test_employee_code_matching(self, commission_service, rhn_commission_input):
        """Test that employees are matched correctly by employee code"""
        result = commission_service.calculate_store_commission_v2(
            store_code=rhn_commission_input['store_code'],
            store_target=rhn_commission_input['store_target'],
            store_fp_ratio_target=rhn_commission_input['store_fp_ratio_target'],
            query_date=rhn_commission_input['query_date'],
            employees=rhn_commission_input['employees']
        )

        # Check that all input employees are in the result
        input_employee_codes = {emp['employee_code'] for emp in rhn_commission_input['employees']}
        result_employee_codes = {emp['employee_code'] for emp in result['employees']}

        assert input_employee_codes == result_employee_codes, \
            "All input employees should be in the result"

    def test_commission_totals_consistency(self, commission_service, rhn_commission_input):
        """Test that commission totals are consistent"""
        result = commission_service.calculate_store_commission_v2(
            store_code=rhn_commission_input['store_code'],
            store_target=rhn_commission_input['store_target'],
            store_fp_ratio_target=rhn_commission_input['store_fp_ratio_target'],
            query_date=rhn_commission_input['query_date'],
            employees=rhn_commission_input['employees']
        )

        if result['eligible']:
            for emp in result['employees']:
                # For non-probation employees
                if not emp['is_probation']:
                    expected_total = emp['individual_share'] + emp['equal_share'] + emp['manager_bonus']
                    assert abs(emp['total_store_commission'] - expected_total) < 0.01, \
                        f"Total commission mismatch for {emp['employee_code']}"
                else:
                    # For probation employees
                    assert emp['total_store_commission'] == emp['equal_share'], \
                        f"Probation employee {emp['employee_code']} should only receive equal share"

    def test_full_commission_output_display(self, commission_service, rhn_commission_input):
        """Display the complete commission calculation output"""
        result = commission_service.calculate_store_commission_v2(
            store_code=rhn_commission_input['store_code'],
            store_target=rhn_commission_input['store_target'],
            store_fp_ratio_target=rhn_commission_input['store_fp_ratio_target'],
            query_date=rhn_commission_input['query_date'],
            employees=rhn_commission_input['employees']
        )

        print("\n" + "="*120)
        print("FULL STORE COMMISSION CALCULATION OUTPUT")
        print("="*120)

        # Store-level information
        print(f"\n[STORE INFORMATION]")
        print(f"  Store Code:                 {result['store_code']}")
        print(f"  Eligible:                   {'YES' if result['eligible'] else 'NO'}")
        print(f"  Achievement:                {result['achievement_pct']:.2f}%")
        print(f"  Target Revenue:             {result['store_target']:>20,.0f} VND")
        print(f"  Actual Revenue:             {result['actual_revenue']:>20,.0f} VND")
        print(f"  Target FP Ratio:            {rhn_commission_input['store_fp_ratio_target']:>20.1%}")
        print(f"  Actual FP Ratio:            {result['actual_fp_ratio']:>20.1%}")
        print(f"  Store Commission Pool:      {result['store_pool']:>20,.0f} VND")
        print(f"  Total Employees:            {result['total_employee_count']}")
        print(f"  Total Working Days:         {result['total_working_days']}")

        # Calculate totals
        total_individual = sum(emp['individual_share'] for emp in result['employees'])
        total_equal = sum(emp['equal_share'] for emp in result['employees'])
        total_manager = sum(emp['manager_bonus'] for emp in result['employees'])
        total_commission = sum(emp['total_store_commission'] for emp in result['employees'])

        print(f"\n[PAYOUT SUMMARY]")
        print(f"  Individual Share (70%):     {total_individual:>20,.0f} VND")
        print(f"  Equal Share (30%):          {total_equal:>20,.0f} VND")
        print(f"  Manager Bonuses:            {total_manager:>20,.0f} VND")
        print(f"  {'─'*50}")
        print(f"  TOTAL COMMISSION PAYOUT:    {total_commission:>20,.0f} VND")

        # Employee breakdown table
        print(f"\n[EMPLOYEE COMMISSION BREAKDOWN]")
        print(f"{'Code':<12} {'Name':<25} {'Status':<12} {'FP Rev':>18} {'Disc Rev':>18} {'Individual':>15} {'Equal':>15} {'Manager':>12} {'Total':>15}")
        print("─"*150)

        for emp in result['employees']:
            emp_code = emp['employee_code']
            emp_name = emp['full_name'][:23]

            if emp['is_probation']:
                status = "Probation"
            elif emp['is_manager']:
                status = "Manager"
            else:
                status = "Regular"

            fp_rev = emp['fp_revenue']
            disc_rev = emp['disc_revenue']
            individual = emp['individual_share']
            equal = emp['equal_share']
            manager = emp['manager_bonus']
            total = emp['total_store_commission']

            print(f"{emp_code:<12} {emp_name:<25} {status:<12} {fp_rev:>18,.0f} {disc_rev:>18,.0f} {individual:>15,.0f} {equal:>15,.0f} {manager:>12,.0f} {total:>15,.0f}")

        # Detailed employee information
        print(f"\n[DETAILED EMPLOYEE BREAKDOWN]")
        print("─"*120)

        for emp in result['employees']:
            print(f"\nEmployee: {emp['full_name']} ({emp['employee_code']})")
            print(f"  Seniority:                  {emp['seniority']} years")
            print(f"  Working Days:               {emp['working_day_count']} days")
            print(f"  Is Manager:                 {'Yes' if emp['is_manager'] else 'No'}")
            print(f"  Is Probation:               {'Yes' if emp['is_probation'] else 'No'}")
            print(f"\n  SALES DATA:")
            print(f"  FP Revenue:                 {emp['fp_revenue']:>20,.0f} VND")
            print(f"  Discounted Revenue:         {emp['disc_revenue']:>20,.0f} VND")
            print(f"  Total Sales Revenue:        {emp['fp_revenue'] + emp['disc_revenue']:>20,.0f} VND")
            print(f"\n  COMMISSION BREAKDOWN:")
            print(f"  Individual Share (70%):     {emp['individual_share']:>20,.0f} VND")
            print(f"  Equal Share (30%):          {emp['equal_share']:>20,.0f} VND")
            if emp['manager_bonus'] > 0:
                print(f"  Manager Bonus:              {emp['manager_bonus']:>20,.0f} VND")
            print(f"  {'─'*50}")
            print(f"  TOTAL COMMISSION:           {emp['total_store_commission']:>20,.0f} VND")

            if emp['is_probation']:
                print(f"  ⚠ Note: Probation employee receives only equal share (30%)")

        print("\n" + "="*120)
        print("END OF COMMISSION CALCULATION OUTPUT")
        print("="*120 + "\n")

        # Verify calculations are correct
        assert result is not None
        assert result['eligible'] is True
        assert result['store_pool'] > 0

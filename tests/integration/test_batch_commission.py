import pytest
from app.services.commission_service import CommissionService


class TestBatchCommissionCalculation:
    """Test batch commission calculation with multiple employees and stores"""

    def test_batch_commission_rwt_nov_2025(self):
        """Test batch commission calculation for RWT store in November 2025"""
        service = CommissionService()

        # Sample employees from RWT store
        employees = [
            {
                'employee_code': 'GL137',
                'employee_name': 'Nguyen Quoc Huy',
                'store_code': 'RWT',
                'personal_target': 500000000,
                'seniority': 24  # 2 years
            },
            {
                'employee_code': 'GL013',
                'employee_name': 'Vuong Que Chau',
                'store_code': 'RWT',
                'personal_target': 450000000,
                'seniority': 36  # 3 years
            },
            {
                'employee_code': 'GL188',
                'employee_name': 'Pham Thi Diem Trang',
                'store_code': 'RWT',
                'personal_target': 400000000,
                'seniority': 18  # 1.5 years
            }
        ]

        # Store configuration
        stores = [
            {
                'store_code': 'RWT',
                'store_name': 'RUNWAY TAKASHIMAYA',
                'target_revenue': 4000000000,  # 4 billion VND
                'target_fp_ratio': 0.65  # 65% full price target
            }
        ]

        # November 2025 period
        result = service.calculate_batch_store_commissions(
            employees=employees,
            stores=stores,
            year=2025,
            month=11,
            start_date='2025-11-01 00:00:00',
            end_date='2025-11-30 23:59:59'
        )

        # Print results
        print(f"\n{'='*100}")
        print(f"Batch Commission Calculation - November 2025")
        print(f"{'='*100}")

        assert result['success'] is True, "Calculation should be successful"

        # Print summary
        summary = result['summary']
        print(f"\nSUMMARY:")
        print(f"  Total Employees:        {summary['total_employees']}")
        print(f"  Eligible Employees:     {summary['eligible_employees']}")
        print(f"  Ineligible Employees:   {summary['ineligible_employees']}")
        print(f"  Total Stores:           {summary['total_stores']}")
        print(f"  Eligible Stores:        {summary['eligible_stores']}")
        print(f"  Total Commission:       {summary['total_commission_payout']:>20,.0f} VND")

        # Print store results
        print(f"\nSTORE RESULTS:")
        print(f"{'-'*100}")
        for store in result['stores']:
            print(f"  Store: {store['store_code']} - {store.get('store_name', 'N/A')}")
            print(f"  Eligible: {store['eligible']}")
            print(f"  Achievement: {store.get('achievement_pct', 0):.2f}%")
            if store['eligible']:
                print(f"  Store Pool: {store.get('store_pool', 0):>20,.0f} VND")
                print(f"  Employee Count: {store.get('employee_count', 0)}")
            else:
                print(f"  Reason: {store.get('reason', 'N/A')}")
            print(f"{'-'*100}")

        # Print employee results
        print(f"\nEMPLOYEE COMMISSION DETAILS:")
        print(f"{'-'*100}")
        print(f"{'Code':<10} {'Name':<30} {'Store':<8} {'Eligible':<10} {'FP Revenue':>15} {'Commission':>15}")
        print(f"{'-'*100}")

        for emp in result['employees']:
            emp_code = emp.get('employee_code', 'N/A')
            emp_name = emp.get('employee_name', 'N/A')[:28]
            store_code = emp.get('store_code', 'N/A')
            eligible = 'Yes' if emp.get('eligible', False) else 'No'
            fp_revenue = emp.get('fp_revenue', 0)
            commission = emp.get('total_commission', 0)

            print(f"{emp_code:<10} {emp_name:<30} {store_code:<8} {eligible:<10} {fp_revenue:>15,.0f} {commission:>15,.0f}")

            if emp.get('eligible', False):
                print(f"  - Tenure: {emp.get('tenure_months', 0)} months")
                print(f"  - Achievement: {emp.get('achievement_pct', 0):.2f}%")
                print(f"  - Contribution: {emp.get('contribution', 0):,.0f}")
                print(f"  - 70% Share: {emp.get('commission_70pct', 0):,.0f}")
                print(f"  - 30% Share: {emp.get('commission_30pct', 0):,.0f}")
                if emp.get('manager_bonus', 0) > 0:
                    print(f"  - Manager Bonus: {emp.get('manager_bonus', 0):,.0f}")
            else:
                print(f"  - Reason: {emp.get('reason', 'N/A')}")

            print(f"{'-'*100}")

        print(f"\n{'='*100}\n")

        # Assertions
        assert summary['total_employees'] >= 2, "Should have at least 2 employees with sales data"
        assert summary['total_stores'] == 1, "Should have 1 store"
        assert summary['eligible_stores'] == 1, "Store should be eligible"

    def test_batch_commission_multiple_stores(self):
        """Test batch commission calculation across multiple stores"""
        service = CommissionService()

        # Employees from different stores
        employees = [
            {
                'employee_code': 'GL137',
                'employee_name': 'Nguyen Quoc Huy',
                'store_code': 'RWT',
                'personal_target': 500000000,
                'seniority': 24
            },
            {
                'employee_code': 'RWD001',
                'employee_name': 'Test Employee 1',
                'store_code': 'RWD',
                'personal_target': 300000000,
                'seniority': 12
            }
        ]

        # Multiple stores configuration
        stores = [
            {
                'store_code': 'RWT',
                'store_name': 'RUNWAY TAKASHIMAYA',
                'target_revenue': 4000000000,
                'target_fp_ratio': 0.65
            },
            {
                'store_code': 'RWD',
                'store_name': 'RUNWAY DIAMOND',
                'target_revenue': 2000000000,
                'target_fp_ratio': 0.60
            }
        ]

        result = service.calculate_batch_store_commissions(
            employees=employees,
            stores=stores,
            year=2025,
            month=11,
            start_date='2025-11-01 00:00:00',
            end_date='2025-11-30 23:59:59'
        )

        print(f"\n{'='*80}")
        print(f"Multi-Store Batch Commission - November 2025")
        print(f"{'='*80}")

        assert result['success'] is True
        assert result['summary']['total_stores'] == 2
        assert result['summary']['total_employees'] == 2

        print(f"\nProcessed {result['summary']['total_stores']} stores")
        print(f"Processed {result['summary']['total_employees']} employees")
        print(f"Total Commission: {result['summary']['total_commission_payout']:,.0f} VND\n")

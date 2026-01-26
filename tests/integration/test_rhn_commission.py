import pytest
from app.services.commission_service import CommissionService


class TestRHNStoreCommission:
    """Test store commission calculation for RHN store"""

    def test_rhn_commission_nov_2025(self):
        """
        Test RHN store commission for November 2025

        Store: RHN (RUNWAY HA NOI)
        Target Revenue: 16,800,000,000 VND
        Target FP Ratio: 65% (0.65)
        Employees: GH016 (Dang Thanh Huyen), GH003 (Nguyen Bich Ngoc)
        """
        service = CommissionService()

        # RHN employees for testing
        employees = [
            {
                'employee_code': 'GH016',
                'employee_name': 'Dang Thanh Huyen',
                'store_code': 'RHN',
                'personal_target': 800000000,  # 800 million VND
                'seniority': 18  # 1.5 years
            },
            {
                'employee_code': 'GH003',
                'employee_name': 'Nguyen Bich Ngoc',
                'store_code': 'RHN',
                'personal_target': 850000000,  # 850 million VND
                'seniority': 30  # 2.5 years
            }
        ]

        # RHN store configuration
        stores = [
            {
                'store_code': 'RHN',
                'store_name': 'RUNWAY HA NOI',
                'target_revenue': 16800000000,  # 16.8 billion VND
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
        print(f"\n{'='*120}")
        print(f"RHN STORE COMMISSION CALCULATION - NOVEMBER 2025")
        print(f"{'='*120}")

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
        print(f"{'-'*120}")
        for store in result['stores']:
            print(f"  Store Code:           {store['store_code']}")
            print(f"  Store Name:           {store.get('store_name', 'N/A')}")
            print(f"  Eligible:             {store['eligible']}")

            if store['eligible']:
                print(f"  Target Revenue:       {store.get('target_revenue', 0):>20,.0f} VND")
                print(f"  Target FP Ratio:      {store.get('target_fp_ratio', 0):>20.2%}")
                print(f"  Achievement:          {store.get('achievement_pct', 0):>20.2f}%")
                print(f"  Store Pool:           {store.get('store_pool', 0):>20,.0f} VND")
                print(f"  Employee Count:       {store.get('employee_count', 0)}")
            else:
                print(f"  Reason:               {store.get('reason', 'N/A')}")
            print(f"{'-'*120}")

        # Print employee results
        print(f"\nEMPLOYEE COMMISSION DETAILS:")
        print(f"{'-'*120}")
        print(f"{'Code':<10} {'Name':<25} {'Store':<8} {'Eligible':<10} {'FP Revenue':>18} {'Disc Revenue':>18} {'Commission':>18}")
        print(f"{'-'*120}")

        for emp in result['employees']:
            emp_code = emp.get('employee_code', 'N/A')
            emp_name = emp.get('employee_name', 'N/A')[:23]
            store_code = emp.get('store_code', 'N/A')
            eligible = 'Yes' if emp.get('eligible', False) else 'No'
            fp_revenue = emp.get('fp_revenue', 0)
            disc_revenue = emp.get('discounted_revenue', 0)
            commission = emp.get('total_commission', 0)

            print(f"{emp_code:<10} {emp_name:<25} {store_code:<8} {eligible:<10} {fp_revenue:>18,.0f} {disc_revenue:>18,.0f} {commission:>18,.0f}")

            if emp.get('eligible', False):
                print(f"  Details:")
                print(f"    - Seniority:         {emp.get('tenure_months', 0)} months")
                print(f"    - Achievement:       {emp.get('achievement_pct', 0):.2f}%")
                print(f"    - Contribution:      {emp.get('contribution', 0):>18,.0f} VND")
                print(f"    - 70% Pool Share:    {emp.get('commission_70pct', 0):>18,.0f} VND")
                print(f"    - 30% Pool Share:    {emp.get('commission_30pct', 0):>18,.0f} VND")
                if emp.get('manager_bonus', 0) > 0:
                    print(f"    - Manager Bonus:     {emp.get('manager_bonus', 0):>18,.0f} VND")
                print(f"    - Total Commission:  {emp.get('total_commission', 0):>18,.0f} VND")
            else:
                print(f"  Reason: {emp.get('reason', 'N/A')}")

            print(f"{'-'*120}")

        print(f"\n{'='*120}\n")

        # Assertions
        assert summary['total_stores'] == 1, "Should have 1 store"
        assert summary['total_employees'] >= 1, "Should have at least 1 employee"

    def test_rhn_commission_all_employees_nov_2025(self):
        """
        Test RHN store commission with more employees from November 2025
        """
        service = CommissionService()

        # More RHN employees
        employees = [
            {
                'employee_code': 'GH016',
                'employee_name': 'Dang Thanh Huyen',
                'store_code': 'RHN',
                'personal_target': 800000000,
                'seniority': 18
            },
            {
                'employee_code': 'GH003',
                'employee_name': 'Nguyen Bich Ngoc',
                'store_code': 'RHN',
                'personal_target': 850000000,
                'seniority': 30
            },
            {
                'employee_code': 'GH007',
                'employee_name': 'Nguyen Van Vy',
                'store_code': 'RHN',
                'personal_target': 750000000,
                'seniority': 24
            },
            {
                'employee_code': 'GH025',
                'employee_name': 'Bui Thi Hue',
                'store_code': 'RHN',
                'personal_target': 700000000,
                'seniority': 20
            }
        ]

        stores = [
            {
                'store_code': 'RHN',
                'store_name': 'RUNWAY HA NOI',
                'target_revenue': 16800000000,
                'target_fp_ratio': 0.65
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

        print(f"\n{'='*100}")
        print(f"RHN Store - Multiple Employees Test")
        print(f"{'='*100}")

        assert result['success'] is True

        summary = result['summary']
        print(f"\nSummary:")
        print(f"  Total Employees Processed:  {summary['total_employees']}")
        print(f"  Eligible Employees:         {summary['eligible_employees']}")
        print(f"  Total Commission Payout:    {summary['total_commission_payout']:>18,.0f} VND")

        if result['stores'] and result['stores'][0]['eligible']:
            store = result['stores'][0]
            print(f"\nStore Achievement:            {store['achievement_pct']:.2f}%")
            print(f"Store Pool:                   {store['store_pool']:>18,.0f} VND")

        print()

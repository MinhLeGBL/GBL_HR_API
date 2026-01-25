"""
Test script for personal commission calculation endpoint
"""
import requests
import json

# API endpoint
BASE_URL = "http://localhost:5000"
ENDPOINT = f"{BASE_URL}/api/v1/commission/personal/calculate"

# Test data
test_data = {
    "month": 11,
    "year": 2025,
    "employees": [
        {
            "employee_id": "อาทิตย์",  # Replace with actual employee name from database
            "target": 50000000,  # 50 million target
            "employee_name": "อาทิตย์",
            "department": "Fashion"
        },
        {
            "employee_id": "สมชาย ใจดี",  # Replace with actual employee name
            "target": 30000000,  # 30 million target
            "employee_name": "สมชาย ใจดี",
            "department": "Accessories"
        }
    ]
}

def test_personal_commission():
    """Test the personal commission calculation endpoint"""
    print("Testing Personal Commission Calculation Endpoint")
    print("=" * 60)
    print(f"\nRequest URL: {ENDPOINT}")
    print(f"\nRequest Data:")
    print(json.dumps(test_data, indent=2, ensure_ascii=False))
    print("\n" + "=" * 60)

    try:
        # Make POST request
        response = requests.post(
            ENDPOINT,
            json=test_data,
            headers={"Content-Type": "application/json"}
        )

        # Print response
        print(f"\nResponse Status Code: {response.status_code}")
        print(f"\nResponse Data:")

        if response.status_code == 200:
            result = response.json()
            print(json.dumps(result, indent=2, ensure_ascii=False))

            # Print summary
            if result.get('success'):
                summary = result.get('summary', {})
                print("\n" + "=" * 60)
                print("SUMMARY")
                print("=" * 60)
                print(f"Total Employees: {summary.get('total_employees')}")
                print(f"Eligible Employees: {summary.get('eligible_employees')}")
                print(f"Ineligible Employees: {summary.get('ineligible_employees')}")
                print(f"Total Commission Payout: {summary.get('total_commission_payout'):,.2f} THB")

                # Print individual employee results
                print("\n" + "=" * 60)
                print("EMPLOYEE DETAILS")
                print("=" * 60)
                for emp in result.get('employee_commissions', []):
                    print(f"\nEmployee: {emp.get('employee_name', emp.get('employee_id'))}")
                    print(f"  Department: {emp.get('department', 'N/A')}")
                    print(f"  Target: {emp.get('target', 0):,.2f} THB")
                    print(f"  Total Revenue: {emp.get('total_revenue', 0):,.2f} THB")
                    print(f"  Achievement Rate: {emp.get('achievement_rate', 0):.2f}%")
                    print(f"  Eligible: {emp.get('eligible', False)}")
                    if emp.get('eligible'):
                        print(f"  Full Price Sales: {emp.get('full_price_sales', 0):,.2f} THB")
                        print(f"  Discounted Sales: {emp.get('discounted_sales', 0):,.2f} THB")
                        print(f"  Total Commission: {emp.get('total_commission', 0):,.2f} THB")
                        print(f"  Commission Breakdown:")
                        for tier in emp.get('commission_breakdown', []):
                            print(f"    - {tier.get('tier')}: {tier.get('commission', 0):,.2f} THB")
                    else:
                        print(f"  Reason: {emp.get('reason', 'N/A')}")
        else:
            print(json.dumps(response.json(), indent=2, ensure_ascii=False))

    except requests.exceptions.ConnectionError:
        print("\nERROR: Could not connect to the API server.")
        print("Please make sure the Flask app is running on http://localhost:5000")
    except Exception as e:
        print(f"\nERROR: {str(e)}")


if __name__ == "__main__":
    test_personal_commission()

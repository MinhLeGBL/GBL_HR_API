#!/usr/bin/env python3
"""
Query testing utility - Test your complex HR queries
"""
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.services.reports.hr_report_service import HRReportService
from app.queries.hr_reports.salary_queries import SalaryQueries
import json


def test_query_with_params(query_name: str, query: str, params: dict = None):
    """Test a specific query and display results"""
    print(f"\n{'='*60}")
    print(f"🔍 Testing: {query_name}")
    print(f"{'='*60}")
    
    if params:
        print(f"Parameters: {json.dumps(params, indent=2)}")
    
    try:
        hr_service = HRReportService()
        test_info = hr_service.test_query(query, params)
        
        print(f"\n✅ Query executed successfully!")
        print(f"   ⏱️  Execution time: {test_info['execution_time_seconds']} seconds")
        print(f"   📊 Rows returned: {test_info['row_count']}")
        print(f"   📋 Columns: {', '.join(test_info['columns'])}")
        
        if test_info['sample_data']:
            print(f"\n📄 Sample Data (first 5 rows):")
            for i, row in enumerate(test_info['sample_data'], 1):
                print(f"\n   Row {i}:")
                for key, value in row.items():
                    print(f"      {key}: {value}")
        else:
            print("\n   No data returned")
            
        return True
        
    except Exception as e:
        print(f"\n❌ Query failed: {str(e)}")
        return False


def main():
    """Main query testing function"""
    print("🧪 HR Query Testing Utility")
    
    queries = SalaryQueries()
    
    # Test all predefined queries
    test_cases = [
        {
            'name': 'Complex HR Report',
            'query': queries.COMPLEX_HR_REPORT,
            'params': {'pay_period': '2024-01'}
        },
        {
            'name': 'Employee Salary Detail', 
            'query': queries.EMPLOYEE_SALARY_DETAIL,
            'params': {'employee_id': 'EMP001', 'pay_period': '2024-01'}
        },
        {
            'name': 'Department Salary Summary',
            'query': queries.DEPARTMENT_SALARY_SUMMARY,
            'params': {'pay_period': '2024-01'}
        }
    ]
    
    # Basic connection test
    print(f"\n🔌 Testing database connection...")
    hr_service = HRReportService()
    try:
        results = hr_service.execute_query("SELECT 'Connection OK' as status FROM DUAL")
        print(f"✅ Database connection: {results[0]['STATUS']}")
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False
    
    # Test each query
    success_count = 0
    for test_case in test_cases:
        success = test_query_with_params(
            test_case['name'],
            test_case['query'],
            test_case['params']
        )
        if success:
            success_count += 1
    
    # Summary
    print(f"\n{'='*60}")
    print(f"📈 TESTING SUMMARY")
    print(f"{'='*60}")
    print(f"Total queries tested: {len(test_cases)}")
    print(f"Successful: {success_count}")
    print(f"Failed: {len(test_cases) - success_count}")
    
    if success_count == len(test_cases):
        print("🎉 All queries executed successfully!")
        return True
    else:
        print("⚠️  Some queries failed - check your table structure and data")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
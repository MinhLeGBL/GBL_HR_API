#!/usr/bin/env python3
"""
Sales Query Testing Utility - Test your retail sales queries
"""
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.services.sales.retail_sales_service import RetailSalesService
from app.queries.sales_reports.retail_queries import RetailSalesQueries
import json


def test_query_with_params(query_name: str, query: str, params: dict = None):
    """Test a specific query and display results"""
    print(f"\n{'='*70}")
    print(f"🛒 Testing: {query_name}")
    print(f"{'='*70}")
    
    if params:
        print(f"Parameters: {json.dumps(params, indent=2)}")
    
    sales_service = RetailSalesService()
    test_info = sales_service.test_query(query, params)
    
    if test_info['success']:
        print(f"\n✅ Query executed successfully!")
        print(f"   ⏱️  Execution time: {test_info['execution_time_seconds']} seconds")
        print(f"   📊 Rows returned: {test_info['row_count']}")
        print(f"   📋 Columns ({len(test_info['columns'])}): {', '.join(test_info['columns'][:10])}")
        if len(test_info['columns']) > 10:
            print(f"      ... and {len(test_info['columns']) - 10} more columns")
        
        if test_info['sample_data']:
            print(f"\n📄 Sample Data (first 3 rows):")
            for i, row in enumerate(test_info['sample_data'][:3], 1):
                print(f"\n   📄 Row {i}:")
                # Show only first 8 columns to avoid overwhelming output
                for j, (key, value) in enumerate(row.items()):
                    if j < 8:
                        print(f"      {key}: {value}")
                    elif j == 8:
                        print(f"      ... and {len(row) - 8} more columns")
                        break
        else:
            print(f"\n   ℹ️  No data returned (empty result set)")
            
        return True
        
    else:
        print(f"\n❌ Query failed!")
        print(f"   ⏱️  Execution time: {test_info['execution_time_seconds']} seconds")
        print(f"   🚫 Error: {test_info['error']}")
        return False


def main():
    """Main sales query testing function"""
    print("🛒 RETAIL SALES QUERY TESTING UTILITY")
    print("="*70)
    
    queries = RetailSalesQueries()
    
    # Test database connection first
    print(f"\n🔌 Testing database connection...")
    sales_service = RetailSalesService()
    try:
        results = sales_service.execute_query("SELECT 'Connection OK' as status FROM DUAL")
        print(f"✅ Database connection: {results[0]['STATUS']}")
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False
    
    # Test queries with date parameters
    test_cases = [
        {
            'name': 'Detailed Sales Report (1 week sample)',
            'query': queries.DETAILED_SALES_REPORT.replace(
                ':start_date', "TO_DATE('2024-01-01 00:00:00', 'YYYY-MM-DD HH24:MI:SS')"
            ).replace(
                ':end_date', "TO_DATE('2024-01-07 23:59:59', 'YYYY-MM-DD HH24:MI:SS')"
            ),
            'params': None
        },
        {
            'name': 'Store Sales Summary (January 2024)',
            'query': queries.STORE_SALES_SUMMARY.replace(
                ':start_date', "TO_DATE('2024-01-01 00:00:00', 'YYYY-MM-DD HH24:MI:SS')"
            ).replace(
                ':end_date', "TO_DATE('2024-01-31 23:59:59', 'YYYY-MM-DD HH24:MI:SS')"
            ),
            'params': None
        },
        {
            'name': 'Customer Analysis (January 2024)',
            'query': queries.CUSTOMER_ANALYSIS.replace(
                ':start_date', "TO_DATE('2024-01-01 00:00:00', 'YYYY-MM-DD HH24:MI:SS')"
            ).replace(
                ':end_date', "TO_DATE('2024-01-31 23:59:59', 'YYYY-MM-DD HH24:MI:SS')"
            ),
            'params': None
        }
    ]
    
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
    
    # Test the service methods
    print(f"\n{'='*70}")
    print(f"🔧 Testing Service Methods")
    print(f"{'='*70}")
    
    try:
        # Test service method calls
        start_date = "2024-01-01 00:00:00"
        end_date = "2024-01-07 23:59:59"
        
        print(f"\n📊 Testing get_detailed_sales_report()...")
        detailed_results = sales_service.get_detailed_sales_report(start_date, end_date)
        print(f"   ✅ Success! Retrieved {len(detailed_results)} records")
        
        print(f"\n🏪 Testing get_store_sales_summary()...")
        store_results = sales_service.get_store_sales_summary(start_date, end_date)
        print(f"   ✅ Success! Retrieved {len(store_results)} store summaries")
        
        print(f"\n👥 Testing get_customer_analysis()...")
        customer_results = sales_service.get_customer_analysis(start_date, end_date)
        print(f"   ✅ Success! Retrieved {len(customer_results)} customer records")
        
        service_success = True
        
    except Exception as e:
        print(f"   ❌ Service methods failed: {str(e)}")
        service_success = False
    
    # Summary
    print(f"\n{'='*70}")
    print(f"📈 TESTING SUMMARY")
    print(f"{'='*70}")
    print(f"Direct SQL queries tested: {len(test_cases)}")
    print(f"Direct queries successful: {success_count}")
    print(f"Direct queries failed: {len(test_cases) - success_count}")
    print(f"Service methods: {'✅ Working' if service_success else '❌ Failed'}")
    
    overall_success = (success_count > 0 and service_success)
    
    if overall_success:
        print("🎉 Sales query system is working!")
        print("\n💡 Next steps:")
        print("   1. Add your actual date ranges")
        print("   2. Uncomment columns you need in the queries")
        print("   3. Test with your specific business requirements")
        return True
    else:
        print("⚠️  Some issues detected - check your table structure and data")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
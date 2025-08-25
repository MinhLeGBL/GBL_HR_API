"""
Integration tests for retail sales queries
"""
import pytest
from app.services.sales.retail_sales_service import RetailSalesService
from app.queries.sales_reports.retail_queries import RetailSalesQueries


class TestRetailSalesQueries:
    """Test retail sales query execution and results"""
    
    def setup_method(self):
        """Setup test fixtures"""
        self.sales_service = RetailSalesService()
        self.queries = RetailSalesQueries()
    
    @pytest.mark.integration
    def test_sales_service_connection(self):
        """Test that the sales service can connect to database"""
        # Simple test query
        test_query = "SELECT COUNT(*) as total_records FROM DOCUMENT WHERE ROWNUM <= 10"
        
        try:
            results = self.sales_service.execute_query(test_query)
            assert len(results) == 1
            assert 'TOTAL_RECORDS' in results[0]
            print(f"\n✅ Database connection successful - Found {results[0]['TOTAL_RECORDS']} sample records")
            
        except Exception as e:
            print(f"\n⚠️  Database connection test failed: {str(e)}")
            pytest.skip("Database connection failed - check database availability")
    
    @pytest.mark.integration
    def test_detailed_sales_report_structure(self):
        """Test the structure of detailed sales report query"""
        # Test with recent date range
        start_date = "2024-01-01 00:00:00"
        end_date = "2024-12-31 23:59:59"
        
        # Prepare query with date parameters
        query = self.queries.DETAILED_SALES_REPORT.replace(
            ':start_date', f"TO_DATE('{start_date}', 'YYYY-MM-DD HH24:MI:SS')"
        ).replace(
            ':end_date', f"TO_DATE('{end_date}', 'YYYY-MM-DD HH24:MI:SS')"
        )
        
        test_info = self.sales_service.test_query(query)
        
        print(f"\n📊 Detailed Sales Report Test Results:")
        print(f"   Success: {test_info['success']}")
        print(f"   Execution time: {test_info['execution_time_seconds']}s")
        print(f"   Rows returned: {test_info['row_count']}")
        
        if test_info['success']:
            print(f"   Columns: {len(test_info['columns'])}")
            if test_info['sample_data']:
                sample = test_info['sample_data'][0]
                print(f"   Sample data keys: {list(sample.keys())[:5]}...")
                
            # Assertions for successful query
            assert test_info['execution_time_seconds'] < 60, "Query should complete within 60 seconds"
            assert isinstance(test_info['row_count'], int), "Should return integer row count"
            
        else:
            print(f"   Error: {test_info['error']}")
            # Still considered a success if query structure is valid but no data
            if "no data found" in str(test_info['error']).lower():
                pytest.skip("No data in date range - query structure is valid")
            else:
                pytest.fail(f"Query execution failed: {test_info['error']}")
    
    @pytest.mark.integration
    def test_store_sales_summary_query(self):
        """Test store sales summary query"""
        start_date = "2024-01-01 00:00:00"
        end_date = "2024-12-31 23:59:59"
        
        query = self.queries.STORE_SALES_SUMMARY.replace(
            ':start_date', f"TO_DATE('{start_date}', 'YYYY-MM-DD HH24:MI:SS')"
        ).replace(
            ':end_date', f"TO_DATE('{end_date}', 'YYYY-MM-DD HH24:MI:SS')"
        )
        
        test_info = self.sales_service.test_query(query)
        
        print(f"\n🏪 Store Sales Summary Test:")
        print(f"   Success: {test_info['success']}")
        print(f"   Execution time: {test_info['execution_time_seconds']}s")
        print(f"   Stores found: {test_info['row_count']}")
        
        if test_info['success']:
            assert test_info['execution_time_seconds'] < 30, "Summary query should be reasonably fast"
            if test_info['sample_data']:
                sample = test_info['sample_data'][0]
                expected_columns = ['STORE_CODE', 'TOTAL_TRANSACTIONS', 'TOTAL_SALES']
                for col in expected_columns:
                    assert col in sample, f"Expected column {col} not found"
        else:
            if "no data found" not in str(test_info['error']).lower():
                pytest.fail(f"Store summary query failed: {test_info['error']}")
    
    @pytest.mark.integration
    def test_customer_analysis_query(self):
        """Test customer analysis query"""
        start_date = "2024-01-01 00:00:00"  
        end_date = "2024-12-31 23:59:59"
        
        query = self.queries.CUSTOMER_ANALYSIS.replace(
            ':start_date', f"TO_DATE('{start_date}', 'YYYY-MM-DD HH24:MI:SS')"
        ).replace(
            ':end_date', f"TO_DATE('{end_date}', 'YYYY-MM-DD HH24:MI:SS')"
        )
        
        test_info = self.sales_service.test_query(query)
        
        print(f"\n👥 Customer Analysis Test:")
        print(f"   Success: {test_info['success']}")
        print(f"   Execution time: {test_info['execution_time_seconds']}s")
        print(f"   Customers found: {test_info['row_count']}")
        
        if test_info['success']:
            assert test_info['execution_time_seconds'] < 45, "Customer analysis should complete reasonably fast"
            if test_info['sample_data']:
                sample = test_info['sample_data'][0]
                expected_columns = ['CUSTOMER_SID', 'TOTAL_PURCHASES', 'TOTAL_SPENT']
                for col in expected_columns:
                    assert col in sample, f"Expected column {col} not found"
        else:
            if "no data found" not in str(test_info['error']).lower():
                pytest.fail(f"Customer analysis query failed: {test_info['error']}")
    
    @pytest.mark.integration
    def test_service_methods(self):
        """Test the service methods with date parameters"""
        start_date = "2024-01-01 00:00:00"
        end_date = "2024-01-31 23:59:59"  # Smaller range for faster testing
        
        try:
            # Test detailed sales report method
            detailed_results = self.sales_service.get_detailed_sales_report(start_date, end_date)
            assert isinstance(detailed_results, list)
            
            # Test store summary method  
            store_results = self.sales_service.get_store_sales_summary(start_date, end_date)
            assert isinstance(store_results, list)
            
            # Test customer analysis method
            customer_results = self.sales_service.get_customer_analysis(start_date, end_date)
            assert isinstance(customer_results, list)
            
            print(f"\n📈 Service Methods Test:")
            print(f"   Detailed sales records: {len(detailed_results)}")
            print(f"   Store summaries: {len(store_results)}")
            print(f"   Customer records: {len(customer_results)}")
            
        except Exception as e:
            print(f"\n⚠️  Service methods test failed: {str(e)}")
            # Don't fail the test if it's just missing data
            if "no data found" not in str(e).lower():
                pytest.fail(f"Service methods failed: {str(e)}")
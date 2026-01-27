"""
Query specific employee GL018 data for November 2025
"""
import pandas as pd
from app.database.connection import get_oracle_connection
from app.queries.commission_queries import CommissionQueries


def test_gl018_employee_data():
    """Get all sales data for employee GL018"""
    conn = get_oracle_connection()
    assert conn is not None, "Failed to establish database connection"

    try:
        cursor = conn.cursor()

        # Query parameters for November 2025
        query_params = {'year_month': '2025-11'}

        # Execute query
        cursor.execute(
            CommissionQueries.PERSONAL_COMMISSION_SALES_DATA,
            **query_params
        )

        # Get results as DataFrame
        columns = [col[0].lower() for col in cursor.description]
        rows = cursor.fetchall()
        df = pd.DataFrame(rows, columns=columns)

        # Filter for employee GL018
        gl018_df = df[df['employee_code'] == 'GL018'].copy()

        if len(gl018_df) == 0:
            print("\n" + "="*80)
            print("NO DATA FOUND FOR EMPLOYEE GL018")
            print("="*80)
            return

        print("\n" + "="*80)
        print(f"EMPLOYEE GL018 - NOVEMBER 2025 SALES DATA")
        print("="*80)
        print(f"\nTotal transactions: {len(gl018_df)}")
        print(f"Unique bills: {gl018_df['bill_number'].nunique()}")
        print(f"Date range: {gl018_df['sale_date'].min()} to {gl018_df['sale_date'].max()}")

        # Revenue summary
        total_rev_with_vat = gl018_df['revenue_with_vat'].sum()
        total_rev_before_vat = gl018_df['revenue_before_vat'].sum()

        print("\n" + "-"*80)
        print("REVENUE SUMMARY:")
        print("-"*80)
        print(f"Total revenue WITH VAT: {total_rev_with_vat:,.0f} VND")
        print(f"Total revenue BEFORE VAT: {total_rev_before_vat:,.0f} VND")

        # Jewelry vs Non-Jewelry breakdown
        jewelry_df = gl018_df[gl018_df['is_jewelry'] == 1]
        non_jewelry_df = gl018_df[gl018_df['is_jewelry'] == 0]

        print("\n" + "-"*80)
        print("JEWELRY vs NON-JEWELRY:")
        print("-"*80)
        print(f"Jewelry items: {len(jewelry_df)}")
        print(f"Jewelry revenue (before VAT): {jewelry_df['revenue_before_vat'].sum():,.0f} VND")
        print(f"\nNon-jewelry items: {len(non_jewelry_df)}")
        print(f"Non-jewelry revenue (before VAT): {non_jewelry_df['revenue_before_vat'].sum():,.0f} VND")

        # Full-price vs Discounted
        fp_df = non_jewelry_df[non_jewelry_df['discount_rate'] <= 0.30]
        disc_df = non_jewelry_df[non_jewelry_df['discount_rate'] > 0.30]

        print("\n" + "-"*80)
        print("NON-JEWELRY: FULL-PRICE vs DISCOUNTED:")
        print("-"*80)
        print(f"Full-price items (≤30% discount): {len(fp_df)}")
        print(f"FP revenue (before VAT): {fp_df['revenue_before_vat'].sum():,.0f} VND")
        print(f"\nDiscounted items (>30% discount): {len(disc_df)}")
        print(f"Disc revenue (before VAT): {disc_df['revenue_before_vat'].sum():,.0f} VND")

        # Jewelry breakdown by vendor
        if len(jewelry_df) > 0:
            print("\n" + "-"*80)
            print("JEWELRY BREAKDOWN BY VENDOR:")
            print("-"*80)
            jewelry_vendor_summary = jewelry_df.groupby('vendor_code').agg({
                'sale_id': 'count',
                'revenue_before_vat': 'sum'
            }).reset_index()
            jewelry_vendor_summary.columns = ['vendor_code', 'item_count', 'revenue_before_vat']

            for _, row in jewelry_vendor_summary.iterrows():
                print(f"Vendor {row['vendor_code']}: {row['item_count']} items, {row['revenue_before_vat']:,.0f} VND")

        # Store breakdown
        print("\n" + "-"*80)
        print("STORE BREAKDOWN:")
        print("-"*80)
        store_summary = gl018_df.groupby('store_code').agg({
            'sale_id': 'count',
            'revenue_with_vat': 'sum'
        }).reset_index()
        store_summary.columns = ['store_code', 'item_count', 'revenue_with_vat']

        for _, row in store_summary.iterrows():
            print(f"Store {row['store_code']}: {row['item_count']} items, {row['revenue_with_vat']:,.0f} VND")

        # First 10 transactions
        print("\n" + "="*80)
        print("FIRST 10 TRANSACTIONS:")
        print("="*80)

        for idx, row in gl018_df.head(10).iterrows():
            print(f"\n[{idx + 1}] Sale ID: {row['sale_id']}")
            print(f"    UPC: {row['upc']}")
            print(f"    Bill: {row['bill_number']} | Store: {row['store_code']} | Date: {row['sale_date']}")
            print(f"    Revenue: {row['revenue_with_vat']:,.0f} (with VAT) / {row['revenue_before_vat']:,.0f} (before VAT)")
            print(f"    Discount: {row['discount_rate']:.1%} | Jewelry: {row['is_jewelry']}")
            print(f"    Vendor: {row['vendor_code']} | Dept: {row['department']}")

        # Last 10 transactions
        if len(gl018_df) > 10:
            print("\n" + "="*80)
            print("LAST 10 TRANSACTIONS:")
            print("="*80)

            for idx, row in gl018_df.tail(10).iterrows():
                print(f"\n[{idx + 1}] Sale ID: {row['sale_id']}")
                print(f"    UPC: {row['upc']}")
                print(f"    Bill: {row['bill_number']} | Store: {row['store_code']} | Date: {row['sale_date']}")
                print(f"    Revenue: {row['revenue_with_vat']:,.0f} (with VAT) / {row['revenue_before_vat']:,.0f} (before VAT)")
                print(f"    Discount: {row['discount_rate']:.1%} | Jewelry: {row['is_jewelry']}")
                print(f"    Vendor: {row['vendor_code']} | Dept: {row['department']}")

        print("\n" + "="*80)

        cursor.close()
    finally:
        conn.close()


if __name__ == "__main__":
    test_gl018_employee_data()

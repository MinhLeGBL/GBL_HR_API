"""
One-off: detailed sales report for vendor NAK (Nak Armstrong).
Period: 2023-01-01 -> today.
Output: CSV in document/ directory.
"""
import csv
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from app.core.database import get_oracle_connection

QUERY = """
SELECT
    -- ITEM INFO
    i.SID                                                                    as ITEM_SID,
    TO_CHAR(i.UPC)                                                           as UPC,
    i.UDF5_STRING                                                            as SEASON,
    di.VEND_CODE                                                             as VENDOR_CODE,
    v.VEND_NAME                                                              as VENDOR_NAME,
    dep.D_LONG_NAME                                                          as DEPARTMENT,
    SUBSTR(i.DESCRIPTION2, INSTR(i.DESCRIPTION2, '-', -1) + 1)               as CATEGORY,
    di.DESCRIPTION1                                                          as ITEM_NAME,
    di.ATTRIBUTE                                                             as ATTRIBUTE,
    di.ITEM_SIZE                                                             as ITEM_SIZE,
    ROUND(di.ORIG_PRICE, 0)                                                  as FULL_PRICE_VAT,
    ROUND(di.ORIG_PRICE - di.ORIG_TAX_AMT, 0)                                as FULL_PRICE,
    i.TEXT1                                                                  as DESCRIPTION,

    -- BILL INFO
    d.STORE_CODE || d.DOC_NO                                                 as STORE_BILL,
    d.CREATED_DATETIME                                                       as BILL_DATE,
    TO_CHAR(d.invc_post_date, 'YYYY')                                        as YEAR,
    TO_CHAR(d.invc_post_date, 'MM')                                          as MONTH,
    TO_CHAR(d.invc_post_date, 'WW')                                          as WEEK,
    d.STORE_CODE                                                             as STORE_CODE,
    d.DOC_NO                                                                 as BILL_NO,
    CASE d.RECEIPT_TYPE
        WHEN 0 THEN 'SALE'
        WHEN 1 THEN 'RETURN'
        ELSE 'DEPOSIT' END                                                   as RECEIPT_TYPE,
    di.sid                                                                   as DOC_ITEM_SID,
    di.RETURNED_ITEM_INVOICE_SID                                             as REF_ITEM_SID,
    ((CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END))          AS QTY_SOLD,
    d.TENDER_NAME                                                            as PAYMENT_FORM,
    ROUND(((CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) *
           di.price))                                                        AS PAYMENT_VAT,
    ROUND(((CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) *
           (di.price - di.tax_amt)))                                         AS PAYMENT,
    d.DISC_PERC                                                              as BILL_DISC_PERC,
    di.DISC_PERC                                                             as ITEM_DISC_PERC,

    -- DISCOUNT CALCULATIONS
    ROUND((1 - (1 - di.DISC_PERC / 100) * (1 - d.DISC_PERC / 100)) * 100, 1) as TOTAL_DISC_PERC,
    ROUND((((CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) * (di.orig_price)) -
           ((CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) * (di.price))),
          0)                                                                 as DISC_AMT_VAT,
    ROUND(((CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) *
           (di.orig_price - di.ORIG_TAX_AMT)) *
          (1 - (1 - di.DISC_PERC / 100) * (1 - d.DISC_PERC / 100)))          as DISC_AMT,

    -- TAX CALCULATIONS
    ROUND(((CASE WHEN di.ITEM_TYPE = 2 THEN di.QTY * -1 ELSE di.QTY END) * (di.TAX_AMT)),
          0)                                                                 as TAX_AMT,
    ROUND(((CASE WHEN di.ITEM_TYPE = 2 THEN di.QTY * -1 ELSE di.QTY END) * (di.ORIG_TAX_AMT)),
          0)                                                                 as FULL_PRICE_TAX_AMT,

    -- EMPLOYEE INFO
    d.CASHIER_FULL_NAME                                                      as CASHIER,
    di.EMPLOYEE1_FULL_NAME                                                   as ASSOCIATE_1,
    di.EMPLOYEE2_FULL_NAME                                                   as ASSOCIATE_2,
    di.EMPLOYEE3_FULL_NAME                                                   as ASSOCIATE_3,
    di.EMPLOYEE4_FULL_NAME                                                   as ASSOCIATE_4,

    -- CUSTOMER INFO
    d.BT_CUID                                                                as CUSTOMER_SID,
    c.EMAIL                                                                  as CUSTOMER_EMAIL,
    c.INFO1                                                                  as CUSTOMER_TYPE,
    TRUNC(c.UDF1_DATE)                                                       as CUS_BIRTHDAY,
    c.FIRST_SALE_DATE                                                        as CUS_FIRST_SALE_DATE,
    c.LAST_SALE_DATE                                                         as CUS_LAST_SALE_DATE

FROM DOCUMENT d
         JOIN DOCUMENT_ITEM di ON d.SID = di.DOC_SID
         JOIN SUBSIDIARY s ON d.SBS_NO = s.SBS_NO
         JOIN STORE st ON st.SID = d.STORE_SID
         JOIN INVN_SBS_ITEM i ON i.SID = di.INVN_SBS_ITEM_SID
         JOIN DCS dep on dep.SID = i.DCS_SID
         JOIN CUSTOMER c ON c.SID = d.BT_CUID
         JOIN VENDOR v ON v.VEND_CODE = di.VEND_CODE
WHERE d.STATUS != 2
  AND d.receipt_type in (0, 1)
  AND di.ITEM_TYPE in (1, 2)
  AND di.VEND_CODE = 'NAK'
  AND d.invc_post_date >= :start_date
  AND d.invc_post_date <= :end_date
ORDER BY d.invc_post_date, d.STORE_CODE, d.DOC_NO
"""


def main():
    start_date = datetime(2023, 1, 1)
    end_date = datetime.now()

    output_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'document'))
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(
        output_dir,
        f"nak_armstrong_sales_{start_date.strftime('%Y%m%d')}_to_{end_date.strftime('%Y%m%d')}.csv"
    )

    print(f"Connecting to Oracle...")
    conn = get_oracle_connection()
    if conn is None:
        print("ERROR: Could not connect to Oracle.")
        sys.exit(1)

    try:
        cursor = conn.cursor()
        print(f"Running query (vendor=NAK, {start_date.date()} -> {end_date.date()})...")
        cursor.execute(QUERY, {'start_date': start_date, 'end_date': end_date})

        columns = [col[0] for col in cursor.description]
        row_count = 0

        with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            for row in cursor:
                writer.writerow(row)
                row_count += 1

        print(f"\n[OK] {row_count} rows written")
        print(f"     {output_path}")
    finally:
        conn.close()


if __name__ == '__main__':
    main()

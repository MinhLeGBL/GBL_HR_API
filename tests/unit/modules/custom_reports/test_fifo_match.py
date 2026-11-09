"""Unit tests for the FIFO unit-matching logic.

The matcher is the load-bearing part of `avg_days_to_sell`. These tests
exercise it directly with hand-constructed DataFrames so we can pin the
behaviour without hitting Oracle.
"""
from datetime import datetime

import pandas as pd

from app.modules.custom_reports.service import CustomReportsService


def _receipts(*rows):
    """rows: (item_sid, post_date_str 'YYYY-MM-DD', qty)"""
    df = pd.DataFrame(rows, columns=['item_sid', 'post_date', 'qty'])
    df['post_date'] = pd.to_datetime(df['post_date'])
    df['qty'] = df['qty'].astype(int)
    df['item_sid'] = df['item_sid'].astype('int64')
    return df


def _sales(*rows):
    """rows: (item_sid, sale_date_str 'YYYY-MM-DD', qty)"""
    df = pd.DataFrame(rows, columns=['item_sid', 'sale_date', 'qty'])
    df['sale_date'] = pd.to_datetime(df['sale_date'])
    df['qty'] = df['qty'].astype(int)
    df['item_sid'] = df['item_sid'].astype('int64')
    return df


def _match(receipts_df, sales_df):
    return CustomReportsService._fifo_match(receipts_df, sales_df)


class TestFifoMatch:

    def test_single_receipt_single_sale(self):
        rec = _receipts((1, '2025-01-01', 10))
        sal = _sales((1, '2025-01-11', 3))

        m = _match(rec, sal)

        assert len(m) == 1
        assert m.iloc[0]['days_to_sell'] == 10
        assert m.iloc[0]['qty'] == 3

    def test_sale_spans_two_receipts(self):
        # 5 units from batch 1 (Jan 1), then 5 units from batch 2 (Feb 1)
        rec = _receipts((1, '2025-01-01', 5),
                        (1, '2025-02-01', 5))
        sal = _sales((1, '2025-02-15', 7))   # 7 sold: 5 from batch 1, 2 from batch 2

        m = _match(rec, sal)

        assert len(m) == 2
        # Batch 1: 5 units × (Feb 15 − Jan 1) = 5 × 45 days
        # Batch 2: 2 units × (Feb 15 − Feb 1) = 2 × 14 days
        first, second = m.iloc[0], m.iloc[1]
        assert first['qty'] == 5 and first['days_to_sell'] == 45
        assert second['qty'] == 2 and second['days_to_sell'] == 14

    def test_multiple_sales_consume_batches_in_order(self):
        rec = _receipts((1, '2025-01-01', 3),
                        (1, '2025-02-01', 3))
        sal = _sales((1, '2025-01-05', 2),    # 2 from batch 1 (4 days)
                     (1, '2025-02-10', 4))    # 1 from batch 1 (40 days) + 3 from batch 2 (9 days)

        m = _match(rec, sal)

        # 3 match rows: (sale1→batch1), (sale2→batch1 leftover), (sale2→batch2)
        assert len(m) == 3
        assert m.iloc[0]['qty'] == 2 and m.iloc[0]['days_to_sell'] == 4
        assert m.iloc[1]['qty'] == 1 and m.iloc[1]['days_to_sell'] == 40
        assert m.iloc[2]['qty'] == 3 and m.iloc[2]['days_to_sell'] == 9

    def test_sale_exceeds_receipts_drops_excess(self):
        rec = _receipts((1, '2025-01-01', 2))
        sal = _sales((1, '2025-01-10', 5))   # 3 units lack a matching receipt

        m = _match(rec, sal)

        # Only 2 units are matched; the remaining 3 are silently dropped
        assert len(m) == 1
        assert m.iloc[0]['qty'] == 2
        assert m.iloc[0]['days_to_sell'] == 9

    def test_no_receipts_no_matches(self):
        rec = _receipts()  # empty (uses default columns)
        sal = _sales((1, '2025-01-10', 5))

        m = _match(rec, sal)

        assert len(m) == 0

    def test_multiple_items_independent_queues(self):
        rec = _receipts((1, '2025-01-01', 10),
                        (2, '2025-03-01', 10))
        sal = _sales((1, '2025-01-11', 3),
                     (2, '2025-03-06', 4))

        m = _match(rec, sal)

        assert len(m) == 2
        # Order in output is by item_sid (groupby preserves), then by sale date
        item1 = m[m['item_sid'] == 1].iloc[0]
        item2 = m[m['item_sid'] == 2].iloc[0]
        assert item1['days_to_sell'] == 10
        assert item2['days_to_sell'] == 5

    def test_empty_sales(self):
        rec = _receipts((1, '2025-01-01', 10))
        sal = _sales()
        m = _match(rec, sal)
        assert len(m) == 0

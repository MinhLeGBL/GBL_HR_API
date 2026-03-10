"""
Compare per-employee, per-store revenue by type from Oracle unified query
against the Commission_Dec 2025.xls sample (Sheet 1 'Doanh số').

Per-store column layout (from row 6 headers):
  col+0: FP (New ≤30%)
  col+1: Fine Jewelry
  col+2: Vhernier
  col+3: Hand Carry (Hàng kí gửi)
  col+4: Suitcase/Travelite (count)
  col+5: Rosa Maria Earrings
  col+6: Markdown (Sale >30%)
  col+12: Store total (TỔNG)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import xlrd
import pandas as pd
from app.modules.commission.repository import CommissionRepository
from app.modules.commission.queries import CommissionQueries
from app.modules.commission.service import SUITCASE_VENDORS, DISCOUNT_THRESHOLD
import calendar

YEAR = 2025
MONTH = 12
SAMPLE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'document', 'samples', 'Commission_Dec 2025.xls'
)

# Store block start columns and their total columns
STORE_COLS = {
    'RWR': {'start': 5, 'total': 17},
    'RWT': {'start': 18, 'total': 30},
    'RWD': {'start': 31, 'total': 43},
    'RHN': {'start': 44, 'total': 56},  # labeled RWH in sheet
}

# Type offsets within each store block
TYPE_OFFSETS = {
    'full_price': 0,
    'jewelry': 1,
    'vhernier': 2,
    'hand_carry': 3,
    'suitcase_count': 4,
    'rosa_maria': 5,
    'markdown': 6,
}

# Total section columns (across all stores)
TOTAL_COL = 82


def read_sample_data(filepath):
    """Read per-employee, per-store values from sheet 1."""
    wb = xlrd.open_workbook(filepath)
    ws = wb.sheet_by_index(0)

    employees = {}
    store_separators = {'RWR', 'RWT', 'RWD', 'RWH', 'RTP'}
    in_rtp_section = False

    for r in range(8, ws.nrows):
        code = str(ws.cell_value(r, 1)).strip()
        username = str(ws.cell_value(r, 2)).strip()
        name = str(ws.cell_value(r, 3)).strip()

        if not code and not name:
            continue
        if name == 'RTP':
            in_rtp_section = True
            continue
        if name in store_separators:
            in_rtp_section = False
            continue
        if in_rtp_section or code.endswith('TP'):
            continue
        if code.startswith('Total') or code.startswith('TỔNG'):
            continue
        if not username or username == 'SYSADMIN':
            continue

        emp = {'code': code, 'name': name, 'stores': {}}

        for store_code, col_info in STORE_COLS.items():
            start = col_info['start']
            total_col = col_info['total']

            store_vals = {}
            for type_key, offset in TYPE_OFFSETS.items():
                v = ws.cell_value(r, start + offset)
                store_vals[type_key] = int(v) if v else 0

            store_vals['store_total'] = int(ws.cell_value(r, total_col)) if ws.cell_value(r, total_col) else 0

            # Only include store if any non-zero value
            if any(v != 0 for v in store_vals.values()):
                emp['stores'][store_code] = store_vals

        emp['total_actual'] = int(ws.cell_value(r, TOTAL_COL)) if ws.cell_value(r, TOTAL_COL) else 0

        employees[username] = emp

    return employees


def classify_employee_per_store(emp_username, raw_df, hc_upc_set):
    """Classify an employee's sales per doc_store_code."""
    df = raw_df[raw_df['employee_username'] == emp_username].copy()

    if len(df) == 0:
        return {}

    if 'upc_clean' not in df.columns:
        df['upc_clean'] = df['upc'].astype(str).str.strip()

    result = {}
    for store_code, store_df in df.groupby('doc_store_code'):
        # Priority chain: hand_carry → suitcase → jewelry → non-jewelry
        hc_mask = store_df['upc_clean'].isin(hc_upc_set) if hc_upc_set else pd.Series(False, index=store_df.index)
        hand_carry_df = store_df[hc_mask]
        remainder = store_df[~hc_mask]

        sc_mask = remainder['vendor_code'].isin(SUITCASE_VENDORS)
        suitcase_df = remainder[sc_mask]
        remainder2 = remainder[~sc_mask]

        jewelry_df = remainder2[remainder2['is_jewelry'] == 1]
        non_jewelry_df = remainder2[remainder2['is_jewelry'] == 0]

        # Jewelry sub-types
        vhn_df = jewelry_df[jewelry_df['vendor_code'] == 'VHN']
        rom_ear_mask = (jewelry_df['vendor_code'] == 'ROM') & \
                       (jewelry_df['category'].str.upper().str.contains('EARRING', na=False))
        rom_ear_df = jewelry_df[rom_ear_mask]
        other_jw = jewelry_df[~jewelry_df.index.isin(vhn_df.index) & ~jewelry_df.index.isin(rom_ear_df.index)]

        # Non-jewelry sub-types
        fp_df = non_jewelry_df[non_jewelry_df['discount_rate'] <= DISCOUNT_THRESHOLD]
        md_df = non_jewelry_df[non_jewelry_df['discount_rate'] > DISCOUNT_THRESHOLD]

        result[store_code] = {
            'full_price': int(fp_df['revenue_with_vat'].sum()),
            'jewelry': int(other_jw['revenue_with_vat'].sum()),
            'vhernier': int(vhn_df['revenue_with_vat'].sum()),
            'hand_carry': int(hand_carry_df['revenue_with_vat'].sum()),
            'suitcase_count': len(suitcase_df),
            'rosa_maria': int(rom_ear_df['revenue_with_vat'].sum()),
            'markdown': int(md_df['revenue_with_vat'].sum()),
            'store_total': int(store_df['revenue_with_vat'].sum()),
        }

    return result


def fmt(v):
    return f"{int(v):>15,}"


# ---- Main ----
repo = CommissionRepository()

# Load RAW data (before COSM re-attribution) to see true employee assignments
print(f"Loading RAW sales data for {YEAR}-{MONTH:02d} (before COSM re-attribution)...")
queries = CommissionQueries()
last_day = calendar.monthrange(YEAR, MONTH)[1]
params = {'start_date': f'{YEAR:04d}-{MONTH:02d}-01 00:00:00',
          'end_date': f'{YEAR:04d}-{MONTH:02d}-{last_day} 23:59:59'}
results = repo.execute_query(queries.ALL_SALES_DATA, params)
raw_df = pd.DataFrame(results)
raw_df.columns = raw_df.columns.str.lower()
if 'upc_clean' not in raw_df.columns and 'upc' in raw_df.columns:
    raw_df['upc_clean'] = raw_df['upc'].astype(str).str.strip()
print(f"Total rows: {len(raw_df)}")

hc_upcs = repo.get_hand_carry_upcs()
hc_upc_set = set(hc_upcs)
print(f"Hand carry UPCs: {len(hc_upc_set)}")

print(f"\nReading sample: {os.path.basename(SAMPLE_FILE)}")
sample_employees = read_sample_data(SAMPLE_FILE)
print(f"Sample employees: {len(sample_employees)}")

type_labels = {
    'full_price': 'Full Price',
    'jewelry': 'Jewelry',
    'vhernier': 'Vhernier',
    'hand_carry': 'Hand Carry',
    'suitcase_count': 'Suitcase (cnt)',
    'rosa_maria': 'Rosa Maria',
    'markdown': 'Markdown',
    'store_total': 'Store Total',
}

total_mismatches = 0

for username in sorted(sample_employees.keys()):
    samp = sample_employees[username]
    ours = classify_employee_per_store(username, raw_df, hc_upc_set)

    emp_mismatches = 0
    emp_lines = []

    # Collect all stores from both sides
    all_stores = sorted(set(samp['stores'].keys()) | set(ours.keys()))

    for store_code in all_stores:
        s_store = samp['stores'].get(store_code, {})
        o_store = ours.get(store_code, {})

        for type_key, type_label in type_labels.items():
            s_val = s_store.get(type_key, 0)
            o_val = o_store.get(type_key, 0)

            if s_val == 0 and o_val == 0:
                continue

            diff = o_val - s_val
            marker = " ***" if diff != 0 else ""
            if diff != 0:
                emp_mismatches += 1
            emp_lines.append(f"  {store_code:<5} {type_label:<18} {fmt(s_val)} {fmt(o_val)} {fmt(diff)}{marker}")

    # Also check if employee has sales in stores NOT in the sample's store list
    extra_stores = set(ours.keys()) - set(STORE_COLS.keys())
    for store_code in sorted(extra_stores):
        o_store = ours[store_code]
        for type_key, type_label in type_labels.items():
            o_val = o_store.get(type_key, 0)
            if o_val != 0:
                emp_lines.append(f"  {store_code:<5} {type_label:<18} {'N/A':>15} {fmt(o_val)} {'???':>15}  [EXTRA STORE]")
                emp_mismatches += 1

    # Cross-store total
    our_total = sum(s.get('store_total', 0) for s in ours.values())
    samp_total = samp['total_actual']
    total_diff = our_total - samp_total
    total_marker = " ***" if total_diff != 0 else ""
    if total_diff != 0:
        emp_mismatches += 1

    if emp_mismatches > 0:
        print(f"\n{username} ({samp['code']}, {samp['name']})  — {emp_mismatches} mismatches")
        header = f"  {'Store':<5} {'Type':<18} {'Sample':>15} {'Ours':>15} {'Diff':>15}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for line in emp_lines:
            if '***' in line:
                print(line)
        print(f"  {'ALL':<5} {'TOTAL':<18} {fmt(samp_total)} {fmt(our_total)} {fmt(total_diff)}{total_marker}")
        total_mismatches += emp_mismatches

print(f"\n{'='*80}")
print(f"  TOTAL MISMATCHES: {total_mismatches}")
print(f"{'='*80}")

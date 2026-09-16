"""
Generate the size-by-brand-season report and save it to document/reports/
as Excel (.xlsx) and/or CSV.

Usage:
    python scripts/reports/generate_size_report.py
        # → document/reports/size_report_SS25_SS26_<YYYY-MM-DD>.xlsx + .csv (default)

    python scripts/reports/generate_size_report.py --format xlsx
    python scripts/reports/generate_size_report.py --seasons SS24,FW24
    python scripts/reports/generate_size_report.py --brands AKRIS,MARNI
    python scripts/reports/generate_size_report.py --brands '*'    # all brands
"""
import argparse
import os
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd

from app.modules.custom_reports.service import CustomReportsService, FOCUS_BRANDS


DEFAULT_OUT_DIR = REPO_ROOT / 'document' / 'reports'

# Column order in the output file (left to right). The service-side response
# columns map 1:1; this just enforces a friendly reading order.
COLUMN_ORDER = [
    'brand', 'department', 'normalized_size',
    'sku_count',
    'imported_qty', 'sold_qty', 'on_hand_qty',
    'sell_through_pct',
    'avg_days_to_sell',
    'units_matched',
    'raw_sizes',
]


def parse_args():
    parser = argparse.ArgumentParser(
        description='Generate size-by-brand-season report from Oracle',
    )
    parser.add_argument(
        '--seasons', default='SS25,SS26',
        help='Comma-separated season codes (default: SS25,SS26)',
    )
    parser.add_argument(
        '--brands', default=None,
        help='Comma-separated vendor names. Omit (or pass "*") to include '
             'every vendor. To restrict to the curated 13-brand list, pass '
             'them by name (the FOCUS_BRANDS constant is a convenient preset).',
    )
    parser.add_argument(
        '--format', choices=['xlsx', 'csv', 'both'], default='both',
        help='Output format (default: both)',
    )
    parser.add_argument(
        '--out-dir', default=str(DEFAULT_OUT_DIR),
        help=f'Output directory (default: {DEFAULT_OUT_DIR})',
    )
    parser.add_argument(
        '--include-zero-sales', action='store_true',
        help='Keep rows where sold_qty = 0 (default behaviour — kept). '
             'Provided for symmetry with --no-zero-sales.',
    )
    parser.add_argument(
        '--no-zero-sales', dest='include_zero_sales', action='store_false',
        help='Drop rows where sold_qty = 0 (groups with no sales yet).',
    )
    parser.set_defaults(include_zero_sales=True)
    parser.add_argument(
        '--include-never-received', action='store_true',
        help='Include catalog ghost SKUs (first_rcvd_date IS NULL) in the '
             'underlying counts. Default: dropped — they inflate sku_count '
             'without contributing to any qty metric.',
    )
    return parser.parse_args()


def main():
    args = parse_args()

    seasons = [s.strip() for s in args.seasons.split(',') if s.strip()]
    if args.brands == '*':
        brands = ['*']
    elif args.brands:
        brands = [b.strip() for b in args.brands.split(',') if b.strip()]
    else:
        brands = None  # service falls back to FOCUS_BRANDS

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    svc = CustomReportsService()
    print(f'Running size report:')
    print(f'  seasons:             {seasons}')
    print(f'  brands:              {"(all vendors)" if brands is None else brands}')
    print(f'  include_never_recvd: {args.include_never_received}')
    result = svc.get_size_by_brand_season(
        seasons=seasons, brands=brands,
        include_never_received=args.include_never_received,
    )
    if not result['success']:
        print(f'FAILED: {result.get("error")}')
        sys.exit(1)

    rows = result['rows']
    if not args.include_zero_sales:
        rows = [r for r in rows if r['sold_qty'] > 0]

    print(f'  rows:    {len(rows)}')

    df = pd.DataFrame(rows)
    if not df.empty:
        # Stable column ordering — only keep what's present (for forward-compat)
        ordered = [c for c in COLUMN_ORDER if c in df.columns]
        df = df[ordered]

    today = date.today().isoformat()
    season_tag = '_'.join(seasons)
    base = f'size_report_{season_tag}_{today}'

    written = []
    if args.format in ('xlsx', 'both'):
        path = out_dir / f'{base}.xlsx'
        _write_xlsx(path, df, result, seasons)
        written.append(path)
    if args.format in ('csv', 'both'):
        path = out_dir / f'{base}.csv'
        df.to_csv(path, index=False)
        written.append(path)

    print(f'\nWrote:')
    for p in written:
        size_kb = os.path.getsize(p) / 1024
        print(f'  {p}  ({size_kb:.1f} KB)')


def _write_xlsx(path, df, result, seasons):
    """Write the report + a metadata sheet."""
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Size Report', index=False)

        # Metadata sheet — gives the reader the filter parameters used
        resolved_brands = result.get('brands')
        meta_rows = [
            ('Generated (UTC date)', date.today().isoformat()),
            ('Seasons',              ', '.join(seasons)),
            ('Brands',               ', '.join(resolved_brands) if resolved_brands else '(all brands — no filter)'),
            ('Total rows',           len(df)),
            ('Total imported_qty',   int(df['imported_qty'].sum()) if 'imported_qty' in df else 0),
            ('Total sold_qty',       int(df['sold_qty'].sum()) if 'sold_qty' in df else 0),
            ('Total on_hand_qty',    int(df['on_hand_qty'].sum()) if 'on_hand_qty' in df else 0),
            ('Total units_matched',  int(df['units_matched'].sum()) if 'units_matched' in df else 0),
        ]
        pd.DataFrame(meta_rows, columns=['Field', 'Value']).to_excel(
            writer, sheet_name='Metadata', index=False,
        )

        # Light formatting: freeze header row, auto-width columns on the data sheet
        ws = writer.sheets['Size Report']
        ws.freeze_panes = 'A2'
        for col_idx, col in enumerate(df.columns, start=1):
            longest = max(
                [len(str(col))] + [len(str(v)) for v in df[col].head(50)]
            )
            # openpyxl uses approximate character widths
            ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = min(longest + 2, 40)


if __name__ == '__main__':
    main()

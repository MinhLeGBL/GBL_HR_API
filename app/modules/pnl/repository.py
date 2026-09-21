"""P&L repository — Postgres only.

Unlike `periodic_report`, nothing here touches Oracle: the P&L comes from the
FAST accounting system by upload, not from Retail Pro. One connection per call,
always closed in `finally`.

Amounts are NUMERIC and come back as `Decimal`. That matters for the same
reason it does in the weekly report: these figures are summed across thousands
of rows and compared against an accounting system to the đồng, and float64
drifts over that many additions.
"""
import json
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from app.core.database import get_postgres_connection

_FACT_COLUMNS = ('import_id, year, month, entity, entity_code, '
                 'category, fast_account, amount')


class PNLRepository:

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------
    def init_tables(self) -> Dict[str, Any]:
        """Create the three `pnl_*` tables. Idempotent."""
        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to database'}
        try:
            with conn.cursor() as cur:
                # One row per accepted upload. `total_check` records where the
                # file's own Total column disagreed with the sum of its stores
                # — kept because it is evidence about the SOURCE, not about us:
                # the July 2026 export's Total omits a whole store.
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS pnl_imports (
                        id           SERIAL PRIMARY KEY,
                        filename     TEXT NOT NULL,
                        sheet_name   TEXT,
                        layout       TEXT NOT NULL,
                        uploaded_by  TEXT,
                        uploaded_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        months       JSONB NOT NULL,
                        entities     JSONB NOT NULL,
                        fact_count   INTEGER NOT NULL,
                        total_check  JSONB,
                        replaced     JSONB
                    )
                ''')
                # The grain is (month, store, account). `Total` is never a row
                # here — company figures are summed from the stores, so a store
                # missing from the source's Total column is still counted.
                # NUMERIC without a scale cap: exact decimal, no rounding
                # decision baked into storage.
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS pnl_facts (
                        id           BIGSERIAL PRIMARY KEY,
                        import_id    INTEGER NOT NULL
                                     REFERENCES pnl_imports(id) ON DELETE CASCADE,
                        year         SMALLINT NOT NULL,
                        month        SMALLINT NOT NULL,
                        entity       TEXT NOT NULL,
                        entity_code  TEXT,
                        category     TEXT NOT NULL,
                        fast_account TEXT NOT NULL,
                        amount       NUMERIC NOT NULL
                    )
                ''')
                # Every statement read filters on (year, month), and a re-import
                # deletes by (year, month) before inserting.
                cur.execute('''CREATE INDEX IF NOT EXISTS idx_pnl_facts_period
                               ON pnl_facts (year, month)''')
                cur.execute('''CREATE INDEX IF NOT EXISTS idx_pnl_facts_import
                               ON pnl_facts (import_id)''')
                # A closed month. Accounting closes its books; this is the
                # tool's equivalent, so a later upload cannot quietly restate a
                # month someone has already reported on.
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS pnl_month_locks (
                        year      SMALLINT NOT NULL,
                        month     SMALLINT NOT NULL,
                        locked_by TEXT,
                        locked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (year, month)
                    )
                ''')
            conn.commit()
            return {'success': True,
                    'tables': ['pnl_imports', 'pnl_facts', 'pnl_month_locks']}
        except Exception as e:                                # noqa: BLE001
            conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    def replace_months(self, *, filename: str, sheet_name: Optional[str],
                       layout: str, uploaded_by: Optional[str],
                       months: Sequence, entities: Sequence,
                       total_check: Sequence, facts: Sequence) -> Dict[str, Any]:
        """Store an import, replacing every month it covers.

        ONE transaction. Either the whole upload lands or the database is
        untouched — a partial import would leave the stored P&L in a state that
        is neither the old figures nor the new file, and nobody could tell
        which months were which.

        Replacement is by (year, month): re-uploading a corrected month fixes
        that month and leaves every other month alone.
        """
        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to database'}
        try:
            with conn.cursor() as cur:
                cur.execute(
                    '''INSERT INTO pnl_imports
                         (filename, sheet_name, layout, uploaded_by,
                          months, entities, fact_count, total_check, replaced)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       RETURNING id''',
                    (filename, sheet_name, layout, uploaded_by,
                     json.dumps([list(m) for m in months]),
                     json.dumps(list(entities)), len(facts),
                     json.dumps(list(total_check), default=str), None))
                import_id = cur.fetchone()[0]

                replaced = []
                for year, month in months:
                    cur.execute(
                        'DELETE FROM pnl_facts WHERE year = %s AND month = %s',
                        (year, month))
                    if cur.rowcount:
                        replaced.append({'year': year, 'month': month,
                                         'rows': cur.rowcount})

                if facts:
                    cur.executemany(
                        f'INSERT INTO pnl_facts ({_FACT_COLUMNS}) '
                        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
                        [(import_id, f['year'], f['month'], f['entity'],
                          f.get('entity_code'), f['category'],
                          f['fast_account'], f['amount']) for f in facts])

                cur.execute('UPDATE pnl_imports SET replaced = %s WHERE id = %s',
                            (json.dumps(replaced), import_id))
            conn.commit()
            return {'success': True, 'import_id': import_id,
                    'replaced': replaced}
        except Exception as e:                                # noqa: BLE001
            conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def fetch_facts(self, years: Sequence[int], first_month: int,
                    last_month: int, entity: Optional[str] = None) -> List[Dict]:
        """Facts for the given years, within a month window.

        Both the current and prior year are read in one query — a statement
        always shows the comparison, so two round trips would be wasted.
        """
        conn = get_postgres_connection()
        if conn is None:
            return []
        try:
            sql = ('SELECT year, month, entity, entity_code, category, '
                   'fast_account, amount FROM pnl_facts '
                   'WHERE year = ANY(%s) AND month BETWEEN %s AND %s')
            params: List[Any] = [list(years), first_month, last_month]
            if entity is not None:
                sql += ' AND entity = %s'
                params.append(entity)
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
            return [{'year': r[0], 'month': r[1], 'entity': r[2],
                     'entity_code': r[3], 'category': r[4],
                     'fast_account': r[5],
                     'amount': r[6] if isinstance(r[6], Decimal)
                     else Decimal(str(r[6]))}
                    for r in rows]
        finally:
            conn.close()

    def stored_months(self) -> List[Dict[str, Any]]:
        """Every month that has data, with its row count and lock state."""
        conn = get_postgres_connection()
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    SELECT f.year, f.month, COUNT(*), COUNT(DISTINCT f.entity),
                           l.locked_at, l.locked_by
                      FROM pnl_facts f
                      LEFT JOIN pnl_month_locks l
                             ON l.year = f.year AND l.month = f.month
                     GROUP BY f.year, f.month, l.locked_at, l.locked_by
                     ORDER BY f.year, f.month
                ''')
                rows = cur.fetchall()
            return [{'year': r[0], 'month': r[1], 'fact_count': r[2],
                     'entity_count': r[3], 'locked': r[4] is not None,
                     'locked_at': r[4].isoformat() if r[4] else None,
                     'locked_by': r[5]} for r in rows]
        finally:
            conn.close()

    def stored_entities(self) -> List[str]:
        """Every store that appears in the stored facts, sorted.

        Drives the store picker. Taken from the facts rather than a fixed list
        so a store that opens later (RWC did) appears without a code change.
        """
        conn = get_postgres_connection()
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT DISTINCT entity FROM pnl_facts ORDER BY entity')
                return [r[0] for r in cur.fetchall()]
        finally:
            conn.close()

    def locked_months(self) -> set:
        conn = get_postgres_connection()
        if conn is None:
            return set()
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT year, month FROM pnl_month_locks')
                return {(r[0], r[1]) for r in cur.fetchall()}
        finally:
            conn.close()

    def list_imports(self, limit: int = 50) -> List[Dict[str, Any]]:
        conn = get_postgres_connection()
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    '''SELECT id, filename, sheet_name, layout, uploaded_by,
                              uploaded_at, months, entities, fact_count,
                              total_check, replaced
                         FROM pnl_imports ORDER BY uploaded_at DESC LIMIT %s''',
                    (limit,))
                rows = cur.fetchall()
            return [{'id': r[0], 'filename': r[1], 'sheet_name': r[2],
                     'layout': r[3], 'uploaded_by': r[4],
                     'uploaded_at': r[5].isoformat() if r[5] else None,
                     'months': r[6], 'entities': r[7], 'fact_count': r[8],
                     'total_check': r[9], 'replaced': r[10]} for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Locks
    # ------------------------------------------------------------------
    def set_lock(self, year: int, month: int, user: Optional[str]) -> bool:
        conn = get_postgres_connection()
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    '''INSERT INTO pnl_month_locks (year, month, locked_by)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (year, month) DO NOTHING''',
                    (year, month, user))
            conn.commit()
            return True
        except Exception:                                     # noqa: BLE001
            conn.rollback()
            return False
        finally:
            conn.close()

    def clear_lock(self, year: int, month: int) -> bool:
        conn = get_postgres_connection()
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    'DELETE FROM pnl_month_locks WHERE year = %s AND month = %s',
                    (year, month))
            conn.commit()
            return True
        except Exception:                                     # noqa: BLE001
            conn.rollback()
            return False
        finally:
            conn.close()

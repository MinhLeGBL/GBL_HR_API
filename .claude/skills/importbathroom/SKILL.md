---
name: importbathroom
description: Check bathroom data import status and prompt user for next import action (brand/collection import, price update, reimport)
---

Look up the current bathroom product import status and ask the user what they want to do next.

## Steps

### 1. Read the import status memory file

Read the memory file at:
```
/Users/minhle/.claude/projects/-Users-minhle-Desktop-GBL-HR-API/memory/bathroom_paffoni_status.md
```

This contains the current state of all bathroom product imports — which collections are done, which are pending, and any notes about remaining work.

### 2. Query the database for current counts

Run a Python script to get live counts from the database:

```python
import sys
sys.path.insert(0, '.')
from app.core.database.connection import get_postgres_connection

conn = get_postgres_connection()
cur = conn.cursor()

# Total by brand
cur.execute("SELECT brand, COUNT(*), COUNT(*) FILTER (WHERE is_active) FROM bathroom_products GROUP BY brand ORDER BY brand")
print("Products by brand:")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]} total, {row[2]} active")

# Paffoni sections
cur.execute("SELECT section, COUNT(*) FROM bathroom_products WHERE brand='paffoni' GROUP BY section ORDER BY section")
print("\nPaffoni sections:")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")

cur.close()
conn.close()
```

### 3. Present status summary to the user

Combine the memory file info and live DB counts into a concise summary:
- Total products per brand
- Which collections are complete
- Which collections are pending or partially done
- Any notes about upcoming work (e.g., Kitchen collection)

### 4. Ask the user what they want to do

Present these options:
1. **Import a new collection** — specify brand and collection name, provide source file (PDF/Excel)
2. **Update prices** — reimport prices from a catalog for existing products
3. **Reimport from source** — re-extract a collection from its source PDF/Excel (e.g., if corrections needed)
4. **Add a new brand** — start importing a completely new brand
5. **Check specific collection status** — drill into a single collection's details

Wait for the user's choice before proceeding. Do NOT start any import automatically.

### 5. Source files reference

- **Paffoni catalog:** `document/template/LISTINO 21.pdf` (LISTINO 21, 658 pages)
- **Extraction method:** pdftotext for bulk data + 200 DPI image reading for verification
- **Import workflow:** Extract → JSON in /tmp/ → compare against DB → INSERT new / UPDATE changed prices
- **Normalization rules:** documented in the memory file (space removal, `..` finish substitution, dash preservation)

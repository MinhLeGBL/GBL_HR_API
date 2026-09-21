"""FAST chart-of-accounts → P&L category mapping.

Seeded from `Runway_Company Overview H1 2026 vs 25 NEW v2.xlsx`, the Excel the
P&L was produced in before this module existed. Column A of its `Inputs Fast`
sheet carried this mapping by hand; the tool owns it now, so a typo in an
uploaded file can no longer silently move cost between P&L lines.

Keyed on (SECTION, LABEL), never on the label alone
---------------------------------------------------
Twelve labels appear twice in a FAST P&L and mean different things depending on
which numbered section they sit under:

    '- Lương cơ bản' under '8. Chi phí bán hàng'            -> O/W Wages   (store)
    '- Lương cơ bản' under '9. Chi phí quản lý doanh nghiệp' -> G&A payroll

Keying on the label alone would post store base salary into G&A payroll, moving
cost between `Total Store Direct Operating Costs` and `General & Administrative`
and so changing `Store operating profit` — a silent misstatement. The section is
the most recent numbered heading above the row, which is why the importer
requires the FAST export in statement order.

EXCLUDED is not "unmapped"
--------------------------
A value of None means the account is deliberately NOT posted: FAST's own
subtotals (`3. Doanh thu thuần`, `5. Lợi nhuận gộp`, `8. Chi phí bán hàng`, …)
and "of which" breakdowns (`- Trong đó: Chi phí lãi vay`), all of which would
double-count. An account in neither this table nor the export is UNKNOWN, and
the importer rejects the file rather than dropping it — the spreadsheet had no
such check, and a missing mapping there is currently hiding 123,798,726 of
field travel.

Labels are normalised with `normalise()` before lookup: NFC, whitespace
collapsed, trimmed. Keep the keys below in that same normalised form.
"""
import re
import unicodedata

# (section, label) -> P&L category, or None for a deliberate exclusion.
FAST_ACCOUNTS = {
    ('1. Doanh thu bán hàng và cung cấp dịch vụ',
     '1. Doanh thu bán hàng và cung cấp dịch vụ'): 'Gross sales',
    ('2. Các khoản giảm trừ doanh thu',
     '2. Các khoản giảm trừ doanh thu'): 'Discounts',
    ('3. Doanh thu thuần về bán hàng và cung cấp dịch vụ (10=01- 02)',
     '3. Doanh thu thuần về bán hàng và cung cấp dịch vụ (10=01- 02)'): None,
    ('4. Giá vốn hàng bán',
     '4. Giá vốn hàng bán'): 'Cost of sales',
    ('5. Lợi nhuận gộp về bán hàng và cung cấp dịch vụ (20=10-11)',
     '5. Lợi nhuận gộp về bán hàng và cung cấp dịch vụ (20=10-11)'): None,
    ('6. Doanh thu hoạt động tài chính',
     '6. Doanh thu hoạt động tài chính'): 'Financial profit',
    ('7. Chi phí tài chính',
     '7. Chi phí tài chính'): 'Financial loss',
    ('7. Chi phí tài chính',
     '- Trong đó: Chi phí lãi vay'): None,
    ('8. Chi phí bán hàng',
     '8. Chi phí bán hàng'): None,
    ('8. Chi phí bán hàng',
     '- Lương cơ bản'): 'O/W Wages',
    ('8. Chi phí bán hàng',
     '- Chi phí phụ cấp, trợ cấp'): 'O/W Wages',
    ('8. Chi phí bán hàng',
     '- Chi phí thưởng KPI, Tháng 13'): 'O/W Wages',
    ('8. Chi phí bán hàng',
     '- Chi phí lương ngoài giờ'): 'O/W Wages',
    ('8. Chi phí bán hàng',
     '- Chi phí thưởng doanh số'): 'O/W Commissions',
    ('8. Chi phí bán hàng',
     '- Chi phí BHXH, YT, TN và KPCĐ'): 'O/W Wages',
    ('8. Chi phí bán hàng',
     '- Chi phí đồng phục'): 'Other selling',
    ('8. Chi phí bán hàng',
     '- Chi phí đạo tào'): None,
    ('8. Chi phí bán hàng',
     '- Chi phí khác cho nhân viên'): 'Other selling',
    ('8. Chi phí bán hàng',
     '- Chi phí vật liệu, bao bì'): 'Other selling',
    ('8. Chi phí bán hàng',
     '- Chi phí dụng cụ, đồ dùng văn phòng phẩm'): 'Other selling',
    ('8. Chi phí bán hàng',
     '- Chi phí vệ sinh cửa hàng'): 'Other selling',
    ('8. Chi phí bán hàng',
     '- Chi phí điện thoại, tin nhắn, internet'): 'Other selling',
    ('8. Chi phí bán hàng',
     '- Chi phí chuyển phát và vận chuyển hàng'): 'Logistics',
    ('8. Chi phí bán hàng',
     '- Chi phí thuê mặt bằng, phí dịch vụ'): 'Fixed rent',
    ('8. Chi phí bán hàng',
     '- Chi phí tiền điện, nước sinh hoạt'): 'Other selling',
    ('8. Chi phí bán hàng',
     '- Chi phí quảng cáo marketing'): 'Marketing and training fees',
    ('8. Chi phí bán hàng',
     '- Chi phí đẩy mạnh việc bán hàng (promotion)'): 'Obsolescence',
    ('8. Chi phí bán hàng',
     '- Chi phí event'): 'Obsolescence',
    ('8. Chi phí bán hàng',
     '- Chi phí cà thẻ'): 'Other selling',
    ('8. Chi phí bán hàng',
     '- Chi phí sửa sản phẩm, hàng hóa'): 'After sales',
    ('8. Chi phí bán hàng',
     '- Chi phí công tác trong nước'): 'Field - Training and travel',
    ('8. Chi phí bán hàng',
     '- Chi phí công tác nước ngoài'): 'Field - Training and travel',
    ('8. Chi phí bán hàng',
     '- Chi phí tiếp khách'): 'Other selling',
    ('8. Chi phí bán hàng',
     '- Chi phí hoa, quà tặng, chiết khấu khác cho khách hàng'): 'Other selling',
    ('8. Chi phí bán hàng',
     '- Chi phí thiệt hại do hàng mất, hàng hỏng'): 'Obsolescence',
    ('8. Chi phí bán hàng',
     '- Chi phí khấu hao TSCĐ'): 'Store D&A',
    ('8. Chi phí bán hàng',
     '- Chi phí khấu hao CCDC'): 'Store D&A',
    ('8. Chi phí bán hàng',
     '- Chi phí bằng tiền khác'): 'Other selling',
    ('8. Chi phí bán hàng',
     '- Chi phí bảo hành'): 'After sales',
    ('8. Chi phí bán hàng',
     '- Chi phí dự phòng'): 'Other selling',
    ('9. Chi phí quản lý doanh nghiệp',
     '9. Chi phí quản lý doanh nghiệp'): None,
    ('9. Chi phí quản lý doanh nghiệp',
     '- Lương cơ bản'): 'G&A payroll',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí phụ cấp, trợ cấp'): 'G&A payroll',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí thưởng KPI, Tháng 13'): 'G&A payroll',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí lương ngoài giờ'): 'G&A payroll',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí thưởng doanh số'): 'G&A payroll',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí BHXH, YT, TN và KPCĐ'): 'G&A payroll',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí đồng phục'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí đạo tạo'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí khác cho nhân viên'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí vật liệu bao bì quản lý'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí dụng cụ, đồ dùng văn phòng phẩm'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí vệ sinh văn phòng'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí điện thoại, tin nhắn, internet'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí nước uống cho văn phòng'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí thuê văn phòng, phí dịch vụ'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí tiền điện, nước sinh hoạt'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí chuyển phát, vận chuyển hàng'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí ngân hàng'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí taxi, đi lại, gửi xe'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí tiếp khách'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí quà tặng'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí tư vấn luật, kế toán tài chính'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí tuyển dụng'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí dịch vụ mua ngoài khác'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí công tác trong nước'): 'Field - Training and travel',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí công tác nước ngoài'): 'Field - Training and travel',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí bằng tiền khác'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí khấu hao TSCĐ'): 'Store D&A',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí khấu hao CCDC'): 'Store D&A',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí bảo hiểm tài sản'): 'G&A expenses',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Thuế, phí và lệ phí'): 'Tax',
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí dự phòng'): 'G&A expenses',
    ('10. Lợi nhuận thuần từ hoạt động kinh doanh {30=20+(21-22)-(25+26)}',
     '10. Lợi nhuận thuần từ hoạt động kinh doanh {30=20+(21-22)-(25+26)}'): None,
    ('11. Thu nhập khác',
     '11. Thu nhập khác'): None,
    ('12. Chi phí khác',
     '12. Chi phí khác'): None,
    ('13. Lợi nhuận khác (40=31-32)',
     '13. Lợi nhuận khác (40=31-32)'): 'Others fees/incomes',
    ('14. Tổng lợi nhuận kế toán trước thuế (50=30+40)',
     '14. Tổng lợi nhuận kế toán trước thuế (50=30+40)'): None,
    ('15. Chi phí thuế TNDN hiện hành',
     '15. Chi phí thuế TNDN hiện hành'): None,
    ('16. Chi phí thuế TNDN hoãn lại',
     '16. Chi phí thuế TNDN hoãn lại'): None,
    ('17. Lợi nhuận sau thuế thu nhập doanh nghiệp (60=50-51-52)',
     '17. Lợi nhuận sau thuế thu nhập doanh nghiệp (60=50-51-52)'): None,
    ('17. Lợi nhuận sau thuế thu nhập doanh nghiệp (60=50-51-52)',
     'Marketing payroll'): 'Marketing payroll',
    # --- Added 2026-09-19 from the first real FAST export (PL thang 7.2026) ---
    # The 2025-26 history sheet and the live export word six accounts
    # differently. Each is mapped to whatever its older equivalent mapped to,
    # listed beside it. `- Chi phí khấu hao TSCD` differs from the historical
    # `- Chi phí khấu hao TSCĐ` by ONE diacritic (D vs Đ) — normalise()
    # deliberately preserves diacritics, so these are genuinely distinct keys
    # and both must be present.
    ('8. Chi phí bán hàng',
     '- Chi phí chuyển phát và vận chuyển hàng bộ phận bán hàng'): 'Logistics',
    #   was '- Chi phí chuyển phát và vận chuyển hàng' -> Logistics
    ('8. Chi phí bán hàng',
     '- Chi phí tiền điện, nước sinh hoạt cho cửa hàng'): 'Other selling',
    #   was '- Chi phí tiền điện, nước sinh hoạt' -> Other selling
    ('8. Chi phí bán hàng',
     '- Chi phí khấu hao TSCD'): 'Store D&A',
    #   was '- Chi phí khấu hao TSCĐ' -> Store D&A
    ('8. Chi phí bán hàng',
     '- Chi phí khác'): 'Other selling',
    #   nearest: '- Chi phí bằng tiền khác' -> Other selling
    ('8. Chi phí bán hàng',
     '- Chi phí dự phòng bộ phận bán hàng'): 'Other selling',
    #   was '- Chi phí dự phòng' -> Other selling
    ('9. Chi phí quản lý doanh nghiệp',
     '- Chi phí cho xe'): 'G&A expenses',
    #   nearest: other G&A running costs -> G&A expenses
}


def normalise(text) -> str:
    """NFC, whitespace collapsed, trimmed — how account text is compared.

    FAST exports vary in trailing spaces and in whether Vietnamese diacritics
    arrive composed or decomposed; both would otherwise miss the lookup.
    Diacritics are PRESERVED: stripping them would collide distinct accounts.
    """
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', str(text))).strip()


def is_section_heading(label: str) -> bool:
    """A numbered FAST heading, e.g. '8. Chi phí bán hàng'."""
    return bool(re.match(r'^\d+\.', label))


# Categories the P&L statement consumes. Derived rather than hand-listed so it
# cannot drift from the table above.
CATEGORIES = frozenset(c for c in FAST_ACCOUNTS.values() if c is not None)

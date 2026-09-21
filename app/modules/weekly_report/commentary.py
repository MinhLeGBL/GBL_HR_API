"""LLM commentary for the weekly report email.

Fills the `{{commentary}}` slot with the three-part Vietnamese analysis the
email template is built around: the latest week, month to date, then year to
date. Everything around it — greeting, the sentence naming the three parts, the
sign-off — is fixed template text.

Best-effort by design. `service.draft_email` catches everything this raises and
leaves the placeholder empty, because a missing analysis is a much better Monday
than no report at all. The draft is reviewed and editable before anything is
sent, so a weak paragraph costs an edit, not a wrong email.

Arithmetic policy
-----------------
The model is given figures and a block of PRE-COMPUTED derived facts, and is
told not to calculate anything new. Every ratio the house style uses — a week's
share of the month's returns, a store's discount-rate movement in percentage
points — is worked out here in Python. An LLM asked to divide 375 by 489 in
prose will usually be right and occasionally not, and this email goes to the
management board.

Configuration (all optional):
  ANTHROPIC_API_KEY                      required; absent → commentary skipped
  WEEKLY_REPORT_MODEL                  model id (default: claude-opus-5)
  WEEKLY_REPORT_COMMENTARY_MAX_WORDS   length target (default: 600)
"""
import os

# COMMENTARY_LABELS, not PERIOD_LABELS: WTD is a figures-only view and is
# deliberately kept out of the analysis.
from .period import COMMENTARY_LABELS

DEFAULT_MODEL = 'claude-opus-5'
DEFAULT_MAX_WORDS = 400

# Vietnamese names for the figures, so the model does not have to invent
# terminology or echo internal keys into the email.
METRIC_VI = {
    'total_sales':           'Doanh thu',
    'qty_sold':              'Sản lượng',
    'bills':                 'Số hóa đơn',
    'avg_unit_price':        'Giá bán trung bình mỗi sản phẩm',
    'avg_transaction_value': 'Giá trị trung bình mỗi hóa đơn',
    'avg_discount_pct':      'Tỷ lệ chiết khấu',
    'returns_value':         'Giá trị hàng trả lại',
}

# How each figure is rendered for the model — the SAME unit the email declares
# ("Đơn vị tiền: triệu đồng"), so a quoted number needs no conversion.
_MONEY_0 = ('total_sales', 'returns_value')
_MONEY_1 = ('avg_unit_price', 'avg_transaction_value')
_COUNTS = ('qty_sold', 'bills')

PERIOD_VI = {
    'WOW': 'TUẦN GẦN NHẤT',
    'MTD': 'LŨY KẾ THÁNG',
    'YTD': 'LŨY KẾ TỪ ĐẦU NĂM',
}

# A store's discount rate moving more than this many percentage points, or its
# sales moving more than this percent, is worth naming in the analysis.
DISCOUNT_PP_THRESHOLD = 5.0
SALES_PCT_THRESHOLD = 15.0

SYSTEM_PROMPT = """\
Bạn viết phần phân tích của email báo cáo bán hàng định kỳ hàng tuần cho một \
tập đoàn bán lẻ hàng cao cấp tại Việt Nam. Người đọc là Ban Giám đốc.

NHIỆM VỤ
Viết đúng BA phần được đánh số, theo thứ tự từ tuần gần nhất đến lũy kế năm:

1. Tuần gần nhất (Tuần X so với Tuần Y)
2. Lũy kế tháng M (dd/mm–dd/mm)
3. Lũy kế từ đầu năm (dd/mm – dd/mm)

Tiêu đề phần 3 có thể thêm một mệnh đề ngắn nêu kết luận chính, ví dụ \
"— cho thấy tháng 9 đang đi ngược xu hướng cả năm", khi dữ liệu thực sự cho \
thấy điều đó.

CÁCH VIẾT — điều quan trọng nhất
Không liệt kê số liệu. Hãy GIẢI THÍCH. Với mỗi phần:
- Nêu biến động doanh thu, rồi PHÂN TÍCH NGUYÊN NHÂN bằng các cấu phần của nó \
(số hóa đơn và giá trị mỗi hóa đơn; hoặc sản lượng và giá bán trung bình). \
Ví dụ: "Doanh thu giảm 4.4%, mặc dù số hóa đơn tăng 6.6% — nguyên nhân là giá \
trị mỗi hóa đơn giảm 4.6% và sản lượng giảm 7.0%."
- Nêu rõ "Điểm tích cực:" khi có diễn biến tốt, và "Cần lưu ý:" khi có điều \
cần chú ý. Dùng đúng hai cụm từ này.
- ĐỊNH VỊ vấn đề theo thời gian bằng cách so sánh tuần với lũy kế tháng. \
Ví dụ: nếu tỷ lệ chiết khấu cả tháng cao hơn hẳn tuần gần nhất, điều đó nghĩa \
là chiết khấu cao tập trung ở đầu tháng và đã được siết lại.
- Nêu MỨC ĐỘ TẬP TRUNG khi phần "SỐ LIỆU ĐÃ TÍNH SẴN" cho thấy một tuần chiếm \
tỷ trọng lớn trong cả kỳ.
- Ở phần 3, so sánh CƠ CẤU tăng trưởng cả năm với tháng hiện tại — chúng có thể \
ngược chiều nhau, và đó thường là phát hiện đáng giá nhất.
- Nêu tên cửa hàng cụ thể khi phần "BIẾN ĐỘNG ĐÁNG CHÚ Ý" liệt kê chúng.

QUY TẮC BẮT BUỘC
- CHỈ dùng những con số được cung cấp. TUYỆT ĐỐI KHÔNG tự tính toán thêm — mọi \
tỷ lệ cần thiết đều đã có sẵn trong phần "SỐ LIỆU ĐÃ TÍNH SẴN".
- KHÔNG suy đoán nguyên nhân bên ngoài dữ liệu: không nhắc đến chương trình \
khuyến mãi, thời tiết, đối thủ, nhà cung cấp hay sự kiện nào không có trong số \
liệu. Chỉ giải thích bằng chính các cấu phần của số liệu.
- Tỷ lệ chiết khấu GIẢM là tốt; TĂNG là xấu. Hàng trả lại GIẢM là tốt; TĂNG là \
xấu. Đừng mô tả việc chiết khấu giảm như một diễn biến tiêu cực.
- Số hóa đơn theo ngành hàng là "số hóa đơn CÓ CHỨA ngành hàng đó" nên KHÔNG \
cộng lại thành tổng cửa hàng. Không bao giờ cộng chúng.
- Đơn vị tiền là TRIỆU ĐỒNG, đúng như các con số được cung cấp. Không đổi đơn vị.
- Khi một thay đổi ghi là "không xác định", nghĩa là kỳ trước bằng 0 — hãy nói \
rõ chứ đừng ghi là tăng 100%.
- Chênh lệch của một TỶ LỆ tính bằng ĐIỂM phần trăm ("tăng 15.9 điểm"), không \
phải phần trăm.

ĐỊNH DẠNG — bám sát mẫu của công ty
- Viết bằng tiếng Việt.
- Mỗi phần bắt đầu bằng một dòng tiêu đề in đậm, theo đúng mẫu:
  **1. Tuần gần nhất (Tuần 37 so với Tuần 36)**
  **2. Lũy kế tháng 9 (01–15/09)**
  **3. Lũy kế từ đầu năm (01/01 – 15/09)**
- Dưới mỗi tiêu đề là 2-4 GẠCH ĐẦU DÒNG, mỗi dòng bắt đầu bằng "- ".
- Mỗi gạch đầu dòng là một ý trọn vẹn, 1-3 câu. NGẮN GỌN.
- IN ĐẬM bằng **...** cho: nhận định chính kèm con số của nó. Ví dụ:
  "Doanh thu **3,961 so với 4,142 — giảm 4.4%**, mặc dù **số hóa đơn tăng 6.6%**
  (113 so với 106)."
- KHÔNG in đậm phần số liệu phụ trong ngoặc đơn — để chúng ở dạng thường.
- Các nhãn "Điểm tích cực:" và "Cần lưu ý:" đứng đầu gạch đầu dòng, và phần
  nhận định ngay sau đó được in đậm.
- CHỈ dùng hai ký hiệu: **...** để in đậm và "- " để gạch đầu dòng. Không dùng
  bất kỳ ký hiệu Markdown nào khác (không #, không *, không bảng).
- Chỉ trả về ba phần. KHÔNG có lời chào, KHÔNG có lời kết, KHÔNG có tiêu đề
  email — phần này được chèn vào giữa một mẫu email có sẵn.
- Nếu dữ liệu quá thưa để đưa ra nhận định, hãy nói thẳng trong một gạch đầu dòng.
"""


def _fmt(key, value):
    """Render one figure in the unit the email declares."""
    if value is None:
        return '—'
    if key in _MONEY_0:
        return f'{value / 1e6:,.0f}'
    if key in _MONEY_1:
        return f'{value / 1e6:,.1f}'
    if key in _COUNTS:
        return f'{value:,.0f}'
    if key == 'avg_discount_pct':
        return f'{value:.1f}%'
    return f'{value:,.1f}'


def _fmt_change(change):
    if not change or change.get('value') is None:
        return 'không xác định (kỳ trước bằng 0)'
    value = change['value']
    if change.get('kind') == 'pp':
        return f'{value:+.1f} điểm'
    return f'{value:+.1f}%'


def _metric_lines(row, indent='    '):
    out = []
    for key, name in METRIC_VI.items():
        cur = (row.get('current') or {}).get(key)
        pri = (row.get('prior') or {}).get(key)
        if cur is None and pri is None:
            continue
        chg = (row.get('change') or {}).get(key)
        out.append(f'{indent}{name}: {_fmt(key, cur)} '
                   f'(kỳ trước {_fmt(key, pri)}, {_fmt_change(chg)})')
    return out


def _vi_date(iso, with_year=False):
    """'2026-09-13' -> '13/09', or '13/09/2026' when the year matters."""
    try:
        y, m, d = iso.split('-')
        return f'{d}/{m}/{y}' if with_year else f'{d}/{m}'
    except (AttributeError, ValueError):
        return iso or '?'


def _vi_week(label):
    """'Week 37 2026' -> 'Tuần 37'. The figures block is Vietnamese; an English
    label inside it invites the model to echo it into the email."""
    if not label:
        return ''
    parts = str(label).split()
    return f'Tuần {parts[1]}' if len(parts) >= 2 else str(label)


def _share(part, whole):
    """`part` as a percent of `whole`, or None when it cannot be stated."""
    if part is None or whole in (None, 0):
        return None
    return 100.0 * part / whole


def derived_facts(payload):
    """Cross-period ratios the house style uses, computed here rather than by
    the model.

    These are exactly the numbers that are easy to state and easy to get wrong:
    what share of the month's returns landed in the reported week, how the
    week's discount rate sits against the month's. Handing them over
    pre-computed removes the model's need to do arithmetic in prose.
    """
    facts = []
    periods = payload.get('periods') or {}
    wow = (periods.get('WOW') or {}).get('total') or {}
    mtd = (periods.get('MTD') or {}).get('total') or {}
    if not wow or not mtd:
        return facts

    wow_cur = wow.get('current') or {}
    mtd_cur = mtd.get('current') or {}

    for key, label in (('returns_value', 'hàng trả lại'),
                       ('total_sales', 'doanh thu')):
        share = _share(wow_cur.get(key), mtd_cur.get(key))
        if share is not None:
            facts.append(
                f'  - {label.capitalize()} của tuần gần nhất '
                f'({_fmt(key, wow_cur.get(key))}) chiếm {share:.1f}% '
                f'{label} lũy kế tháng ({_fmt(key, mtd_cur.get(key))}).')

    w_disc = wow_cur.get('avg_discount_pct')
    m_disc = mtd_cur.get('avg_discount_pct')
    if w_disc is not None and m_disc is not None:
        gap = w_disc - m_disc
        direction = ('thấp hơn' if gap < 0 else 'cao hơn')
        facts.append(
            f'  - Tỷ lệ chiết khấu tuần gần nhất ({w_disc:.1f}%) {direction} '
            f'tỷ lệ chiết khấu lũy kế tháng ({m_disc:.1f}%) '
            f'{abs(gap):.1f} điểm.')
    return facts


def notable_movements(payload):
    """Store-level moves worth naming — the 'fluctuation to pay attention to'.

    Thresholds rather than "the biggest": in a quiet week nothing should be
    flagged, and a forced top-3 would manufacture significance.
    """
    out = []
    names = payload.get('store_names') or {}
    for label in COMMENTARY_LABELS:
        period = (payload.get('periods') or {}).get(label) or {}
        rows = []
        for store in period.get('stores') or []:
            code = store.get('store')
            change = store.get('change') or {}
            disc = (change.get('avg_discount_pct') or {}).get('value')
            sales = (change.get('total_sales') or {}).get('value')
            cur_disc = (store.get('current') or {}).get('avg_discount_pct')
            pri_disc = (store.get('prior') or {}).get('avg_discount_pct')

            if disc is not None and abs(disc) >= DISCOUNT_PP_THRESHOLD:
                rows.append(
                    f'    - {code} ({names.get(code, code)}): tỷ lệ chiết khấu '
                    f'{pri_disc:.1f}% → {cur_disc:.1f}% ({disc:+.1f} điểm)'
                    + (f', doanh thu {sales:+.1f}%' if sales is not None else ''))
            elif sales is not None and abs(sales) >= SALES_PCT_THRESHOLD:
                rows.append(
                    f'    - {code} ({names.get(code, code)}): '
                    f'doanh thu {sales:+.1f}%')
        if rows:
            out.append(f'  {PERIOD_VI[label]}:')
            out.extend(rows)
    return out


def build_prompt(payload, max_words: int = DEFAULT_MAX_WORDS) -> str:
    """Render the snapshot into the figures block the model reads."""
    names = payload.get('store_names') or {}
    periods = payload.get('periods') or {}
    out: list = []

    for label in COMMENTARY_LABELS:
        period = periods.get(label)
        if not period:
            continue
        # Tolerate a partial period rather than raising: commentary is
        # best-effort, and a payload missing one block should still produce an
        # analysis of the blocks it does have.
        cur = period.get('current') or {}
        pri = period.get('prior') or {}
        if not cur.get('from') or not period.get('total'):
            continue
        header = f'{PERIOD_VI[label]}: '
        if label == 'WOW':
            header += (f"{_vi_week(cur.get('label'))} "
                       f"({_vi_date(cur.get('from'))}–{_vi_date(cur.get('to'))})"
                       f" so với {_vi_week(pri.get('label'))} "
                       f"({_vi_date(pri.get('from'))}–{_vi_date(pri.get('to'))})")
        else:
            # The prior side is a DIFFERENT YEAR, so it carries the year —
            # without it both ranges read "01/09–13/09" and look identical.
            header += (f"{_vi_date(cur.get('from'), True)}–"
                       f"{_vi_date(cur.get('to'), True)} so với cùng kỳ năm "
                       f"trước {_vi_date(pri.get('from'), True)}–"
                       f"{_vi_date(pri.get('to'), True)}")
        out.append(header)
        out.append('  TOÀN HỆ THỐNG:')
        out.extend(_metric_lines(period.get('total') or {}))

        for store in period.get('stores') or []:
            code = store.get('store')
            out.append(f'  {code} ({names.get(code, code)}):')
            out.extend(_metric_lines(store, indent='      '))
        out.append('')

    facts = derived_facts(payload)
    if facts:
        out.append('SỐ LIỆU ĐÃ TÍNH SẴN (dùng trực tiếp, KHÔNG tự tính lại):')
        out.extend(facts)
        out.append('')

    movements = notable_movements(payload)
    if movements:
        out.append('BIẾN ĐỘNG ĐÁNG CHÚ Ý (cửa hàng vượt ngưỡng theo dõi):')
        out.extend(movements)
        out.append('')
    else:
        out.append('BIẾN ĐỘNG ĐÁNG CHÚ Ý: không có cửa hàng nào vượt ngưỡng '
                   'theo dõi trong kỳ này.')
        out.append('')

    out.append(f'Viết phần phân tích trong khoảng {max_words} từ.')
    return '\n'.join(out)


def generate_commentary(payload, model: str = None,
                        max_words: int = None) -> str:
    """Return the three-part analysis, or '' when not configured.

    Raises on an API failure so the caller can log it; the caller treats that as
    "no commentary" rather than a failed run.
    """
    if not os.getenv('ANTHROPIC_API_KEY'):
        return ''

    import anthropic

    model = model or os.getenv('WEEKLY_REPORT_MODEL') or DEFAULT_MODEL
    if max_words is None:
        max_words = int(os.getenv('WEEKLY_REPORT_COMMENTARY_MAX_WORDS',
                                  DEFAULT_MAX_WORDS))

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        thinking={'type': 'adaptive'},
        messages=[{'role': 'user', 'content': build_prompt(payload, max_words)}],
    )

    # A safety decline is not an exception — it returns 200 with no text block.
    if response.stop_reason == 'refusal':
        raise RuntimeError('Commentary request was declined by the model')

    # Bold and bullets are kept — `render.py` turns them into the HTML the
    # email is sent as, and into the plain-text alternative.
    return '\n\n'.join(b.text.strip() for b in response.content
                        if b.type == 'text' and b.text.strip())

"""Deterministic pseudonyms for the development database.

THE ONE PROPERTY THAT MATTERS: the same input always yields the same output,
within and across tables. A customer named in `CUSTOMER.FIRST_NAME` and again
in 40 rows of `DOCUMENT.BT_FIRST_NAME` must become the SAME pseudonym in all 41
places, because CRM joins them and counts them.

Faking row-by-row instead would split one customer into 41 strangers. Every
figure would still compute, and RFM segmentation would silently become noise —
a dev database that looks fine and cannot be trusted is worse than no dev
database, because you believe it.

It is a one-way mapping, not encryption: there is no key that turns a pseudonym
back into a person.

DISTINCT PEOPLE MUST STAY DISTINCT, and that took measuring rather than
assuming. The name pools below give 16 x 12 x 28 = 5,376 combinations; against
6,410 customers the birthday problem collapses that to ~3,750 pseudonyms — 41%
of the customer base silently merged, and every CRM segment count wrong by a
similar margin. Names therefore carry a four-digit discriminator, taking the
space to ~54 million and expected collisions to well under one. The digits also
make a pseudonym unmistakable for a real name, which is worth having.

NULL stays NULL and empty stays empty. Both are meaningful — "no phone number
recorded" is a fact about the record, and replacing it with a plausible fake
would make the dev data cleaner than production and hide exactly the
null-handling bugs this database exists to catch.
"""
import hashlib
import re

# Common Vietnamese given and family names — the output should look like the
# input so that display bugs (encoding, column width, sorting on diacritics)
# still reproduce locally.
_FAMILY = ('Nguyễn', 'Trần', 'Lê', 'Phạm', 'Hoàng', 'Huỳnh', 'Phan', 'Vũ',
           'Võ', 'Đặng', 'Bùi', 'Đỗ', 'Hồ', 'Ngô', 'Dương', 'Lý')
_MIDDLE = ('Thị', 'Văn', 'Hữu', 'Minh', 'Ngọc', 'Thanh', 'Quang', 'Đức',
           'Anh', 'Thu', 'Hải', 'Xuân')
_GIVEN = ('An', 'Bình', 'Chi', 'Dung', 'Giang', 'Hà', 'Hạnh', 'Hiếu', 'Hoa',
          'Hương', 'Khanh', 'Lan', 'Linh', 'Mai', 'Nam', 'Nga', 'Nhung',
          'Phúc', 'Quân', 'Quỳnh', 'Sơn', 'Tâm', 'Thảo', 'Trang', 'Trung',
          'Tuấn', 'Vy', 'Yến')

# Vietnamese mobile prefixes, so a number still looks like a number to any code
# that inspects or formats one.
_PHONE_PREFIX = ('032', '033', '034', '035', '036', '037', '038', '039',
                 '070', '076', '077', '078', '079', '081', '082', '083',
                 '084', '085', '086', '088', '089', '090', '091', '094')

_EMAIL_DOMAIN = ('example.com', 'example.net', 'example.org')

DEFAULT_SALT = 'gbl-dev-2026'


def _digest(value: str, salt: str, tag: str) -> int:
    h = hashlib.sha256(f'{salt}|{tag}|{value}'.encode('utf-8')).digest()
    return int.from_bytes(h[:8], 'big')


def _pick(seq, value, salt, tag, shift=0):
    return seq[(_digest(value, salt, tag) >> (shift * 8)) % len(seq)]


def fake_name(value, salt=DEFAULT_SALT):
    """A full Vietnamese-looking name, stable for a given input."""
    if not value or not str(value).strip():
        return value
    v = str(value)
    # The trailing digits are not decoration — see DISTINCT PEOPLE above.
    return (f'{_pick(_FAMILY, v, salt, "fam")} '
            f'{_pick(_MIDDLE, v, salt, "mid", 1)} '
            f'{_pick(_GIVEN, v, salt, "giv", 2)} '
            f'{_digest(v, salt, "disc") % 10_000:04d}')


def fake_phone(value, salt=DEFAULT_SALT):
    """A well-formed Vietnamese mobile number, stable for a given input.

    Length is preserved where the original is a plain 10-digit mobile, because
    some report code slices and formats these.
    """
    if not value or not str(value).strip():
        return value
    v = str(value)
    body = f'{_digest(v, salt, "tel") % 10_000_000:07d}'
    return f'{_pick(_PHONE_PREFIX, v, salt, "pfx")}{body}'


def fake_email(value, salt=DEFAULT_SALT):
    """An address on a reserved example.* domain — it cannot reach anyone.

    Deliberately RFC 2606 domains: if a dev database is ever pointed at a live
    mailer by accident, every message bounces instead of reaching a customer.
    """
    if not value or not str(value).strip():
        return value
    v = str(value)
    return (f'user{_digest(v, salt, "eml") % 1_000_000_000:09d}@'
            f'{_pick(_EMAIL_DOMAIN, v, salt, "dom")}')


def fake_address(value, salt=DEFAULT_SALT):
    if not value or not str(value).strip():
        return value
    v = str(value)
    streets = ('Lê Lợi', 'Nguyễn Huệ', 'Hai Bà Trưng', 'Trần Hưng Đạo',
               'Lý Tự Trọng', 'Pasteur', 'Đồng Khởi', 'Nam Kỳ Khởi Nghĩa')
    wards = ('Quận 1', 'Quận 3', 'Quận 7', 'Bình Thạnh', 'Phú Nhuận',
             'Hoàn Kiếm', 'Ba Đình', 'Cầu Giấy')
    return (f'{_digest(v, salt, "num") % 400 + 1}/{_digest(v, salt, "sub") % 100} '
            f'{_pick(streets, v, salt, "st")}, {_pick(wards, v, salt, "wd", 1)}')


def fake_text(value, salt=DEFAULT_SALT):
    """Last resort for a free-text column that may hold anything."""
    if not value or not str(value).strip():
        return value
    return f'redacted-{_digest(str(value), salt, "txt") % 1_000_000:06d}'


_EMAILISH = re.compile(r'^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$')
_PHONEISH = re.compile(r'^[+()\d][\d\s().-]{7,19}$')


# Chosen by column NAME first, so a column added to the spec later gets
# sensible treatment without another edit here.
def strategy_for(column: str):
    c = column.upper()
    if 'PHONE' in c or c.endswith('_TEL') or c == 'TEL':
        return fake_phone
    if 'EMAIL' in c:
        return fake_email
    if 'ADDRESS' in c or 'ADDR' in c:
        return fake_address
    if 'NAME' in c or 'USERNAME' in c:
        return fake_name
    return None                     # no signal in the name — see below


def anonymise_value(column: str, value, salt=DEFAULT_SALT):
    """Fake one value. Handles arrays, and falls back to the value's shape.

    Two cases the column name alone gets wrong, both found by the leak scan on
    real data rather than by reading the schema:

    - **Arrays.** `periodic_report_sends.recipients` is a Postgres text[] of
      the management board's addresses. Treating it as a scalar stringifies the
      whole array into one pseudonym and destroys the column; skipping it ships
      their real addresses.
    - **Names that say nothing.** `uploaded_by`, `tagged_by`, `untagged_by` all
      hold email addresses. Nothing in those names suggests it. Falling back to
      generic redaction would work but throws away the shape, so anything that
      LOOKS like an email or a phone number is treated as one.
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return type(value)(anonymise_value(column, v, salt) for v in value)

    strategy = strategy_for(column)
    if strategy is None:
        text = str(value).strip()
        if _EMAILISH.match(text):
            strategy = fake_email
        elif _PHONEISH.match(text):
            strategy = fake_phone
        else:
            strategy = fake_text
    return strategy(value, salt)

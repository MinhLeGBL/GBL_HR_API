"""
Generate DOCX policy documents from markdown sources.
Supports: headings, bold, italic, tables, code blocks, bullet lists, numbered lists,
and highlights ***THÔNG TIN NỘI BỘ*** in red.
"""
import re
from pathlib import Path
from docx import Document
from docx.shared import Pt, Inches, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn, nsdecls
from docx.oxml import parse_xml


POLICY_DIR = Path(__file__).parent

# Map of input markdown -> output docx
FILES = {
    'chinh-sach-thuong-doanh-so-cua-hang.md': 'CHÍNH SÁCH THƯỞNG DOANH SỐ (CỬA HÀNG).docx',
    'chinh-sach-thuong-doanh-so-ca-nhan.md': 'CHÍNH SÁCH THƯỞNG DOANH SỐ (CÁ NHÂN).docx',
    'chinh-sach-thuong-dac-biet.md': 'CHÍNH SÁCH THƯỞNG ĐẶC BIỆT.docx',
}


def setup_styles(doc):
    """Configure document styles."""
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Times New Roman'
    font.size = Pt(12)
    style.paragraph_format.space_after = Pt(4)
    style.paragraph_format.space_before = Pt(2)

    for i in range(1, 6):
        hstyle = doc.styles[f'Heading {i}']
        hstyle.font.name = 'Times New Roman'
        hstyle.font.color.rgb = RGBColor(0, 0, 0)
        hstyle.paragraph_format.space_before = Pt(12 if i <= 2 else 8)
        hstyle.paragraph_format.space_after = Pt(4)
        if i == 1:
            hstyle.font.size = Pt(18)
        elif i == 2:
            hstyle.font.size = Pt(15)
        elif i == 3:
            hstyle.font.size = Pt(13)
        elif i == 4:
            hstyle.font.size = Pt(12)
        else:
            hstyle.font.size = Pt(11)


def add_formatted_text(paragraph, text):
    """
    Parse inline markdown formatting and add runs to paragraph.
    Supports: **bold**, *italic*, `code`, and ***THÔNG TIN NỘI BỘ*** (red highlight).
    """
    # Pattern to match inline formatting tokens
    # Order matters: *** before ** before *
    pattern = re.compile(
        r'(\*\*\*THÔNG TIN NỘI BỘ\*\*\*)'  # red highlight marker
        r'|(\*\*\*(.+?)\*\*\*)'              # bold+italic
        r'|(\*\*(.+?)\*\*)'                   # bold
        r'|(\*(.+?)\*)'                        # italic
        r'|(`([^`]+)`)'                        # inline code
    )

    pos = 0
    for m in pattern.finditer(text):
        # Add text before match
        if m.start() > pos:
            paragraph.add_run(text[pos:m.start()])

        if m.group(1):  # ***THÔNG TIN NỘI BỘ***
            run = paragraph.add_run('THÔNG TIN NỘI BỘ')
            run.bold = True
            run.font.color.rgb = RGBColor(255, 0, 0)
            run.font.size = Pt(11)
        elif m.group(2):  # ***bold+italic***
            run = paragraph.add_run(m.group(3))
            run.bold = True
            run.italic = True
        elif m.group(4):  # **bold**
            run = paragraph.add_run(m.group(5))
            run.bold = True
        elif m.group(6):  # *italic*
            run = paragraph.add_run(m.group(7))
            run.italic = True
        elif m.group(8):  # `code`
            run = paragraph.add_run(m.group(9))
            run.font.name = 'Courier New'
            run.font.size = Pt(10)

        pos = m.end()

    # Remaining text
    if pos < len(text):
        paragraph.add_run(text[pos:])


def add_table(doc, rows):
    """Add a formatted table to the document."""
    if not rows or len(rows) < 2:
        return

    header = rows[0]
    # Skip separator row (row[1] is usually |---|---|)
    data_rows = rows[2:] if len(rows) > 2 else []

    num_cols = len(header)
    table = doc.add_table(rows=1 + len(data_rows), cols=num_cols)
    table.style = 'Table Grid'
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    # Header row with grey background
    for j, cell_text in enumerate(header):
        cell = table.rows[0].cells[j]
        cell.text = ''
        p = cell.paragraphs[0]
        run = p.add_run(cell_text.strip())
        run.bold = True
        run.font.size = Pt(10)
        run.font.name = 'Times New Roman'
        # Light grey background
        shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="D9E2F3"/>')
        cell._tc.get_or_add_tcPr().append(shading)

    # Data rows
    for i, row_cells in enumerate(data_rows):
        for j in range(min(len(row_cells), num_cols)):
            cell = table.rows[i + 1].cells[j]
            cell.text = ''
            p = cell.paragraphs[0]
            cell_text = row_cells[j].strip()
            # Use formatted text to preserve bold in table cells
            add_formatted_text(p, cell_text)
            for run in p.runs:
                run.font.size = Pt(10)
                run.font.name = 'Times New Roman'

    # Auto-fit
    table.autofit = True
    # Add spacing after table
    doc.add_paragraph('')


def parse_table_row(line):
    """Parse a markdown table row into cells."""
    line = line.strip()
    if line.startswith('|'):
        line = line[1:]
    if line.endswith('|'):
        line = line[:-1]
    return [c.strip() for c in line.split('|')]


def is_separator_row(line):
    """Check if line is a table separator (|---|---|)."""
    return bool(re.match(r'^\|[\s\-:|]+\|$', line.strip()))


def convert_md_to_docx(md_path, docx_path):
    """Convert a markdown file to a formatted DOCX."""
    doc = Document()

    # Page margins
    for section in doc.sections:
        section.top_margin = Cm(2.5)
        section.bottom_margin = Cm(2.5)
        section.left_margin = Cm(2.5)
        section.right_margin = Cm(2.5)

    setup_styles(doc)

    lines = md_path.read_text(encoding='utf-8').split('\n')
    i = 0
    in_code_block = False
    code_lines = []

    while i < len(lines):
        line = lines[i]

        # Code block
        if line.strip().startswith('```'):
            if in_code_block:
                # End code block — add all collected lines
                for cl in code_lines:
                    p = doc.add_paragraph()
                    run = p.add_run(cl)
                    run.font.name = 'Courier New'
                    run.font.size = Pt(10)
                    p.paragraph_format.space_after = Pt(0)
                    p.paragraph_format.space_before = Pt(0)
                    p.paragraph_format.left_indent = Cm(1)
                code_lines = []
                in_code_block = False
                i += 1
                continue
            else:
                in_code_block = True
                i += 1
                continue

        if in_code_block:
            code_lines.append(line)
            i += 1
            continue

        # Horizontal rule
        if line.strip() == '---':
            # Add a thin line
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(6)
            p.paragraph_format.space_after = Pt(6)
            # Add border bottom to paragraph
            pPr = p._p.get_or_add_pPr()
            pBdr = parse_xml(
                f'<w:pBdr {nsdecls("w")}>'
                '<w:bottom w:val="single" w:sz="4" w:space="1" w:color="999999"/>'
                '</w:pBdr>'
            )
            pPr.append(pBdr)
            i += 1
            continue

        # Empty line
        if not line.strip():
            i += 1
            continue

        # Headings
        heading_match = re.match(r'^(#{1,5})\s+(.*)', line)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            p = doc.add_heading(level=level)
            add_formatted_text(p, text)
            i += 1
            continue

        # Table detection
        if line.strip().startswith('|') and '|' in line.strip()[1:]:
            table_rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                if not is_separator_row(lines[i]):
                    table_rows.append(parse_table_row(lines[i]))
                else:
                    table_rows.append(None)  # placeholder for separator
                i += 1
            # Remove None separators but keep track
            clean_rows = [r for r in table_rows if r is not None]
            if len(clean_rows) >= 2:
                # Re-insert separator marker
                add_table(doc, [clean_rows[0], ['---'] * len(clean_rows[0])] + clean_rows[1:])
            continue

        # Blockquote
        if line.strip().startswith('>'):
            text = line.strip()[1:].strip()
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(1)
            add_formatted_text(p, text)
            for run in p.runs:
                run.italic = True
                run.font.color.rgb = RGBColor(100, 100, 100)
            i += 1
            continue

        # Numbered list
        num_match = re.match(r'^(\d+)\.\s+(.*)', line)
        if num_match:
            text = num_match.group(2)
            p = doc.add_paragraph(style='List Number')
            add_formatted_text(p, text)
            i += 1
            continue

        # Bullet list
        if line.strip().startswith('- '):
            text = line.strip()[2:]
            p = doc.add_paragraph(style='List Bullet')
            add_formatted_text(p, text)
            i += 1
            continue

        # Regular paragraph
        p = doc.add_paragraph()
        add_formatted_text(p, line)
        i += 1

    doc.save(str(docx_path))
    print(f'  Created: {docx_path.name}')


def main():
    print('Generating DOCX policy documents...\n')
    for md_name, docx_name in FILES.items():
        md_path = POLICY_DIR / md_name
        docx_path = POLICY_DIR / docx_name
        if not md_path.exists():
            print(f'  SKIP: {md_name} not found')
            continue
        convert_md_to_docx(md_path, docx_path)
    print('\nDone.')


if __name__ == '__main__':
    main()

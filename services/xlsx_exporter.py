"""
Lightweight, Zero-Dependency XLSX (OpenXML) Exporter for Lead Finder.

Generates standard-compliant Office Open XML (.xlsx) spreadsheets using Python's
built-in zipfile and XML utilities. Fully compatible with Microsoft Excel,
LibreOffice Calc, Google Sheets, and Apple Numbers.
"""

import io
import re
import zipfile
from xml.sax.saxutils import escape as xml_escape
from typing import Any, Iterable, Sequence


def escape_xml_text(value: Any) -> str:
    """Escape string for XML text nodes and strip invalid XML control characters."""
    if value is None:
        return ""
    text = str(value)
    # Remove XML-incompatible control characters (ASCII 0-31 except tab, newline, carriage return)
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", text)
    return xml_escape(text)


def _get_column_letter(col_idx: int) -> str:
    """Convert 1-based column index to Excel column letter (1 -> A, 2 -> B, 3 -> C, 27 -> AA)."""
    result = ""
    while col_idx > 0:
        col_idx, remainder = divmod(col_idx - 1, 26)
        result = chr(65 + remainder) + result
    return result


def sanitize_sheet_name(name: str) -> str:
    """Sanitize worksheet name to conform to Excel constraints (max 31 chars, no forbidden chars)."""
    if not name:
        return "Sheet1"
    cleaned = re.sub(r"[\\/*?:\[\]]", "_", name).strip()
    return cleaned[:31] if cleaned else "Sheet1"


def build_xlsx_bytes(
    headers: Sequence[str],
    rows: Iterable[Sequence[Any]],
    sheet_name: str = "Sheet1",
) -> bytes:
    """
    Build a standard OpenXML (.xlsx) workbook containing a single sheet.

    Args:
        headers: List of column header strings.
        rows: Iterable of row data sequences.
        sheet_name: Name of the worksheet tab.

    Returns:
        Raw bytes of the .xlsx zip archive.
    """
    safe_sheet_name = sanitize_sheet_name(sheet_name)
    out = io.BytesIO()
    with zipfile.ZipFile(out, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 1. [Content_Types].xml
        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
            '  <Default Extension="xml" ContentType="application/xml"/>\n'
            '  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>\n'
            '  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>\n'
            '  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>\n'
            '</Types>'
        )
        zf.writestr("[Content_Types].xml", content_types)

        # 2. _rels/.rels
        root_rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>\n'
            '</Relationships>'
        )
        zf.writestr("_rels/.rels", root_rels)

        # 3. xl/workbook.xml
        escaped_sheet_name = escape_xml_text(safe_sheet_name)
        workbook_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">\n'
            '  <sheets>\n'
            f'    <sheet name="{escaped_sheet_name}" sheetId="1" r:id="rId1"/>\n'
            '  </sheets>\n'
            '</workbook>'
        )
        zf.writestr("xl/workbook.xml", workbook_xml)

        # 4. xl/_rels/workbook.xml.rels
        workbook_rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>\n'
            '  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>\n'
            '</Relationships>'
        )
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)

        # 5. xl/styles.xml
        styles_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">\n'
            '  <fonts count="2">\n'
            '    <font><name val="Calibri"/><sz val="11"/></font>\n'
            '    <font><b/><name val="Calibri"/><sz val="11"/></font>\n'
            '  </fonts>\n'
            '  <fills count="2">\n'
            '    <fill><patternFill patternType="none"/></fill>\n'
            '    <fill><patternFill patternType="gray125"/></fill>\n'
            '  </fills>\n'
            '  <borders count="1">\n'
            '    <border><left/><right/><top/><bottom/><diagonal/></border>\n'
            '  </borders>\n'
            '  <cellStyleXfs count="1">\n'
            '    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>\n'
            '  </cellStyleXfs>\n'
            '  <cellXfs count="2">\n'
            '    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>\n'
            '    <xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>\n'
            '  </cellXfs>\n'
            '</styleSheet>'
        )
        zf.writestr("xl/styles.xml", styles_xml)

        # 6. xl/worksheets/sheet1.xml
        sheet_parts = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n',
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">\n',
            '  <sheetData>\n',
        ]

        row_idx = 1

        # Header row (with bold style s="1")
        if headers:
            sheet_parts.append(f'    <row r="{row_idx}">\n')
            for col_idx, header in enumerate(headers):
                col_letter = _get_column_letter(col_idx + 1)
                cell_ref = f"{col_letter}{row_idx}"
                escaped_val = escape_xml_text(header)
                sheet_parts.append(
                    f'      <c r="{cell_ref}" t="inlineStr" s="1"><is><t>{escaped_val}</t></is></c>\n'
                )
            sheet_parts.append('    </row>\n')
            row_idx += 1

        # Data rows
        for row_data in rows:
            sheet_parts.append(f'    <row r="{row_idx}">\n')
            for col_idx, cell_value in enumerate(row_data):
                col_letter = _get_column_letter(col_idx + 1)
                cell_ref = f"{col_letter}{row_idx}"
                escaped_val = escape_xml_text(cell_value)
                sheet_parts.append(
                    f'      <c r="{cell_ref}" t="inlineStr"><is><t>{escaped_val}</t></is></c>\n'
                )
            sheet_parts.append('    </row>\n')
            row_idx += 1

        sheet_parts.append('  </sheetData>\n')
        sheet_parts.append('</worksheet>')

        zf.writestr("xl/worksheets/sheet1.xml", "".join(sheet_parts))

    return out.getvalue()

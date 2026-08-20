"""테스트 공용 도구 — 전부 오프라인이고, 합성 데이터만 씁니다."""

import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
X_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R_NS = ('xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships"')


def write(path, text, encoding="utf-8"):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding=encoding, newline="") as handle:
        handle.write(text)
    return path


def make_bundle(root, files):
    """{상대경로: 내용} → 폴더."""
    os.makedirs(root, exist_ok=True)
    for name, text in files.items():
        write(os.path.join(root, name), text)
    return str(root)


def make_docx(path, paragraphs, table=None, styles=None):
    """문단 목록(+선택 표)으로 최소한의 `.docx` 를 만듭니다."""
    styles = styles or {}
    body = []
    for text in paragraphs:
        style = styles.get(text)
        pr = ('<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style) if style else ""
        body.append("<w:p>%s<w:r><w:t>%s</w:t></w:r></w:p>" % (pr, _esc(text)))
    if table:
        rows = []
        for row in table:
            cells = "".join(
                "<w:tc><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:tc>" % _esc(c)
                for c in row)
            rows.append("<w:tr>%s</w:tr>" % cells)
        body.append("<w:tbl>%s</w:tbl>" % "".join(rows))
    document = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document %s><w:body>%s</w:body></w:document>'
                % (W_NS, "".join(body)))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        zf.writestr("word/document.xml", document)
    return str(path)


def make_xlsx(path, sheets, shared=False):
    """{시트명: [[셀,...], ...]} → 최소한의 `.xlsx`.

    `shared=True` 면 진짜 엑셀이 쓰는 방식(sharedStrings 참조)으로 문자열을 씁니다.
    inlineStr 로만 테스트하면 실제 파일에서만 깨지는 경로가 생깁니다.
    """
    sheet_xml = []
    entries = []
    strings = []
    for i, (name, rows) in enumerate(sheets.items(), start=1):
        body = []
        for r, row in enumerate(rows, start=1):
            cells = []
            for c, value in enumerate(row):
                ref = "%s%d" % (_col_letter(c), r)
                if _is_number(value):
                    cells.append('<c r="%s"><v>%s</v></c>' % (ref, value))
                elif shared:
                    if str(value) not in strings:
                        strings.append(str(value))
                    cells.append('<c r="%s" t="s"><v>%d</v></c>'
                                 % (ref, strings.index(str(value))))
            body.append("<row r='%d'>%s</row>" % (r, "".join(cells)))
        sheet_xml.append(("xl/worksheets/sheet%d.xml" % i,
                          '<?xml version="1.0"?><worksheet %s><sheetData>%s'
                          '</sheetData></worksheet>' % (X_NS, "".join(body))))
        entries.append((name, "rId%d" % i, i))
    workbook = ('<?xml version="1.0"?><workbook %s %s><sheets>%s</sheets></workbook>'
                % (X_NS, R_NS,
                   "".join('<sheet name="%s" sheetId="%d" r:id="%s"/>'
                           % (_esc(n), i, rid) for n, rid, i in entries)))
    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxml'
            'formats.org/package/2006/relationships">%s</Relationships>'
            % "".join('<Relationship Id="%s" Target="worksheets/sheet%d.xml"/>'
                      % (rid, i) for _n, rid, i in entries))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        for name, xml in sheet_xml:
            zf.writestr(name, xml)
        if shared:
            zf.writestr("xl/sharedStrings.xml",
                        '<?xml version="1.0"?><sst %s count="%d">%s</sst>'
                        % (X_NS, len(strings),
                           "".join("<si><t>%s</t></si>" % _esc(s)
                                   for s in strings)))
    return str(path)


def _col_letter(index):
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _is_number(value):
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


@pytest.fixture
def examples_dir():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "examples")


@pytest.fixture
def simple_case(tmp_path):
    """Results 절 숫자 6개가 전부 번들에 있는 최소 사례."""
    manuscript = tmp_path / "원고.md"
    manuscript.write_text(
        "## Results\n"
        "평균은 12.44 (SD 4.08), 대조군은 15.91 (SD 4.63)이었다.\n"
        "평균차 -3.47 (p = 0.0021).\n",
        encoding="utf-8")
    bundle = make_bundle(tmp_path / "out", {
        "stat.csv": "group,mean,sd,diff,p\n"
                    "a,12.44,4.08,,\n"
                    "b,15.91,4.63,,\n"
                    "between,,,-3.47,0.0021\n"})
    return str(manuscript), bundle

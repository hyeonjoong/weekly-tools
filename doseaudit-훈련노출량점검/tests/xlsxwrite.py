"""테스트·예제 생성용 최소 .xlsx 라이터 (개발 도구 — 패키지에는 들어가지 않는다).

doseaudit 본체는 **읽기 전용**이다. 쓰기 능력은 여기에만 있고, 여기 있는 것은
테스트 픽스처와 합성 예제를 만들기 위한 것뿐이다.
"""

import decimal
import zipfile

_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
%s</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""


def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _col_name(idx):
    name = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        name = chr(65 + rem) + name
    return name


def _sheet_xml(rows):
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
             "<sheetData>"]
    for r_idx, row in enumerate(rows, start=1):
        if row is None:
            continue
        cells = []
        for c_idx, value in enumerate(row):
            if value is None or value == "":
                continue
            ref = "%s%d" % (_col_name(c_idx), r_idx)
            cells.append('<c r="%s" t="str"><v>%s</v></c>' % (ref, _esc(value)))
        if cells:
            parts.append('<row r="%d">%s</row>' % (r_idx, "".join(cells)))
    parts.append("</sheetData></worksheet>")
    return "".join(parts)


def write_xlsx(path, sheets):
    """`sheets` = `[(시트이름, [[셀, ...], ...]), ...]` 를 .xlsx 로 쓴다.

    `None` 인 행은 **빈 행**으로 건너뛴다(실데이터의 4행 공백을 재현하기 위함).
    """
    overrides = "".join(
        '<Override PartName="/xl/worksheets/sheet%d.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>\n'
        % i for i in range(1, len(sheets) + 1))
    wb = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>']
    rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for i, (name, _rows) in enumerate(sheets, start=1):
        wb.append('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (_esc(name), i, i))
        rels.append('<Relationship Id="rId%d" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                    'Target="worksheets/sheet%d.xml"/>' % (i, i))
    wb.append("</sheets></workbook>")
    rels.append("</Relationships>")

    def entry(name):
        """타임스탬프를 **고정**한 zip 항목.

        `writestr(name, ...)` 은 현재 시각을 찍는다. 그러면 `examples/_예제생성.py`
        를 돌릴 때마다 번들 예제 15개가 전부 다른 바이트가 되어, 내용이 하나도
        바뀌지 않았는데 git diff 에 올라온다. 재현 가능한 산출물이 이 저장소의
        주제이므로 예제 생성기부터 재현 가능해야 한다.
        """
        info = zipfile.ZipInfo(name, date_time=FIXED_TIMESTAMP)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o600 << 16
        return info

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(entry("[Content_Types].xml"), _CT % overrides)
        zf.writestr(entry("_rels/.rels"), _ROOT_RELS)
        zf.writestr(entry("xl/workbook.xml"), "".join(wb))
        zf.writestr(entry("xl/_rels/workbook.xml.rels"), "".join(rels))
        for i, (_name, rows) in enumerate(sheets, start=1):
            zf.writestr(entry("xl/worksheets/sheet%d.xml" % i), _sheet_xml(rows))
    return path


#: 번들 예제를 바이트 단위로 재현 가능하게 만드는 고정 타임스탬프.
FIXED_TIMESTAMP = (2026, 9, 17, 0, 0, 0)

MODULES = ("소리그림감상", "소리스케치북", "사전자가진단", "사후자가진단", "음소쌍", "발성훈련")
HEADER = ["날짜", "레벨", "훈련유형", "반복횟수", "소요시간(초)"]


def _half_up(value):
    """스프레드시트처럼 **0에서 먼 쪽으로** 반올림한다.

    파이썬의 `round()` 는 짝수로 붙이므로(12.5 → 12) 이걸로 `[요약]` 블록을 만들면
    실제 엑셀이 만들지 않는 값이 들어가고, 벤더 대조기가 그것을 불일치로 잡는다.
    """
    return int(decimal.Decimal(repr(float(value))).quantize(decimal.Decimal(1),
                                                            rounding=decimal.ROUND_HALF_UP))


def module_sheet(data_rows, mean_seconds=None, mean_reps=None, header=None):
    """실데이터와 같은 모양의 시트 한 장: `[요약]` 3행 + 빈 행 + 헤더 + 데이터."""
    rows = [["[요약]"]]
    rows.append(["평균 소요시간", "%d초" % _half_up(mean_seconds if mean_seconds is not None else 0)])
    rows.append(["평균 반복횟수", "%d회" % _half_up(mean_reps if mean_reps is not None else 0)])
    rows.append(None)
    rows.append(list(header or HEADER))
    rows.extend(data_rows)
    return rows

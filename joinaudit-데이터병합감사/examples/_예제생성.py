"""번들 예제 데이터를 만든다 — **전부 난수 기반 합성이며 실제 환자 자료가 아니다.**

두 벌을 만든다.

* `clean/`  — 완벽하게 깨끗한 세 파일. 여기서 경고가 하나라도 뜨면 이 툴은
  "매번 우는 체커"이고, 그건 조용히 통과시키는 체커만큼 나쁘다. 기대 결과는
  **문제 0건, 종료코드 0.**
* `flawed/` — BELL-001 현장에서 실제로 오는 형태의 결함을 일부러 심은 네 파일.
  ID 표기 혼재, 중복 키(재업로드), 깨진 날짜, cp949 인코딩, 시트 앞 안내문,
  중복 열 이름, 범위 이탈, 단위 혼동, 그리고 타임존 혼재.

`python3 examples/_예제생성.py` 로 언제든 다시 만들 수 있다(같은 시드 → 같은 파일).
"""

from __future__ import annotations

import datetime as _dt
import os
import random
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = 20260807

# 합성 코호트: BELL-001 SERENE 을 흉내 낸 규모(값은 전부 난수).
N_SUBJECTS = 16
N_NIGHTS = 10
FIRST_NIGHT = _dt.date(2026, 3, 2)


# --------------------------------------------------------------------------
# 아주 작은 .xlsx 작성기 (외부 의존성 없이 예제를 만들기 위한 것)
# --------------------------------------------------------------------------

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WB_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

# cellXfs: 0 = 일반, 1 = 날짜(numFmtId 14 = m/d/yy)
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="1"><fill><patternFill patternType="none"/></fill></fills>
<borders count="1"><border/></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
</cellXfs>
</styleSheet>"""


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _col_ref(index: int) -> str:
    letters = ""
    n = index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _serial(date: _dt.date) -> int:
    """날짜 -> 엑셀 1900 체계 시리얼."""
    return (date - _dt.date(1899, 12, 30)).days


def write_xlsx(path: str, rows, sheet_name: str = "Sheet1") -> None:
    """행 목록을 .xlsx 로 쓴다.

    셀 값은 `str`(문자열), `int`/`float`(숫자), `datetime.date`(날짜 서식이
    걸린 시리얼), `None`(빈 칸) 중 하나. 빈 칸은 XML 에서 아예 생략해서
    **희소 행**을 만든다 — 실제 엑셀이 그렇게 저장하기 때문이다.
    """
    shared: list = []
    index: dict = {}

    def sid(text: str) -> int:
        if text not in index:
            index[text] = len(shared)
            shared.append(text)
        return index[text]

    body = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            if value is None or value == "":
                continue
            ref = f"{_col_ref(c)}{r}"
            if isinstance(value, _dt.date) and not isinstance(value, _dt.datetime):
                cells.append(f'<c r="{ref}" s="1"><v>{_serial(value)}</v></c>')
            elif isinstance(value, bool):
                cells.append(f'<c r="{ref}" t="b"><v>{int(value)}</v></c>')
            elif isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="s"><v>{sid(str(value))}</v></c>')
        body.append(f'<row r="{r}">' + "".join(cells) + "</row>")

    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             "<sheetData>" + "".join(body) + "</sheetData></worksheet>")
    strings = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
               f'count="{len(shared)}" uniqueCount="{len(shared)}">'
               + "".join(f"<si><t>{_esc(s)}</t></si>" for s in shared) + "</sst>")
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f'<sheets><sheet name="{_esc(sheet_name)}" sheetId="1" r:id="rId1"/></sheets>'
                "</workbook>")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _ROOT_RELS)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", _WB_RELS)
        zf.writestr("xl/styles.xml", _STYLES)
        zf.writestr("xl/sharedStrings.xml", strings)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)


def write_csv(path: str, rows, encoding: str = "utf-8", delimiter: str = ",",
              newline: str = "\n") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = newline.join(delimiter.join(str(c) for c in row) for row in rows) + newline
    with open(path, "wb") as fh:
        fh.write(text.encode(encoding))


# --------------------------------------------------------------------------
# 합성 코호트
# --------------------------------------------------------------------------

def _cohort(rng: random.Random):
    """피험자별 기저값. 사람마다 다른 수준을 갖게 해서 표가 밋밋하지 않게 한다."""
    out = {}
    for i in range(1, N_SUBJECTS + 1):
        out[f"S{i:02d}"] = {
            "rmssd": rng.uniform(22, 58),
            "sdnn": rng.uniform(38, 92),
            "hr": rng.uniform(52, 72),
            "tst": rng.uniform(330, 460),
            "sol": rng.uniform(12, 55),
            "waso": rng.uniform(15, 70),
            "isi": rng.randint(9, 24),
            "arm": "치료" if i % 2 == 1 else "대조",
        }
    return out


def _nights():
    return [FIRST_NIGHT + _dt.timedelta(days=d) for d in range(N_NIGHTS)]


# --------------------------------------------------------------------------
# clean/
# --------------------------------------------------------------------------

def build_clean() -> None:
    rng = random.Random(SEED)
    base = _cohort(rng)
    nights = _nights()
    root = os.path.join(HERE, "clean")

    # 1) 워치 HRV 요약 — 밤마다 한 행. 요약 타임스탬프는 자정을 넘기기도 한다.
    watch = [["subject_id", "measured_at", "rmssd_ms", "sdnn_ms", "mean_hr_bpm"]]
    for i, (sid, b) in enumerate(sorted(base.items())):
        for j, night in enumerate(nights):
            if (i + j) % 3 == 0:                       # 취침 직후 기록
                stamp = f"{night.isoformat()} 23:{rng.randint(10, 58):02d}"
            else:                                      # 자정을 넘긴 기록
                after = night + _dt.timedelta(days=1)
                stamp = f"{after.isoformat()} 0{rng.randint(1, 4)}:{rng.randint(0, 59):02d}"
            watch.append([sid, stamp,
                          f"{b['rmssd'] + rng.gauss(0, 4):.1f}",
                          f"{b['sdnn'] + rng.gauss(0, 6):.1f}",
                          f"{b['hr'] + rng.gauss(0, 2):.1f}"])
    write_csv(os.path.join(root, "watch_hrv.csv"), watch)

    # 2) 수면일기 — 엑셀, 한국어 열 이름, 날짜는 엑셀 날짜 서식
    diary = [["피험자번호", "날짜", "총수면시간_min", "입면시간_min",
              "각성시간_min", "수면효율_pct"]]
    for sid, b in sorted(base.items()):
        for night in nights:
            tst = b["tst"] + rng.gauss(0, 25)
            sol = max(3.0, b["sol"] + rng.gauss(0, 8))
            waso = max(0.0, b["waso"] + rng.gauss(0, 12))
            diary.append([sid, night, round(tst), round(sol), round(waso),
                          round(100 * tst / (tst + sol + waso), 1)])
    write_xlsx(os.path.join(root, "diary.xlsx"), diary, sheet_name="수면일기")

    # 3) ISI — 피험자당 한 행(시점 열 없음 → 피험자 단위로 붙는다)
    isi = [["subject_id", "isi_total", "group"]]
    for sid, b in sorted(base.items()):
        isi.append([sid, b["isi"], b["arm"]])
    write_csv(os.path.join(root, "isi.csv"), isi, encoding="utf-8-sig")


# --------------------------------------------------------------------------
# flawed/
# --------------------------------------------------------------------------

def build_flawed() -> None:
    rng = random.Random(SEED + 1)
    base = _cohort(rng)
    nights = _nights()
    root = os.path.join(HERE, "flawed")
    ids = sorted(base)

    # ---- 1) 워치 HRV: ID 표기 혼재 + 중복 키 + 깨진 날짜 ------------------
    def mangle(sid: str, j: int) -> str:
        n = int(sid[1:])
        if n == 3:
            return f"S{n}"                     # 제로패딩 없음 (S3 vs S03)
        if n == 5 and j % 2 == 0:
            return "Ｓ０５"                     # 전각
        if n == 7:
            return "피험자7"                    # 규칙으로는 못 붙음 → alias.csv
        if n == 9:
            return f" S{n:02d} "               # 앞뒤 공백
        if n == 11:
            return f"s{n:02d}"                 # 소문자
        return sid

    watch = [["subject_id", "measured_at", "rmssd_ms", "sdnn_ms",
              "총수면시간_min", "메모"]]
    for i, sid in enumerate(ids):
        b = base[sid]
        for j, night in enumerate(nights):
            if (i + j) % 3 == 0:
                stamp = f"{night.isoformat()} 23:{rng.randint(10, 58):02d}"
            else:
                after = night + _dt.timedelta(days=1)
                stamp = f"{after.isoformat()} 0{rng.randint(1, 4)}:{rng.randint(0, 59):02d}"
            # 깨진 날짜 두 건: 존재하지 않는 날짜와 빈 값
            if sid == "S02" and j == 4:
                stamp = "2026-13-45 03:10"
            if sid == "S04" and j == 6:
                stamp = ""
            row = [mangle(sid, j), stamp,
                   f"{b['rmssd'] + rng.gauss(0, 4):.1f}",
                   f"{b['sdnn'] + rng.gauss(0, 6):.1f}",
                   f"{b['tst'] + rng.gauss(0, 25):.0f}", ""]
            watch.append(row)
            # 재업로드로 같은 밤이 두 번 들어온 경우
            if sid in ("S03", "S12") and j in (2, 5):
                dup = list(row)
                dup[2] = f"{b['rmssd'] + rng.gauss(0, 4):.1f}"
                dup[5] = "재업로드"
                watch.append(dup)
    write_csv(os.path.join(root, "watch_hrv.csv"), watch)

    # ---- 2) 수면일기 xlsx: 시트 앞 안내문 + 중복 열 이름 + 시간 단위 -------
    diary = [
        ["2026년 3월 수면일기 취합 (BENTRI 내부용, 합성 데이터)", None, None,
         None, None, None],
        [None, None, None, None, None, None],
        ["피험자번호", "날짜", "총수면시간_시간", "입면시간_min", "비고", "비고"],
    ]
    for sid in ids:
        b = base[sid]
        for night in nights:
            tst_h = (b["tst"] + rng.gauss(0, 25)) / 60.0
            diary.append([sid, night, round(tst_h, 2),
                          round(max(3.0, b["sol"] + rng.gauss(0, 8))),
                          "", ""])
    # 수면일기에만 있는 피험자(다른 파일에는 없음) → 커버리지 차집합 시연
    for night in nights[:3]:
        diary.append(["S21", night, 6.5, 20, "중도탈락", ""])
    write_xlsx(os.path.join(root, "diary.xlsx"), diary, sheet_name="3월")

    # ---- 3) ISI: cp949 + 접두어가 붙은 ID + 범위 이탈 ---------------------
    isi = [["피험자", "ISI총점", "배정군"]]
    for k, sid in enumerate(ids):
        total = base[sid]["isi"]
        if k == 8:
            total = 45          # ISI 는 0~28 이므로 범위 이탈 (spec.json)
        isi.append([f"BELL-001-{sid[1:]}", total, base[sid]["arm"]])
    write_csv(os.path.join(root, "isi.csv"), isi, encoding="cp949")

    # ---- 4) 호흡: 타임존 표기가 섞인 파일 (단독으로 쓰면 종료코드 3) -------
    resp = [["subject_id", "measured_at", "resp_rate", "rsa_index"]]
    for i, sid in enumerate(ids[:8]):
        for j, night in enumerate(nights[:4]):
            after = night + _dt.timedelta(days=1)
            stamp = f"{after.isoformat()} 02:{10 + j:02d}"
            if (i + j) % 4 == 0:
                stamp += "+09:00"          # 어떤 행에만 오프셋이 붙어 있다
            resp.append([sid, stamp, f"{rng.uniform(11, 17):.2f}",
                         f"{rng.uniform(0.2, 0.9):.3f}"])
    write_csv(os.path.join(root, "respiration_tz.csv"), resp)

    # ---- 5) 사람이 명시하는 대응표와 연구 규칙 ---------------------------
    write_csv(os.path.join(root, "alias.csv"),
              [["파일", "원본ID", "표준ID"],
               ["watch_hrv.csv", "피험자7", "S07"]],
              encoding="utf-8-sig")
    with open(os.path.join(root, "spec.json"), "w", encoding="utf-8") as fh:
        fh.write(
            "{\n"
            '  "id_prefixes": [],\n'
            '  "ranges": {\n'
            '    "ISI총점": [0, 28],\n'
            '    "총수면시간_min": [120, 720],\n'
            '    "rmssd_ms": [1, 300]\n'
            "  }\n"
            "}\n")


def main() -> None:
    build_clean()
    build_flawed()
    print("예제 데이터를 다시 만들었습니다 (전부 합성 · 실제 환자 자료 아님):")
    for folder in ("clean", "flawed"):
        for name in sorted(os.listdir(os.path.join(HERE, folder))):
            print(f"  examples/{folder}/{name}")


if __name__ == "__main__":
    main()

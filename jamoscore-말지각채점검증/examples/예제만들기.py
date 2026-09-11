#!/usr/bin/env python3
"""번들 예제 워크북을 만드는 스크립트 (합성 자료 — 실제 대상자 자료 아님).

jamoscore 본체에는 **쓰기 코드가 없다**(원본을 고치지 않는다는 보장을 코드로
지키기 위해서다). 예제를 만들 때만 쓰는 이 스크립트는 패키지 밖에 둔다.

    python3 examples/예제만들기.py
"""

from __future__ import annotations

import os
import zipfile
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))

CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
%s
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""


def col_name(idx):
    name = ""
    while idx:
        idx, rem = divmod(idx - 1, 26)
        name = chr(65 + rem) + name
    return name


def sheet_xml(rows):
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
             "<sheetData>"]
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row, start=1):
            if value is None or value == "":
                continue
            ref = "%s%d" % (col_name(c), r)
            cells.append('<c r="%s" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
                         % (ref, escape(str(value))))
        if cells:
            parts.append('<row r="%d">%s</row>' % (r, "".join(cells)))
    parts.append("</sheetData></worksheet>")
    return "".join(parts)


def write_workbook(path, sheets):
    """``sheets`` 는 ``[(시트이름, [[셀,...], ...]), ...]``."""
    overrides, wb_sheets, wb_rels = [], [], []
    for i, (name, _) in enumerate(sheets, start=1):
        overrides.append(
            '<Override PartName="/xl/worksheets/sheet%d.xml" ContentType='
            '"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % i)
        wb_sheets.append('<sheet name="%s" sheetId="%d" r:id="rId%d"/>'
                         % (escape(name), i, i))
        wb_rels.append(
            '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>'
            % (i, i))
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>%s</sheets></workbook>" % "".join(wb_sheets))
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            "%s</Relationships>" % "".join(wb_rels))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CT % "\n".join(overrides))
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        for i, (_, rows) in enumerate(sheets, start=1):
            z.writestr("xl/worksheets/sheet%d.xml" % i, sheet_xml(rows))


# ----------------------------------------------------------- 자료 정의
CONSONANT = ["아라", "아따", "아가", "아자", "아까", "아하", "아파", "아사",
             "아싸", "아타", "아짜", "아나", "아차", "아마", "아다", "아바",
             "아빠", "아카"]
VOWEL = ["후드", "히드", "효드", "흐드", "휘드", "혀드", "햐드", "화드",
         "허드", "회드", "하드", "해드"]
SYLLABLE = ["햄", "볼", "컵", "종", "딸", "넷", "톱", "끈", "쌀", "책"]

#: (자극, 응답) — 자음검사는 2음절 초성만 맞으면 정답이므로 '아라→라라'도 1점.
CONSONANT_RESPONSES = {
    "아라": "라라", "아따": "아빠", "아타": "아파", "아카": "아파",
    "아차": "아삼", "아다": "아바",
}
VOWEL_RESPONSES = {"후드": "푸드", "호드": "포드", "해드": "해디", "휘드": "히두",
                   "회드": "해드", "화드": "꽈드"}


def onset(ch):
    idx = ord(ch) - 0xAC00
    return "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"[idx // 588]


def nucleus(ch):
    idx = ord(ch) - 0xAC00
    return "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"[(idx % 588) // 28]


def coda(ch):
    idx = ord(ch) - 0xAC00
    return ",ㄱ,ㄲ,ㄳ,ㄴ,ㄵ,ㄶ,ㄷ,ㄹ,ㄺ,ㄻ,ㄼ,ㄽ,ㄾ,ㄿ,ㅀ,ㅁ,ㅂ,ㅄ,ㅅ,ㅆ,ㅇ,ㅈ,ㅊ,ㅋ,ㅌ,ㅍ,ㅎ".split(",")[idx % 28]


def consonant_score(stim, resp):
    if len(resp) < 2:
        return 0
    return 1 if onset(stim[1]) == onset(resp[1]) else 0


def vowel_score(stim, resp):
    if not resp:
        return 0
    return 1 if nucleus(stim[0]) == nucleus(resp[0]) else 0


def phoneme_score(stim, resp):
    if len(resp) != 1 or not (0xAC00 <= ord(resp) < 0xD7A4):
        return 0
    return sum([onset(stim) == onset(resp), nucleus(stim) == nucleus(resp),
                coda(stim) == coda(resp)])


def build_subject(seed, n_vowel=12, memo_at=None, blank_scores=False,
                  drop_vowel_item=False, force_vowel=None):
    """한 피험자 시트의 행 목록을 만든다."""
    rows = [["No.", "자음-사전", "응답", "점수", "자음-사후", "응답", "점수",
             "모음-사전", "응답", "점수", "일음절-사전", "응답", "점수"]]
    cons = CONSONANT
    cons_post = list(reversed(CONSONANT))   # 상쇄균형: 사후는 2번 리스트
    vowels = VOWEL[:n_vowel]
    if drop_vowel_item:
        vowels = vowels[:-1]
    syll = SYLLABLE
    n = max(len(cons), len(vowels), len(syll))
    pre_correct = post_correct = vowel_correct = word_correct = 0
    for i in range(n):
        row = [str(i + 1)]
        for which, items, responses in (
            ("pre", cons, CONSONANT_RESPONSES),
            ("post", cons_post, CONSONANT_RESPONSES),
        ):
            if i < len(items):
                stim = items[i]
                wrong = ((i + seed) % 3 == 0) if which == "pre" else ((i + seed) % 4 == 0)
                resp = responses.get(stim, stim) if wrong else stim
                score = consonant_score(stim, resp)
                if which == "pre":
                    pre_correct += score
                else:
                    post_correct += score
                row += [stim, resp, "" if blank_scores and i % 2 else str(score)]
            else:
                row += ["", "", ""]
        if i < len(vowels):
            stim = vowels[i]
            wrong = (i + seed) % 3 == 1
            resp = VOWEL_RESPONSES.get(stim, stim) if wrong else stim
            if memo_at is not None and i == memo_at:
                resp = "뜨디(휴지라고 말하는 것 같았음)"
            score = vowel_score(stim, resp)
            if force_vowel and stim in force_vowel:
                # 같은 (제시, 응답) 쌍을 피험자마다 다르게 채점한 상황을 심는다.
                resp, score = force_vowel[stim]
            if memo_at is not None and i == memo_at:
                score = 0                     # 사람은 0, 규칙은 1 → 확인 필요
            vowel_correct += int(score)
            row += [stim, resp, "" if blank_scores and i % 2 else str(score)]
        else:
            row += ["", "", ""]
        if i < len(syll):
            stim = syll[i]
            wrong = (i + seed) % 4 == 2
            resp = {"볼": "물", "종": "좀", "넷": "맵", "책": "새"}.get(stim, stim) if wrong else stim
            score = 1 if stim == resp else 0
            word_correct += score
            row += [stim, resp, "" if blank_scores and i % 2 else str(score)]
        else:
            row += ["", "", ""]
        rows.append(row)
    summary = [""] * 13
    summary[3] = "%.1f" % (100.0 * pre_correct / len(cons))
    summary[6] = "%.1f" % (100.0 * post_correct / len(cons))
    summary[9] = "%.1f" % (100.0 * vowel_correct / len(vowels))
    summary[12] = "%.1f" % (100.0 * word_correct / len(syll))
    rows.append(summary)
    return rows, {
        "자음_전": summary[3], "자음_후": summary[6],
        "모음_전": summary[9], "일음절_전": summary[12],
    }


def answer_sheet(n_vowel=12):
    rows = [["No.", "자음1", "자음2", "모음1", "일음절1"]]
    for i in range(max(len(CONSONANT), n_vowel, len(SYLLABLE))):
        rows.append([
            str(i + 1),
            CONSONANT[i] if i < len(CONSONANT) else "",
            list(reversed(CONSONANT))[i] if i < len(CONSONANT) else "",
            VOWEL[i] if i < n_vowel else "",
            SYLLABLE[i] if i < len(SYLLABLE) else "",
        ])
    return rows


def summary_sheet(totals, tamper=None):
    header = ["ID", "자음_전", "자음_후", "모음_전", "일음절_전"]
    rows = [header]
    for sid, values in totals:
        row = [sid, values["자음_전"], values["자음_후"], values["모음_전"],
               values["일음절_전"]]
        if tamper and sid == tamper[0]:
            row[tamper[1]] = tamper[2]
        rows.append(row)
    return rows


def make_clean():
    sheets, totals = [], []
    for i, sid in enumerate(["S01", "S02", "S03"], start=1):
        rows, values = build_subject(i)
        sheets.append((sid, rows))
        totals.append((sid, values))
    sheets.append(("언어검사_정답", answer_sheet()))
    sheets.append(("total-최종", summary_sheet(totals)))
    write_workbook(os.path.join(HERE, "정상_채점.xlsx"), sheets)


def make_problem():
    sheets, totals = [], []
    for i, sid in enumerate(["S01", "S02", "S03"], start=1):
        rows, values = build_subject(
            i, memo_at=(3 if sid == "S03" else None),
            drop_vowel_item=(sid == "S02"),
            force_vowel=({"햐드": ("샤드", "1")} if sid == "S01"
                         else {"햐드": ("샤드", "0")} if sid == "S03" else None))
        if sid == "S03":
            # 시트 안의 요약 % 를 손으로 고쳐 둔다 → 분모 12로는 만들 수 없는 값
            rows[-1][9] = "70.0"
        sheets.append((sid, rows))
        totals.append((sid, values))
    sheets.append(("개인정보", [["이름", "연락처", "생년월일"],
                              ["(합성 예제 — 실제 값 없음)", "", ""]]))
    sheets.append(("언어검사_정답", answer_sheet()))
    # S01 의 자음_전 을 표에서 손으로 잘못 옮겨 적은 상황
    sheets.append(("total-최종", summary_sheet(totals, tamper=("S01", 1, "72.2"))))
    write_workbook(os.path.join(HERE, "문제있음_채점.xlsx"), sheets)


def make_undetermined():
    sheets, totals = [], []
    for i, sid in enumerate(["S01", "S02"], start=1):
        rows, values = build_subject(i, blank_scores=True,
                                     drop_vowel_item=(sid == "S02"))
        sheets.append((sid, rows))
        totals.append((sid, values))
    sheets.append(("언어검사_정답", answer_sheet()))
    sheets.append(("total-최종", summary_sheet(totals, tamper=("S01", 1, "99.9"))))
    write_workbook(os.path.join(HERE, "판정불가_채점.xlsx"), sheets)


def make_summary_only():
    path = os.path.join(HERE, "요약만_있음.csv")
    with open(path, "w", encoding="utf-8-sig", newline="\n") as fh:
        fh.write("ID,자음_전,자음_후,모음_전,모음_후\n")
        fh.write("S01,66.7,77.8,71.4,78.6\n")
        fh.write("S02,61.1,66.7,78.6,78.6\n")
        fh.write("S03,72.2,83.3,85.7,92.9\n")


if __name__ == "__main__":
    make_clean()
    make_problem()
    make_undetermined()
    make_summary_only()
    print("예제 4종을 만들었습니다:", ", ".join(sorted(os.listdir(HERE))))

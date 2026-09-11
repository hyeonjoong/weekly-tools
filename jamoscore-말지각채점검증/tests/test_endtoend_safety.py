# -*- coding: utf-8 -*-
"""끝에서 끝까지의 안전 성질.

단위 테스트가 `sanitize_cell` 을 아무리 검사해도, **쓰는 쪽이 그걸 부르는지**를
확인하지 않으면 리팩터링 한 번에 방어가 통째로 사라진다(적대 검토 1라운드에서
실제로 그 변이가 테스트를 통과했다). 여기서는 산출물 파일을 열어서 본다.
"""

import csv
import os
import re
import subprocess
import sys

import pytest

from jamoscore.cli import _slug, main
from jamoscore.safeio import FORMULA_LEADERS
from tests.conftest import SPEC_ARGS

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "examples"))
from 예제만들기 import write_workbook  # noqa: E402


PHONE = "010-1234-5678"
RRN = "900101-1234567"
BIRTH = "1990-01-01"


def build(path, rows, extra_sheets=()):
    sheets = [("S01", rows)] + list(extra_sheets)
    write_workbook(str(path), sheets)
    return str(path)


def header_rows(items):
    rows = [["No.", "자음-사전", "응답", "점수"]]
    for i, (stim, resp, score) in enumerate(items, start=1):
        rows.append([str(i), stim, resp, score])
    return rows


def run_to_dir(path, out_dir, extra=()):
    return main([path, "--subtest", "자음:목표=2음절초성"] + list(extra)
                + ["--out-dir", str(out_dir)])


def all_artifact_text(out_dir):
    chunks = []
    for root, _, files in os.walk(str(out_dir)):
        for name in files:
            chunks.append(open(os.path.join(root, name), encoding="utf-8-sig",
                               errors="replace").read())
    return "\n".join(chunks)


def all_csv_cells(out_dir):
    cells = []
    for root, _, files in os.walk(str(out_dir)):
        for name in files:
            if not name.endswith(".csv"):
                continue
            with open(os.path.join(root, name), encoding="utf-8-sig") as fh:
                for row in csv.reader(fh):
                    for cell in row:
                        cells.append((name, cell))
    return cells


# ------------------------------------------------- 수식 주입 (끝에서 끝까지)
@pytest.mark.parametrize("payload", [
    '=HYPERLINK("http://x","click")', "@SUM(1)", "=cmd|'/C calc'!A1",
    "+1+1", "\tHYPERLINK",
])
def test_formula_payload_never_reaches_a_csv_as_a_leader(tmp_path, payload,
                                                         capsys):
    """단위 테스트가 아니라 **실제로 써진 파일**에서 확인한다.

    페이로드를 **시트 이름**에도 심는다. 시트 이름은 피험자 ID 가 되어 세 CSV 의
    1열을 통째로 차지하므로, 위생 처리가 빠지면 여기서 바로 드러난다(응답 칸에만
    심으면 문장 가운데 박혀 '시작 문자' 검사를 통과해 버린다).
    """
    path = build(tmp_path / "주입.xlsx",
                 header_rows([("아라", payload, "1"), ("아마", "아마", "1")]))
    write_workbook(str(tmp_path / "주입2.xlsx"),
                   [(payload, header_rows([("아라", "아라", "1")]))])
    path = str(tmp_path / "주입2.xlsx")
    out_dir = tmp_path / "결과"
    run_to_dir(path, out_dir)
    capsys.readouterr()
    offenders = [(name, cell) for name, cell in all_csv_cells(out_dir)
                 if cell.startswith(tuple(FORMULA_LEADERS))]
    assert offenders == [], offenders


def test_negative_numbers_are_not_quoted(tmp_path, capsys):
    """임계차의 '변화%p' 는 음수가 정상이다 — 따옴표가 붙으면 다음 툴이 못 읽는다."""
    rows = [["No.", "자음-사전", "응답", "점수", "자음-사후", "응답", "점수"]]
    for i, stim in enumerate(["아라", "아마", "아가", "아자"], start=1):
        rows.append([str(i), stim, stim, "1", stim, "아바", "0"])
    path = build(tmp_path / "하락.xlsx", rows)
    out_dir = tmp_path / "결과"
    main([path, "--subtest", "자음:목표=2음절초성,문항=4", "--out-dir", str(out_dir)])
    capsys.readouterr()
    with open(str(out_dir / "임계차.csv"), encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    values = [r["변화%p"] for r in rows if r["변화%p"]]
    assert values
    for value in values:
        float(value)                 # 문자열 따옴표가 붙으면 여기서 터진다


# ------------------------------------------------------------- 식별정보
def test_identifier_patterns_in_cells_are_masked_in_every_artifact(tmp_path,
                                                                   capsys):
    """전사 칸에 전화번호·주민번호가 적혀 들어오는 일이 실제로 있다."""
    path = build(tmp_path / "식별.xlsx",
                 header_rows([("아라", PHONE, "0"), ("아마", RRN, "0"),
                              ("아가", BIRTH, "0"), ("아자", "아자", "1")]))
    out_dir = tmp_path / "결과"
    run_to_dir(path, out_dir)
    capsys.readouterr()
    text = all_artifact_text(out_dir)
    assert PHONE not in text
    assert RRN not in text
    assert BIRTH not in text


def test_identifier_patterns_are_masked_in_the_printed_report(tmp_path, capsys):
    path = build(tmp_path / "식별2.xlsx",
                 header_rows([("아라", PHONE, "0"), ("아마", "아마", "1")]))
    main([path, "--subtest", "자음:목표=2음절초성"])
    out = capsys.readouterr().out
    assert PHONE not in out
    assert "010-****-****" in out


def test_identifier_patterns_are_masked_in_inspect_output(tmp_path, capsys):
    """--inspect 는 시트 이름을 인쇄한다 — 거기에 심어야 실제로 검사가 된다."""
    write_workbook(str(tmp_path / "식별3.xlsx"),
                   [("S" + PHONE, header_rows([("아라", "아라", "1")]))])
    main([str(tmp_path / "식별3.xlsx"), "--inspect",
          "--subtest", "자음:목표=2음절초성"])
    out = capsys.readouterr().out
    assert PHONE not in out
    assert "010-****-****" in out


def test_pii_sheet_flag_adds_custom_patterns(tmp_path, capsys):
    path = build(tmp_path / "custom.xlsx", header_rows([("아라", "아라", "1")]),
                 extra_sheets=[("대상자표", [["ID"], ["x"]])])
    main([path, "--subtest", "자음:목표=2음절초성", "--pii-sheet", "대상자표"])
    out = capsys.readouterr().out
    assert "대상자표" in out and "열지 않았습니다" in out


# ---------------------------------------------------- 줄 위조 (끝에서 끝까지)
@pytest.mark.parametrize("where", ["시트이름", "응답칸"])
def test_forged_finding_line_is_neutralised(tmp_path, capsys, where):
    forged = "\n[치명] 있지도 않은 소견"
    if where == "시트이름":
        write_workbook(str(tmp_path / "위조.xlsx"),
                       [("S01" + forged, header_rows([("아라", "아라", "1")]))])
    else:
        write_workbook(str(tmp_path / "위조.xlsx"),
                       [("S01", header_rows([("아라", "아라" + forged, "1")]))])
    path = str(tmp_path / "위조.xlsx")
    out_dir = tmp_path / "결과"
    run_to_dir(path, out_dir)
    out = capsys.readouterr().out
    text = all_artifact_text(out_dir)
    for blob in (out, text):
        assert [ln for ln in blob.splitlines()
                if ln.startswith("[치명]")] == []


# ------------------------------------------------------------- _slug
@pytest.mark.parametrize("raw", ["../../evil", "a/b", "/etc/passwd", "..",
                                 "C:\\x", "／슬래시", "⁄", "∕", "\x00"])
def test_slug_neutralises_separators(raw):
    out = _slug(raw)
    assert "/" not in out and "\\" not in out
    assert out not in ("", ".", "..")
    assert not out.startswith("..")


def test_slug_keeps_korean_and_ascii():
    assert _slug("자음") == "자음"
    assert _slug("word") == "word"


def test_feature_column_name_cannot_escape_out_dir(tmp_path, capsys):
    features = tmp_path / "자질.csv"
    features.write_text("자모,../../탈출\nㄱ,연구개\nㅋ,연구개\n", encoding="utf-8")
    path = build(tmp_path / "f.xlsx",
                 header_rows([("아가", "아가", "1"), ("아까", "아가", "0")]))
    out_dir = tmp_path / "결과"
    run_to_dir(path, out_dir, extra=["--features", str(features)])
    capsys.readouterr()
    # out_dir 위쪽 두 단계까지 훑어 탈출한 파일이 없는지 본다
    # (`결과/혼동행렬_초성_../../탈출.csv` 는 tmp_path 보다 한 단계 위에 떨어진다).
    root = os.path.dirname(os.path.dirname(str(tmp_path)))
    escaped = []
    for dirpath, _, names in os.walk(root):
        if dirpath.startswith(str(out_dir)):
            continue
        escaped += [n for n in names if n == "탈출.csv"]
    assert escaped == [], escaped
    assert all(os.path.commonpath([str(out_dir), os.path.join(r, f)])
               == str(out_dir)
               for r, _, fs in os.walk(str(out_dir)) for f in fs)


# ------------------------------------------------- 종료코드 총체성
MALFORMED = {
    "빈시트": [["No."]],
    "헤더만": [["No.", "자음-사전", "응답", "점수"]],
    "점수전부텍스트": [["No.", "자음-사전", "응답", "점수"],
                  ["1", "아라", "아라", "O"], ["2", "아마", "아마", "X"]],
    "응답전부공란": [["No.", "자음-사전", "응답", "점수"],
                 ["1", "아라", "", "0"], ["2", "아마", "", "0"]],
    "자극만": [["No.", "자음-사전", "응답", "점수"], ["1", "아라", "", ""]],
    "이모지": [["No.", "자음-사전", "응답", "점수"], ["1", "아라", "🙂", "0"]],
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_malformed_input_never_produces_a_traceback(tmp_path, name, capsys):
    path = build(tmp_path / ("%s.xlsx" % name), MALFORMED[name])
    code = main([path, "--subtest", "자음:목표=2음절초성"])
    captured = capsys.readouterr()
    assert code in (0, 1, 2, 3), code
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_zero_items_is_undetermined_not_clean(tmp_path, capsys):
    """블록은 있는데 대조한 문항이 0 이면 '치명 소견 없음'은 거짓말이다."""
    rows = [["No.", "자음-사전", "응답", "점수"],
            ["1", "아라", "", ""], ["2", "아마", "", ""]]
    path = build(tmp_path / "빈문항.xlsx", rows)
    code = main([path, "--subtest", "자음:목표=2음절초성,문항=2"])
    out = capsys.readouterr().out
    assert code == 3
    assert "판정 불가" in out


def test_phoneme_rule_declared_as_word_unit_does_not_crash(tmp_path, capsys):
    """`단위=음소` 를 빠뜨린 흔한 오타에서 트레이스백이 나오면 안 된다."""
    rows = [["No.", "자음-사전", "응답", "점수", "자음-사후", "응답", "점수"]]
    for i, stim in enumerate(["각", "논", "답"], start=1):
        rows.append([str(i), stim, stim, "3", stim, stim, "3"])
    path = build(tmp_path / "오타.xlsx", rows)
    code = main([path, "--subtest", "자음:목표=초중종,만점=3,단위=단어,문항=3"])
    captured = capsys.readouterr()
    assert code in (0, 1, 2, 3)
    assert "Traceback" not in captured.err


def test_broken_sheet_xml_is_refused_with_korean_message(tmp_path, examples_dir,
                                                         capsys):
    import zipfile
    src = os.path.join(examples_dir, "정상_채점.xlsx")
    dst = tmp_path / "손상.xlsx"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(str(dst), "w") as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item.endswith("sheet1.xml"):
                data = data[:len(data) // 2]
            zout.writestr(item, data)
    code = main([str(dst), "--subtest", "자음:목표=2음절초성,문항=18"])
    captured = capsys.readouterr()
    # 손상된 시트 하나 때문에 판정하면 거짓말이다 → 판정 불가(3), 또는 거절(2).
    assert code in (2, 3)
    assert "Traceback" not in captured.err and "Traceback" not in captured.out
    assert "읽지 못" in (captured.out + captured.err)


def test_dtd_hidden_behind_padding_is_still_refused(tmp_path, examples_dir,
                                                    capsys):
    """앞부분만 보고 판단하면 주석으로 밀어내 우회할 수 있다."""
    import zipfile
    src = os.path.join(examples_dir, "정상_채점.xlsx")
    dst = tmp_path / "폭탄.xlsx"
    padding = b"<!-- " + b"x" * 9000 + b" -->"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(str(dst), "w") as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item == "xl/workbook.xml":
                data = (b'<?xml version="1.0"?>' + padding
                        + b'<!DOCTYPE x [<!ENTITY a "b">]>' + data)
            zout.writestr(item, data)
    code = main([str(dst), "--subtest", "자음:목표=2음절초성,문항=18"])
    captured = capsys.readouterr()
    assert code == 2
    assert "DTD" in captured.err


def test_huge_row_number_does_not_silently_delete_a_sheet(tmp_path,
                                                          examples_dir, capsys):
    """`<row r="999999999">` 하나로 시트가 통째로 사라지면 안 된다."""
    import zipfile
    src = os.path.join(examples_dir, "정상_채점.xlsx")
    dst = tmp_path / "큰행.xlsx"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(str(dst), "w") as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item.endswith("sheet1.xml"):
                data = data.replace(
                    b"<sheetData>",
                    b'<sheetData><row r="999999999"><c r="A999999999" '
                    b't="inlineStr"><is><t>x</t></is></c></row>', 1)
            zout.writestr(item, data)
    code = main([str(dst)] + SPEC_ARGS)
    out = capsys.readouterr().out
    assert code == 3
    assert "끝까지 읽지 못" in out


def test_redos_response_cell_finishes_quickly(tmp_path, capsys):
    """닫히지 않은 괄호가 긴 칸에서 정규식이 폭발하면 안 된다."""
    import time
    payload = "(" * 60000
    path = build(tmp_path / "redos.xlsx",
                 header_rows([("아라", payload, "0"), ("아마", "아마", "1")]))
    start = time.time()
    code = main([path, "--subtest", "자음:목표=2음절초성"])
    capsys.readouterr()
    assert time.time() - start < 5.0
    assert code in (0, 1, 2, 3)


def test_cr_only_csv_is_readable(tmp_path, capsys):
    """엑셀 for Mac 의 'CSV (Macintosh)' 는 줄바꿈이 CR 하나뿐이다."""
    path = tmp_path / "mac.csv"
    path.write_text("ID,시점,검사,제시,응답,점수\r"
                    "s01,사전,자음,아라,아라,1\r"
                    "s01,사전,자음,아마,아마,1\r", encoding="utf-8")
    code = main([str(path), "--long", "--subtest", "자음:목표=2음절초성"])
    captured = capsys.readouterr()
    assert code in (0, 1, 3)
    assert "Traceback" not in captured.err


def test_symlinked_ready_subdir_cannot_escape_out_dir(tmp_path, examples_dir,
                                                      capsys):
    victim_dir = tmp_path / "바깥"
    victim_dir.mkdir()
    victim = victim_dir / "자음_단어.csv"
    victim.write_text("ORIGINAL", encoding="utf-8")
    out_dir = tmp_path / "결과"
    out_dir.mkdir()
    os.symlink(str(victim_dir), str(out_dir / "statwise_longistat_입력"))
    code = main([os.path.join(examples_dir, "정상_채점.xlsx")] + SPEC_ARGS
                + ["--out-dir", str(out_dir)])
    err = capsys.readouterr().err
    assert code == 2
    assert victim.read_text(encoding="utf-8") == "ORIGINAL"
    assert "링크" in err

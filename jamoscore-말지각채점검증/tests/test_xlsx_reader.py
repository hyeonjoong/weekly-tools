# -*- coding: utf-8 -*-
"""XLSX 리더와 블록·패스 검출."""

import os
import zipfile

import pytest

from jamoscore.analyze import score_items
from jamoscore.reader import (LayoutError, classify_sheets, extract_passes,
                              find_blocks, find_header_row, looks_like_pii_header,
                              looks_like_pii_sheet, parse_number)
from jamoscore.spec import parse_all, specs_by_name
from jamoscore.xlsx import Workbook, XlsxError, column_index, column_name

TESTS = ["자음", "모음", "일음절"]
TPS = ["사전", "사후"]


@pytest.fixture
def clean_book(examples_dir):
    wb = Workbook(os.path.join(examples_dir, "정상_채점.xlsx"))
    yield wb
    wb.close()


@pytest.mark.parametrize("index,name", [
    (1, "A"), (26, "Z"), (27, "AA"), (52, "AZ"), (55, "BC"), (702, "ZZ"),
    (703, "AAA"),
])
def test_column_name_roundtrip(index, name):
    assert column_name(index) == name
    assert column_index(name + "12") == index


def test_column_name_rejects_zero():
    with pytest.raises(ValueError):
        column_name(0)


def test_column_index_rejects_garbage():
    with pytest.raises(XlsxError):
        column_index("12AB")


def test_workbook_lists_sheets(clean_book):
    assert "S01" in clean_book.sheet_names
    assert "언어검사_정답" in clean_book.sheet_names
    assert "total-최종" in clean_book.sheet_names


def test_workbook_rejects_non_zip(tmp_path):
    bad = tmp_path / "가짜.xlsx"
    bad.write_text("이건 엑셀이 아닙니다", encoding="utf-8")
    with pytest.raises(XlsxError) as exc:
        Workbook(str(bad))
    assert ".xlsx" in str(exc.value)


def test_workbook_rejects_missing_file(tmp_path):
    with pytest.raises(XlsxError):
        Workbook(str(tmp_path / "없는파일.xlsx"))


def test_workbook_rejects_dtd(tmp_path, examples_dir):
    """XXE·billion-laughs 방어 — DTD 선언이 있으면 열지 않는다."""
    src = os.path.join(examples_dir, "정상_채점.xlsx")
    dst = tmp_path / "폭탄.xlsx"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(str(dst), "w") as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item == "xl/workbook.xml":
                data = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]>' + data
            zout.writestr(item, data)
    with pytest.raises(XlsxError) as exc:
        Workbook(str(dst))
    assert "DTD" in str(exc.value)


def test_read_sheet_rejects_unknown_name(clean_book):
    with pytest.raises(XlsxError):
        clean_book.read_sheet("없는시트")


def test_sheet_cells_are_one_based(clean_book):
    rows = clean_book.read_sheet("S01")
    assert rows[1][1] == "No."
    assert rows[1][2] == "자음-사전"


def test_empty_cells_are_absent(clean_book):
    rows = clean_book.read_sheet("S01")
    # 12번째 이후 모음 문항은 비어 있다 → 키 자체가 없어야 한다
    last = max(rows)
    assert all(isinstance(v, str) and v != "" for v in rows[last].values())


@pytest.mark.parametrize("name", ["개인정보", "대상자정보", "연락처목록",
                                  "Personal Data", "환자 명단", "roster"])
def test_pii_sheet_names_detected(name):
    assert looks_like_pii_sheet(name)


@pytest.mark.parametrize("name", ["s01", "total-최종", "언어검사_정답", "요약"])
def test_non_pii_sheet_names_pass(name):
    assert not looks_like_pii_sheet(name)


def test_pii_header_detection_needs_two_signals():
    assert looks_like_pii_header({1: {1: "ID", 2: "name", 3: "생년월일"}})
    assert not looks_like_pii_header({1: {1: "ID", 2: "자음-사전"}})


def test_pii_sheet_is_never_read(examples_dir):
    """'열지 않았다'는 말이 참이려면 read_sheet 가 호출되지 않아야 한다."""
    wb = Workbook(os.path.join(examples_dir, "문제있음_채점.xlsx"))
    opened = []
    original = wb.read_sheet
    wb.read_sheet = lambda name: (opened.append(name), original(name))[1]
    roles = classify_sheets(wb, TESTS, TPS, "언어검사_정답", "total-최종",
                            r"^S[0-9]+$")
    assert "개인정보" in roles.pii
    assert "개인정보" not in opened
    wb.close()


def test_classify_sheets_respects_subject_pattern(clean_book):
    roles = classify_sheets(clean_book, TESTS, TPS, "언어검사_정답", "total-최종",
                            r"^S0[12]$")
    assert roles.subjects == ["S01", "S02"]


def test_classify_sheets_without_pattern_finds_all(clean_book):
    roles = classify_sheets(clean_book, TESTS, TPS, "언어검사_정답", "total-최종",
                            None)
    assert set(roles.subjects) == {"S01", "S02", "S03"}


def test_find_header_row(clean_book):
    rows = clean_book.read_sheet("S01")
    assert find_header_row(rows, TESTS, TPS) == 1


def test_find_header_row_returns_none_for_stats_sheet(clean_book):
    rows = clean_book.read_sheet("total-최종")
    assert find_header_row(rows, TESTS, TPS) is None


def test_find_blocks_locates_triples(clean_book):
    rows = clean_book.read_sheet("S01")
    blocks = find_blocks(rows, 1, TESTS, TPS, "S01")
    assert [(b[1], b[2]) for b in blocks] == [
        ("자음", "사전"), ("자음", "사후"), ("모음", "사전"), ("일음절", "사전")]
    for col, _, _, resp, score in blocks:
        assert resp == col + 1 and score == col + 2


def test_find_blocks_refuses_when_response_header_missing():
    rows = {1: {1: "No.", 2: "자음-사전", 3: "뭔가", 4: "다른것"}}
    with pytest.raises(LayoutError) as exc:
        find_blocks(rows, 1, TESTS, TPS, "S99")
    assert "추측하지" not in str(exc.value)
    assert "응답" in str(exc.value)


def test_find_blocks_refuses_when_score_left_of_response():
    rows = {1: {1: "자음-사전", 2: "점수", 3: "응답"}}
    with pytest.raises(LayoutError) as exc:
        find_blocks(rows, 1, TESTS, TPS, "S99")
    assert "왼쪽" in str(exc.value)


def test_find_blocks_tolerates_gap_between_headers():
    rows = {1: {1: "자음-사전", 2: "메모", 3: "응답", 4: "점수"}}
    blocks = find_blocks(rows, 1, TESTS, TPS, "S99")
    assert blocks == [(1, "자음", "사전", 3, 4)]


@pytest.mark.parametrize("header", ["자음-사전", "자음_사전", "자음사전",
                                    "사전-자음", "자음 - 사전"])
def test_header_variants_match(header):
    rows = {1: {1: header, 2: "응답", 3: "점수"}}
    assert find_blocks(rows, 1, TESTS, TPS, "S") != []


def test_extract_passes_splits_two_scoring_passes(clean_book):
    specs = specs_by_name(parse_all([
        "일음절:단위=단어,목표=전체,문항=10",
        "일음절:단위=음소,목표=초중종,문항=10,만점=3"]))
    rows = clean_book.read_sheet("S01")
    block = [b for b in find_blocks(rows, 1, TESTS, TPS, "S01")
             if b[1] == "일음절"][0]
    passes = extract_passes(rows, 1, block, "S01", specs["일음절"], "S01")
    assert len(passes) == 2
    assert len(passes[0].items) == 10
    # 두 번째 패스는 예제에 없다 → 조용히 넘기지 않고 결측으로 기록
    assert passes[1].notes and passes[1].notes[0][0] == "결측"


def test_extract_passes_flags_wrong_item_count(clean_book):
    specs = specs_by_name(parse_all(["자음:목표=2음절초성,문항=99"]))
    rows = clean_book.read_sheet("S01")
    block = find_blocks(rows, 1, TESTS, TPS, "S01")[0]
    passes = extract_passes(rows, 1, block, "S01", specs["자음"], "S01")
    assert passes[0].notes[0][0] == "치명"
    assert "문항수가 선언(99)과 다릅니다" in passes[0].notes[0][1]


def test_pass_denominator_multiplies_max_points(clean_book):
    specs = specs_by_name(parse_all(["일음절:목표=초중종,문항=10,만점=3"]))
    rows = clean_book.read_sheet("S01")
    block = [b for b in find_blocks(rows, 1, TESTS, TPS, "S01")
             if b[1] == "일음절"][0]
    p = extract_passes(rows, 1, block, "S01", specs["일음절"], "S01")[0]
    assert p.denominator == 10 * 3


def test_pass_percent_is_none_when_any_score_unreadable(clean_book):
    specs = specs_by_name(parse_all(["자음:목표=2음절초성,문항=18"]))
    rows = clean_book.read_sheet("S01")
    block = find_blocks(rows, 1, TESTS, TPS, "S01")[0]
    p = extract_passes(rows, 1, block, "S01", specs["자음"], "S01")[0]
    p.items[0].human = None
    assert p.percent is None


@pytest.mark.parametrize("raw,expected", [
    ("1", 1.0), ("1.0", 1.0), ("77.8", 77.8), ("77.8%", 77.8),
    ("-3", -3.0), ("1,234", 1234.0),
])
def test_parse_number_accepts(raw, expected):
    assert parse_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "없음", "N/A", "-", "1.2.3", "#DIV/0!",
                                 "1e5", None, "1 2"])
def test_parse_number_rejects(raw):
    assert parse_number(raw) is None


def test_reported_summary_is_picked_up(clean_book):
    specs = specs_by_name(parse_all(["자음:목표=2음절초성,문항=18"]))
    rows = clean_book.read_sheet("S01")
    block = find_blocks(rows, 1, TESTS, TPS, "S01")[0]
    p = extract_passes(rows, 1, block, "S01", specs["자음"], "S01")[0]
    score_items([p])
    assert p.reported is not None
    assert float(p.reported) == pytest.approx(p.percent)


def test_reported_summary_ignores_item_like_numbers():
    """문항 점수와 구별되지 않는 값(0/1)을 요약 %로 오인하면 안 된다."""
    specs = specs_by_name(parse_all(["자음:목표=2음절초성,문항=2"]))
    rows = {1: {1: "자음-사전", 2: "응답", 3: "점수"},
            2: {1: "아라", 2: "아라", 3: "1"},
            3: {1: "아마", 2: "아마", 3: "1"},
            4: {3: "1"}}
    block = find_blocks(rows, 1, TESTS, TPS, "S")[0]
    p = extract_passes(rows, 1, block, "S", specs["자음"], "S")[0]
    assert p.reported is None

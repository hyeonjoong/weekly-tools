"""망가진 입력·극단 입력에서도 죽지 않고, 조용히 통과시키지도 않는가."""

from __future__ import annotations

import pytest

from conftest import analyze_text
from numcheck.docio import manuscript_from_text, read_manuscript
from numcheck.engine import analyze, analyze_manuscript
from numcheck.textutil import normalize, sentence_at, sentences, snippet


@pytest.mark.parametrize("text", [
    "",
    "\n\n\n",
    "   ",
    "제목만 있는 원고",
    "###",
    "|||||",
    "0" * 5000,
    "%" * 200,
    "p = " * 200,
    "(((((((((",
    "23/48 (47.9%)" * 300,
    "t(45) = 2.31, p = .003 " * 200,
    "\x00\x01 제어문자",
    "😀 이모지와 숫자 23/48 (47.9%)",
])
def test_never_crashes(text):
    report = analyze_text(text)
    assert report.exit_code() in (0, 1, 2, 3)


def test_empty_manuscript_says_it_could_not_check():
    report = analyze_text("")
    assert report.n_checked == 0
    assert report.exit_code() == 3


def test_single_line_manuscript():
    report = analyze_text("23/48 (45.2%)")
    assert report.n_candidates >= 1


def test_huge_numbers_do_not_overflow():
    report = analyze_text("## Results\n9999999/9999999 (100.0%) 이었다.\n")
    assert report.exit_code() in (0, 2, 3)


def test_absurd_degrees_of_freedom_are_handled():
    """말도 안 되게 큰 자유도에서도 계산은 되고 판정은 정상이어야 한다.

    원고에서 나올 수 있는 최대 자유도(정규식 상한 999999)는 지원 범위 안이며,
    그보다 큰 값은 dists.MAX_DF 가 막는다(tests/test_dists.py).
    """
    # 지원 범위 안 — 정상 재계산
    report = analyze_text("## Results\nt(40000) = 2.31, p = .021.\n")
    claims = [c for c in report.claims if c.item == "p 재계산"]
    assert claims and claims[0].checked and claims[0].verdict == "일치"

    # 지원 범위 밖 — 틀린 값을 내지 말고 건너뛴다
    for text in ("t(999999) = 2.31, p = .021.",
                 "F(999999, 999999) = 1.00, p = .50.",
                 "χ²(999999) = 1000000.0, p = .30."):
        report = analyze_text("## Results\n" + text + "\n")
        claims = [c for c in report.claims if c.item == "p 재계산"]
        assert claims, text
        assert not claims[0].checked, (text, claims[0])
        assert claims[0].skip_reason == "표기 불명확", (text, claims[0])


def test_extreme_statistic_gives_tiny_p_not_zero_division():
    report = analyze_text("## Results\nt(45) = 40.0, p = .30.\n")
    findings = [f for f in report.findings if f.item == "p 재계산"]
    assert len(findings) == 1


def test_binary_garbage_file_is_refused_or_read_as_text(tmp_path):
    path = tmp_path / "m.md"
    path.write_bytes(bytes(range(256)) * 10)
    ms = read_manuscript(path)          # 죽지 않는다
    report = analyze_manuscript(ms)
    assert report.exit_code() == 3      # 그리고 '이상 없음'이라고 하지 않는다


def test_very_long_single_line_is_truncated_and_says_so():
    import time
    start = time.monotonic()
    report = analyze_text("## Results\n" + "가나다 " * 200_000 + "\n")
    assert time.monotonic() - start < 10.0
    assert report.exit_code() == 3          # 검사할 수 있는 게 없다고 말한다
    assert any("잘랐습니다" in note for note in report.notes)


def test_duplicate_headings_do_not_confuse_sections():
    report = analyze_text("## Results\n23/48 (47.9%).\n\n## Results\n14/23 (60.9%).\n")
    assert all(c.section in ("Results", "Title") for c in report.claims)


def test_reference_only_manuscript_is_not_reported_as_clean():
    text = ("## References\n"
            "1. A. Sleep. 2019;28(3):112-120.\n"
            "2. B. Sleep. 2020;29(4):200-210.\n")
    report = analyze_text(text)
    assert report.exit_code() == 3


# ── textutil ─────────────────────────────────────────────────────────────────


def test_normalize_preserves_length():
    for text in ("−7.4", "χ²(1)＝6.44", "정상 텍스트", "（48명）"):
        assert len(normalize(text)) == len(text)


def test_sentence_split_keeps_decimals_and_abbreviations():
    parts = sentences("값은 2.31 이었다. e.g. 두 번째 문장이다. 끝.")
    assert len(parts) == 3
    assert "2.31" in parts[0][2]


def test_sentence_split_on_table_separator():
    parts = sentences("ISI | 14.37 (N = 23) | 11.2")
    assert len(parts) >= 3


def test_sentence_at_returns_whole_line_when_out_of_range():
    assert sentence_at("abc", 99)[2] == "abc"


def test_sentences_of_empty_text():
    assert list(sentences("")) == []


def test_snippet_is_bounded():
    long_text = "가" * 5000
    piece = snippet(long_text, 2000, 2010)
    assert len(piece) <= 165


def test_manuscript_from_text_roundtrip():
    ms = manuscript_from_text("첫 줄.\n둘째 줄.")
    assert [ln.no for ln in ms.lines] == [1, 2]


def test_analyze_accepts_path_objects(tmp_path):
    path = tmp_path / "m.md"
    path.write_text("## Results\n23/48 (45.2%).\n", encoding="utf-8")
    assert analyze(path).by_level("치명")

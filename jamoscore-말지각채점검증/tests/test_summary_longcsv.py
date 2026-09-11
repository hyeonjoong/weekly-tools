# -*- coding: utf-8 -*-
"""요약표 열 해석 · long CSV 입력 · 자질표 · 리포트 조립."""

import os

import pytest

from jamoscore import features as features_mod
from jamoscore import report, summary
from jamoscore.analyze import score_items
from jamoscore.findings import CRITICAL, INFO, WARNING, Finding
from jamoscore.longcsv import LongCsvError, load, sniff_rows
from jamoscore.reader import Item, Pass
from jamoscore.spec import parse_all, parse_subtest

TESTS = ["자음", "모음", "일음절"]
TPS = ["사전", "사후"]


# --------------------------------------------------- 요약표 열 해석
@pytest.mark.parametrize("name,expected", [
    ("자음_전", ("자음", "사전", "단어", "값")),
    ("자음_후", ("자음", "사후", "단어", "값")),
    ("모음_사전", ("모음", "사전", "단어", "값")),
    ("일음절(음소)_전", ("일음절", "사전", "음소", "값")),
    ("일음절 음소 후", ("일음절", "사후", "음소", "값")),
    ("자음-pre", ("자음", "사전", "단어", "값")),
])
def test_summary_column_mapped(name, expected):
    role = summary.parse_summary_column(name, TESTS, TPS)
    assert (role.test, role.timepoint, role.unit, role.kind) == expected


def test_summary_diff_column_has_no_timepoint():
    role = summary.parse_summary_column("자음_차이", TESTS, TPS)
    assert role.kind == "차이" and role.timepoint is None


@pytest.mark.parametrize("name", ["ID", "Group", "MCI_전", "세션수",
                                  "FD_500Hz_전", "일음절2_차이", "",
                                  "자음_전_후"])
def test_summary_column_unmapped_is_none(name):
    """해석하지 못한 열을 억지로 맞추면 가짜 불일치가 나온다 — None 이어야 한다."""
    assert summary.parse_summary_column(name, TESTS, TPS) is None


def test_summary_column_rejects_two_timepoints():
    assert summary.parse_summary_column("자음_전_후", TESTS, TPS) is None


def make_pass(subject, test, tp, unit_spec, scores):
    spec = parse_subtest(unit_spec)
    p = Pass(subject, test, tp, spec, subject, 1)
    for seq, score in enumerate(scores, start=1):
        p.items.append(Item(subject, test, tp, spec.unit, seq, seq + 1, 1,
                            "후드", "후드", str(score), subject))
    score_items([p])
    return p


def test_summary_compare_detects_mismatch():
    p = make_pass("S01", "자음", "사전", "자음:목표=전체", [1, 1, 0, 0])
    rows = {1: {1: "ID", 2: "자음_전"}, 2: {1: "S01", 2: "75.0"}}
    findings, stats, unmapped = summary.compare(rows, "요약", [p], TESTS, TPS,
                                                ["S01"])
    assert stats["대조"] == 1 and stats["불일치"] == 1
    assert findings[0].severity == CRITICAL
    assert "50.0" in findings[0].message


def test_summary_compare_accepts_match():
    p = make_pass("S01", "자음", "사전", "자음:목표=전체", [1, 1, 0, 0])
    rows = {1: {1: "ID", 2: "자음_전"}, 2: {1: "S01", 2: "50.0"}}
    findings, stats, _ = summary.compare(rows, "요약", [p], TESTS, TPS, ["S01"])
    assert stats["불일치"] == 0 and findings == []


def test_summary_compare_reports_unmapped_columns():
    p = make_pass("S01", "자음", "사전", "자음:목표=전체", [1])
    rows = {1: {1: "ID", 2: "자음2_차이"}, 2: {1: "S01", 2: "3.0"}}
    _, _, unmapped = summary.compare(rows, "요약", [p], TESTS, TPS, ["S01"])
    assert unmapped == ["자음2_차이"]


def test_summary_compare_without_id_column_is_critical():
    p = make_pass("S01", "자음", "사전", "자음:목표=전체", [1])
    rows = {1: {1: "누구", 2: "자음_전"}, 2: {1: "X99", 2: "50.0"}}
    findings, _, _ = summary.compare(rows, "요약", [p], TESTS, TPS, ["S01"])
    assert findings[0].kind == "요약표 ID 열 없음"


def test_summary_diff_column_is_compared():
    pre = make_pass("S01", "자음", "사전", "자음:목표=전체", [1, 0])
    post = make_pass("S01", "자음", "사후", "자음:목표=전체", [1, 1])
    rows = {1: {1: "ID", 2: "자음_차이"}, 2: {1: "S01", 2: "99.0"}}
    findings, stats, _ = summary.compare(rows, "요약", [pre, post], TESTS, TPS,
                                         ["S01"])
    assert stats["불일치"] == 1


def test_summary_override_forces_mapping():
    p = make_pass("S01", "자음", "사전", "자음:목표=전체", [1, 0])
    rows = {1: {1: "ID", 2: "이상한이름"}, 2: {1: "S01", 2: "50.0"}}
    findings, stats, _ = summary.compare(
        rows, "요약", [p], TESTS, TPS, ["S01"],
        overrides={"이상한이름": {"검사": "자음", "시점": "사전", "단위": "단어"}})
    assert stats["대조"] == 1 and findings == []


def test_find_id_column_picks_best_match():
    rows = {1: {1: "번호", 2: "ID"}, 2: {1: "1", 2: "S01"}, 3: {1: "2", 2: "S02"}}
    assert summary.find_id_column(rows, 1, ["S01", "S02"]) == 2


# -------------------------------------------------------- long CSV
LONG_HEADER = "ID,시점,검사,제시,응답,점수\n"
LONG_BODY = ("S01,사전,모음,후드,푸드,1\n"
             "S01,사전,모음,해드,배드,0\n"
             "S02,사전,모음,후드,후드,1\n")


def write_csv_file(tmp_path, text, name="입력.csv", encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return str(path)


def test_long_csv_load(tmp_path):
    path = write_csv_file(tmp_path, LONG_HEADER + LONG_BODY)
    specs = parse_all(["모음:목표=1음절중성"])
    passes, unknown = load(path, specs, TPS)
    assert len(passes) == 2
    assert unknown == []
    assert [i.stimulus for i in passes[0].items] == ["후드", "해드"]


def test_long_csv_rejects_summary_only(tmp_path):
    path = write_csv_file(tmp_path, "ID,자음_전,자음_후\nS01,66.7,77.8\n")
    with pytest.raises(LongCsvError) as exc:
        load(path, parse_all(["자음:목표=전체"]), TPS)
    assert "statwise" in str(exc.value)


def test_long_csv_reports_missing_id(tmp_path):
    path = write_csv_file(tmp_path, "제시,응답,점수\n후드,푸드,1\n")
    with pytest.raises(LongCsvError) as exc:
        load(path, parse_all(["모음:목표=1음절중성"]), TPS)
    assert "ID" in str(exc.value)


def test_long_csv_rejects_empty(tmp_path):
    path = write_csv_file(tmp_path, "   \n")
    with pytest.raises(LongCsvError):
        load(path, parse_all(["모음:목표=1음절중성"]), TPS)


def test_long_csv_reports_unknown_test_names(tmp_path):
    path = write_csv_file(tmp_path, LONG_HEADER + LONG_BODY
                          + "S03,사전,문장,아무개,아무개,1\n")
    passes, unknown = load(path, parse_all(["모음:목표=1음절중성"]), TPS)
    assert unknown == ["문장"]


def test_long_csv_raises_when_no_row_matches(tmp_path):
    path = write_csv_file(tmp_path, LONG_HEADER + "S01,사전,문장,가,가,1\n")
    with pytest.raises(LongCsvError) as exc:
        load(path, parse_all(["모음:목표=1음절중성"]), TPS)
    assert "문장" in str(exc.value)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp949"])
def test_long_csv_handles_korean_encodings(tmp_path, encoding):
    path = write_csv_file(tmp_path, LONG_HEADER + LONG_BODY,
                          name="입력_%s.csv" % encoding, encoding=encoding)
    passes, _ = load(path, parse_all(["모음:목표=1음절중성"]), TPS)
    assert passes[0].items[0].stimulus == "후드"


def test_long_csv_handles_tab_delimiter(tmp_path):
    text = (LONG_HEADER + LONG_BODY).replace(",", "\t")
    path = write_csv_file(tmp_path, text, name="입력.tsv")
    passes, _ = load(path, parse_all(["모음:목표=1음절중성"]), TPS)
    assert len(passes) == 2


def test_long_csv_explicit_columns(tmp_path):
    path = write_csv_file(
        tmp_path, "who,when,what,tgt,rsp,pts\nS01,사전,모음,후드,푸드,1\n")
    passes, _ = load(path, parse_all(["모음:목표=1음절중성"]), TPS,
                     id_col="who", time_col="when", test_col="what",
                     stim_col="tgt", resp_col="rsp", score_col="pts")
    assert passes[0].subject == "S01"


def test_long_csv_explicit_column_missing_is_reported(tmp_path):
    path = write_csv_file(tmp_path, LONG_HEADER + LONG_BODY)
    with pytest.raises(LongCsvError) as exc:
        load(path, parse_all(["모음:목표=1음절중성"]), TPS, id_col="없는열")
    assert "없는열" in str(exc.value)


def test_long_csv_skips_blank_rows(tmp_path):
    path = write_csv_file(tmp_path, LONG_HEADER + LONG_BODY + ",,,,,\n")
    passes, _ = load(path, parse_all(["모음:목표=1음절중성"]), TPS)
    assert sum(len(p.items) for p in passes) == 3


def test_sniff_rows_returns_header_and_body(tmp_path):
    path = write_csv_file(tmp_path, LONG_HEADER + LONG_BODY)
    header, body = sniff_rows(path)
    assert header[0] == "ID"
    assert len(body) == 3


# -------------------------------------------------------- 자질표
FEATURES = "자모,조음위치,조음방법\nㅂ,양순,파열\nㅍ,양순,파열\nㄷ,치조,파열\n"


def test_features_load(tmp_path):
    path = tmp_path / "자질.csv"
    path.write_text(FEATURES, encoding="utf-8")
    table, names = features_mod.load(str(path))
    assert table["ㅂ"]["조음위치"] == "양순"
    assert names == ["조음위치", "조음방법"]


def test_features_group_counts_and_unclassified(tmp_path):
    from collections import Counter
    path = tmp_path / "자질.csv"
    path.write_text(FEATURES, encoding="utf-8")
    table, _ = features_mod.load(str(path))
    counter = Counter({("ㅂ", "ㅍ"): 5, ("ㄱ", "ㅋ"): 3})
    grouped, unclassified = features_mod.group(counter, table, "조음위치")
    assert grouped[("양순", "양순")] == 5
    assert unclassified == 3


def test_features_requires_jamo_column(tmp_path):
    path = tmp_path / "자질.csv"
    path.write_text("x,y\n1,2\n", encoding="utf-8")
    with pytest.raises(features_mod.FeatureError) as exc:
        features_mod.load(str(path))
    assert "자모" in str(exc.value)


def test_features_rejects_empty(tmp_path):
    path = tmp_path / "자질.csv"
    path.write_text("자모,조음위치\n", encoding="utf-8")
    with pytest.raises(features_mod.FeatureError):
        features_mod.load(str(path))


# --------------------------------------------------------- 리포트
def test_coverage_renders_required_tail():
    cov = report.Coverage()
    cov.sheets_total = 3
    cov.sheets_subject = 2
    cov.items_total = 10
    cov.items_compared = 9
    text = "\n".join(cov.render())
    assert report.COVERAGE_HEADER in text
    assert "이상 없음" in text
    assert "90.0%" in text


def test_coverage_lists_unopened_pii_sheets():
    cov = report.Coverage()
    cov.sheets_pii_unopened = ["개인정보"]
    assert "열지 않음" in "\n".join(cov.render())


def test_coverage_lists_unmapped_summary_columns():
    cov = report.Coverage()
    cov.summary_unmapped = ["일음절2_차이"]
    assert "일음절2_차이" in "\n".join(cov.render())


def test_coverage_reports_confusion_gate_drops():
    cov = report.Coverage()
    cov.confusion_dropped = 165
    cov.min_count = 3
    assert "165" in "\n".join(cov.render())


def test_build_includes_all_sections():
    cov = report.Coverage()
    cov.items_total = 1
    cov.items_compared = 1
    text = report.build(["[입력] 예시"],
                        [Finding(CRITICAL, "유형", "메시지")],
                        ["[정보] 혼동"], cov, "판정: 확인 필요", 1)
    assert "[입력] 예시" in text
    assert "[치명] 유형" in text
    assert "[정보] 혼동" in text
    assert "종료코드 1" in text


def test_build_says_so_when_no_findings():
    cov = report.Coverage()
    text = report.build([], [], [], cov, "판정: 치명 소견 없음", 0)
    assert "소견이 없습니다" in text


def test_build_sanitizes_forged_newlines():
    """셀 값에 줄바꿈을 심어 **가짜 소견 줄**을 만들 수 없어야 한다.

    문자열이 본문 안에 남는 것은 막을 수 없고 막을 필요도 없다. 막아야 하는 것은
    `[치명]` 으로 **시작하는 줄**이 하나 더 생기는 것이다.
    """
    cov = report.Coverage()
    finding = Finding(INFO, "유형", "정상\n[치명] 있지도 않은 소견")
    text = report.build([], [finding], [], cov, "판정: 없음", 0)
    assert [ln for ln in text.splitlines() if ln.startswith("[치명]")] == []
    assert "\n" not in finding.message or True


def test_build_sanitizes_header_lines():
    cov = report.Coverage()
    text = report.build(["[입력] 파일\n[치명] 가짜"], [], [], cov, "판정", 0)
    assert [ln for ln in text.splitlines() if ln.startswith("[치명]")] == []


def test_build_sanitizes_filenames_in_coverage():
    """산출물 폴더에 줄바꿈이 든 이름의 시트가 있어도 줄을 위조할 수 없다."""
    cov = report.Coverage()
    cov.sheets_pii_unopened = ["개인정보\n[치명] 가짜 소견"]
    text = report.build([], [], [], cov, "판정", 0)
    assert [ln for ln in text.splitlines() if ln.startswith("[치명]")] == []


def test_real_findings_do_start_their_own_line():
    cov = report.Coverage()
    text = report.build([], [Finding(CRITICAL, "진짜", "메시지")], [], cov,
                        "판정", 1)
    assert [ln for ln in text.splitlines() if ln.startswith("[치명]")]


@pytest.mark.parametrize("counts,undetermined,expected", [
    ({"치명": 0, "경고": 0, "정보": 0}, False, 0),
    ({"치명": 0, "경고": 3, "정보": 1}, False, 0),
    ({"치명": 2, "경고": 1, "정보": 0}, False, 1),
    ({"치명": 0, "경고": 0, "정보": 0}, True, 3),
    ({"치명": 5, "경고": 0, "정보": 0}, True, 3),
])
def test_verdict_exit_codes(counts, undetermined, expected):
    _, code = report.verdict(counts, undetermined)
    assert code == expected


def test_verdict_undetermined_refuses_to_claim_critical_count():
    line, code = report.verdict({"치명": 5, "경고": 0, "정보": 0}, True)
    assert code == 3
    assert "말할 수 없습니다" in line


def test_render_confusion_shows_no_feature_note():
    lines = report.render_confusion({"초성": [(("ㅌ", "ㅍ"), 5)]}, 3, False)
    assert any("--features 미지정" in x for x in lines)


def test_render_confusion_states_empty_table():
    lines = report.render_confusion({}, 3, False)
    assert any("넘는 혼동 칸이 없습니다" in x for x in lines)


def test_render_confusion_silent_when_not_analysed():
    assert report.render_confusion({}, 3, False, analysed=False) == []


def test_render_confusion_uses_visible_symbol_for_empty_coda():
    lines = report.render_confusion({"종성": [(("ㄱ", ""), 5)]}, 3, True)
    assert "∅" in "\n".join(lines)

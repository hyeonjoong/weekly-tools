# -*- coding: utf-8 -*-
"""소견 생성기 — 각 검사가 잡아야 할 것을 잡고, 잡지 말아야 할 것을 안 잡는가."""

import pytest

from jamoscore import analyze
from jamoscore.findings import CRITICAL, INFO, WARNING, Finding, count_by_severity, sort_findings
from jamoscore.reader import Item, Pass
from jamoscore.spec import parse_subtest


def make_pass(subject="S01", test="모음", tp="사전", spec="모음:목표=1음절중성",
              items=(), reported=None):
    s = parse_subtest(spec)
    p = Pass(subject, test, tp, s, subject, 1)
    for seq, (stim, resp, score) in enumerate(items, start=1):
        p.items.append(Item(subject, test, tp, s.unit, seq, seq + 1, 1,
                            stim, resp, score, subject))
    p.reported = reported
    p.reported_row = 99 if reported else None
    return p


def scored(*args, **kwargs):
    p = make_pass(*args, **kwargs)
    analyze.score_items([p])
    return p


# ------------------------------------------------------------ 채점 읽기
def test_score_items_reads_integers_and_floats():
    p = scored(items=[("후드", "후드", "1"), ("히드", "히드", "1.0")])
    assert [i.human for i in p.items] == [1.0, 1.0]


def test_score_items_rejects_out_of_range_as_unreadable():
    p = scored(items=[("후드", "후드", "5")])
    assert p.items[0].human is None


def test_score_items_treats_text_as_unreadable():
    p = scored(items=[("후드", "후드", "정답")])
    assert p.items[0].human is None
    assert p.percent is None


def test_phoneme_scores_up_to_max():
    p = scored(test="일음절", spec="일음절:목표=초중종,만점=3",
               items=[("김", "귄", "2"), ("햄", "햄", "3")])
    assert [i.human for i in p.items] == [2.0, 3.0]
    assert p.denominator == 6
    assert p.percent == pytest.approx(83.3, abs=0.05)


# ------------------------------------------------------------ 점수 칸
def test_check_score_cells_flags_text():
    p = scored(items=[("후드", "후드", "O"), ("히드", "히드", "1")])
    out = analyze.check_score_cells([p])
    assert any(f.kind == "점수 칸에 숫자 아닌 값" for f in out)


def test_check_score_cells_flags_out_of_range_as_critical():
    p = scored(items=[("후드", "후드", "7")])
    out = analyze.check_score_cells([p])
    # 7 은 숫자로 읽히지만 만점 1 을 벗어난다
    assert any(f.severity == CRITICAL for f in out)


def test_check_score_cells_silent_when_clean():
    p = scored(items=[("후드", "후드", "1"), ("히드", "히드", "0")])
    assert analyze.check_score_cells([p]) == []


# ------------------------------------------------------------ 응답 칸
def test_memo_in_response_is_flagged():
    p = scored(items=[("흐드", "뜨디(휴지라고 말하는 것 같았음)", "0")])
    out = analyze.check_response_cells([p])
    assert any(f.kind == "응답 칸에 관찰 메모 혼입" for f in out)


def test_non_hangul_response_is_flagged():
    p = scored(items=[("아가", "아디?", "0")])
    out = analyze.check_response_cells([p])
    assert any(f.kind == "응답 칸에 한글 외 문자" for f in out)


def test_blank_response_with_score_is_flagged():
    p = scored(items=[("아가", "", "0")])
    out = analyze.check_response_cells([p])
    assert any(f.kind == "응답이 비었는데 점수가 있음" for f in out)


def test_response_without_score_is_flagged():
    p = scored(items=[("아가", "아가", "")])
    out = analyze.check_response_cells([p])
    assert any(f.kind == "응답은 있는데 점수가 비었음" for f in out)


def test_clean_responses_produce_nothing():
    p = scored(items=[("후드", "후드", "1"), ("히드", "히드", "0")])
    assert analyze.check_response_cells([p]) == []


# --------------------------------------------------------- 규칙 대조
def test_rule_comparison_finds_only_disagreements():
    p = scored(items=[
        ("후드", "푸드", "1"),     # 규칙 1, 사람 1 → 일치
        ("해드", "배드", "0"),     # 규칙 1, 사람 0 → 불일치
    ])
    findings, rows, stats = analyze.compare_rule([p])
    assert stats["비교"] == 2 and stats["일치"] == 1 and stats["불일치"] == 1
    assert len(rows) == 1
    assert rows[0]["제시"] == "해드"
    assert findings[0].severity == WARNING


def test_rule_comparison_counts_undecidable_separately():
    p = scored(items=[("후드", "", "0"), ("히드", "히드", "1")])
    _, rows, stats = analyze.compare_rule([p])
    assert stats["대조불가"] == 1
    assert stats["사유:응답 공란"] == 1
    assert rows == []


def test_rule_comparison_silent_when_all_agree():
    p = scored(items=[("후드", "푸드", "1"), ("히드", "미드", "1")])
    findings, rows, _ = analyze.compare_rule([p])
    assert findings == [] and rows == []


def test_rule_comparison_vocabulary_does_not_judge():
    p = scored(items=[("해드", "배드", "0")])
    findings, _, _ = analyze.compare_rule([p])
    text = findings[0].kind + findings[0].message
    assert "확인 필요" in text
    assert "판정하지 않습니다" in findings[0].message


# --------------------------------------------------- 동일 쌍 다른 채점
def test_identical_pair_scored_differently_is_found():
    a = scored(subject="S01", items=[("햐드", "샤드", "1")])
    b = scored(subject="S02", items=[("햐드", "샤드", "0")])
    findings, rows = analyze.inconsistent_pairs([a, b])
    assert len(findings) == 1
    assert findings[0].kind == "동일 쌍 다른 채점"
    assert len(rows) == 2


def test_identical_pair_consistent_is_silent():
    a = scored(subject="S01", items=[("햐드", "샤드", "1")])
    b = scored(subject="S02", items=[("햐드", "샤드", "1")])
    findings, rows = analyze.inconsistent_pairs([a, b])
    assert findings == [] and rows == []


def test_identical_pair_check_is_independent_of_rules():
    """규칙이 무엇이든 성립하는 소견이라 규칙 대조와 따로 나와야 한다."""
    a = scored(subject="S01", spec="모음:목표=전체", items=[("햐드", "샤드", "1")])
    b = scored(subject="S02", spec="모음:목표=전체", items=[("햐드", "샤드", "0")])
    findings, _ = analyze.inconsistent_pairs([a, b])
    assert len(findings) == 1


def test_identical_pair_ignores_blank_responses():
    a = scored(subject="S01", items=[("햐드", "", "1")])
    b = scored(subject="S02", items=[("햐드", "", "0")])
    findings, _ = analyze.inconsistent_pairs([a, b])
    assert findings == []


def test_identical_pair_subject_list_is_capped():
    passes = [scored(subject="S%02d" % i, items=[("햐드", "샤드", "1")])
              for i in range(1, 20)]
    passes.append(scored(subject="S99", items=[("햐드", "샤드", "0")]))
    findings, _ = analyze.inconsistent_pairs(passes)
    assert "외 " in findings[0].message


# ------------------------------------------------ 두 패스 전사 불일치
def test_cross_pass_transcript_mismatch():
    a = scored(test="일음절", tp="사후", spec="일음절:단위=단어,목표=전체",
               items=[("김", "김", "1")])
    b = scored(test="일음절", tp="사후", spec="일음절:단위=음소,목표=초중종,만점=3",
               items=[("김", "귄", "2")])
    out = analyze.cross_pass_transcripts([a, b])
    assert len(out) == 1
    assert "김" in out[0].message


def test_cross_pass_transcript_agreement_is_silent():
    a = scored(test="일음절", tp="사후", spec="일음절:단위=단어,목표=전체",
               items=[("김", "김", "1")])
    b = scored(test="일음절", tp="사후", spec="일음절:단위=음소,목표=초중종,만점=3",
               items=[("김", "김", "3")])
    assert analyze.cross_pass_transcripts([a, b]) == []


# ------------------------------------------------------ 시트 요약 대조
def test_in_sheet_summary_match_is_silent():
    p = scored(items=[("후드", "후드", "1"), ("히드", "히드", "0")], reported="50.0")
    out, compared, mismatched = analyze.check_inhsheet_summary([p])
    assert out == [] and compared == 1 and mismatched == 0


def test_in_sheet_summary_mismatch_is_critical():
    p = scored(items=[("후드", "후드", "1"), ("히드", "히드", "0")], reported="100.0")
    out, _, mismatched = analyze.check_inhsheet_summary([p])
    assert mismatched == 1
    assert out[0].severity == CRITICAL
    assert "분모 2" in out[0].evidence


def test_in_sheet_summary_rounding_tolerance():
    """14/18 = 77.777… 은 77.8 로 적히는 것이 정상이다."""
    items = [("후드", "후드", "1")] * 14 + [("히드", "미드", "0")] * 4
    p = scored(items=items, reported="77.8")
    out, _, mismatched = analyze.check_inhsheet_summary([p])
    assert mismatched == 0


def test_in_sheet_summary_uncomparable_is_aggregated():
    p1 = scored(items=[("후드", "후드", "?")], reported="50.0")
    p2 = scored(subject="S02", items=[("후드", "후드", "?")], reported="50.0")
    out, _, _ = analyze.check_inhsheet_summary([p1, p2])
    assert len([f for f in out if f.kind == "시트 요약과 대조 불가"]) == 1


# ---------------------------------------------------------- 불가능값
@pytest.mark.parametrize("value,denom,ok", [
    (77.8, 18, True), (66.7, 18, True), (70.0, 18, False), (0.0, 18, True),
    (100.0, 18, True), (71.4, 14, True), (72.0, 14, False), (81.5, 54, True),
    (50.0, 2, True), (33.3, 3, True), (35.0, 3, False), (12.0, 18, False),
])
def test_reachable_percent(value, denom, ok):
    assert analyze.reachable_percent(value, denom) is ok


def test_impossible_percent_is_critical():
    items = [("후드", "후드", "1")] * 14 + [("히드", "미드", "0")] * 4
    p = scored(items=items, reported="70.0")
    out = analyze.impossible_percent([p])
    assert out and out[0].severity == CRITICAL
    assert "18" in out[0].message


def test_possible_percent_is_not_flagged():
    items = [("후드", "후드", "1")] * 14 + [("히드", "미드", "0")] * 4
    p = scored(items=items, reported="77.8")
    assert analyze.impossible_percent([p]) == []


def test_nearest_percents_lists_neighbours():
    text = analyze.nearest_percents(70.0, 18)
    assert "18" in text and "%" in text


# -------------------------------------------------------- 바닥·천장
def test_ceiling_and_floor_are_aggregated():
    ceil = scored(subject="S01", items=[("후드", "후드", "1")])
    floor = scored(subject="S02", items=[("후드", "미드", "0")])
    out = analyze.floor_ceiling([ceil, floor])
    kinds = {f.kind for f in out}
    assert kinds == {"천장 도달", "바닥 도달"}
    assert all(f.severity == INFO for f in out)
    assert len(out) == 2          # 블록마다 한 줄씩 쏟아내지 않는다


def test_ceiling_collapses_many_blocks_into_one_line():
    passes = [scored(subject="S%02d" % i, test=t, items=[("후드", "후드", "1")])
              for i in range(1, 5) for t in ("자음", "모음")]
    out = analyze.floor_ceiling(passes)
    assert len([f for f in out if f.kind == "천장 도달"]) == 1


def test_blank_pass_is_not_counted_as_floor():
    """응답이 전부 비었는데 0점인 블록은 '바닥 수행'이 아니다."""
    p = scored(items=[("후드", "", "0"), ("히드", "", "0")])
    assert analyze.floor_ceiling([p]) == []
    findings, keys = analyze.blank_passes([p])
    assert findings and findings[0].kind == "응답이 전부 비었는데 0점"
    assert p.key in keys


def test_blank_pass_requires_all_blank():
    p = scored(items=[("후드", "후드", "0"), ("히드", "", "0")])
    findings, keys = analyze.blank_passes([p])
    assert findings == [] and keys == set()


def test_duplicate_block_is_critical():
    a = scored(subject="S01", items=[("후드", "후드", "1")])
    b = scored(subject="S01", items=[("후드", "미드", "0")])
    out = analyze.duplicate_blocks([a, b])
    assert out and out[0].severity == CRITICAL
    assert "2번 나옵니다" in out[0].message


def test_ceiling_message_states_no_room_to_improve():
    p = scored(items=[("후드", "후드", "1")])
    out = analyze.floor_ceiling([p])
    assert "구조적으로 없습니다" in out[0].message


def test_partial_score_is_neither_floor_nor_ceiling():
    p = scored(items=[("후드", "후드", "1"), ("히드", "미드", "0")])
    assert analyze.floor_ceiling([p]) == []


# ---------------------------------------------------------- 혼동행렬
def test_confusion_skips_non_response_cells():
    """`모름`(2음절) 을 자리별로 대조하면 '모든 모음이 ㅗ로 간다'는 가짜 패턴이 생긴다."""
    p = scored(items=[("후드", "모름", "0"), ("히드", "히드", "1")])
    _, gated, _, skipped = analyze.confusion([p], min_count=1)
    assert skipped == 1
    assert ("ㅜ", "ㅗ") not in gated.get("중성", {})


def test_confusion_skips_responses_of_different_length():
    p = scored(items=[("후드", "조", "0"), ("히드", "히드", "1")])
    _, gated, _, skipped = analyze.confusion([p], min_count=1)
    assert skipped == 1


def test_non_response_is_excluded_from_rule_comparison():
    p = scored(items=[("후드", "모름", "0")])
    _, rows, stats = analyze.compare_rule([p])
    assert rows == []
    assert stats["사유:비전사 응답(모름 등)"] == 1


@pytest.mark.parametrize("text,expected", [
    ("모름", True), ("무응답", True), ("못 들음", True), ("없음", True),
    ("푸드", False), ("해디", False), ("", False), ("모드", False),
])
def test_is_non_response(text, expected):
    assert analyze.is_non_response(text) is expected


def test_custom_non_response_marker():
    assert analyze.is_non_response("NR", ["NR"]) is True


def test_confusion_counts_pairs():
    p = scored(items=[("후드", "호드", "0"), ("휘드", "히드", "0"),
                      ("후드", "호드", "0")])
    raw, gated, dropped, skipped = analyze.confusion([p], min_count=2)
    assert raw["중성"][("ㅜ", "ㅗ")] == 2
    assert ("ㅟ", "ㅣ") not in gated["중성"]
    assert dropped == 1


def test_confusion_keeps_diagonal_regardless_of_gate():
    p = scored(items=[("후드", "푸드", "1")])
    _, gated, _, _ = analyze.confusion([p], min_count=99)
    assert gated["중성"][("ㅜ", "ㅜ")] == 1


def test_top_confusions_excludes_diagonal():
    p = scored(items=[("후드", "후드", "1")] * 5 + [("후드", "호드", "0")] * 3)
    _, gated, _, _ = analyze.confusion([p], min_count=3)
    top = analyze.top_confusions(gated)
    assert top["중성"] == [(("ㅜ", "ㅗ"), 3)]


def test_confusion_covers_all_three_jamo_positions():
    p = scored(test="일음절", spec="일음절:목표=초중종,만점=3",
               items=[("김", "귄", "1")] * 3)
    _, gated, _, _ = analyze.confusion([p], min_count=3)
    assert set(gated) == {"초성", "중성", "종성"}


# ------------------------------------------------------------ 임계차
def test_critical_difference_rows_and_verdicts():
    pre = scored(items=[("후드", "후드", "1")] * 12 + [("히드", "미드", "0")] * 6)
    post = scored(tp="사후", items=[("후드", "후드", "1")] * 14
                  + [("히드", "미드", "0")] * 4)
    rows, findings = analyze.critical_differences([pre, post], ["사전", "사후"])
    assert len(rows) == 1
    assert rows[0]["판정"] == "미달"          # 2문항 변화는 임계차에 못 미친다
    assert findings and findings[0].severity == INFO


def test_critical_difference_skips_phoneme_unit():
    """음소 점수는 한 단어 안에서 독립이 아니므로 이항 가정을 쓰지 않는다."""
    pre = scored(test="일음절", spec="일음절:목표=초중종,만점=3",
                 items=[("김", "김", "3")])
    post = scored(test="일음절", tp="사후", spec="일음절:목표=초중종,만점=3",
                  items=[("김", "귄", "1")])
    rows, _ = analyze.critical_differences([pre, post], ["사전", "사후"])
    assert rows == []


def test_critical_difference_undetermined_without_post():
    pre = scored(items=[("후드", "후드", "1")] * 18)
    rows, _ = analyze.critical_differences([pre], ["사전", "사후"])
    assert rows[0]["판정"] == "판정불가"


def test_critical_difference_undetermined_when_item_counts_differ():
    pre = scored(items=[("후드", "후드", "1")] * 18)
    post = scored(tp="사후", items=[("후드", "후드", "1")] * 14)
    rows, _ = analyze.critical_differences([pre, post], ["사전", "사후"])
    assert rows[0]["판정"] == "판정불가"


def test_critical_difference_detects_large_change():
    pre = scored(items=[("후드", "후드", "1")] * 4 + [("히드", "미드", "0")] * 14)
    post = scored(tp="사후", items=[("후드", "후드", "1")] * 17
                  + [("히드", "미드", "0")])
    rows, _ = analyze.critical_differences([pre, post], ["사전", "사후"])
    assert rows[0]["판정"] == "초과"


def test_critical_difference_needs_two_timepoints():
    pre = scored(items=[("후드", "후드", "1")])
    assert analyze.critical_differences([pre], ["사전"]) == ([], [])


# ------------------------------------------------------- 정답 시트 대조
KEY = {"모음": {"모음1": ["후드", "히드", "효드"], "모음2": ["효드", "후드", "히드"]}}


def test_answer_key_exact_match_is_silent():
    p = scored(items=[("후드", "후드", "1"), ("히드", "히드", "1"),
                      ("효드", "효드", "1")])
    out, unmatched, assign = analyze.check_answer_key([p], KEY, "정답")
    assert [f for f in out if f.severity == CRITICAL] == []
    assert assign[p.key].column == "모음1"
    assert assign[p.key].order_same is True


def test_answer_key_counterbalanced_list_is_not_an_error():
    """사전에 2번 리스트를 쓰는 상쇄균형 설계를 불일치로 토해내면 안 된다."""
    p = scored(items=[("효드", "효드", "1"), ("후드", "후드", "1"),
                      ("히드", "히드", "1")])
    out, _, assign = analyze.check_answer_key([p], KEY, "정답")
    assert [f for f in out if f.severity == CRITICAL] == []
    assert assign[p.key].column == "모음2"


def test_answer_key_shuffled_order_is_info_only():
    p = scored(items=[("히드", "히드", "1"), ("후드", "후드", "1"),
                      ("효드", "효드", "1")])
    out, _, assign = analyze.check_answer_key([p], KEY, "정답")
    assert [f for f in out if f.severity == CRITICAL] == []
    assert assign[p.key].order_same is False
    assert any(f.kind == "제시 순서가 정답 시트와 다름" for f in out)


def test_answer_key_missing_item_is_critical():
    p = scored(items=[("후드", "후드", "1"), ("히드", "히드", "1")])
    out, _, _ = analyze.check_answer_key([p], KEY, "정답")
    crit = [f for f in out if f.severity == CRITICAL]
    assert crit and "빠진 자극" in crit[0].message


def test_answer_key_duplicate_item_is_critical():
    p = scored(items=[("후드", "후드", "1"), ("후드", "후드", "1"),
                      ("히드", "히드", "1")])
    out, _, _ = analyze.check_answer_key([p], KEY, "정답")
    crit = [f for f in out if f.severity == CRITICAL]
    assert crit and "중복 자극" in crit[0].message


def test_answer_key_unmatched_test_is_reported_not_silent():
    p = scored(test="자음", spec="자음:목표=2음절초성",
               items=[("아라", "아라", "1")])
    out, unmatched, _ = analyze.check_answer_key([p], KEY, "정답")
    assert unmatched == ["자음"]


def test_answer_key_skips_empty_pass():
    p = scored(items=[])
    out, _, _ = analyze.check_answer_key([p], KEY, "정답")
    assert [f for f in out if f.severity == CRITICAL] == []


# ------------------------------------------------ 사전·사후 같은 리스트
def test_same_list_identical_order_is_warning_not_critical():
    """단일 목록 검사(K-CNC 계열)에서는 정상이므로 치명이면 안 된다."""
    pre = scored(items=[("후드", "후드", "1"), ("히드", "미드", "0")])
    post = scored(tp="사후", items=[("후드", "푸드", "1"), ("히드", "히드", "1")])
    out = analyze.check_same_list_across_time([pre, post], ["사전", "사후"])
    assert out[0].severity == WARNING
    assert "K-CNC" in out[0].message


def test_identical_stimuli_and_responses_is_critical():
    """자극도 응답도 한 글자도 다르지 않으면 복사 의심 — 이건 치명."""
    pre = scored(items=[("후드", "푸드", "1"), ("히드", "히드", "1")])
    post = scored(tp="사후", items=[("후드", "푸드", "1"), ("히드", "히드", "1")])
    out = analyze.check_same_list_across_time([pre, post], ["사전", "사후"])
    assert out[0].severity == CRITICAL
    assert "한 글자도" in out[0].message


def test_same_set_different_order_is_warning():
    pre = scored(items=[("후드", "후드", "1"), ("히드", "히드", "1")])
    post = scored(tp="사후", items=[("히드", "히드", "1"), ("후드", "후드", "1")])
    out = analyze.check_same_list_across_time([pre, post], ["사전", "사후"])
    assert out[0].severity == WARNING
    assert "의도한 것일 수 있지만" in out[0].message


def test_different_lists_are_silent():
    pre = scored(items=[("후드", "후드", "1")])
    post = scored(tp="사후", items=[("히드", "히드", "1")])
    assert analyze.check_same_list_across_time([pre, post], ["사전", "사후"]) == []


# ---------------------------------------------------------- Finding
def test_finding_rejects_unknown_severity():
    with pytest.raises(ValueError):
        Finding("심각", "유형", "메시지")


def test_findings_sort_critical_first():
    items = [Finding(INFO, "b", "m"), Finding(CRITICAL, "a", "m"),
             Finding(WARNING, "c", "m")]
    assert [f.severity for f in sort_findings(items)] == [CRITICAL, WARNING, INFO]


def test_count_by_severity():
    items = [Finding(CRITICAL, "a", "m"), Finding(CRITICAL, "b", "m"),
             Finding(WARNING, "c", "m")]
    counts = count_by_severity(items)
    assert counts[CRITICAL] == 2 and counts[WARNING] == 1 and counts[INFO] == 0


def test_finding_row_has_six_columns():
    assert len(Finding(INFO, "k", "m", "S01", "loc", "ev").as_row()) == 6

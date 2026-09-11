# -*- coding: utf-8 -*-
"""``--subtest`` 명세 파싱과 목표음소 규칙 채점.

여기의 회귀 픽스처는 실제 채점 엑셀에서 나온 **익명화된 (제시, 응답) 쌍 문자열**
이다. 원본 파일은 예제에 복사하지 않는다.
"""

import pytest

from jamoscore.spec import Rule, SpecError, UNDECIDABLE, parse_all, parse_subtest

# 실측: 문자열이 다른데 사람이 1점을 준 쌍 — 전부 **정상 채점**이어야 한다.
# (자음검사는 2음절 초성만, 모음검사는 1음절 중성만 맞으면 정답)
NORMAL_CONSONANT = [
    ("아나", "아놔"), ("아다", "아댜"), ("아따", "안따"), ("아라", "나라"),
    ("아라", "라라"), ("아마", "하마"), ("아짜", "가짜"), ("아짜", "안짜"),
    ("아하", "아화"),
]
NORMAL_VOWEL = [
    ("하드", "가드"), ("하드", "다드"), ("하드", "밥을"), ("하드", "카드"),
    ("해드", "개굴"), ("해드", "새근"), ("해드", "애디"), ("해드", "패드"),
    ("해드", "해그"), ("해드", "해디"), ("해드", "해비"), ("해드", "해조"),
    ("해드", "해지"), ("해드", "해피"), ("해드", "핸드"), ("햐드", "샤드"),
    ("햐드", "햐그"), ("허드", "머드"), ("허드", "어드"), ("허드", "퍼드"),
    ("허드", "허기"), ("허드", "허두"), ("혀드", "혀두"), ("호드", "또드"),
    ("호드", "보들"), ("호드", "오드"), ("호드", "포드"), ("호드", "호두"),
    ("화드", "과드"), ("화드", "꽈드"), ("화드", "와드"), ("화드", "콰드"),
    ("화드", "화두"), ("회드", "꾀드"), ("후드", "부드"), ("후드", "부들"),
    ("후드", "푸드"), ("휴드", "슈드"), ("휴드", "쥬스"), ("휴드", "휴지"),
    ("흐드", "흐르"), ("히드", "기드"), ("히드", "띠드"), ("히드", "띠디"),
    ("히드", "미드"), ("히드", "힘드"),
]
# 같은 실데이터에서 사람 점수와 규칙 점수가 갈렸던 4건(사람=0, 규칙=1).
DISAGREEMENTS = [
    ("호드", "조"),
    ("햐드", "샤드"),
    ("해드", "배드"),
    ("흐드", "뜨디(휴지라고 말하는 것 같았음)"),
]


@pytest.mark.parametrize("stim,resp", NORMAL_CONSONANT)
def test_consonant_rule_accepts_known_normal_pairs(stim, resp):
    """자음검사: 가운데 자음만 맞으면 정답 — 55종 정상 쌍을 전부 통과시켜야 한다."""
    rule = Rule("2음절초성")
    score, _ = rule.score(stim, resp)
    assert score == 1, (stim, resp)


@pytest.mark.parametrize("stim,resp", NORMAL_VOWEL)
def test_vowel_rule_accepts_known_normal_pairs(stim, resp):
    rule = Rule("1음절중성")
    score, _ = rule.score(stim, resp)
    assert score == 1, (stim, resp)


def test_normal_pairs_would_all_fail_naive_string_equality():
    """문자열 비교로 판정하면 첫 실행에서 55건을 토해낸다 — 그래서 쓰지 않는다."""
    pairs = NORMAL_CONSONANT + NORMAL_VOWEL
    # 실데이터에서 '문자열이 다른데 사람이 1점을 준' 쌍은 정확히 55종이었다.
    assert len(pairs) == 55
    assert len(set(pairs)) == 55
    assert all(a != b for a, b in pairs)


@pytest.mark.parametrize("stim,resp", DISAGREEMENTS)
def test_known_disagreements_score_one_by_rule(stim, resp):
    """실측 4건: 규칙은 1점, 사람은 0점 — 툴은 '확인 필요'로만 내보내야 한다."""
    rule = Rule("1음절중성")
    score, _ = rule.score(stim, resp)
    assert score == 1


def test_memo_contaminated_response_still_decidable():
    """응답 칸에 관찰 메모가 섞여도 첫 음절은 읽힌다(별도 위생 경고로 나간다)."""
    score, reason = Rule("1음절중성").score("흐드", "뜨디(휴지라고 말하는 것 같았음)")
    assert score == 1
    assert "ㅡ" in reason


@pytest.mark.parametrize("stim,resp,expected", [
    ("김", "김", 3), ("김", "귄", 1), ("넷", "맵", 0), ("종", "좀", 2),
    ("앞", "앞", 3), ("닭", "밥", 1), ("뻥", "뻠", 2), ("책", "새", 1),
    ("톱", "김", 0), ("핀", "힘", 1),
])
def test_phoneme_rule_scores(stim, resp, expected):
    score, _ = Rule("초중종").score(stim, resp)
    assert score == expected


def test_phoneme_rule_max_points_is_three():
    assert Rule("초중종").max_points == 3


def test_whole_word_rule():
    assert Rule("전체").score("햄", "햄")[0] == 1
    assert Rule("전체").score("햄", "햅")[0] == 0


@pytest.mark.parametrize("stim,resp,reason", [
    ("", "가", "제시 자극 공란"),
    ("가", "", "응답 공란"),
])
def test_rule_reports_blank_cells(stim, resp, reason):
    score, got = Rule("1음절중성").score(stim, resp)
    assert score == UNDECIDABLE
    assert got == reason


def test_rule_undecidable_when_syllable_missing():
    score, reason = Rule("2음절초성").score("아라", "아")
    assert score == UNDECIDABLE
    assert "응답" in reason


def test_rule_undecidable_for_non_hangul_response():
    score, _ = Rule("1음절중성").score("후드", "???")
    assert score == UNDECIDABLE


def test_coda_rule_handles_missing_coda():
    assert Rule("1음절종성").score("가", "가")[0] == 1
    assert Rule("1음절종성").score("가", "간")[0] == 0
    assert Rule("1음절종성").score("간", "간")[0] == 1


@pytest.mark.parametrize("bad", ["1음절받침", "0음절초성", "초성중성", "", "전부"])
def test_rule_rejects_unknown_targets(bad):
    with pytest.raises(SpecError):
        Rule(bad)


def test_parse_subtest_minimal():
    spec = parse_subtest("자음:목표=2음절초성")
    assert spec.name == "자음"
    assert spec.max_points == 1
    assert spec.unit == "단어"
    assert spec.n_items is None


def test_parse_subtest_full():
    spec = parse_subtest("일음절:단위=음소,목표=초중종,문항=18,만점=3")
    assert (spec.name, spec.unit, spec.n_items, spec.max_points) == (
        "일음절", "음소", 18, 3)


def test_unit_defaults_to_phoneme_when_max_points_gt_one():
    assert parse_subtest("일음절:목표=초중종").unit == "음소"


@pytest.mark.parametrize("bad,fragment", [
    ("자음", "이름:목표"),
    ("자음:문항=18", "목표="),
    ("자음:목표=2음절초성,문항=0", "1 이상"),
    ("자음:목표=2음절초성,문항=abc", "정수가 아닙니다"),
    ("자음:목표=2음절초성,만점=3", "만점"),
    ("자음:목표=2음절초성,이상한키=1", "모르는 키"),
    ("자음:목표=2음절초성,문항", "키=값"),
    ("자음:목표=2음절초성,단위=문장", "단위는"),
    (":목표=전체", "검사 이름"),
    ("자음:목표=전체,목표=전체", "두 번"),
])
def test_parse_subtest_rejects(bad, fragment):
    with pytest.raises(SpecError) as exc:
        parse_subtest(bad)
    assert fragment in str(exc.value)


def test_max_points_must_agree_with_rule():
    """만점을 추론하지 않는 대신, 명시한 값이 규칙과 어긋나면 멈춘다."""
    with pytest.raises(SpecError) as exc:
        parse_subtest("일음절:목표=초중종,만점=2")
    assert "추론하지 않습니다" in str(exc.value)


def test_parse_all_assigns_pass_index():
    specs = parse_all(["일음절:단위=단어,목표=전체,문항=18",
                       "일음절:단위=음소,목표=초중종,문항=18,만점=3"])
    assert [s.index for s in specs] == [0, 1]


def test_parse_all_rejects_duplicate_unit():
    with pytest.raises(SpecError) as exc:
        parse_all(["자음:목표=2음절초성", "자음:목표=1음절중성"])
    assert "단위=단어" in str(exc.value)


def test_targets_for_confusion_matrix():
    assert Rule("2음절초성").targets("아라", "라라") == [("초성", "ㄹ", "ㄹ")]
    assert Rule("초중종").targets("김", "귄") == [
        ("초성", "ㄱ", "ㄱ"), ("중성", "ㅣ", "ㅟ"), ("종성", "ㅁ", "ㄴ")]


def test_targets_empty_when_undecidable():
    assert Rule("초중종").targets("김", "") == []
    assert Rule("전체").targets("김", "김") == []


def test_spec_label_is_unique_per_pass():
    specs = parse_all(["일음절:단위=단어,목표=전체", "일음절:단위=음소,목표=초중종"])
    assert len({s.label for s in specs}) == 2

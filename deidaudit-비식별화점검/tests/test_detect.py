"""직접식별자 탐지 — 손으로 계산한 값과 대조합니다."""

from __future__ import annotations

import pytest

from deidaudit.detect import (
    is_korean_name,
    scan_landline,
    rrn_checksum_ok,
    rrn_date_ok,
    scan_email,
    scan_free_text_person,
    scan_name_cell,
    scan_phone,
    scan_rrn,
    scan_structured,
)
from deidaudit.findings import CRITICAL, WARNING


def _hand_checksum(first12: str) -> int:
    """가중치 2,3,4,5,6,7,8,9,2,3,4,5 로 손으로 다시 계산합니다."""
    weights = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]
    total = 0
    for digit, weight in zip(first12, weights):
        total += int(digit) * weight
    return (11 - (total % 11)) % 10


@pytest.mark.parametrize("first12", ["880402312345", "010203112233", "991231498765", "000101300000"])
def test_checksum_matches_hand_computation(first12):
    expected = _hand_checksum(first12)
    assert rrn_checksum_ok(first12 + str(expected))
    for wrong in range(10):
        if wrong != expected:
            assert not rrn_checksum_ok(first12 + str(wrong))


def test_checksum_known_value():
    # 880402-312345? → 2*8+3*8+4*0+5*4+6*0+7*2+8*3+9*1+2*2+3*3+4*4+5*5
    #                = 16+24+0+20+0+14+24+9+4+9+16+25 = 161 ; 161 % 11 = 7 ; (11-7)%10 = 4
    assert _hand_checksum("880402312345") == 4
    assert rrn_checksum_ok("8804023123454")
    assert not rrn_checksum_ok("8804023123455")


def test_rrn_date_validation():
    assert rrn_date_ok("8804023123454")       # 2088-04-02 (성별자리 3)
    assert not rrn_date_ok("8813013123454")   # 13월
    assert not rrn_date_ok("8802303123454")   # 2088-02-30 (없는 날)
    assert not rrn_date_ok("8804029123454")   # 성별자리 9 → 1800년대, 취급하지 않음


def test_rrn_leap_day_uses_century_from_gender_digit():
    # 성별자리 1 → 1900년대. 1900년은 윤년이 아니므로 00-02-29 는 존재하지 않습니다.
    assert not rrn_date_ok("0002291234567")
    # 성별자리 3 → 2000년대. 2000년은 윤년이므로 존재합니다.
    assert rrn_date_ok("0002293234567")


def test_rrn_hyphenated_without_checksum_is_warning_not_critical():
    hits = scan_rrn("880402-3123455")  # 체크섬 불일치
    assert len(hits) == 1
    assert hits[0].severity == WARNING


def test_rrn_plain_13_digits_only_reported_when_checksum_passes():
    assert scan_rrn("8804023123455") == []           # 체크섬 실패 → 보고 안 함(오탐 억제)
    hits = scan_rrn("8804023123454")
    assert len(hits) == 1 and hits[0].severity == CRITICAL


def test_rrn_not_matched_inside_longer_number():
    assert scan_rrn("128804023123454") == []
    assert scan_rrn("88040231234549") == []


def test_phone_detection_and_masking():
    hits = scan_phone("연락처는 010-2345-6789 입니다")
    assert len(hits) == 1
    assert hits[0].evidence == "010-****-**89"
    assert "2345" not in hits[0].evidence


@pytest.mark.parametrize(
    "text",
    [
        "BELL-001-010-1234-5678",   # 피험자 코드
        "0102345678901",            # 13자리 숫자
        "TST 412, RR 1010 2345 6789",  # 공백 구분 측정값
        "02-345-6789",              # 유선번호(범위 밖)
    ],
)
def test_phone_false_positives_are_suppressed(text):
    assert scan_phone(text) == []


def test_phone_variants_are_detected():
    for text in ["01012345678", "010.1234.5678", "011-234-5678", "010 1234 5678"]:
        assert scan_phone(text), text


def test_email_masking_hides_local_part():
    hits = scan_email("문의: hong.gildong@bell.co.kr")
    assert len(hits) == 1
    assert hits[0].evidence == "h***@b***.kr"
    assert "gildong" not in hits[0].evidence


def test_korean_name_cell():
    assert is_korean_name("김현중")
    assert is_korean_name("남궁민수")
    assert not is_korean_name("특이사항")   # 특 은 성씨가 아님
    assert not is_korean_name("김")          # 한 글자
    assert not is_korean_name("김 현중")     # 공백 포함
    hits = scan_name_cell("김현중")
    assert hits[0].severity == CRITICAL and hits[0].evidence == "김○○"


def test_free_text_person_evidence_is_synthesized_not_copied():
    hits = scan_free_text_person("새벽에 깨서 ○○○ 간호사한테 얘기함")
    assert len(hits) == 1
    assert hits[0].evidence == "…○○○ 간호사…"
    assert "새벽" not in hits[0].evidence  # 원문을 잘라 오지 않습니다


def test_free_text_person_with_space_before_title():
    hits = scan_free_text_person("김철수 씨가 대신 기록해 줌")
    assert len(hits) == 1
    assert hits[0].evidence == "…김○○ 씨…"


@pytest.mark.parametrize(
    "text",
    [
        "오늘날씨가 좋아서 잘 잤다",
        "씨앗을 심었다",
        "특이사항 없음",
        "중간에 두 번 깼다",
        "잠들기까지 오래 걸림",
        "약 복용 없음",
        "이상 없음",
        "코골이 심했다고 배우자가 말함",
    ],
)
def test_free_text_person_does_not_cry_wolf(text):
    assert scan_free_text_person(text) == []


def test_name_label_in_free_text():
    hits = scan_free_text_person("보호자 이름: 이서연 으로 기재")
    kinds = {h.evidence for h in hits}
    assert "…이름: 이○○…" in kinds


def test_scan_structured_skips_cells_without_digits_or_at():
    assert scan_structured("아무 문제 없음") == []
    assert scan_structured("") == []


def test_scan_structured_finds_everything_in_one_cell():
    hits = scan_structured("010-1111-2222 / a@b.kr / 880402-3123454")
    kinds = sorted(h.kind for h in hits)
    assert "휴대전화" in kinds and "이메일" in kinds
    assert any("주민등록번호" in k for k in kinds)


# --- 라운드 2: 공백 필수 규칙이 너무 넓게 적용됐던 자리 ---


@pytest.mark.parametrize(
    "text,expected",
    [
        ("김철수님께 결과 안내함", "…김○○ 님…"),        # 붙여 쓴 형태가 오히려 표준
        ("보호자 이영희씨와 통화", "…이○○ 씨…"),
        ("환자 김철수 님께 안내", "…김○○ 님…"),
        ("남궁민수 교수 회신", "…남○○○ 교수…"),          # 복성 4음절
        ("황보라 선생님 상담", "…황○○ 선생님…"),
    ],
)
def test_attached_honorifics_and_compound_surnames_are_caught(text, expected):
    hits = scan_free_text_person(text)
    assert hits and hits[0].evidence == expected


@pytest.mark.parametrize(
    "text",
    [
        "연구간호사 확인함", "담당 연구 간호사에게 전달", "지도 교수 검토 완료",
        "심리 상담사 면담 병행", "방문 담당자 교체됨", "임상 담당자 확인",
        "조사 담당자 서명", "방문 간호사 왕진", "안전성 담당자 보고",
        "주간 담당자 변경", "전화 상담사 연결", "수면 상담사 안내",
        "기기 담당자 방문", "야간 간호사 라운딩",
        "오늘날씨가 좋아서 잘 잤다", "씨앗을 심었다", "아저씨가 도와줌",
        "손님이 왔다", "고객님 응대", "아가씨 방문",
        "기록지 작성함", "수면일지 제출", "설문지 회수함", "동의서 서명함",
    ],
)
def test_attached_honorifics_do_not_reopen_the_false_positives(text):
    assert scan_free_text_person(text) == []


@pytest.mark.parametrize("text", ["02)345-6789", "02 345 6789", "031.123.4567"])
def test_common_landline_notations_are_caught(text):
    assert scan_landline(text)


@pytest.mark.parametrize("text", ["0234567890", "TST 412 031 5", "RR 02 345 12", "코드 070"])
def test_landline_rule_still_requires_a_separator(text):
    assert scan_landline(text) == []

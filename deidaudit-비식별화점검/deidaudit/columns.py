"""열 성격 판별 — 자유텍스트·날짜·생년월일·연령·이름·ID.

판별은 **자백 대상**입니다. 무엇을 자유텍스트로 봤고 무엇을 안 봤는지
리포트에 그대로 적습니다. 자동 판별이 틀렸을 때 사용자가 그 사실을
알 수 있어야 하기 때문입니다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from .dates import ambiguous_date_ratio, date_ratio, looks_like_birth, parse_date
from .detect import is_korean_name
from .tabular import Table, norm_key

# 자유텍스트 판별 임계값 (한 곳에 모아 두어 테스트가 고정합니다)
FREETEXT_MIN_MEAN_LEN = 10.0
FREETEXT_MIN_MEAN_SPACES = 0.5
FREETEXT_MIN_UNIQUE_RATIO = 0.4
DATE_COLUMN_MIN_RATIO = 0.8

_FREETEXT_HEADERS = (
    "비고", "메모", "기타", "자유", "의견", "응답", "코멘트", "특이사항", "증상",
    "서술", "기술", "설명", "내용", "사유", "소감", "피드백", "note", "notes",
    "comment", "comments", "remark", "remarks", "feedback", "freetext", "openend",
    "description", "detail", "details", "reason",
)
_NAME_HEADERS = (
    "이름", "성명", "성함", "환자명", "대상자명", "참여자명", "보호자", "담당자", "작성자", "name",
    "평가자", "진행자", "검사자", "조사자", "면담자", "연구자", "기록자", "시행자", "상담자",
    "측정자", "판독자", "의뢰자", "서명자", "확인자", "책임자",
)
_NAME_HEADER_EXCLUDE = (
    "파일", "file", "변수", "variable", "item", "문항", "column", "열", "필드", "field",
    "사용자명", "username", "코드명", "약어", "id", "번호", "코드", "code", "no",
)
_BIRTH_HEADERS = ("생년", "생일", "출생", "birth", "dob", "birthdate", "birthday")
_AGE_HEADERS = ("나이", "연령", "age")
_AGE_EXCLUDE = ("page", "average", "usage", "stage", "language", "percentage", "메시지")
_PHONE_HEADERS = ("전화", "휴대", "핸드폰", "연락처", "폰번호", "phone", "mobile", "tel", "hp")
_RRN_HEADERS = ("주민", "주민등록", "rrn", "ssn", "jumin", "residentregistration")
_EMAIL_HEADERS = ("이메일", "메일주소", "email", "mail")
_ADDRESS_HEADERS = ("주소", "거주지", "address", "addr")
# 값이 반복되는 범주 열 — 첫 음절이 성씨인 지역·부서 이름이 많아 이름 열로 오인되기 쉽습니다.
_CATEGORY_HEADERS = (
    "지역", "구역", "site", "기관", "센터", "병원", "부서", "팀", "학교", "직업",
    "국적", "인종", "군", "arm", "그룹", "group", "분류", "구분", "카테고리",
)
_ID_HEADERS = ("id", "아이디", "식별", "subject", "subjectid", "대상자", "피험자", "환자번호", "등록번호", "record", "recordid", "pid", "usercode")

# 이 툴이 '열 이름으로 알고 있는' 모든 단어. `이름`·`연락처`·`주소` 처럼 첫 음절이
# 한국 성씨인 헤더가 많아서, 헤더가 데이터인지 판정할 때 반드시 걸러야 합니다.
KNOWN_HEADER_TOKENS = frozenset(
    _FREETEXT_HEADERS + _NAME_HEADERS + _BIRTH_HEADERS + _AGE_HEADERS
    + _PHONE_HEADERS + _RRN_HEADERS + _EMAIL_HEADERS + _ADDRESS_HEADERS + _ID_HEADERS
    + ("최종본", "원본값", "최종안", "초안본", "정리본", "임상시", "전체본", "요약본",
       "성별", "연령대", "군", "그룹", "방문", "시점", "차수", "회차", "점수", "비고",
       "구분", "분류", "상태", "결과", "일자", "일시", "날짜", "기관", "센터", "지역",
       "직업", "학력", "국적", "인종", "체중", "신장", "번호", "코드", "값", "단위")
)


def looks_like_known_header(name: str) -> bool:
    """이 문자열이 '흔한 열 이름'인지."""
    key = norm_key(name)
    if not key:
        return False
    return any(token in key for token in KNOWN_HEADER_TOKENS)


_NUMERIC_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
_HANGUL_RE = re.compile(r"[가-힣]")


@dataclass
class ColumnProfile:
    """열 하나의 성격 판정 결과."""

    index: int
    name: str
    n_total: int
    n_non_empty: int
    mean_len: float
    mean_spaces: float
    unique_ratio: float
    numeric_ratio: float
    date_ratio: float
    ambiguous_date_ratio: float
    hangul_ratio: float
    is_free_text: bool = False
    free_text_reason: str = ""
    is_date_column: bool = False
    is_birth_column: bool = False
    is_age_column: bool = False
    is_name_column: bool = False
    is_phone_header: bool = False
    is_rrn_header: bool = False
    is_email_header: bool = False
    is_address_header: bool = False
    is_id_column: bool = False
    is_hidden: bool = False
    korean_name_ratio: float = 0.0
    name_by_content: bool = False
    is_partial_date_column: bool = False
    notes: List[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        if self.n_non_empty == 0:
            return "빈 열"
        if self.is_birth_column:
            return "생년월일"
        if self.is_date_column:
            return "날짜"
        if self.is_free_text:
            return "자유텍스트"
        if self.numeric_ratio >= 0.8:
            return "숫자"
        return "범주/짧은문자"


def _header_hit(key: str, needles) -> bool:
    return any(n in key for n in needles)


def _header_exact_or_suffix(key: str, needles) -> bool:
    for n in needles:
        if key == n or key.endswith("_" + n) or key.startswith(n + "_"):
            return True
    return False


def profile_column(table: Table, index: int) -> ColumnProfile:
    """열 하나를 프로파일링합니다."""
    name = table.columns[index]
    key = norm_key(name)
    values = table.column_values(index)
    non_empty = [str(v) for v in values if str(v).strip()]
    n = len(values)
    ne = len(non_empty)

    if ne:
        mean_len = sum(len(v) for v in non_empty) / ne
        mean_spaces = sum(v.count(" ") + v.count("\n") + v.count("\t") for v in non_empty) / ne
        unique_ratio = len(set(non_empty)) / ne
        numeric_ratio = sum(1 for v in non_empty if _NUMERIC_RE.match(v.strip())) / ne
        hangul_ratio = sum(1 for v in non_empty if _HANGUL_RE.search(v)) / ne
    else:
        mean_len = mean_spaces = unique_ratio = numeric_ratio = hangul_ratio = 0.0

    d_ratio = date_ratio(values)
    amb_ratio = ambiguous_date_ratio(values)

    profile = ColumnProfile(
        index=index,
        name=name,
        n_total=n,
        n_non_empty=ne,
        mean_len=mean_len,
        mean_spaces=mean_spaces,
        unique_ratio=unique_ratio,
        numeric_ratio=numeric_ratio,
        date_ratio=d_ratio,
        ambiguous_date_ratio=amb_ratio,
        hangul_ratio=hangul_ratio,
        is_hidden=index in table.hidden_columns,
    )

    profile.is_phone_header = _header_hit(key, _PHONE_HEADERS)
    profile.is_rrn_header = _header_hit(key, _RRN_HEADERS)
    profile.is_email_header = _header_hit(key, _EMAIL_HEADERS)
    profile.is_address_header = _header_hit(key, _ADDRESS_HEADERS)
    profile.is_id_column = _header_exact_or_suffix(key, _ID_HEADERS) or _header_hit(key, ("subjectid", "피험자", "대상자id"))

    if _header_hit(key, _AGE_HEADERS) and not _header_hit(key, _AGE_EXCLUDE):
        profile.is_age_column = True

    if _header_hit(key, _BIRTH_HEADERS):
        profile.is_birth_column = ne > 0 and d_ratio >= 0.5
        if ne and d_ratio < 0.5:
            profile.notes.append("헤더는 생년월일인데 값이 날짜로 읽히지 않아 생년월일 열로 보지 않았습니다")
    elif ne and d_ratio >= DATE_COLUMN_MIN_RATIO:
        # 열 전체가 그럴듯한 출생일 분포일 때만 생년월일로 봅니다.
        parsed = [parse_date(v) for v in non_empty]
        parsed = [p for p in parsed if p is not None]
        if parsed and all(looks_like_birth(p.value) for p in parsed):
            spread_years = max(p.value.year for p in parsed) - min(p.value.year for p in parsed)
            if spread_years >= 5:
                profile.is_birth_column = True
                profile.notes.append("헤더에 단서가 없지만 값 전체가 출생일 분포라 생년월일 열로 봤습니다")

    if ne and d_ratio >= DATE_COLUMN_MIN_RATIO:
        profile.is_date_column = True
    elif ne and 0.3 <= d_ratio < DATE_COLUMN_MIN_RATIO:
        # 날짜처럼 보이지만 `미상`·`N/A` 가 섞여 임계를 못 넘긴 열입니다.
        # 그냥 넘기면 --shift-dates 가 이 열을 건드리지 않고, 경고도 없이
        # 원본 날짜가 그대로 나갑니다.
        profile.is_partial_date_column = True
        profile.notes.append(
            f"값의 {d_ratio:.0%}만 날짜로 읽혀 날짜 열로 보지 않았습니다 — 이동 대상에서 빠집니다"
        )

    if _header_hit(key, _NAME_HEADERS) and not _header_hit(key, _NAME_HEADER_EXCLUDE):
        profile.is_name_column = True
    elif ne >= 3 and not profile.is_address_header and not _header_hit(key, _CATEGORY_HEADERS):
        # 헤더에 단서가 없어도 **값이 한국 이름 형태로 몰려 있으면** 이름 열로 봅니다.
        # `담당간호사`·`주치의`·`대상자`·`Q3` 처럼 헤더 사전에 없는 이름 열이 조용히
        # 통과하는 것이 이 툴에서 가장 위험한 실패이기 때문입니다.
        # (ID 계열 헤더도 제외하지 않습니다 — `대상자` 열이 이름으로 차 있다면
        #  그 ID 가 곧 사람이라는 뜻이므로 오히려 더 위험합니다.)
        distinct = set(non_empty)
        ratio = sum(1 for v in distinct if is_korean_name(v)) / len(distinct)
        profile.korean_name_ratio = ratio
        # 이름 열은 값이 거의 겹치지 않습니다. `강남·서초·노원…` 같은 지역·부서
        # 범주 열은 첫 음절이 성씨라 이름처럼 보이지만 값이 반복됩니다.
        if ratio >= 0.6 and profile.unique_ratio >= 0.7:
            profile.is_name_column = True
            profile.name_by_content = True
            profile.notes.append(
                f"헤더에 단서가 없지만 값의 {ratio:.0%}가 한국 성명 형태이고 고유값 비율이 "
                f"{profile.unique_ratio:.0%}라 이름 열로 봤습니다"
            )

    _classify_free_text(profile, key)
    return profile


def _classify_free_text(profile: ColumnProfile, key: str) -> None:
    """자유텍스트 여부와 그 사유를 채웁니다."""
    if profile.n_non_empty == 0:
        profile.free_text_reason = "빈 열"
        return
    if profile.is_date_column or profile.numeric_ratio >= 0.8:
        profile.free_text_reason = "날짜/숫자 열"
        return
    if _header_hit(key, _FREETEXT_HEADERS):
        profile.is_free_text = True
        profile.free_text_reason = "헤더가 자유기술 열 이름"
        return
    if (
        profile.mean_len >= FREETEXT_MIN_MEAN_LEN
        and profile.mean_spaces >= FREETEXT_MIN_MEAN_SPACES
        and profile.unique_ratio >= FREETEXT_MIN_UNIQUE_RATIO
    ):
        profile.is_free_text = True
        profile.free_text_reason = (
            f"평균 {profile.mean_len:.1f}자 · 공백 {profile.mean_spaces:.1f}개 · 고유값 {profile.unique_ratio:.0%}"
        )
        return
    profile.free_text_reason = (
        f"평균 {profile.mean_len:.1f}자 · 공백 {profile.mean_spaces:.1f}개 · 고유값 {profile.unique_ratio:.0%} "
        "— 임계 미만"
    )


def profile_table(table: Table) -> List[ColumnProfile]:
    """표의 모든 열을 프로파일링합니다."""
    return [profile_column(table, i) for i in range(table.n_cols)]


def pick_link_column(table: Table, candidates: List[str]) -> Optional[int]:
    """`--link-id` 후보 중 이 표에 있는 첫 열의 인덱스를 돌려줍니다."""
    for cand in candidates:
        idx = table.column_index(cand)
        if idx is not None:
            return idx
    return None

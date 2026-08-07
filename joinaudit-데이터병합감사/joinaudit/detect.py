"""키·날짜·시점 열 자동 탐지 — **확신이 없으면 병합하지 않는다.**

이 툴이 가장 크게 실패하는 방식은 크래시가 아니라 **틀린 표를 자신 있게
내놓는 것**이다. 엉뚱한 열을 피험자 키로 잡으면 병합은 성공한 것처럼 보이고
통계까지 돌아간다. 그래서 탐지 규칙은 넓히지 않고 **좁힌다**.

* 후보가 정확히 하나일 때만 확정한다.
* 같은 등급의 후보가 둘 이상이면 추측하지 않고 사람에게 `--key`/`--date`/
  `--visit` 를 요구하며 종료코드 3(병합 불가)으로 끝낸다.
* 탐지 결과는 **언제나 화면 첫 블록에 출력한다.** 무엇을 키로 잡았는지 보이지
  않는 자동 탐지는 자동 오염이다.

이름 후보는 `sleepdiary` 의 한/영 열 이름 인식 아이디어를 참고했으나 코드는
공유하지 않는다(이 툴은 지표가 아니라 **조인 키**를 찾는다).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .dataio import Frame, is_missing
from .timeline import DatePlan, VisitNormalizer, plan_date_column

__all__ = ["Detection", "detect_key", "detect_date", "detect_visit", "norm_name"]

# 확신 등급
EXPLICIT = "명시"      # 사용자가 --key/--date/--visit 로 지정
BY_NAME = "이름"       # 열 이름이 확정적
BY_CONTENT = "내용"    # 이름 근거는 없고 값의 모양만으로 판단


def norm_name(name: str) -> str:
    """열 이름 비교용 정규화: NFKC, 소문자, 공백·구분자 제거."""
    text = unicodedata.normalize("NFKC", name or "").strip().lower()
    return re.sub(r"[\s_\-.()\[\]]+", "", text)


@dataclass
class Detection:
    """탐지 결과 하나."""

    role: str                       # 'key' | 'date' | 'visit'
    column: Optional[str] = None
    confidence: str = ""
    candidates: List[str] = field(default_factory=list)
    reason: str = ""
    plan: Optional[DatePlan] = None

    @property
    def ok(self) -> bool:
        return self.column is not None

    @property
    def ambiguous(self) -> bool:
        return self.column is None and len(self.candidates) > 1


# --------------------------------------------------------------------------
# 피험자 키
# --------------------------------------------------------------------------

# 등급이 낮은 숫자일수록 강한 근거. 같은 등급에 후보가 둘 이상이면 모호로 본다.
_KEY_NAMES: Sequence[Tuple[int, Tuple[str, ...]]] = (
    (0, ("subjectid", "subject", "피험자id", "피험자번호", "피험자", "대상자id",
         "대상자번호", "대상자", "usubjid", "연구번호", "등록번호")),
    (1, ("participantid", "participant", "patientid", "patient", "recordid",
         "수검자id", "수검자번호", "환자id", "환자번호")),
    (2, ("id", "pid", "sid", "no", "번호", "아이디")),
)

_DATE_LIKE_RE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")


def _key_name_rank(name: str) -> Optional[int]:
    probe = norm_name(name)
    for rank, names in _KEY_NAMES:
        if probe in names:
            return rank
    # 접미사 근거: subject_id 계열의 변형(`subj_id`, `pt_id`)
    if probe.endswith("id") and len(probe) <= 12 and probe not in ("valid", "grid"):
        return 3
    return None


def _plausible_key_column(frame: Frame, column: str) -> Tuple[bool, str]:
    """값의 모양이 피험자 키로 쓸 만한가."""
    values = [v.strip() for v in frame.column(column)]
    present = [v for v in values if not is_missing(v)]
    if not present:
        return False, "값이 전부 비어 있음"
    if len(present) < len(values) * 0.9:
        return False, f"결측이 많음({len(values) - len(present)}/{len(values)})"
    if any(_DATE_LIKE_RE.match(v) for v in present[:50]):
        return False, "값이 날짜처럼 보임"
    if all(("." in v and v.replace(".", "", 1).replace("-", "", 1).isdigit())
           for v in present[:50]):
        return False, "값이 소수(측정값)처럼 보임"
    return True, ""


def detect_key(frame: Frame, explicit: Optional[str] = None) -> Detection:
    """피험자 키 열을 찾는다."""
    det = Detection(role="key")
    if explicit:
        if not frame.has(explicit):
            det.reason = (f"지정한 키 열 '{explicit}' 이(가) '{frame.label}' 에 "
                          f"없습니다. 이 파일의 열: {', '.join(frame.header)}")
            return det
        det.column, det.confidence = explicit, EXPLICIT
        det.reason = "사용자가 --key 로 지정"
        return det

    ranked: Dict[int, List[str]] = {}
    for name in frame.header:
        rank = _key_name_rank(name)
        if rank is None:
            continue
        ok, _ = _plausible_key_column(frame, name)
        if ok:
            ranked.setdefault(rank, []).append(name)

    if not ranked:
        det.reason = ("피험자 ID로 볼 만한 열을 찾지 못했습니다. "
                      "`--key 열이름` 으로 지정하세요.")
        return det

    best = min(ranked)
    candidates = ranked[best]
    if len(candidates) > 1:
        det.candidates = candidates
        det.reason = ("피험자 ID 후보가 여러 개입니다: " + ", ".join(candidates) +
                      ". 추측하지 않습니다 — `--key 열이름` 으로 지정하세요.")
        return det

    det.column, det.confidence = candidates[0], BY_NAME
    det.candidates = candidates
    det.reason = f"열 이름 '{candidates[0]}' 으로 판단"
    return det


# --------------------------------------------------------------------------
# 날짜
# --------------------------------------------------------------------------

_DATE_NAMES = (
    "date", "날짜", "일자", "측정일", "측정일자", "검사일", "기록일", "방문일",
    "datetime", "timestamp", "measuredat", "recordedat", "visitdate",
    "sleepdate", "취침일", "night", "야간", "measurementdate", "일시",
    "starttime", "시작시각", "시작시간", "취침시각", "기상시각", "collecteddate",
)


def _date_name_rank(name: str) -> Optional[int]:
    probe = norm_name(name)
    if probe in _DATE_NAMES:
        return 0
    if any(h in probe for h in ("date", "날짜", "일자", "일시")):
        return 1
    if any(h in probe for h in ("time", "시각", "timestamp")):
        return 2
    return None


def detect_date(frame: Frame, explicit: Optional[str] = None
                ) -> Detection:
    """날짜/시각 열을 찾고, 그 열의 날짜 형식 해석까지 확정한다."""
    det = Detection(role="date")
    if explicit:
        if not frame.has(explicit):
            det.reason = (f"지정한 날짜 열 '{explicit}' 이(가) '{frame.label}' 에 "
                          f"없습니다. 이 파일의 열: {', '.join(frame.header)}")
            return det
        det.column, det.confidence = explicit, EXPLICIT
        det.plan = plan_date_column(frame.column(explicit))
        det.reason = "사용자가 --date 로 지정"
        return det

    ranked: Dict[int, List[Tuple[str, DatePlan]]] = {}
    content_only: List[Tuple[str, DatePlan]] = []
    for name in frame.header:
        column = frame.column(name)
        present = [v for v in column if not is_missing(v)]
        if not present:
            continue
        plan = plan_date_column(column)
        if plan.parsed < len(present) * 0.8:
            continue
        rank = _date_name_rank(name)
        # 엑셀 시리얼 해석은 **이름 근거가 있을 때만** 인정한다. 그렇지 않으면
        # 20000~65000 범위의 걸음 수·비용 같은 평범한 숫자 열이 날짜로 둔갑한다.
        if plan.excel_serial and rank is None:
            continue
        if rank is None:
            content_only.append((name, plan))
        else:
            ranked.setdefault(rank, []).append((name, plan))

    if ranked:
        best = min(ranked)
        candidates = ranked[best]
        if len(candidates) > 1:
            det.candidates = [n for n, _ in candidates]
            det.reason = ("날짜 열 후보가 여러 개입니다: " +
                          ", ".join(det.candidates) +
                          ". `--date 열이름` 으로 지정하세요.")
            return det
        name, plan = candidates[0]
        det.column, det.confidence, det.plan = name, BY_NAME, plan
        det.candidates = [name]
        det.reason = f"열 이름 '{name}' 과 값의 형식으로 판단"
        return det

    if len(content_only) == 1:
        name, plan = content_only[0]
        det.column, det.confidence, det.plan = name, BY_CONTENT, plan
        det.candidates = [name]
        det.reason = (f"열 이름에는 근거가 없지만 '{name}' 의 값이 모두 날짜 "
                      "형식이라 날짜 열로 보았습니다")
        return det
    if len(content_only) > 1:
        det.candidates = [n for n, _ in content_only]
        det.reason = ("날짜처럼 보이는 열이 여러 개입니다: " +
                      ", ".join(det.candidates) + ". `--date 열이름` 으로 지정하세요.")
        return det

    det.reason = "날짜 열을 찾지 못했습니다."
    return det


# --------------------------------------------------------------------------
# 방문/시점
# --------------------------------------------------------------------------

_VISIT_NAMES = ("visit", "방문", "방문차수", "timepoint", "시점", "period",
                "phase", "회차", "event", "redcapeventname", "visitname",
                "측정시점", "구분")


def _visit_name_rank(name: str) -> Optional[int]:
    probe = norm_name(name)
    if probe in _VISIT_NAMES:
        return 0
    if any(h in probe for h in ("visit", "방문", "시점", "timepoint", "회차")):
        return 1
    return None


def detect_visit(frame: Frame, explicit: Optional[str] = None,
                 normalizer: Optional[VisitNormalizer] = None) -> Detection:
    """방문/시점 라벨 열을 찾는다."""
    det = Detection(role="visit")
    if explicit:
        if not frame.has(explicit):
            det.reason = (f"지정한 시점 열 '{explicit}' 이(가) '{frame.label}' 에 "
                          f"없습니다. 이 파일의 열: {', '.join(frame.header)}")
            return det
        det.column, det.confidence = explicit, EXPLICIT
        det.reason = "사용자가 --visit 로 지정"
        return det

    normalizer = normalizer or VisitNormalizer()
    ranked: Dict[int, List[str]] = {}
    for name in frame.header:
        rank = _visit_name_rank(name)
        if rank is None:
            continue
        present = [v for v in frame.column(name) if not is_missing(v)]
        if not present:
            continue
        known = sum(1 for v in present if normalizer(v)[1])
        # 이름이 명확하면(rank 0) 값이 낯설어도 시점 열로 본다 — 대신 낯선
        # 라벨은 나중에 개별로 보고된다.
        if rank > 0 and known < len(present) * 0.7:
            continue
        ranked.setdefault(rank, []).append(name)

    if not ranked:
        det.reason = "시점(방문) 열을 찾지 못했습니다."
        return det
    best = min(ranked)
    candidates = ranked[best]
    if len(candidates) > 1:
        det.candidates = candidates
        det.reason = ("시점 열 후보가 여러 개입니다: " + ", ".join(candidates) +
                      ". `--visit 열이름` 으로 지정하세요.")
        return det
    det.column, det.confidence = candidates[0], BY_NAME
    det.candidates = candidates
    det.reason = f"열 이름 '{candidates[0]}' 으로 판단"
    return det

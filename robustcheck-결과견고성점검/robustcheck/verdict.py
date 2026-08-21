"""뒤집힘 판정과 취약도 등급.

판정 4종:
  ① 유의 → 비유의        치명
  ② 비유의 → 유의        경고   (※ "이렇게 하면 유의해진다"는 뜻이 아니다)
  ③ 효과크기 부호 반전    치명   (양쪽 모두 최소 '小' 이상일 때만)
  ④ 효과크기 등급 변화    경고   (Cohen 경계 통과 **+ 최소 변화폭** 동시 충족)

③④ 에 건 세 겹의 하한 — 전부 **오탐을 줄이려고 규칙을 좁힌** 것이다:
  · 최소 크기: 0.499 → 0.501 처럼 경계를 스치는 변화는 세지 않는다.
  · **양쪽 다 비유의면 아예 보지 않는다.** 귀무 결과("차이를 확인하지 못했다")는
    효과크기가 0 근처에서 흔들려도 결론이 바뀐 게 아니다. 이 조건이 없으면
    r = 0 짜리 자료 40건 중 39건이 '취약'으로 나온다(실측).
  · **척도가 다르면 등급을 비교하지 않는다.** 로그변환한 g 와 원척도 g 는 단위가
    다르다 — 이 모듈이 검정 축에 대해 금지하는 바로 그 비교다.

이렇게 좁히지 않으면 이 툴은 **매번 우는 체커**가 되고, 두 번째부터는 아무도
열지 않는다.

**정렬은 유의성이 아니라 뒤집힘 여부 순이다.** 이건 편의가 아니라 윤리
문제다 — `order_key` 에 p 가 들어가는 순간 이 툴은 p-해킹 자동화 도구가 된다.
"""

import math
from typing import List, Optional, Sequence, Tuple

from .effects import effect_grade, family_min_delta, sign_flip_floor

__all__ = [
    "Flip",
    "CRITICAL",
    "WARNING",
    "judge_flips",
    "severity_of",
    "SEVERITY_RANK",
    "order_key",
    "Verdict",
    "grade_formula_text",
    "MULTIPLICITY_NOTE",
]

CRITICAL = "치명"
WARNING = "경고"

SEVERITY_RANK = {CRITICAL: 0, WARNING: 1, "": 2, "건너뜀": 3}

MULTIPLICITY_NOTE = (
    "여기 나온 p 들은 서로 독립인 가설이 아니라 **같은 가설의 재계산**이다. "
    "다중비교 보정을 하지 않았고, 하면 오히려 거짓말이 된다."
)


class Flip:
    """뒤집힘 1건."""

    __slots__ = ("code", "label", "severity", "detail")

    def __init__(self, code: str, label: str, severity: str, detail: str) -> None:
        self.code = code
        self.label = label
        self.severity = severity
        self.detail = detail

    def __repr__(self) -> str:  # pragma: no cover
        return "Flip(%s, %s, %s)" % (self.code, self.severity, self.detail)


def _fmt_p(p: float) -> str:
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return "NA"
    if p < 0.001:
        return "<.001"
    return ("%.3f" % p).lstrip("0")


def judge_flips(
    base_p: float,
    base_effect: float,
    p: float,
    effect: float,
    alpha: float,
    family: str,
    same_scale: bool = True,
) -> List[Flip]:
    """기준선 대비 뒤집힘 목록. 아무것도 없으면 빈 리스트.

    `same_scale=False` 는 시나리오가 기준선과 **다른 척도**(로그변환 적용 여부가
    다름)에서 계산됐다는 뜻이다. 그 경우 효과크기 등급 비교(④)를 하지 않는다 —
    단위가 다른 두 수의 크기를 견주는 것이라 거짓말이 된다.
    """
    flips: List[Flip] = []
    if any(math.isnan(v) for v in (base_p, p)):
        return flips
    base_sig = base_p < alpha
    now_sig = p < alpha
    if base_sig and not now_sig:
        flips.append(Flip("①", "유의 → 비유의", CRITICAL,
                          "p %s → %s" % (_fmt_p(base_p), _fmt_p(p))))
    elif not base_sig and now_sig:
        flips.append(Flip("②", "비유의 → 유의", WARNING,
                          "p %s → %s" % (_fmt_p(base_p), _fmt_p(p))))

    if not (base_sig or now_sig):
        # 양쪽 모두 비유의 = 결론("차이를 확인하지 못했다")이 그대로 유지된 것이다.
        # 0 근처에서 효과크기가 흔들린 것을 '뒤집힘'이라 부르면, 귀무 결과를 넣을
        # 때마다 취약 판정이 나와(실측: r=0 자료 40건 중 39건) 두 번 다시 안 열린다.
        return flips

    if not (math.isnan(base_effect) or math.isnan(effect)):
        min_delta = family_min_delta(family)
        floor = sign_flip_floor(family)
        if (base_effect > 0) != (effect > 0) and base_effect != 0 and effect != 0:
            if min(abs(base_effect), abs(effect)) >= floor:
                flips.append(Flip("③", "효과크기 부호 반전", CRITICAL,
                                  "%+.3f → %+.3f" % (base_effect, effect)))
        base_grade = effect_grade(base_effect, family)
        now_grade = effect_grade(effect, family)
        if (same_scale and base_grade != now_grade
                and abs(effect - base_effect) >= min_delta):
            flips.append(Flip("④", "효과크기 등급 변화", WARNING,
                              "%s → %s (%+.3f → %+.3f)"
                              % (base_grade, now_grade, base_effect, effect)))
    return flips


def severity_of(flips: Sequence[Flip]) -> str:
    if any(f.severity == CRITICAL for f in flips):
        return CRITICAL
    if flips:
        return WARNING
    return ""


def order_key(severity: str, computed: bool, axes_order: Tuple[int, ...]):
    """정렬 키 — (심각도, 축 순서). p 값은 **의도적으로** 들어가지 않는다.

    유의성이 정렬에 끼면 "가장 유의한 조합"이 맨 위로 올라온다 — 하지 않는다.
    그 순간 이 툴은 p-해킹 자동화 도구가 되기 때문이다.
    `tests/test_no_phacking.py` 가 이 성질을 강제한다.
    """
    rank = SEVERITY_RANK["건너뜀"] if not computed else SEVERITY_RANK[severity]
    return (rank,) + tuple(axes_order)


class Verdict:
    """최종 등급과 그 산출 근거."""

    __slots__ = ("grade", "critical_scenarios", "warning_scenarios",
                 "critical_subjects", "warning_subjects", "computed", "total",
                 "undecidable_reason")

    def __init__(
        self,
        critical_scenarios: int,
        warning_scenarios: int,
        critical_subjects: int,
        warning_subjects: int,
        computed: int,
        total: int,
        undecidable_reason: str = "",
    ) -> None:
        self.critical_scenarios = critical_scenarios
        self.warning_scenarios = warning_scenarios
        self.critical_subjects = critical_subjects
        self.warning_subjects = warning_subjects
        self.computed = computed
        self.total = total
        self.undecidable_reason = undecidable_reason
        if undecidable_reason:
            self.grade = "판정불가"
        elif critical_scenarios + critical_subjects > 0:
            self.grade = "취약"
        elif warning_scenarios + warning_subjects > 0:
            self.grade = "주의"
        else:
            self.grade = "견고"

    @property
    def total_critical(self) -> int:
        return self.critical_scenarios + self.critical_subjects

    @property
    def total_warning(self) -> int:
        return self.warning_scenarios + self.warning_subjects

    def summary(self) -> str:
        if self.grade == "판정불가":
            return "판정불가 (%s)" % self.undecidable_reason
        if self.grade == "견고":
            return "견고 (뒤집힘 0건)"
        parts = []
        if self.critical_scenarios:
            parts.append("치명 시나리오 %d건" % self.critical_scenarios)
        if self.critical_subjects:
            parts.append("단독 뒤집기 피험자 %d명" % self.critical_subjects)
        if self.warning_scenarios:
            parts.append("경고 시나리오 %d건" % self.warning_scenarios)
        if self.warning_subjects:
            parts.append("경고 피험자 %d명" % self.warning_subjects)
        return "%s (%s)" % (self.grade, " · ".join(parts))


def grade_formula_text() -> List[str]:
    """등급 산출식. 리포트에 **그대로** 인쇄된다(숨은 기준 없음)."""
    return [
        "취약 = (치명 시나리오 뒤집힘 ① 또는 ③) ≥ 1  **또는**  단독 뒤집기 피험자 ≥ 1",
        "주의 = 치명 0 이면서 경고(② 또는 ④) ≥ 1",
        "견고 = 뒤집힘 0건",
        "판정불가 = 유효 N < 6 또는 계산된 시나리오 < 5 (다른 무엇보다 우선)",
    ]

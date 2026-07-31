"""설계별 검정력 함수 — 계획 단계에서 실제로 쓰는 17가지 검정력 기반 설계.

각 설계는 같은 인터페이스를 따른다:

- ``power(unit)``            : 연속형 unit(군당 n 등)에서의 검정력 — 표본수 탐색용
- ``allocation(unit)``       : 정수 배분 (n1, n2, total …)
- ``power_of_allocation(a)`` : 그 정수 배분에서 실제 달성되는 검정력
- ``scaled(factor)``         : 효과크기를 factor배로 바꾼 같은 설계 (민감도 분석용)

**정확 계산**: 비중심 t/F — ``ttest2`` ``paired`` ``onesample`` ``repeated``
``crossover``, 연속형 ``noninf``·``equiv``, ``anova``. 정확 이항검정 — ``prop1``.
**정규근사**: ``prop2``(z), ``corr``(Fisher z), 이분형 ``noninf``·``equiv``(위험차 z),
``mcnemar``(Connor), ``survival``(Schoenfeld/Freedman), ``count``(음이항 log 발생률비),
``ordinal``(Whitehead 비례오즈).
어느 쪽인지는 각 설계의 ``notes()``에 반드시 적는다 — 과대주장하지 않는 것이 이 툴의
원칙이다.

기저값이 있는 설계는 분산 배율을 따로 계산한다. ``ttest2 --analysis``는 추적값만
비교(raw), 기저값 공변량 보정(ancova, 1−r²), 변화량 비교(change, 2(1−r))를 지원하고,
``repeated``는 여기에 반복측정(Frison–Pocock)과 ``--estimand``(마지막 방문 / 사후 평균)를
더한다. ANCOVA 계열은 공변량 불균형에 따른 분산 팽창 1+1/(N−3)까지 반영한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .distributions import chi_expectation, f_ppf, ncf_sf, nct_cdf, nct_sf, t_ppf
from .effects import fisher_z, label_d, label_f, label_r
from .special import bisect_increasing, norm_cdf, norm_ppf
from .validate import PowerPlanError

__all__ = [
    "Design",
    "TwoSampleT",
    "PairedT",
    "OneSampleT",
    "TwoProportions",
    "OneWayAnova",
    "CorrelationTest",
    "NonInferiorityT",
    "EquivalenceT",
    "NonInferiorityProportions",
    "EquivalenceProportions",
    "McNemarPaired",
    "RepeatedMeasuresT",
    "LogRankSurvival",
    "OneSampleProportion",
    "CrossoverT",
    "CountRateRatio",
    "OrdinalProportionalOdds",
    "po_shift",
    "exponential_event_prob",
    "binomial_sf",
    "DESIGN_KEYS",
]


#: 표시용 문자열에서 제거할 문자 — 터미널 이스케이프·줄바꿈 위장·양방향 조작·폭 0
_UNSAFE_CHARS = {c: None for c in range(32)}
_UNSAFE_CHARS[127] = None
_UNSAFE_CHARS.update({c: None for c in range(0x80, 0xA0)})
_UNSAFE_CHARS.update({c: None for c in (0x2028, 0x2029, 0xFEFF)})
_UNSAFE_CHARS.update({c: None for c in range(0x200B, 0x2010)})
_UNSAFE_CHARS.update({c: None for c in range(0x202A, 0x2030)})
_UNSAFE_CHARS.update({c: None for c in range(0x2066, 0x206A)})


def _clean_label(text: str, limit: int = 40) -> str:
    """사용자가 준 자유 문자열을 보고서에 넣기 전에 정리한다."""
    cleaned = str(text).translate(_UNSAFE_CHARS).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1] + "…"
    if cleaned[:1] in ("=", "+", "-", "@"):     # 스프레드시트 수식 무력화
        cleaned = "'" + cleaned
    return cleaned


def ancova_inflation(total_n: float, ancova: bool) -> float:
    """ANCOVA 처리효과 추정량의 **분산 팽창** 1 + 1/(N − 3).

    두 군의 공변량 평균은 무작위배정에서도 우연히 어긋난다. 그 불균형을 보정하느라
    Var(β̂_trt) = σ²_r·(1/n₁+1/n₂)·(1 + 1/(N−3))가 되며, 자유도만 1 빼고 이 항을
    빼먹으면 검정력이 0.5~1.5%p 과대평가된다(작은 n에서는 7%p까지). Borm 등(2007)이
    "ANCOVA에는 군당 1명을 더하라"고 말하는 근거가 바로 이 항이다.
    """
    if not ancova:
        return 1.0
    df = total_n - 3.0
    if df <= 1.0:
        return 2.0          # 방어적: 이 영역의 검정력은 어차피 의미가 없다
    return 1.0 + 1.0 / df


def _check_alpha(alpha: float) -> float:
    if not (0.0 < alpha < 1.0):
        raise PowerPlanError(f"--alpha: 0과 1 사이여야 합니다 (받은 값: {alpha:g})")
    if alpha >= 0.5:
        raise PowerPlanError(
            f"--alpha: 0.5 이상은 의미가 없습니다 (받은 값: {alpha:g}). 보통 0.05를 씁니다"
        )
    return float(alpha)


def _check_sides(sides: int) -> int:
    if sides not in (1, 2):
        raise PowerPlanError(f"--sides: 1 또는 2여야 합니다 (받은 값: {sides!r})")
    return int(sides)


def _check_ratio(ratio: float) -> float:
    if not (math.isfinite(ratio) and ratio > 0.0):
        raise PowerPlanError(f"--ratio: 0보다 큰 유한한 값이어야 합니다 (받은 값: {ratio!r})")
    if ratio > 100.0:
        raise PowerPlanError(f"--ratio: 100을 넘는 배분비는 지원하지 않습니다 (받은 값: {ratio:g})")
    return float(ratio)


class Design:
    """설계 공통 인터페이스 (하위 클래스가 아래를 채운다)."""

    key = "base"
    name_kr = ""
    name_en = ""
    test_kr = ""
    test_en = ""
    unit_kr = "n"
    min_unit = 2
    #: 검정력이 unit에 대해 이론적으로 단조증가하는가 (연속성 보정 등은 예외)
    monotone = True
    #: 중간분석(군차별설계)을 붙일 수 있는가 — 방향성 있는 Z 통계량이 있어야 한다.
    supports_sequential = True
    #: 붙일 수 없을 때 사용자에게 말할 **그 설계에 맞는** 이유
    sequential_reason = ""
    #: 군집 무작위배정(설계효과)을 붙일 수 있는가 — 군집을 **군에 배정**하는
    #: 설계여야 한다. 단일군·대응·교차설계는 해당하지 않는다.
    supports_cluster = True
    cluster_reason = ""

    # --- 하위 클래스가 구현 ---
    def power(self, unit: float) -> float:  # pragma: no cover - 추상
        raise NotImplementedError

    def allocation(self, unit: float) -> dict:  # pragma: no cover - 추상
        raise NotImplementedError

    def power_of_allocation(self, alloc: dict) -> float:  # pragma: no cover - 추상
        raise NotImplementedError

    def effect(self) -> dict:  # pragma: no cover - 추상
        raise NotImplementedError

    def scaled(self, factor: float) -> "Design":  # pragma: no cover - 추상
        raise NotImplementedError

    def notes(self) -> list[str]:
        return []

    def references(self) -> list[str]:
        return []

    def plan_lines(self, alloc: dict) -> list[tuple[str, str]]:
        """정해진 배분에서 **그 설계에만 있는 핵심 숫자**를 (라벨, 값)으로.

        표본수 표 바로 아래에 출력되며 마크다운·JSON에도 들어간다. 기본값은 없음.
        """
        return []

    def information(self, alloc: dict) -> dict:
        """중간분석의 **정보량**이 무엇으로 세어지는지.

        대부분의 설계에서 정보량은 1차 결과변수를 완료한 인원 수에 비례하지만,
        생존분석은 **사건 수**에 비례한다. 정보비율 50% 시점을 '등록 인원의 절반'으로
        읽으면 실제로는 정보가 거의 없는 시점에 중간분석을 하게 되고, α 소비함수의
        보장이 통째로 무너진다. 그래서 설계마다 이 값을 따로 알려 준다.
        """
        return {"label": "누적 N", "unit": "명", "total": alloc.get("total"),
                "caveat": "'누적 N'은 **1차 결과변수를 완료한** 인원입니다 "
                          "(등록 인원이 아닙니다 — 탈락률만큼 더 등록해야 그 시점에 "
                          "이 인원의 결과가 모입니다)."}

    #: --sensitivity 표의 열 제목 (효과크기가 아니라 마진을 흔드는 설계가 있다)
    sensitivity_kr = "효과크기 가정 배율"

    def sensitivity_value(self, factor: float) -> str:
        """민감도 표 열 머리에 보일 '그 배율에서의 실제 값'."""
        try:
            scaled = self.scaled(factor)
        except PowerPlanError:
            return f"×{factor:g}"
        return f"{scaled.effect()['value']:.3g}"


# --------------------------------------------------------------------------
# 1) 두 독립표본 평균 비교 (Student t)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class TwoSampleT(Design):
    """두 독립군 평균 비교. 정확 비중심 t 검정력."""

    d: float
    alpha: float = 0.05
    sides: int = 2
    ratio: float = 1.0
    #: 기저값과 추적값의 상관 r (ANCOVA/변화량 분석에서만 사용)
    baseline_r: float = 0.0
    #: raw = 추적값만 비교 · ancova = 기저값 공변량 보정 · change = 변화량 비교
    analysis: str = "raw"

    key = "ttest2"
    name_kr = "두 독립군 평균 비교"
    name_en = "Two independent means"
    unit_kr = "1군 n"
    min_unit = 2

    _TEST_NAMES = {
        "raw": ("독립표본 t 검정 (등분산 가정)", "two-sample t-test (equal variances)"),
        "ancova": ("공분산분석 ANCOVA (기저값을 공변량으로 보정)",
                   "ANCOVA with the baseline value as covariate"),
        "change": ("변화량(사후−사전) 독립표본 t 검정",
                   "two-sample t-test on change scores"),
    }

    def __post_init__(self) -> None:
        object.__setattr__(self, "alpha", _check_alpha(self.alpha))
        object.__setattr__(self, "sides", _check_sides(self.sides))
        object.__setattr__(self, "ratio", _check_ratio(self.ratio))
        if not math.isfinite(self.d) or self.d == 0.0:
            raise PowerPlanError(
                "--d(효과크기)는 0이 아닌 유한한 값이어야 합니다. "
                "차이가 0이면 어떤 표본수로도 목표 검정력에 도달할 수 없습니다"
            )
        if self.analysis not in self._TEST_NAMES:
            raise PowerPlanError(
                f"--analysis: raw, ancova, change 중 하나여야 합니다 (받은 값: {self.analysis!r})"
            )
        r = self.baseline_r
        if not (math.isfinite(r) and -1.0 < r < 1.0):
            raise PowerPlanError(
                f"--baseline-r: -1과 1 사이여야 합니다 (받은 값: {self.baseline_r!r})"
            )
        if self.analysis != "raw" and r == 0.0:
            raise PowerPlanError(
                f"--analysis {self.analysis}는 기저값-추적값 상관이 필요합니다 — "
                "--baseline-r 0.7 처럼 지정하세요 (사전연구가 있으면 "
                "`powerplan pilot ... --baseline 기저값열`로 추정할 수 있습니다)"
            )

    @property
    def test_kr(self) -> str:
        return self._TEST_NAMES[self.analysis][0]

    @property
    def test_en(self) -> str:
        return self._TEST_NAMES[self.analysis][1]

    @property
    def design_factor(self) -> float:
        """추적값만 비교할 때 대비 **분산 배율** (작을수록 표본수가 줄어든다).

        - ANCOVA (기저값 공변량 1개): 1 − r²  (Frison & Pocock 1992)
        - 변화량 분석:                2(1 − r) — r > 0.5 일 때만 유리하다
        """
        r = self.baseline_r
        if self.analysis == "ancova":
            return 1.0 - r * r
        if self.analysis == "change":
            return 2.0 * (1.0 - r)
        return 1.0

    @property
    def effective_d(self) -> float:
        """분석 방법을 반영한 실질 효과크기 d / √(설계배율)."""
        return abs(self.d) / math.sqrt(self.design_factor)

    def _power(self, n1: float, n2: float) -> float:
        # ANCOVA는 공변량 1개에 자유도 1을 쓴다
        ancova = self.analysis == "ancova"
        df = n1 + n2 - 2.0 - (1.0 if ancova else 0.0)
        if df < 1.0:
            return 0.0
        ncp = self.effective_d / math.sqrt(
            (1.0 / n1 + 1.0 / n2) * ancova_inflation(n1 + n2, ancova))
        if self.sides == 1:
            return nct_sf(t_ppf(1.0 - self.alpha, df), df, ncp)
        tc = t_ppf(1.0 - self.alpha / 2.0, df)
        return nct_sf(tc, df, ncp) + nct_cdf(-tc, df, ncp)

    def power(self, unit: float) -> float:
        return self._power(float(unit), self.ratio * float(unit))

    def allocation(self, unit: float) -> dict:
        n1 = max(self.min_unit, math.ceil(unit - 1e-9))
        n2 = max(self.min_unit, math.ceil(self.ratio * n1 - 1e-9))
        return {"n1": n1, "n2": n2, "total": n1 + n2}

    def power_of_allocation(self, alloc: dict) -> float:
        return self._power(float(alloc["n1"]), float(alloc["n2"]))

    def effect(self) -> dict:
        out = {"name": "Cohen's d", "name_en": "Cohen's d", "value": self.d,
               "label": label_d(self.d), "analysis": self.analysis}
        if self.analysis != "raw":
            out["baseline_r"] = self.baseline_r
            out["design_factor"] = self.design_factor
            out["effective_d"] = self.effective_d
            out["label"] = (f"{label_d(self.d)} · 설계배율 {self.design_factor:.3f}"
                            f" → 실질 d {self.effective_d:.3f}")
        return out

    def scaled(self, factor: float) -> "TwoSampleT":
        return TwoSampleT(self.d * factor, self.alpha, self.sides, self.ratio,
                          self.baseline_r, self.analysis)

    def notes(self) -> list[str]:
        out = ["검정력은 비중심 t 분포로 정확히 계산했습니다 (정규근사 아님)."]
        if self.analysis == "ancova":
            out.append(
                f"기저값을 공변량으로 보정(ANCOVA)하면 분산이 (1 − r²) = "
                f"{self.design_factor:.3f}배로 줄어 필요한 표본수가 그만큼 감소합니다 "
                f"(r = {self.baseline_r:g}). 통계분석계획서(SAP)에 ANCOVA를 명시할 때 이 값을 쓰세요."
            )
            out.append("r을 낙관적으로 잡으면 표본수가 과소해집니다 — 사전연구/문헌의 하한을 쓰세요.")
            out.append(
                "공변량 불균형에 따른 분산 팽창 1 + 1/(N−3)까지 반영했습니다 — "
                "Borm 등(2007)이 'ANCOVA에는 몇 명을 더하라'고 말하는 항입니다.")
        elif self.analysis == "change":
            out.append(
                f"변화량 분석의 분산 배율은 2(1 − r) = {self.design_factor:.3f}배입니다 "
                f"(r = {self.baseline_r:g}). **r > 0.5일 때만** 추적값만 비교하는 것보다 "
                "유리하며, 같은 r이면 ANCOVA(1 − r²)가 항상 더 효율적입니다."
            )
        else:
            out.append(
                "기저값(사전 측정)이 있다면 --analysis ancova --baseline-r 0.7 처럼 지정하세요 — "
                "ANCOVA 보정으로 필요한 표본수가 크게 줄어듭니다(같은 가정에서 더 정직한 숫자입니다)."
            )
        if self.ratio != 1.0:
            out.append(
                f"배분비 1:{self.ratio:g} — 같은 총 N이면 1:1이 항상 더 높은 검정력을 줍니다."
            )
        out.append("등분산을 가정합니다. Welch 검정을 쓸 계획이면 약간의 여유(≈5%)를 두세요.")
        out.append(
            "효과크기 옆의 '작음/중간/큼'은 Cohen(1988)의 **관례 라벨**일 뿐 임상적 "
            "중요성이 아닙니다. 프로토콜에는 '중간 효과라서'가 아니라 '임상적으로 "
            "의미있는 최소 차이(MCID)가 얼마라서'를 근거로 쓰세요.")
        return out

    def references(self) -> list[str]:
        out = ["Cohen J. Statistical Power Analysis for the Behavioral Sciences. 2nd ed. 1988."]
        if self.analysis != "raw":
            out.append("Frison L, Pocock SJ. Repeated measures in clinical trials: analysis "
                       "using mean summary statistics and its implications for "
                       "design. Stat Med. 1992;11:1685-1704.")
            out.append("Borm GF, Fransen J, Lemmens WAJG. A simple sample size formula for "
                       "analysis of covariance in randomized clinical trials. "
                       "J Clin Epidemiol. 2007;60:1234-1238.")
        return out


# --------------------------------------------------------------------------
# 2) 대응표본 / 단일표본 평균 (전후 비교)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class PairedT(Design):
    """대응표본(전후) 평균 비교. dz = 변화량 평균 / 변화량 SD."""

    dz: float
    alpha: float = 0.05
    sides: int = 2

    key = "paired"
    supports_cluster = False
    cluster_reason = ("대응표본은 같은 사람의 전후를 비교하므로 군집을 '군에 배정'하는 "
                      "구조가 아닙니다. 병원·학급 단위로 배정하는 설계라면 ttest2에 "
                      "--cluster-* 를 쓰세요")
    name_kr = "대응표본(전후) 평균 비교"
    name_en = "Paired means (pre-post)"
    test_kr = "대응표본 t 검정"
    test_en = "paired t-test"
    unit_kr = "쌍 수 n"
    min_unit = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "alpha", _check_alpha(self.alpha))
        object.__setattr__(self, "sides", _check_sides(self.sides))
        if not math.isfinite(self.dz) or self.dz == 0.0:
            raise PowerPlanError("--dz(효과크기)는 0이 아닌 유한한 값이어야 합니다")

    def power(self, unit: float) -> float:
        n = float(unit)
        df = n - 1.0
        if df < 1.0:
            return 0.0
        ncp = abs(self.dz) * math.sqrt(n)
        if self.sides == 1:
            return nct_sf(t_ppf(1.0 - self.alpha, df), df, ncp)
        tc = t_ppf(1.0 - self.alpha / 2.0, df)
        return nct_sf(tc, df, ncp) + nct_cdf(-tc, df, ncp)

    def allocation(self, unit: float) -> dict:
        n = max(self.min_unit, math.ceil(unit - 1e-9))
        return {"n": n, "total": n}

    def power_of_allocation(self, alloc: dict) -> float:
        return self.power(alloc["n"])

    def effect(self) -> dict:
        return {"name": "Cohen's dz (변화량 기준)", "name_en": "Cohen's dz (change score)",
                "value": self.dz, "label": label_d(self.dz)}

    def scaled(self, factor: float) -> "PairedT":
        return PairedT(self.dz * factor, self.alpha, self.sides)

    def notes(self) -> list[str]:
        return [
            "dz는 **변화량의 평균 ÷ 변화량의 SD**입니다. 사전-사후 SD가 아니라 차이의 SD입니다.",
            "차이의 SD를 모르면 SD_diff = SD·√(2(1−r))로 추정하세요 (r = 사전-사후 상관). "
            "r이 클수록(측정이 안정적일수록) 필요한 n이 크게 줄어듭니다.",
            "검정력은 비중심 t 분포로 정확히 계산했습니다.",
        ]

    def references(self) -> list[str]:
        return ["Cohen J. Statistical Power Analysis for the Behavioral Sciences. 2nd ed. 1988."]


@dataclass(frozen=True)
class OneSampleT(PairedT):
    """단일표본 평균 비교 (기준값 대비). 수식은 대응표본과 동일하다."""

    key = "onesample"
    name_kr = "단일표본 평균 비교 (기준값 대비)"
    name_en = "One-sample mean vs reference"
    test_kr = "단일표본 t 검정"
    test_en = "one-sample t-test"
    unit_kr = "n"

    def effect(self) -> dict:
        return {"name": "Cohen's d", "name_en": "Cohen's d", "value": self.dz,
                "label": label_d(self.dz)}

    def scaled(self, factor: float) -> "OneSampleT":
        return OneSampleT(self.dz * factor, self.alpha, self.sides)

    def notes(self) -> list[str]:
        return ["d = (관측 평균 − 기준값) / SD. 검정력은 비중심 t 분포로 정확히 계산했습니다."]


# --------------------------------------------------------------------------
# 3) 두 비율 비교
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class TwoProportions(Design):
    """두 독립 비율 비교 (정규근사 z 검정, 귀무가설 하 합동분산)."""

    p1: float
    p2: float
    alpha: float = 0.05
    sides: int = 2
    ratio: float = 1.0
    continuity: bool = False

    key = "prop2"
    name_kr = "두 군 비율(반응률) 비교"
    name_en = "Two independent proportions"
    test_kr = "비율 비교 z 검정 (합동분산)"
    test_en = "z-test for two proportions (pooled variance)"
    unit_kr = "1군 n"
    min_unit = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "alpha", _check_alpha(self.alpha))
        object.__setattr__(self, "sides", _check_sides(self.sides))
        object.__setattr__(self, "ratio", _check_ratio(self.ratio))
        for name, p in (("--p1", self.p1), ("--p2", self.p2)):
            if not (math.isfinite(p) and 0.0 < p < 1.0):
                raise PowerPlanError(f"{name}: 0과 1 사이의 비율이어야 합니다 (받은 값: {p!r})")
        if self.p1 == self.p2:
            raise PowerPlanError("--p1과 --p2가 같으면 목표 검정력에 도달할 수 없습니다")
        object.__setattr__(self, "continuity", bool(self.continuity))
        # 연속성 보정을 켜면 검정력이 n에 대해 완전 단조가 아닐 수 있다
        object.__setattr__(self, "monotone", not self.continuity)

    def _power(self, n1: float, n2: float) -> float:
        p1, p2 = self.p1, self.p2
        delta = abs(p1 - p2)
        pbar = (n1 * p1 + n2 * p2) / (n1 + n2)
        inv = 1.0 / n1 + 1.0 / n2
        se_null = math.sqrt(pbar * (1.0 - pbar) * inv)
        se_alt = math.sqrt(p1 * (1.0 - p1) / n1 + p2 * (1.0 - p2) / n2)
        if se_alt <= 0.0 or se_null <= 0.0:
            return 0.0
        zc = norm_ppf(1.0 - self.alpha / self.sides)
        # 기각역: |p̂1 − p̂2| − cc > z_c·SE_null  (cc = Yates 연속성 보정, 없으면 0)
        # 따라서 두 꼬리 모두 cc가 **빼지는** 방향으로 들어간다.
        cc = 0.5 * inv if self.continuity else 0.0
        bound = cc + zc * se_null
        upper = norm_cdf((delta - bound) / se_alt)
        lower = norm_cdf((-delta - bound) / se_alt) if self.sides == 2 else 0.0
        return min(1.0, upper + lower)

    def power(self, unit: float) -> float:
        return self._power(float(unit), self.ratio * float(unit))

    def allocation(self, unit: float) -> dict:
        n1 = max(self.min_unit, math.ceil(unit - 1e-9))
        n2 = max(self.min_unit, math.ceil(self.ratio * n1 - 1e-9))
        return {"n1": n1, "n2": n2, "total": n1 + n2}

    def power_of_allocation(self, alloc: dict) -> float:
        return self._power(float(alloc["n1"]), float(alloc["n2"]))

    def effect(self) -> dict:
        rd = self.p2 - self.p1
        odds = (self.p2 / (1.0 - self.p2)) / (self.p1 / (1.0 - self.p1))
        h = 2.0 * math.asin(math.sqrt(self.p2)) - 2.0 * math.asin(math.sqrt(self.p1))
        return {
            "name": "비율차 (risk difference)",
            "name_en": "risk difference",
            "value": rd,
            "label": f"p1={self.p1:g} → p2={self.p2:g}",
            "risk_ratio": self.p2 / self.p1,
            "odds_ratio": odds,
            "cohen_h": h,
        }

    def scaled(self, factor: float) -> "TwoProportions":
        # p1을 고정하고 '차이'를 factor배 (0/1 경계는 넘지 않게 클리핑)
        p2 = self.p1 + (self.p2 - self.p1) * factor
        p2 = min(max(p2, 1e-6), 1.0 - 1e-6)
        if abs(p2 - self.p1) < 1e-9:
            raise PowerPlanError("민감도 분석: 비율차가 0이 되어 계산할 수 없습니다")
        return TwoProportions(self.p1, p2, self.alpha, self.sides, self.ratio, self.continuity)

    def notes(self) -> list[str]:
        out = [
            "정규근사 z 검정 기준입니다. 실제로 Fisher 정확검정을 쓰면 보수적이므로 "
            "표본수를 5~15% 더 잡는 편이 안전합니다.",
            "기대 사건수(n·p)가 군당 5 미만이면 정규근사가 깨집니다 — 결과를 신뢰하지 마세요.",
        ]
        if self.continuity:
            out.append(
                "Yates 연속성 보정을 검정통계량에 적용해 더 보수적으로 계산했습니다. "
                "표본수를 직접 부풀리는 Casagrande–Pike–Smith 공식과는 몇 명 차이가 날 수 있습니다."
            )
        else:
            out.append("연속성 보정 없음(--continuity로 켤 수 있음).")
        return out

    def references(self) -> list[str]:
        out = ["Fleiss JL, Levin B, Paik MC. Statistical Methods for Rates and "
               "Proportions. 3rd ed. 2003."]
        if self.continuity:
            # 이 툴은 CPS의 표본수 부풀림 공식을 쓰지 않는다 — 비교용으로만 적는다
            out.append("Casagrande JT, Pike MC, Smith PG. Biometrics. 1978;34:483-486. "
                       "(연속성 보정을 표본수에 직접 넣는 대안 — 이 툴은 검정통계량에 "
                       "Yates 보정을 적용한다)")
        return out


# --------------------------------------------------------------------------
# 4) 일원배치 분산분석
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class OneWayAnova(Design):
    """k개 군 평균 동일성 검정 (일원배치 ANOVA, 균등배분)."""

    f: float
    k: int
    alpha: float = 0.05

    key = "anova"
    name_kr = "여러 군(k개) 평균 비교"
    name_en = "One-way ANOVA (k groups)"
    test_kr = "일원배치 분산분석 F 검정"
    test_en = "one-way ANOVA F-test"
    unit_kr = "군당 n"
    min_unit = 2
    supports_sequential = False
    sequential_reason = ("전방향 F 검정은 방향이 없어 상·하측 경계를 정의할 수 "
                         "없습니다. 특정 두 군 비교를 주 분석으로 정하고 ttest2에 "
                         "--interim을 붙이세요")

    def __post_init__(self) -> None:
        object.__setattr__(self, "alpha", _check_alpha(self.alpha))
        if not isinstance(self.k, int) or isinstance(self.k, bool) or self.k < 2:
            raise PowerPlanError(f"--k: 2 이상의 정수여야 합니다 (받은 값: {self.k!r})")
        if self.k > 1000:
            raise PowerPlanError(f"--k: 1000을 넘는 군 수는 지원하지 않습니다 (받은 값: {self.k})")
        if not math.isfinite(self.f) or self.f <= 0.0:
            raise PowerPlanError(f"--f(Cohen's f): 0보다 커야 합니다 (받은 값: {self.f!r})")
        if self.f > 100.0:
            raise PowerPlanError(
                f"--f(Cohen's f): 100을 넘는 값은 현실적인 효과크기가 아닙니다 "
                f"(받은 값: {self.f:g}). 보통 0.1~0.4를 씁니다"
            )

    def power(self, unit: float) -> float:
        n = float(unit)
        total = self.k * n
        df1 = float(self.k - 1)
        df2 = total - self.k
        if df2 < 1.0:
            return 0.0
        lam = self.f * self.f * total
        return ncf_sf(f_ppf(1.0 - self.alpha, df1, df2), df1, df2, lam)

    def allocation(self, unit: float) -> dict:
        n = max(self.min_unit, math.ceil(unit - 1e-9))
        return {"n_per_group": n, "k": self.k, "total": n * self.k}

    def power_of_allocation(self, alloc: dict) -> float:
        return self.power(alloc["n_per_group"])

    def effect(self) -> dict:
        eta2 = self.f * self.f / (1.0 + self.f * self.f)
        return {
            "name": "Cohen's f",
            "name_en": "Cohen's f",
            "value": self.f,
            "label": label_f(self.f),
            "eta_squared": eta2,
        }

    def scaled(self, factor: float) -> "OneWayAnova":
        return OneWayAnova(self.f * factor, self.k, self.alpha)

    def notes(self) -> list[str]:
        return [
            "F 검정은 본질적으로 단측이므로 --sides는 적용되지 않습니다.",
            "이 표본수는 **전체 F 검정(군간 차이 있음)** 기준입니다. 특정 두 군 사후비교까지 "
            "확실히 검출하려면 보정된 α로 두 군 비교(ttest2)를 따로 계산하세요.",
            "균등배분(모든 군 같은 n)을 가정합니다.",
            "검정력은 비중심 F 분포로 정확히 계산했습니다.",
        ]

    def references(self) -> list[str]:
        return ["Cohen J. Statistical Power Analysis for the Behavioral Sciences. 2nd ed. 1988."]


# --------------------------------------------------------------------------
# 5) 상관계수
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class CorrelationTest(Design):
    """Pearson 상관계수가 0인지 검정 (Fisher z 근사)."""

    r: float
    alpha: float = 0.05
    sides: int = 2
    bias_correct: bool = False

    key = "corr"
    name_kr = "상관계수 검정 (r ≠ 0)"
    name_en = "Correlation (r ≠ 0)"
    test_kr = "Pearson 상관 검정 (Fisher z 근사)"
    test_en = "Pearson correlation test (Fisher z approximation)"
    unit_kr = "n"
    min_unit = 4

    def __post_init__(self) -> None:
        object.__setattr__(self, "alpha", _check_alpha(self.alpha))
        object.__setattr__(self, "sides", _check_sides(self.sides))
        if not (math.isfinite(self.r) and -1.0 < self.r < 1.0):
            raise PowerPlanError(f"--r: -1과 1 사이여야 합니다 (받은 값: {self.r!r})")
        if self.r == 0.0:
            raise PowerPlanError("--r: 0이면 목표 검정력에 도달할 수 없습니다")
        object.__setattr__(self, "bias_correct", bool(self.bias_correct))

    def power(self, unit: float) -> float:
        n = float(unit)
        if n <= 3.0:
            return 0.0
        mean_z = abs(fisher_z(self.r))
        if self.bias_correct:
            # E[z] ≈ atanh(r) + r/(2(n−1)) — 정확법에 더 가까워지지만 1명 덜 나올 수 있음
            mean_z += abs(self.r) / (2.0 * (n - 1.0))
        z = mean_z * math.sqrt(n - 3.0)
        zc = norm_ppf(1.0 - self.alpha / self.sides)
        upper = norm_cdf(z - zc)
        lower = norm_cdf(-z - zc) if self.sides == 2 else 0.0
        return min(1.0, upper + lower)

    def allocation(self, unit: float) -> dict:
        n = max(self.min_unit, math.ceil(unit - 1e-9))
        return {"n": n, "total": n}

    def power_of_allocation(self, alloc: dict) -> float:
        return self.power(alloc["n"])

    def effect(self) -> dict:
        return {
            "name": "Pearson r",
            "name_en": "Pearson r",
            "value": self.r,
            "label": label_r(self.r),
            "r_squared": self.r * self.r,
        }

    def scaled(self, factor: float) -> "CorrelationTest":
        r = max(min(self.r * factor, 1.0 - 1e-9), -1.0 + 1e-9)
        return CorrelationTest(r, self.alpha, self.sides, self.bias_correct)

    def notes(self) -> list[str]:
        out = ["이변량 정규성을 가정합니다. Spearman을 쓸 계획이면 약 10% 여유를 두세요."]
        if self.bias_correct:
            out.insert(0, "Fisher z + 편향보정(E[z] ≈ atanh r + r/(2(n−1))) — G*Power의 정확법과 "
                          "대개 일치하지만, 1명 적게 나올 수 있습니다.")
        else:
            out.insert(0, "Fisher z 근사(SE = 1/√(n−3))입니다. 정확법(G*Power exact)보다 보통 "
                          "0~1명 크게 나옵니다 — 계획 단계에서는 보수적이라 안전합니다. "
                          "심사자의 G*Power 값과 맞추려면 --bias-correct를 쓰세요.")
        return out

    def references(self) -> list[str]:
        return ["Cohen J. Statistical Power Analysis for the Behavioral Sciences. 2nd ed. 1988."]


# --------------------------------------------------------------------------
# 6) 비열등성 (평균, 단측)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class NonInferiorityT(Design):
    """두 군 평균의 비열등성 검정 (단측). margin은 원래 단위의 허용 열등폭."""

    margin: float
    sd: float
    diff: float = 0.0
    alpha: float = 0.025
    ratio: float = 1.0
    lower_is_better: bool = False

    key = "noninf"
    sensitivity_kr = "마진 가정 배율"
    name_kr = "비열등성 검정 (두 군 평균)"
    name_en = "Non-inferiority (two means)"
    test_kr = "단측 t 검정 (비열등성 마진)"
    test_en = "one-sided t-test for non-inferiority"
    unit_kr = "1군 n"
    min_unit = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "alpha", _check_alpha(self.alpha))
        object.__setattr__(self, "ratio", _check_ratio(self.ratio))
        if not (math.isfinite(self.margin) and self.margin > 0.0):
            raise PowerPlanError(f"--margin: 0보다 큰 값이어야 합니다 (받은 값: {self.margin!r})")
        if not (math.isfinite(self.sd) and self.sd > 0.0):
            raise PowerPlanError(f"--sd: 0보다 큰 값이어야 합니다 (받은 값: {self.sd!r})")
        if not math.isfinite(self.diff):
            raise PowerPlanError(f"--diff: 유한한 값이어야 합니다 (받은 값: {self.diff!r})")
        object.__setattr__(self, "lower_is_better", bool(self.lower_is_better))
        if self._effective_gap() <= 0.0:
            raise PowerPlanError(
                "가정한 실제 차이가 비열등성 마진을 이미 넘어섰습니다 "
                f"(--diff {self.diff:g}, --margin {self.margin:g}, "
                f"--lower-is-better={self.lower_is_better}). "
                "어떤 표본수로도 비열등성을 입증할 수 없습니다"
            )

    def _effective_gap(self) -> float:
        """표준화 전, 마진까지 남은 여유 (양수여야 입증 가능)."""
        return self.margin - self.diff if self.lower_is_better else self.margin + self.diff

    def _power(self, n1: float, n2: float) -> float:
        df = n1 + n2 - 2.0
        if df < 1.0:
            return 0.0
        se_unit = math.sqrt(1.0 / n1 + 1.0 / n2)
        ncp = self._effective_gap() / (self.sd * se_unit)
        return nct_sf(t_ppf(1.0 - self.alpha, df), df, ncp)

    def power(self, unit: float) -> float:
        return self._power(float(unit), self.ratio * float(unit))

    def allocation(self, unit: float) -> dict:
        n1 = max(self.min_unit, math.ceil(unit - 1e-9))
        n2 = max(self.min_unit, math.ceil(self.ratio * n1 - 1e-9))
        return {"n1": n1, "n2": n2, "total": n1 + n2}

    def power_of_allocation(self, alloc: dict) -> float:
        return self._power(float(alloc["n1"]), float(alloc["n2"]))

    def effect(self) -> dict:
        return {
            "name": "비열등성 마진 (표준화)",
            "name_en": "standardised non-inferiority margin",
            "value": self.margin / self.sd,
            "effective_gap_standardised": self._effective_gap() / self.sd,
            "label": f"마진 {self.margin:g}, 가정 차이 {self.diff:g}, SD {self.sd:g}",
            "margin_raw": self.margin,
            "assumed_diff": self.diff,
            "sd": self.sd,
        }

    def scaled(self, factor: float) -> "NonInferiorityT":
        return NonInferiorityT(
            self.margin * factor, self.sd, self.diff, self.alpha, self.ratio, self.lower_is_better
        )

    def notes(self) -> list[str]:
        direction = "낮을수록 좋은" if self.lower_is_better else "높을수록 좋은"
        return [
            f"결과지표가 **{direction}** 지표라고 가정했습니다 "
            "(--lower-is-better로 바꿀 수 있음).",
            "α는 단측입니다(관례상 0.025). --alpha로 바꿀 수 있습니다.",
            "마진은 통계가 아니라 **임상적 판단**으로 정해야 하며, 근거를 프로토콜에 적어야 합니다.",
            "비열등성 시험은 ITT/PP 두 집단 모두에서 결론이 유지되어야 하므로 탈락 여유를 "
            "넉넉히 두세요(--dropout).",
            "검정력은 비중심 t 분포로 정확히 계산했습니다.",
        ]

    def references(self) -> list[str]:
        return [
            "ICH E9 Statistical Principles for Clinical Trials (1998).",
            "Julious SA. Sample Sizes for Clinical Trials. Chapman & Hall/CRC; 2010.",
        ]


# --------------------------------------------------------------------------
# 7) 동등성 (TOST, 평균)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class EquivalenceT(Design):
    """두 군 평균의 동등성 검정 (TOST). 두 단측검정을 **정확히** 결합해 계산."""

    margin: float
    sd: float
    diff: float = 0.0
    alpha: float = 0.05
    ratio: float = 1.0

    key = "equiv"
    sensitivity_kr = "마진 가정 배율"
    name_kr = "동등성 검정 (TOST, 두 군 평균)"
    name_en = "Equivalence (TOST, two means)"
    test_kr = "TOST (두 개의 단측 t 검정)"
    test_en = "TOST procedure (two one-sided t-tests)"
    unit_kr = "1군 n"
    min_unit = 2
    supports_sequential = False
    sequential_reason = ("TOST는 두 단측검정을 동시에 만족해야 하는 절차라 하나의 "
                         "Z 경계로 α를 나눌 수 없습니다. 동등성 시험의 중간분석은 "
                         "별도 이론(예: 반복 신뢰구간)이 필요합니다")

    def __post_init__(self) -> None:
        object.__setattr__(self, "alpha", _check_alpha(self.alpha))
        object.__setattr__(self, "ratio", _check_ratio(self.ratio))
        if not (math.isfinite(self.margin) and self.margin > 0.0):
            raise PowerPlanError(f"--margin: 0보다 큰 값이어야 합니다 (받은 값: {self.margin!r})")
        if not (math.isfinite(self.sd) and self.sd > 0.0):
            raise PowerPlanError(f"--sd: 0보다 큰 값이어야 합니다 (받은 값: {self.sd!r})")
        if not math.isfinite(self.diff):
            raise PowerPlanError(f"--diff: 유한한 값이어야 합니다 (받은 값: {self.diff!r})")
        if abs(self.diff) >= self.margin:
            raise PowerPlanError(
                f"가정한 실제 차이(|{self.diff:g}|)가 동등성 마진({self.margin:g}) 밖입니다. "
                "어떤 표본수로도 동등성을 입증할 수 없습니다"
            )

    def _power(self, n1: float, n2: float) -> float:
        df = n1 + n2 - 2.0
        if df < 1.0:
            return 0.0
        se_unit = math.sqrt(1.0 / n1 + 1.0 / n2)
        a = self.margin / (self.sd * se_unit)
        dd = self.diff / (self.sd * se_unit)
        tc = t_ppf(1.0 - self.alpha, df)
        root_df = math.sqrt(df)

        def integrand(x: float) -> float:
            shift = tc * x / root_df
            hi = a - dd - shift
            lo = -a - dd + shift
            if hi <= lo:
                return 0.0
            return norm_cdf(hi) - norm_cdf(lo)

        return min(1.0, max(0.0, chi_expectation(df, integrand)))

    def power(self, unit: float) -> float:
        return self._power(float(unit), self.ratio * float(unit))

    def allocation(self, unit: float) -> dict:
        n1 = max(self.min_unit, math.ceil(unit - 1e-9))
        n2 = max(self.min_unit, math.ceil(self.ratio * n1 - 1e-9))
        return {"n1": n1, "n2": n2, "total": n1 + n2}

    def power_of_allocation(self, alloc: dict) -> float:
        return self._power(float(alloc["n1"]), float(alloc["n2"]))

    def effect(self) -> dict:
        return {
            "name": "동등성 마진 (표준화)",
            "name_en": "standardised equivalence margin",
            "value": self.margin / self.sd,
            "label": f"±{self.margin:g}, 가정 차이 {self.diff:g}, SD {self.sd:g}",
            "margin_raw": self.margin,
            "assumed_diff": self.diff,
            "sd": self.sd,
        }

    def scaled(self, factor: float) -> "EquivalenceT":
        margin = self.margin * factor
        if abs(self.diff) >= margin:
            raise PowerPlanError("민감도 분석: 마진이 가정 차이보다 작아져 계산할 수 없습니다")
        return EquivalenceT(margin, self.sd, self.diff, self.alpha, self.ratio)

    def notes(self) -> list[str]:
        return [
            "TOST 검정력을 두 단측검정의 **동시 성립 확률**로, 분산을 추정한다는 점(t 분포)까지 "
            "반영해 정확히 계산했습니다. z 기반 정규근사(2Φ(M/SE − z)−1)는 검정력을 과대평가하므로 "
            "이 툴의 표본수가 약간 더 큽니다 — 그쪽이 맞습니다.",
            "α는 각 단측검정에 적용되며 전체 1종오류도 α로 유지됩니다 (관례상 0.05).",
            "동등성 마진은 임상적 판단으로 정하고 근거를 프로토콜에 남기세요.",
        ]

    def references(self) -> list[str]:
        return [
            "Schuirmann DJ. J Pharmacokinet Biopharm. 1987;15:657-680.",
            "Lakens D. Soc Psychol Personal Sci. 2017;8:355-362.",
        ]


# --------------------------------------------------------------------------
# 8) 이분형 결과의 비열등성 / 동등성 (위험차 기준, 정규근사)
# --------------------------------------------------------------------------
def _binary_se_unit(p1: float, p2: float, ratio: float) -> float:
    """1군 n = 1일 때의 위험차 표준오차 (군간 분산을 따로 쓰는 비합동 SE).

    비열등성·동등성 검정에서는 귀무가설이 '차이 = 마진'이라 합동분산이 맞지 않는다.
    규제 관행(ICH E9, FDA 지침)도 비합동 SE 또는 그 신뢰구간을 쓴다.
    """
    return math.sqrt(p1 * (1.0 - p1) + p2 * (1.0 - p2) / ratio)


def _check_prop(name: str, p: float) -> float:
    if not (math.isfinite(p) and 0.0 < p < 1.0):
        raise PowerPlanError(f"{name}: 0과 1 사이의 비율이어야 합니다 (받은 값: {p!r})")
    return float(p)


@dataclass(frozen=True)
class NonInferiorityProportions(Design):
    """두 군 **비율**의 비열등성 검정 (위험차 마진, 단측 정규근사)."""

    p1: float
    p2: float
    margin: float
    alpha: float = 0.025
    ratio: float = 1.0
    lower_is_better: bool = False

    key = "noninf_prop"
    sensitivity_kr = "마진 가정 배율"
    name_kr = "비열등성 검정 (두 군 비율)"
    name_en = "Non-inferiority (two proportions)"
    test_kr = "단측 비율차 z 검정 (비열등성 마진)"
    test_en = "one-sided z-test for non-inferiority in proportions"
    unit_kr = "1군 n"
    min_unit = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "alpha", _check_alpha(self.alpha))
        object.__setattr__(self, "ratio", _check_ratio(self.ratio))
        object.__setattr__(self, "p1", _check_prop("--p1", self.p1))
        object.__setattr__(self, "p2", _check_prop("--p2", self.p2))
        if not (math.isfinite(self.margin) and 0.0 < self.margin < 1.0):
            raise PowerPlanError(
                f"--margin: 0과 1 사이의 비율차여야 합니다 (받은 값: {self.margin!r}). "
                "10%p 마진은 0.10으로 적습니다"
            )
        object.__setattr__(self, "lower_is_better", bool(self.lower_is_better))
        if self._effective_gap() <= 0.0:
            raise PowerPlanError(
                f"가정한 실제 비율차({self.p2 - self.p1:+g})가 비열등성 마진"
                f"({self.margin:g})을 이미 넘어섰습니다 "
                f"(--lower-is-better={self.lower_is_better}). "
                "어떤 표본수로도 비열등성을 입증할 수 없습니다"
            )

    def _effective_gap(self) -> float:
        """마진까지 남은 여유 (양수여야 입증 가능)."""
        diff = self.p2 - self.p1
        return self.margin - diff if self.lower_is_better else self.margin + diff

    def _power(self, n1: float) -> float:
        se = _binary_se_unit(self.p1, self.p2, self.ratio) / math.sqrt(n1)
        if se <= 0.0:
            return 0.0
        return norm_cdf(self._effective_gap() / se - norm_ppf(1.0 - self.alpha))

    def power(self, unit: float) -> float:
        return self._power(float(unit))

    def allocation(self, unit: float) -> dict:
        n1 = max(self.min_unit, math.ceil(unit - 1e-9))
        n2 = max(self.min_unit, math.ceil(self.ratio * n1 - 1e-9))
        return {"n1": n1, "n2": n2, "total": n1 + n2}

    def power_of_allocation(self, alloc: dict) -> float:
        n1, n2 = float(alloc["n1"]), float(alloc["n2"])
        se = math.sqrt(self.p1 * (1.0 - self.p1) / n1 + self.p2 * (1.0 - self.p2) / n2)
        if se <= 0.0:
            return 0.0
        return norm_cdf(self._effective_gap() / se - norm_ppf(1.0 - self.alpha))

    def effect(self) -> dict:
        return {
            "name": "비열등성 마진 (위험차)",
            "name_en": "non-inferiority margin (risk difference)",
            "value": self.margin,
            "label": f"대조 {self.p1:g} vs 중재 {self.p2:g}, 마진 {self.margin:g}",
            "margin_raw": self.margin,
            "assumed_diff": self.p2 - self.p1,
            "p1": self.p1,
            "p2": self.p2,
            "effective_gap": self._effective_gap(),
        }

    def scaled(self, factor: float) -> "NonInferiorityProportions":
        margin = self.margin * factor
        if not (0.0 < margin < 1.0):
            raise PowerPlanError("민감도 분석: 마진이 유효 범위를 벗어났습니다")
        return NonInferiorityProportions(self.p1, self.p2, margin, self.alpha,
                                         self.ratio, self.lower_is_better)

    def notes(self) -> list[str]:
        direction = "낮을수록 좋은" if self.lower_is_better else "높을수록 좋은"
        return [
            f"결과지표가 **{direction}** 지표라고 가정했습니다 (--lower-is-better로 바꿈).",
            "정규근사 z 검정(**비합동** SE) 기준입니다. 실제 분석에 점수(score) 기반 "
            "방법(Farrington–Manning, Miettinen–Nurminen)이나 비조건부 정확검정(Chan)을 "
            "쓸 계획이면 표본수를 5~10% 더 잡으세요 — 이 표본수는 그 방법들의 식으로 "
            "계산한 것이 아닙니다.",
            "기대 사건수(n·p)가 군당 5 미만이면 정규근사가 깨집니다.",
            "α는 단측입니다(관례상 0.025).",
            "비열등성 마진은 통계가 아니라 **임상적 판단**이며, 활성대조약의 과거 효과 "
            "크기(putative placebo)를 근거로 정해 프로토콜에 적어야 합니다.",
            "비열등성 시험은 ITT/PP 두 집단에서 결론이 유지되어야 하므로 탈락 여유를 "
            "넉넉히 두세요(--dropout).",
        ]

    def references(self) -> list[str]:
        return [
            "Chow SC, Shao J, Wang H. Sample Size Calculations in Clinical Research. "
            "2nd ed. Chapman & Hall/CRC; 2008. (비합동 SE 기반 비열등성 표본수 — 이 툴이 쓴 식)",
            "Farrington CP, Manning G. Stat Med. 1990;9:1447-1454. "
            "(점수 기반 대안 — 이 툴은 쓰지 않음)",
            "ICH E9 Statistical Principles for Clinical Trials (1998).",
        ]


@dataclass(frozen=True)
class EquivalenceProportions(Design):
    """두 군 **비율**의 동등성 검정 (TOST, 위험차 마진, 정규근사)."""

    p1: float
    p2: float
    margin: float
    alpha: float = 0.05
    ratio: float = 1.0

    key = "equiv_prop"
    sensitivity_kr = "마진 가정 배율"
    name_kr = "동등성 검정 (TOST, 두 군 비율)"
    name_en = "Equivalence (TOST, two proportions)"
    test_kr = "비율차 TOST (두 개의 단측 z 검정)"
    test_en = "TOST for two proportions (risk difference)"
    unit_kr = "1군 n"
    min_unit = 2
    supports_sequential = False
    sequential_reason = ("TOST는 두 단측검정을 동시에 만족해야 하는 절차라 하나의 "
                         "Z 경계로 α를 나눌 수 없습니다")

    def __post_init__(self) -> None:
        object.__setattr__(self, "alpha", _check_alpha(self.alpha))
        object.__setattr__(self, "ratio", _check_ratio(self.ratio))
        object.__setattr__(self, "p1", _check_prop("--p1", self.p1))
        object.__setattr__(self, "p2", _check_prop("--p2", self.p2))
        if not (math.isfinite(self.margin) and 0.0 < self.margin < 1.0):
            raise PowerPlanError(
                f"--margin: 0과 1 사이의 비율차여야 합니다 (받은 값: {self.margin!r})"
            )
        if abs(self.p2 - self.p1) >= self.margin:
            raise PowerPlanError(
                f"가정한 실제 비율차(|{self.p2 - self.p1:g}|)가 동등성 마진"
                f"({self.margin:g}) 밖입니다. 어떤 표본수로도 동등성을 입증할 수 없습니다"
            )

    def _power_at_se(self, se: float) -> float:
        if se <= 0.0:
            return 0.0
        diff = self.p2 - self.p1
        zc = norm_ppf(1.0 - self.alpha)
        upper = norm_cdf((self.margin - diff) / se - zc)
        lower = norm_cdf((self.margin + diff) / se - zc)
        return min(1.0, max(0.0, upper + lower - 1.0))

    def power(self, unit: float) -> float:
        return self._power_at_se(_binary_se_unit(self.p1, self.p2, self.ratio)
                                 / math.sqrt(float(unit)))

    def allocation(self, unit: float) -> dict:
        n1 = max(self.min_unit, math.ceil(unit - 1e-9))
        n2 = max(self.min_unit, math.ceil(self.ratio * n1 - 1e-9))
        return {"n1": n1, "n2": n2, "total": n1 + n2}

    def power_of_allocation(self, alloc: dict) -> float:
        n1, n2 = float(alloc["n1"]), float(alloc["n2"])
        return self._power_at_se(
            math.sqrt(self.p1 * (1.0 - self.p1) / n1 + self.p2 * (1.0 - self.p2) / n2))

    def effect(self) -> dict:
        return {
            "name": "동등성 마진 (위험차)",
            "name_en": "equivalence margin (risk difference)",
            "value": self.margin,
            "label": f"±{self.margin:g}, 대조 {self.p1:g} vs 중재 {self.p2:g}",
            "margin_raw": self.margin,
            "assumed_diff": self.p2 - self.p1,
            "p1": self.p1,
            "p2": self.p2,
        }

    def scaled(self, factor: float) -> "EquivalenceProportions":
        margin = self.margin * factor
        if not (0.0 < margin < 1.0) or abs(self.p2 - self.p1) >= margin:
            raise PowerPlanError("민감도 분석: 마진이 가정 차이보다 작아져 계산할 수 없습니다")
        return EquivalenceProportions(self.p1, self.p2, margin, self.alpha, self.ratio)

    def notes(self) -> list[str]:
        return [
            "TOST 검정력을 두 단측검정의 동시 성립 확률로 계산했습니다 "
            "(Φ(·)+Φ(·)−1 형태이므로 두 마진 중 한쪽만 보는 근사보다 보수적입니다).",
            "정규근사 z 검정(비합동 SE)입니다. 정확법을 쓸 계획이면 5~10% 여유를 두세요.",
            "α는 각 단측검정에 적용되며 전체 1종오류도 α로 유지됩니다.",
            "동등성 마진은 임상적 판단으로 정하고 근거를 프로토콜에 남기세요.",
        ]

    def references(self) -> list[str]:
        return [
            "Schuirmann DJ. J Pharmacokinet Biopharm. 1987;15:657-680.",
            "Chow SC, Shao J, Wang H. Sample Size Calculations in Clinical Research. "
            "2nd ed. Chapman & Hall/CRC; 2008.",
        ]


# --------------------------------------------------------------------------
# 9) 대응 비율 (McNemar) — 같은 대상자에서 두 방법/두 시점의 이분형 결과 비교
# --------------------------------------------------------------------------
#: 정확 조건부 McNemar 검정력을 계산할 최대 쌍 수 (계산량이 O(n²))
MAX_EXACT_MCNEMAR_N = 1200


def mcnemar_exact_power(n: int, p01: float, p10: float, alpha: float = 0.05,
                        sides: int = 2) -> float | None:
    """**정확 조건부** McNemar 검정의 검정력 (불일치 쌍 수로 조건부 이항검정).

    실제 분석에서 어떤 검정을 쓰느냐로 검정력이 갈린다. 정확 조건부 이항검정은
    이산분포라 실제 유의수준이 α보다 **낮고**(모의실험 기준 0.05 → 0.032), 그만큼
    검정력도 낮다. 반대로 점근 McNemar χ² 검정은 실제 크기가 α를 살짝 넘고 Connor
    근사보다 검정력이 높다. 계획 단계에서 정확검정을 쓸 작정이라면 이 값을 보고
    표본수를 더 잡아야 한다.

    여기서는 D ~ Bin(n, π_d)에 대해 조건부 이항검정의 기각확률을 전부 더한다:

        power = Σ_D P(D) · P(기각 | D),   기각 | D 는 Bin(D, ψ/(1+ψ))의 꼬리

    n이 크면 O(n²)이라 None을 돌려준다 (그때는 근사가 이미 충분히 정확하다).
    """
    if n > MAX_EXACT_MCNEMAR_N or n < 1:
        return None
    pd = p01 + p10
    if not (0.0 < pd < 1.0):
        return None
    p_up = p10 / pd
    log_fact = [0.0] * (n + 1)
    for i in range(1, n + 1):
        log_fact[i] = log_fact[i - 1] + math.log(i)

    def log_choose(a: int, b: int) -> float:
        return log_fact[a] - log_fact[b] - log_fact[a - b]

    log_pd, log_1mpd = math.log(pd), math.log1p(-pd)
    log_p, log_1mp = math.log(p_up), math.log1p(-p_up)
    total = 0.0
    for disc in range(n + 1):
        log_w = log_choose(n, disc) + disc * log_pd + (n - disc) * log_1mpd
        if log_w < -745.0:
            continue
        weight = math.exp(log_w)
        if weight < 1e-15:
            continue
        # 귀무가설(p=0.5)에서의 기각역: 양측이면 양쪽 꼬리 합이 α 이하
        crit = -1
        acc = 0.0
        for b in range(disc + 1):
            acc += math.exp(log_choose(disc, b) - disc * math.log(2.0))
            if acc * sides > alpha + 1e-15:
                break
            crit = b
        if crit < 0:
            continue
        # 대립가설에서의 기각확률
        reject = 0.0
        if sides == 2:
            for b in range(crit + 1):
                reject += math.exp(log_choose(disc, b) + b * log_p
                                   + (disc - b) * log_1mp)
        for b in range(disc - crit, disc + 1):
            if b < 0:
                continue
            reject += math.exp(log_choose(disc, b) + b * log_p + (disc - b) * log_1mp)
        total += weight * min(1.0, reject)
    return min(1.0, total)


@dataclass(frozen=True)
class McNemarPaired(Design):
    """대응 이분형 자료의 McNemar 검정 (불일치 쌍 기준, Connor 1987)."""

    p01: float
    p10: float
    alpha: float = 0.05
    sides: int = 2

    key = "mcnemar"
    supports_cluster = False
    cluster_reason = "같은 사람의 두 판정을 비교하는 설계라 군집 무작위배정이 아닙니다"
    name_kr = "대응 비율 비교 (McNemar)"
    name_en = "Paired proportions (McNemar)"
    test_kr = "McNemar 검정 (대응 이분형)"
    test_en = "McNemar's test for paired proportions"
    unit_kr = "쌍 수 n"
    min_unit = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "alpha", _check_alpha(self.alpha))
        object.__setattr__(self, "sides", _check_sides(self.sides))
        object.__setattr__(self, "p01", _check_prop("--p01", self.p01))
        object.__setattr__(self, "p10", _check_prop("--p10", self.p10))
        if self.p01 + self.p10 >= 1.0:
            raise PowerPlanError(
                f"--p01 + --p10: 1보다 작아야 합니다 (받은 값: {self.p01 + self.p10:g}). "
                "두 값은 **전체 대상자 중** 불일치 쌍의 비율입니다"
            )
        if self.p01 == self.p10:
            raise PowerPlanError(
                "--p01과 --p10이 같으면(불일치 오즈비 1) 목표 검정력에 도달할 수 없습니다"
            )

    @property
    def discordant(self) -> float:
        """불일치 쌍의 비율 π_d = p01 + p10."""
        return self.p01 + self.p10

    @property
    def odds_ratio(self) -> float:
        """불일치 오즈비 ψ = p10 / p01."""
        return self.p10 / self.p01

    def power(self, unit: float) -> float:
        n = float(unit)
        psi = self.odds_ratio
        pd = self.discordant
        gap = (psi - 1.0) ** 2 * pd
        denom = (psi + 1.0) ** 2 - (psi - 1.0) ** 2 * pd
        if gap <= 0.0 or denom <= 0.0:
            return 0.0
        zc = norm_ppf(1.0 - self.alpha / self.sides)
        z_beta = (math.sqrt(n * gap) - zc * (psi + 1.0)) / math.sqrt(denom)
        return norm_cdf(z_beta)

    def allocation(self, unit: float) -> dict:
        n = max(self.min_unit, math.ceil(unit - 1e-9))
        return {"n": n, "total": n}

    def power_of_allocation(self, alloc: dict) -> float:
        return self.power(alloc["n"])

    def effect(self) -> dict:
        return {
            "name": "불일치 오즈비 (p10/p01)",
            "name_en": "discordant odds ratio",
            "value": self.odds_ratio,
            "label": f"p01={self.p01:g}, p10={self.p10:g} (불일치 {self.discordant:.1%})",
            "discordant": self.discordant,
            "p01": self.p01,
            "p10": self.p10,
        }

    def scaled(self, factor: float) -> "McNemarPaired":
        # 불일치 비율 π_d는 고정하고 오즈비를 ψ^factor로 (로그 오즈비를 factor배)
        psi = self.odds_ratio ** factor
        if abs(psi - 1.0) < 1e-9:
            raise PowerPlanError("민감도 분석: 불일치 오즈비가 1이 되어 계산할 수 없습니다")
        pd = self.discordant
        p01 = pd / (1.0 + psi)
        return McNemarPaired(p01, pd - p01, self.alpha, self.sides)

    def plan_lines(self, alloc: dict) -> list[tuple[str, str]]:
        n = alloc.get("n", alloc.get("total", 0))
        lines = [("예상 불일치 쌍 수",
                  f"{n * self.discordant:.1f}쌍 (전체의 {self.discordant:.1%}) "
                  "— 검정력은 여기서 나옵니다")]
        exact = mcnemar_exact_power(int(n), self.p01, self.p10, self.alpha, self.sides)
        if exact is not None:
            lines.append((
                "정확검정 검정력",
                f"{exact:.1%} — 정확 조건부 이항검정(McNemar exact)을 쓸 계획이면 "
                "이 값이 실제 검정력입니다. 이산분포라 실제 유의수준이 α보다 낮아 "
                "그만큼 보수적이므로, 정확검정으로 분석할 거라면 표본수를 더 잡으세요"))
        return lines

    def notes(self) -> list[str]:
        return [
            "p01·p10은 **전체 대상자 중** 두 방법의 판정이 엇갈린 쌍의 비율입니다 "
            "(불일치 쌍 중의 비율이 아닙니다). 합이 클수록 필요한 표본수가 줄어듭니다.",
            f"불일치 쌍이 전체의 {self.discordant:.1%}뿐이라면 실제 검정에 기여하는 인원은 "
            f"n의 {self.discordant:.1%}입니다 — 일치도가 높은 두 방법을 비교할수록 "
            "표본수가 급격히 커집니다.",
            "표본수는 Connor(1987)의 **정규근사** 기준입니다. 어떤 검정으로 분석하느냐로 "
            "실제 검정력이 갈립니다 — 점근 McNemar χ² 검정은 이 근사보다 검정력이 조금 "
            "높고(실제 크기도 α를 조금 넘습니다), 정확 조건부 이항검정은 보수적이라 "
            "검정력이 낮습니다. 위의 '정확검정 검정력'을 보고 어느 쪽으로 분석할지 "
            "먼저 정하세요.",
            "기대 불일치 쌍 수가 20 미만이면 정규근사를 신뢰하지 말고 정확검정 기준으로 "
            "계획하세요.",
            "일치·불일치 판정이 아니라 '두 방법의 일치도 자체'가 관심이면 kappa(정밀도 기준) "
            "또는 icc/loa를 쓰세요.",
        ]

    def references(self) -> list[str]:
        return [
            "Connor RJ. Sample size for testing differences in proportions for the "
            "paired-sample design. Biometrics. 1987;43:207-211.",
        ]


# --------------------------------------------------------------------------
# 10) 반복측정 (사전 b회 · 사후 p회) — Frison & Pocock 설계배율
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class RepeatedMeasuresT(Design):
    """두 군 비교인데 **측정을 여러 번** 하는 설계 (MMRM / 반복측정 ANCOVA).

    불면증·재활 시험은 ISI를 4주·8주·12주에 반복 측정한다. 측정을 여러 번 하면
    평균의 분산이 줄어 표본수가 감소하는데, 그 감소분은 측정 간 상관 ρ에 달려 있다.
    Frison & Pocock(1992)의 복합대칭 가정 아래 분산 배율은 다음과 같다
    (σ² = 1회 측정의 분산, p = 사후 측정 횟수, b = 사전 측정 횟수):

    - 사후 평균만 비교      : (1 + (p−1)ρ) / p
    - 변화량(사후−사전) 비교 : (1 + (p−1)ρ)/p + (1 + (b−1)ρ)/b − 2ρ
    - ANCOVA (사전 평균 보정): (1 + (p−1)ρ)/p − ρ²·b / (1 + (b−1)ρ)
    """

    d: float
    post: int = 1
    baseline: int = 1
    rho: float = 0.0
    analysis: str = "ancova"
    alpha: float = 0.05
    sides: int = 2
    ratio: float = 1.0
    #: last = 마지막 방문 시점의 군간 차이 (프로토콜 1차 평가변수의 표준)
    #: average = 사후 방문들의 **평균** 차이 (Frison–Pocock 요약통계 접근)
    estimand: str = "last"

    key = "repeated"
    name_kr = "반복측정 두 군 비교 (MMRM/ANCOVA)"
    name_en = "Repeated measures, two groups"
    unit_kr = "1군 n"
    min_unit = 2

    _TEST_NAMES = {
        ("post", "last"): ("마지막 방문 시점의 독립표본 t 검정",
                           "two-sample t-test at the final visit"),
        ("change", "last"): ("마지막 방문의 변화량(사후 − 사전 평균) 독립표본 t 검정",
                             "two-sample t-test on change from baseline at the final visit"),
        ("ancova", "last"): ("마지막 방문의 반복측정 ANCOVA/MMRM (사전 평균 보정)",
                             "repeated-measures ANCOVA (MMRM) at the final visit, "
                             "adjusted for baseline"),
        ("post", "average"): ("사후 방문 평균의 독립표본 t 검정",
                              "two-sample t-test on the mean of the post-baseline visits"),
        ("change", "average"): ("사후 평균의 변화량(사후 평균 − 사전 평균) 독립표본 t 검정",
                                "two-sample t-test on change from baseline in the "
                                "mean of the post-baseline visits"),
        ("ancova", "average"): ("사후 평균의 반복측정 ANCOVA (사전 평균 보정)",
                                "repeated-measures ANCOVA on the mean of the "
                                "post-baseline visits, adjusted for baseline"),
    }

    def __post_init__(self) -> None:
        object.__setattr__(self, "alpha", _check_alpha(self.alpha))
        object.__setattr__(self, "sides", _check_sides(self.sides))
        object.__setattr__(self, "ratio", _check_ratio(self.ratio))
        if not math.isfinite(self.d) or self.d == 0.0:
            raise PowerPlanError("--d(효과크기)는 0이 아닌 유한한 값이어야 합니다")
        if self.analysis not in ("post", "change", "ancova"):
            raise PowerPlanError(
                f"--analysis: post, change, ancova 중 하나여야 합니다 "
                f"(받은 값: {self.analysis!r})"
            )
        if self.estimand not in ("last", "average"):
            raise PowerPlanError(
                f"--estimand: last(마지막 방문) 또는 average(사후 평균)여야 합니다 "
                f"(받은 값: {self.estimand!r})"
            )
        for name, value in (("--post", self.post), ("--baseline-n", self.baseline)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise PowerPlanError(f"{name}: 0 이상의 정수여야 합니다 (받은 값: {value!r})")
            if value > 1000:
                raise PowerPlanError(f"{name}: 1000회를 넘는 측정은 지원하지 않습니다")
        if self.post < 1:
            raise PowerPlanError("--post: 사후 측정은 1회 이상이어야 합니다")
        if self.analysis != "post" and self.baseline < 1:
            raise PowerPlanError(
                f"--analysis {self.analysis}에는 사전 측정이 1회 이상 필요합니다 "
                "(--baseline-n 1)"
            )
        if not (math.isfinite(self.rho) and 0.0 <= self.rho < 1.0):
            raise PowerPlanError(
                f"--rho: 0 이상 1 미만의 측정 간 상관이어야 합니다 (받은 값: {self.rho!r})"
            )
        if self.design_factor <= 1e-12:
            raise PowerPlanError(
                "측정 간 상관 ρ가 1에 너무 가까워 분산 배율이 0이 됩니다 — ρ를 낮추세요"
            )

    @property
    def test_kr(self) -> str:
        return self._TEST_NAMES[(self.analysis, self.estimand)][0]

    @property
    def test_en(self) -> str:
        return self._TEST_NAMES[(self.analysis, self.estimand)][1]

    @property
    def design_factor(self) -> float:
        """1회 측정 대비 **분산 배율** (작을수록 표본수가 준다).

        `estimand="last"`면 관심 추정량은 **마지막 방문 한 시점**의 군간 차이이므로
        사후 측정을 몇 번 하든 그 시점의 분산은 σ² 그대로다(복합대칭·완전자료 가정).
        `estimand="average"`면 사후 p회의 평균이라 분산이 (1+(p−1)ρ)/p로 줄어든다.
        """
        p, b, r = self.post, self.baseline, self.rho
        post_var = 1.0 if self.estimand == "last" else (1.0 + (p - 1) * r) / p
        if self.analysis == "post":
            return post_var
        base_var = (1.0 + (b - 1) * r) / b
        if self.analysis == "change":
            return post_var + base_var - 2.0 * r
        return post_var - r * r / base_var

    @property
    def effective_d(self) -> float:
        return abs(self.d) / math.sqrt(self.design_factor)

    def _power(self, n1: float, n2: float) -> float:
        ancova = self.analysis == "ancova"
        df = n1 + n2 - 2.0 - (1.0 if ancova else 0.0)
        if df < 1.0:
            return 0.0
        ncp = self.effective_d / math.sqrt(
            (1.0 / n1 + 1.0 / n2) * ancova_inflation(n1 + n2, ancova))
        if self.sides == 1:
            return nct_sf(t_ppf(1.0 - self.alpha, df), df, ncp)
        tc = t_ppf(1.0 - self.alpha / 2.0, df)
        return nct_sf(tc, df, ncp) + nct_cdf(-tc, df, ncp)

    def power(self, unit: float) -> float:
        return self._power(float(unit), self.ratio * float(unit))

    def allocation(self, unit: float) -> dict:
        n1 = max(self.min_unit, math.ceil(unit - 1e-9))
        n2 = max(self.min_unit, math.ceil(self.ratio * n1 - 1e-9))
        return {"n1": n1, "n2": n2, "total": n1 + n2}

    def power_of_allocation(self, alloc: dict) -> float:
        return self._power(float(alloc["n1"]), float(alloc["n2"]))

    def effect(self) -> dict:
        return {
            "name": "Cohen's d (1회 측정 기준)",
            "name_en": "Cohen's d (single measurement)",
            "value": self.d,
            "label": (f"{label_d(self.d)} · "
                      f"{'마지막 방문' if self.estimand == 'last' else '사후 평균'} 기준, "
                      f"사후 {self.post}회/사전 {self.baseline}회, "
                      f"ρ={self.rho:g} · 설계배율 {self.design_factor:.3f} "
                      f"→ 실질 d {self.effective_d:.3f}"),
            "analysis": self.analysis,
            "estimand": self.estimand,
            "post_measurements": self.post,
            "baseline_measurements": self.baseline,
            "rho": self.rho,
            "design_factor": self.design_factor,
            "effective_d": self.effective_d,
        }

    def scaled(self, factor: float) -> "RepeatedMeasuresT":
        return RepeatedMeasuresT(self.d * factor, self.post, self.baseline, self.rho,
                                 self.analysis, self.alpha, self.sides, self.ratio,
                                 self.estimand)

    def plan_lines(self, alloc: dict) -> list[tuple[str, str]]:
        per_person = self.post + (self.baseline if self.analysis != "post" else 0)
        total = alloc.get("total", 0) * per_person
        lines = [("총 측정 횟수",
                  f"{total:,}회 (1인당 {per_person}회 × {alloc.get('total', 0):,}명)")]
        lines.append((
            "1차 평가 시점",
            "마지막 방문 1회" if self.estimand == "last"
            else f"사후 {self.post}회 방문의 평균"))
        return lines

    def notes(self) -> list[str]:
        p, b, r = self.post, self.baseline, self.rho
        if self.estimand == "last":
            out = [
                "**1차 평가변수는 마지막 방문 시점의 군간 차이**입니다 "
                f"(--estimand last, 기본값). 중간 방문 {max(p - 1, 0)}회는 이 계산에 "
                "표본수를 줄여 주지 않습니다 — 복합대칭·완전자료에서는 마지막 시점의 "
                "분산이 그대로 σ²이기 때문입니다. 중간 방문의 값어치는 **탈락자의 "
                "정보를 MMRM이 회수해 주는 것**이며, 그 이득은 이 식에 들어 있지 "
                "않습니다(즉 이 표본수는 그만큼 보수적입니다).",
                "사후 방문들의 **평균**을 1차 평가변수로 삼는 프로토콜이라면 "
                "--estimand average 를 쓰세요 — 표본수가 크게 줄지만, SAP의 1차 "
                "분석이 정말 '평균'이어야 합니다(흔한 과소설계 원인).",
            ]
        else:
            out = [
                f"**1차 평가변수는 사후 {p}회 방문의 평균**입니다 "
                "(--estimand average · Frison–Pocock 요약통계 접근). SAP의 1차 분석이 "
                "'마지막 방문'이라면 이 표본수는 **과소**합니다 — --estimand last 로 "
                "다시 계산하세요.",
                f"측정 {p}회를 평균내면 그 평균의 분산이 (1+({p}−1)ρ)/{p} = "
                f"{(1.0 + (p - 1) * r) / p:.3f}배가 됩니다.",
            ]
        out += [
            "d는 **1회 측정의 SD** 기준입니다 (여러 번 측정한 평균의 SD가 아닙니다).",
            f"복합대칭(모든 측정 쌍의 상관이 ρ={r:g})을 가정합니다. 실제로는 시간 간격이 "
            "멀수록 상관이 낮아지므로(AR(1)), 이 계산은 약간 낙관적일 수 있습니다 — "
            "가장 먼 두 시점의 상관을 ρ로 넣으면 보수적입니다.",
            f"분산 배율 {self.design_factor:.4f} → 필요한 표본수가 1회 측정·보정 없음 "
            f"설계의 약 {self.design_factor:.1%}입니다.",
            "검정력은 비중심 t 분포로 정확히 계산했습니다.",
        ]
        if self.analysis == "ancova":
            out.append(
                f"사전 측정 {b}회의 평균을 공변량으로 넣는 ANCOVA입니다. 공변량 불균형에 "
                "따른 분산 팽창 1 + 1/(N−3)까지 반영했습니다(빼먹으면 검정력이 "
                "0.5~1.5%p 과대평가됩니다). SAP에 공변량과 결측 처리(MAR 가정)를 "
                "명시하세요.")
        elif self.analysis == "change":
            out.append("변화량 분석은 같은 ρ에서 ANCOVA보다 항상 비효율적입니다 "
                       "(--analysis ancova와 비교해 보세요).")
        else:
            out.append("사전 측정이 있다면 --analysis ancova가 거의 항상 더 효율적입니다.")
        out.append(
            "결측(중도탈락)이 있으면 MMRM은 관측된 시점을 모두 쓰므로 완전자료 분석보다 "
            "유리하지만, 이 계산은 완전자료를 가정합니다 — --dropout으로 보정하세요.")
        return out

    def references(self) -> list[str]:
        return [
            "Frison L, Pocock SJ. Repeated measures in clinical trials: analysis using "
            "mean summary statistics and its implications for design. "
            "Stat Med. 1992;11:1685-1704.",
            "Mallinckrodt CH, et al. Recommendations for the primary analysis of "
            "continuous endpoints in longitudinal clinical trials. Drug Inf J. 2008;42:303-319.",
        ]


# --------------------------------------------------------------------------
# 11) 생존분석 (로그순위 검정, Schoenfeld)
# --------------------------------------------------------------------------
def exponential_event_prob(median: float, accrual: float, followup: float) -> float:
    """지수 생존모형에서 연구 종료까지 **사건이 관측될 확률**.

    등록이 [0, A] 구간에 균등하게 이루어지고 마지막 등록자도 F만큼 추적한다고 보면
    각 대상자의 추적기간은 F ~ A+F에 균등하다. 따라서

        P(event) = 1 − (1/A)∫₀^A exp(−λ(A+F−a)) da
                 = 1 − (e^{−λF} − e^{−λ(A+F)}) / (λA)     (A > 0)
                 = 1 − e^{−λF}                             (A = 0, 동시 등록)
    """
    if median <= 0.0:
        raise PowerPlanError(f"생존기간 중앙값은 0보다 커야 합니다 (받은 값: {median!r})")
    if accrual < 0.0 or followup < 0.0:
        raise PowerPlanError("등록기간·추적기간은 0 이상이어야 합니다")
    lam = math.log(2.0) / median
    if accrual <= 0.0:
        return 1.0 - math.exp(-lam * followup)
    return 1.0 - (math.exp(-lam * followup)
                  - math.exp(-lam * (accrual + followup))) / (lam * accrual)


@dataclass(frozen=True)
class LogRankSurvival(Design):
    """두 군 생존곡선 비교 (로그순위 검정 / Cox 비례위험, Schoenfeld 1983).

    검정력은 사건 수 E와 위험비 HR만으로 결정된다:

        Var(log HR) ≈ 1 / (E·π₁·π₂),  π는 배분비율

    표본수는 거기서 "그 사건 수를 얻으려면 몇 명을 등록해야 하는가"로 역산한다.
    """

    hr: float
    alpha: float = 0.05
    sides: int = 2
    ratio: float = 1.0
    #: 지수 생존모형용 — 대조군 생존기간 중앙값, 등록기간, 추가 추적기간 (같은 시간 단위)
    median1: float | None = None
    accrual: float = 0.0
    followup: float = 0.0
    #: 위 모형 대신 "대조군에서 연구 종료까지 사건을 겪을 비율"을 직접 줄 때
    event_rate: float | None = None
    time_unit: str = "개월"
    #: schoenfeld = 표준(가장 널리 쓰임) · freedman = 더 보수적인 고전 공식
    method: str = "schoenfeld"

    key = "survival"
    name_kr = "생존분석 두 군 비교 (로그순위)"
    name_en = "Two-group survival (log-rank)"
    test_kr = "로그순위 검정 (Cox 비례위험모형)"
    test_en = "log-rank test (Cox proportional hazards)"
    unit_kr = "1군 n"
    min_unit = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "alpha", _check_alpha(self.alpha))
        object.__setattr__(self, "sides", _check_sides(self.sides))
        object.__setattr__(self, "ratio", _check_ratio(self.ratio))
        if not (math.isfinite(self.hr) and self.hr > 0.0):
            raise PowerPlanError(f"--hr: 0보다 큰 유한한 값이어야 합니다 (받은 값: {self.hr!r})")
        if self.hr == 1.0:
            raise PowerPlanError(
                "--hr: 1이면 두 군의 위험이 같아 어떤 표본수로도 검출할 수 없습니다"
            )
        if self.hr > 1e6 or self.hr < 1e-6:
            raise PowerPlanError(f"--hr: 현실적인 위험비 범위를 벗어났습니다 (받은 값: {self.hr:g})")
        if (self.median1 is None) == (self.event_rate is None):
            raise PowerPlanError(
                "생존분석에는 (a) --median1(대조군 중앙생존)과 --accrual/--followup, 또는 "
                "(b) --event-rate(연구 종료까지 사건을 겪을 비율) 중 **하나**를 지정하세요"
            )
        if self.event_rate is not None:
            rate = float(self.event_rate)
            if not (math.isfinite(rate) and 0.0 < rate <= 1.0):
                raise PowerPlanError(
                    f"--event-rate: 0보다 크고 1 이하여야 합니다 (받은 값: {self.event_rate!r})"
                )
            object.__setattr__(self, "event_rate", rate)
        else:
            if not (math.isfinite(self.median1) and self.median1 > 0.0):
                raise PowerPlanError(
                    f"--median1: 0보다 커야 합니다 (받은 값: {self.median1!r})")
            for name, value in (("--accrual", self.accrual), ("--followup", self.followup)):
                if not (math.isfinite(value) and value >= 0.0):
                    raise PowerPlanError(f"{name}: 0 이상의 유한한 값이어야 합니다 "
                                         f"(받은 값: {value!r})")
            if self.accrual <= 0.0 and self.followup <= 0.0:
                raise PowerPlanError(
                    "--accrual(등록기간)과 --followup(추가 추적기간)이 둘 다 0이면 "
                    "아무도 추적되지 않습니다"
                )
        # --time-unit은 보고서 본문에 그대로 들어가는 유일한 자유 문자열이다.
        # 터미널 이스케이프·양방향 오버라이드가 섞이면 출력이 조작된다.
        object.__setattr__(self, "time_unit",
                           _clean_label(self.time_unit, 20) or "개월")
        if self.method not in ("schoenfeld", "freedman"):
            raise PowerPlanError(
                f"--method: schoenfeld 또는 freedman이어야 합니다 (받은 값: {self.method!r})")
        if self.method == "freedman" and abs(self.ratio - 1.0) > 1e-12:
            # Freedman 공식의 √k(1−ψ)/(1+kψ)는 k = 1/HR에서 최대가 되어, 불균등
            # 배분에서 사건 수가 **줄어드는** 비현실적인 결과를 준다(모의실험 기준
            # 실제 검정력이 4~5%p 낮음). 균등배분에서만 쓰도록 막는다.
            raise PowerPlanError(
                "--method freedman은 1:1 배분에서만 지원합니다 "
                f"(받은 --ratio {self.ratio:g}). 불균등 배분에서는 Freedman 공식이 "
                "사건 수를 과소평가합니다 — --method schoenfeld를 쓰세요")
        if self.prob_event_pooled <= 0.0:
            raise PowerPlanError("사건 발생 확률이 0입니다 — 추적기간이나 중앙생존을 확인하세요")

    # --- 사건 확률 ---
    @property
    def prob1(self) -> float:
        """대조군에서 연구 종료까지 사건이 관측될 확률."""
        if self.event_rate is not None:
            return self.event_rate
        return exponential_event_prob(self.median1, self.accrual, self.followup)

    @property
    def prob2(self) -> float:
        """중재군에서 연구 종료까지 사건이 관측될 확률 (HR로 위험률을 조정).

        --event-rate만 준 경우에도 두 군을 같게 두지 않는다. 비례위험 아래에서는
        S₂(t) = S₁(t)^HR 이므로, **모든 대상자의 추적기간이 같다면** 어떤 시간분포에서도
        p₂ = 1 − (1 − p₁)^HR 이 정확히 성립한다. 등록이 길게 퍼져 추적기간이 사람마다
        다르면 Jensen 부등식 때문에 오차가 생기며(등록 36개월·추적 0인 극단에서 약 9%,
        HR<1이면 보수적·HR>1이면 낙관적), 그 경우에는 --median1/--accrual/--followup을
        써야 정확하다. 예전처럼 p₂ = p₁으로 두면 HR이 1에서 멀 때 사건 수를 크게
        과대평가해 표본수가 낙관적으로 나온다.
        """
        if self.event_rate is not None:
            return 1.0 - (1.0 - self.event_rate) ** self.hr
        return exponential_event_prob(self.median1 / self.hr, self.accrual, self.followup)

    @property
    def prob_event_pooled(self) -> float:
        """전체 대상자 중 사건을 겪는 비율."""
        return (self.prob1 + self.ratio * self.prob2) / (1.0 + self.ratio)

    @property
    def median2(self) -> float | None:
        return None if self.median1 is None else self.median1 / self.hr

    def events_for(self, n1: float) -> float:
        """1군 n1명일 때 기대되는 총 사건 수."""
        return n1 * self.prob1 + n1 * self.ratio * self.prob2

    def _drift_per_root_event(self) -> float:
        """√E 당 이동모수 — Schoenfeld와 Freedman의 유일한 차이가 이 계수다."""
        k = self.ratio
        if self.method == "freedman":
            # Freedman(1982): E = (z+z)²(1+ψk)² / (k(1−ψ)²)
            return abs(1.0 - self.hr) * math.sqrt(k) / (1.0 + self.hr * k)
        # Schoenfeld(1983): E = (z+z)² / (π₁π₂ ln²HR)
        pi1 = 1.0 / (1.0 + k)
        return abs(math.log(self.hr)) * math.sqrt(pi1 * (1.0 - pi1))

    def _power_from_events(self, events: float) -> float:
        if events <= 0.0:
            return 0.0
        z = self._drift_per_root_event() * math.sqrt(events)
        zc = norm_ppf(1.0 - self.alpha / self.sides)
        upper = norm_cdf(z - zc)
        lower = norm_cdf(-z - zc) if self.sides == 2 else 0.0
        return min(1.0, upper + lower)

    def power(self, unit: float) -> float:
        return self._power_from_events(self.events_for(float(unit)))

    def allocation(self, unit: float) -> dict:
        n1 = max(self.min_unit, math.ceil(unit - 1e-9))
        n2 = max(self.min_unit, math.ceil(self.ratio * n1 - 1e-9))
        return {"n1": n1, "n2": n2, "total": n1 + n2}

    def power_of_allocation(self, alloc: dict) -> float:
        events = alloc["n1"] * self.prob1 + alloc["n2"] * self.prob2
        return self._power_from_events(events)

    def effect(self) -> dict:
        out = {
            "name": "위험비 HR",
            "name_en": "hazard ratio",
            "value": self.hr,
            "label": f"log HR = {math.log(self.hr):.4f}",
            "log_hr": math.log(self.hr),
            "prob_event_control": self.prob1,
            "prob_event_treatment": self.prob2,
            "prob_event_pooled": self.prob_event_pooled,
        }
        if self.median1 is not None:
            out["median_control"] = self.median1
            out["median_treatment"] = self.median2
            out["accrual"] = self.accrual
            out["followup"] = self.followup
            out["label"] += (f" · 중앙생존 {self.median1:g} → "
                             f"{self.median2:.4g}{self.time_unit}")
        return out

    def scaled(self, factor: float) -> "LogRankSurvival":
        # log HR을 factor배 (HR 0.7의 '20% 약한 효과'는 exp(0.8·ln0.7) = 0.75)
        hr = math.exp(math.log(self.hr) * factor)
        if abs(hr - 1.0) < 1e-9:
            raise PowerPlanError("민감도 분석: 위험비가 1이 되어 계산할 수 없습니다")
        return LogRankSurvival(hr, self.alpha, self.sides, self.ratio, self.median1,
                               self.accrual, self.followup, self.event_rate,
                               self.time_unit, self.method)

    def information(self, alloc: dict) -> dict:
        """생존분석의 정보량은 **사건 수**다 (등록 인원이 아니다)."""
        events = alloc["n1"] * self.prob1 + alloc["n2"] * self.prob2
        return {
            "label": "누적 사건 수",
            "unit": "건",
            "total": events,
            "caveat": "생존분석의 정보량은 **사건 수**입니다 — 정보비율 50% 시점은 "
                      "'등록 인원의 절반'이 아니라 '목표 사건 수의 절반이 모인 때'이며, "
                      "그때는 이미 대부분의 대상자가 등록을 마친 뒤입니다. 조기중단이 "
                      "아껴 주는 것도 인원이 아니라 **추적기간**입니다. 중간분석 시점은 "
                      "달력 날짜가 아니라 사건 수로 프로토콜에 적으세요.",
        }

    def required_events(self, target_power: float) -> float:
        """목표 검정력에 필요한 총 사건 수 (연속값)."""
        z = norm_ppf(1.0 - self.alpha / self.sides) + norm_ppf(target_power)
        return (z / self._drift_per_root_event()) ** 2

    def plan_lines(self, alloc: dict) -> list[tuple[str, str]]:
        events = alloc["n1"] * self.prob1 + alloc["n2"] * self.prob2
        lines = [("기대 사건 수",
                  f"{events:.1f}건 (대조 {alloc['n1'] * self.prob1:.1f} + 중재 "
                  f"{alloc['n2'] * self.prob2:.1f}) ← 검정력은 사건 수가 결정합니다")]
        if self.median1 is not None:
            lines.append(
                ("사건 발생 확률",
                 f"대조 {self.prob1:.1%} · 중재 {self.prob2:.1%} "
                 f"(등록 {self.accrual:g} + 추적 {self.followup:g}{self.time_unit}, "
                 "지수 생존모형)"))
        return lines

    def notes(self) -> list[str]:
        out = [
            f"표본수 공식: **{'Freedman(1982)' if self.method == 'freedman' else 'Schoenfeld(1983)'}**"
            " (--method로 바꿀 수 있습니다). **1:1 배분에서는** 같은 가정에서 Freedman이 "
            "조금 더 보수적입니다(HR 0.7·검정력 0.8에서 252건 vs 247건) — 심사자가 다른 "
            "값을 제시하면 어느 공식을 썼는지 먼저 확인하세요. 불균등 배분에서는 "
            "Freedman 공식이 성립하지 않아 이 툴이 막습니다.",
            "로그순위 검정의 검정력은 **사건 수**가 결정합니다 — 등록 인원이 아무리 많아도 "
            "사건이 모이지 않으면 검정력이 오르지 않습니다. 추적기간(--followup)을 늘리는 "
            "쪽이 등록을 늘리는 쪽보다 효율적인 경우가 많습니다.",
            "**비례위험(proportional hazards)** 을 가정합니다. 생존곡선이 교차하거나 효과가 "
            "늦게 나타나면(면역치료 등) 이 계산은 맞지 않습니다 — RMST 기반 설계를 보세요.",
            "Schoenfeld(1983)의 정규근사입니다(사건 수가 많을수록 정확). 기대 사건 수가 "
            "50건 미만이면 여유를 두세요.",
        ]
        if abs(math.log(self.hr)) > 0.7:      # HR < 0.5 또는 > 2
            out.append(
                f"⚠ 위험비가 1에서 많이 떨어져 있습니다(HR = {self.hr:g}). 이 영역에서는 "
                "Schoenfeld 근사가 검정력을 최대 3~4%p 과대평가한다는 것이 모의실험으로 "
                "확인됩니다(두 군의 사건확률이 크게 달라지기 때문). 10% 정도 여유를 두거나 "
                "--method freedman(1:1 배분)으로 함께 확인하세요.")
        if self.event_rate is not None:
            out.append(
                f"--event-rate {self.event_rate:g}는 **대조군**의 사건 비율로 읽었고, "
                f"중재군은 비례위험 관계 1 − (1 − p)^HR = {self.prob2:.1%}로 계산했습니다. "
                "이 관계는 **모든 대상자의 추적기간이 같을 때** 정확합니다 — 등록이 길게 "
                "퍼지면 최대 몇 %의 오차가 생기고, 중간분석 시점이나 연구 기간도 알 수 "
                "없습니다. 그것까지 필요하면 --median1/--accrual/--followup을 쓰세요.")
        else:
            out.append(
                "지수 생존(위험률 일정)과 [0, 등록기간] 균등 등록을 가정했습니다. "
                "실제 등록이 느려지면 사건 수가 모자라니 중간에 점검하세요.")
        out.append(
            "--dropout은 '추적 실패(중도절단)'를 단순히 인원 비율로 보정합니다. "
            "중도절단이 많으면(>20%) 경쟁위험을 반영한 계산이 더 정확합니다.")
        return out

    def references(self) -> list[str]:
        primary = (
            "Schoenfeld DA. Sample-size formula for the proportional-hazards "
            "regression model. Biometrics. 1983;39:499-503."
            if self.method == "schoenfeld" else
            "Freedman LS. Tables of the number of patients required in clinical trials "
            "using the logrank test. Stat Med. 1982;1:121-129.")
        other = (
            "Freedman LS. Stat Med. 1982;1:121-129. (--method freedman의 근거)"
            if self.method == "schoenfeld" else
            "Schoenfeld DA. Biometrics. 1983;39:499-503. (--method schoenfeld의 근거)")
        return [primary, other,
                "Lachin JM, Foulkes MA. Evaluation of sample size and power for "
                "analyses of survival with allowance for nonuniform patient entry, "
                "losses to follow-up, noncompliance, and stratification. "
                "Biometrics. 1986;42:507-519. (등록·탈락 모형)"]


# --------------------------------------------------------------------------
# 12) 단일군 비율 vs 성능목표치 (정확 이항검정)
# --------------------------------------------------------------------------
def binomial_sf(k: int, n: int, p: float) -> float:
    """P(X ≥ k), X ~ Bin(n, p) — 로그 공간 누적으로 안정적으로 계산."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    log_p, log_q = math.log(p), math.log1p(-p)
    # 꼬리가 짧은 쪽부터 더한다
    if k > n * p:
        lo, hi, complement = k, n, False
    else:
        lo, hi, complement = 0, k - 1, True
    total = 0.0
    log_c = math.lgamma(n + 1.0) - math.lgamma(lo + 1.0) - math.lgamma(n - lo + 1.0)
    for i in range(lo, hi + 1):
        if i > lo:
            log_c += math.log(n - i + 1.0) - math.log(i)
        term = log_c + i * log_p + (n - i) * log_q
        if term > -745.0:
            total += math.exp(term)
    return min(1.0, max(0.0, 1.0 - total if complement else total))


@dataclass(frozen=True)
class OneSampleProportion(Design):
    """단일군 반응률 vs **성능목표치**(performance goal / OPC) — 정확 이항검정.

    의료기기 확증시험에서 가장 흔한 설계다: "반응률이 성능목표치 p₀보다 높다"를
    한 팔로 보인다. 대조군이 없으므로 검정력은 전적으로 p₁과 p₀의 거리에서 나온다.
    정규근사가 아니라 **정확 이항검정**으로 계산하므로, 기각 임계값(몇 명 이상이면
    성공인가)을 그대로 프로토콜에 적을 수 있다.
    """

    p1: float
    p0: float
    alpha: float = 0.025
    sides: int = 1

    key = "prop1"
    supports_cluster = False
    cluster_reason = "단일군 설계라 군집을 군에 배정하는 구조가 아닙니다"
    name_kr = "단일군 반응률 vs 성능목표치"
    name_en = "One-sample proportion vs performance goal"
    test_kr = "정확 이항검정 (단일군, 성능목표치 대비)"
    test_en = "exact binomial test against a performance goal"
    unit_kr = "n"
    min_unit = 2
    #: 정확검정은 임계값이 정수라 검정력이 톱니 모양이다
    monotone = False
    #: 이항 꼬리를 직접 더하므로 표본수가 크면 느리다. 이 범위를 넘으면 정확검정을
    #: 쓸 이유도 없다(정규근사가 이미 충분히 정확하다).
    max_unit = 200_000
    supports_sequential = False
    sequential_reason = ("정확 이항검정은 이산분포라 α 소비함수로 경계를 나눌 수 "
                         "없습니다. 단일군 이분형 결과의 중간분석은 Simon 2단계 "
                         "설계가 표준이며 이 툴은 아직 제공하지 않습니다")

    def __post_init__(self) -> None:
        object.__setattr__(self, "alpha", _check_alpha(self.alpha))
        object.__setattr__(self, "sides", _check_sides(self.sides))
        object.__setattr__(self, "p1", _check_prop("--p1", self.p1))
        object.__setattr__(self, "p0", _check_prop("--p0", self.p0))
        if self.p1 == self.p0:
            raise PowerPlanError(
                "--p1(예상 반응률)과 --p0(성능목표치)가 같으면 목표 검정력에 "
                "도달할 수 없습니다")

    @property
    def one_sided_alpha(self) -> float:
        return self.alpha / self.sides

    def critical_value(self, n: int) -> int | None:
        """기각 임계값 — 반응자가 이 수 이상이면 성능목표치를 넘었다고 본다.

        (p₁ < p₀ 인 '낮을수록 좋은' 지표에서는 '이 수 이하'로 읽는다.)

        꼬리확률이 k에 대해 단조이므로 **이분법**으로 찾는다. 예전에는 0부터
        훑어 O(n²)이 되어 n = 1만이면 24초, 1000만이면 사실상 멈췄다.
        """
        n = int(n)
        if n < 1:
            return None
        alpha = self.one_sided_alpha
        if self.p1 > self.p0:
            # P(X ≥ k | p₀)는 k에 대해 감소 → 조건을 만족하는 가장 작은 k
            lo, hi = 0, n + 1
            if binomial_sf(n, n, self.p0) > alpha:
                return None
            while lo < hi:
                mid = (lo + hi) // 2
                if binomial_sf(mid, n, self.p0) <= alpha:
                    hi = mid
                else:
                    lo = mid + 1
            return lo
        # P(X ≤ k | p₀)는 k에 대해 증가 → 조건을 만족하는 가장 큰 k
        if 1.0 - binomial_sf(1, n, self.p0) > alpha:
            return None
        lo, hi = 0, n
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if 1.0 - binomial_sf(mid + 1, n, self.p0) <= alpha:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def power(self, unit: float) -> float:
        n = int(math.floor(float(unit) + 1e-9))
        crit = self.critical_value(n)
        if crit is None or crit > n:
            return 0.0
        if self.p1 > self.p0:
            return binomial_sf(crit, n, self.p1)
        return 1.0 - binomial_sf(crit + 1, n, self.p1)

    def allocation(self, unit: float) -> dict:
        n = max(self.min_unit, math.ceil(unit - 1e-9))
        return {"n": n, "total": n}

    def power_of_allocation(self, alloc: dict) -> float:
        return self.power(alloc["n"])

    def effect(self) -> dict:
        return {
            "name": "반응률 차이 (p₁ − p₀)",
            "name_en": "difference from the performance goal",
            "value": self.p1 - self.p0,
            "label": f"성능목표치 p₀={self.p0:g} → 예상 반응률 p₁={self.p1:g}",
            "p1": self.p1,
            "p0": self.p0,
        }

    def scaled(self, factor: float) -> "OneSampleProportion":
        p1 = self.p0 + (self.p1 - self.p0) * factor
        p1 = min(max(p1, 1e-6), 1.0 - 1e-6)
        if abs(p1 - self.p0) < 1e-9:
            raise PowerPlanError("민감도 분석: 반응률 차이가 0이 되어 계산할 수 없습니다")
        return OneSampleProportion(p1, self.p0, self.alpha, self.sides)

    def smallest_n_reaching(self, target_power: float, cap: int,
                            window: int = 300) -> int | None:
        """검정력이 처음으로 목표를 넘는 n (톱니 때문에 그 뒤에서 다시 내려갈 수 있다).

        심사자가 다른 도구로 재현하면 대개 이 값이 나온다. 이 툴은 그 뒤로도
        계속 목표를 넘는 n을 쓰므로, 두 숫자를 함께 보여 줘야 대화가 된다.

        `cap`(이 툴이 고른 n)에서 아래로 `window`만큼만 훑는다. 0부터 훑으면
        검정력 계산이 n에 비례해 무거워져 소수점 마진에서 몇 십 초가 걸린다.
        창 끝까지 계속 목표를 넘으면 확신할 수 없으므로 None을 돌려준다.
        """
        floor = max(self.min_unit, cap - window)
        best = None
        for n in range(cap, floor - 1, -1):
            if self.power(n) >= target_power:
                best = n
            elif best is not None and n < best - 1:
                break
        if best is not None and best <= floor:
            return None          # 창 밖에도 더 작은 n이 있을 수 있다
        return best

    def plan_lines(self, alloc: dict) -> list[tuple[str, str]]:
        n = alloc["n"]
        crit = self.critical_value(n)
        if crit is None:
            return []
        direction = "이상" if self.p1 > self.p0 else "이하"
        actual = (binomial_sf(crit, n, self.p0) if self.p1 > self.p0
                  else 1.0 - binomial_sf(crit + 1, n, self.p0))
        lo, hi = self._clopper_pearson(crit, n)
        bound = f"하한 {lo:.4g}" if self.p1 > self.p0 else f"상한 {hi:.4g}"
        return [
            ("성공 판정 기준", f"반응자 {crit:,}명 {direction} "
                          f"({crit / n:.1%} {direction}) — 이 숫자를 프로토콜에 적으세요"),
            ("그때의 신뢰구간", f"Clopper–Pearson {1 - 2 * self.one_sided_alpha:.0%} "
                         f"양측 구간 [{lo:.4g}, {hi:.4g}] — 결과보고서에는 이 "
                         f"{bound}이 성능목표치 {self.p0:g}을 넘었다고 씁니다"),
            ("실제 유의수준", f"{actual:.4g} (목표 {self.one_sided_alpha:g} — 정확검정은 "
                         "이산분포라 α를 다 쓰지 못합니다)"),
        ]

    def _clopper_pearson(self, k: int, n: int) -> tuple[float, float]:
        """반응자 k/n의 Clopper–Pearson 양측 신뢰구간.

        하한 p_L은 P(X ≥ k | p_L) = α_tail 을, 상한 p_U는 P(X ≤ k | p_U) = α_tail 을
        만족하는 값이다. 두 꼬리확률 모두 p에 대해 증가하므로 그대로 이분법으로 푼다.
        """
        tail = self.one_sided_alpha
        eps = 1e-12

        def clamp(p: float) -> float:
            return min(max(p, eps), 1.0 - eps)

        lo = 0.0
        if k > 0:
            lo = bisect_increasing(lambda p: binomial_sf(k, n, clamp(p)),
                                   tail, eps, 1.0 - eps, tol=1e-13)
        hi = 1.0
        if k < n:
            # P(X ≤ k | p) = 1 − P(X ≥ k+1 | p) = tail  ⇔  P(X ≥ k+1 | p) = 1 − tail
            hi = bisect_increasing(lambda p: binomial_sf(k + 1, n, clamp(p)),
                                   1.0 - tail, eps, 1.0 - eps, tol=1e-13)
        return lo, hi

    def notes(self) -> list[str]:
        out = [
            "**정확 이항검정** 기준입니다(정규근사 아님). 임계값이 정수라 검정력이 "
            "표본수에 대해 톱니 모양으로 변하며, 여기서는 목표를 **안정적으로** 넘는 "
            "n(그 뒤 3명까지 모두 목표 이상)을 고릅니다. 다른 도구는 '처음으로 넘는 n'을 "
            "쓰는 경우가 많아 몇 명 작게 나올 수 있습니다 — 위의 '처음 도달하는 n'과 "
            "비교해 어느 규칙을 쓸지 프로토콜에 밝히세요.",
            "대조군이 없는 단일군 설계입니다 — 성능목표치 p₀의 근거(문헌·과거 자료·"
            "규제기관 합의)를 프로토콜에 반드시 적어야 합니다. 표본수 계산보다 이쪽이 "
            "심사에서 더 많이 지적됩니다.",
            "α는 단측입니다(성능목표치 대비 우월성은 단측 0.025가 관례).",
            "위의 '성공 판정 기준'을 프로토콜의 성공/실패 판정 규칙으로 그대로 쓰세요. "
            "신뢰구간으로 판정할 계획이면 Clopper–Pearson 하한이 p₀를 넘는지로 "
            "쓰며, 이 계산과 같은 결론을 줍니다.",
            "탈락을 --dropout으로 반영하면 모집 인원이 나옵니다. 다만 기기 시험에서는 "
            "탈락자를 **실패로 간주**하는 규칙이 흔하며, 그 경우 p₁ 자체를 낮춰 잡는 "
            "것이 맞습니다(인원만 늘리는 것으로는 부족합니다).",
        ]
        if self.sides == 2:
            out.insert(1, (
                f"--sides 2를 지정했습니다 — α = {self.alpha:g}가 **양측** 전체가 되어 "
                f"각 방향 {self.one_sided_alpha:g}가 됩니다. 성능목표치 설계는 관례상 "
                "단측이므로, 양측으로 쓸 거라면 --alpha 0.05를 함께 지정할지 확인하세요."))
        return out

    def references(self) -> list[str]:
        return [
            "Clopper CJ, Pearson ES. The use of confidence or fiducial limits "
            "illustrated in the case of the binomial. Biometrika. 1934;26:404-413.",
            "FDA. Design Considerations for Pivotal Clinical Investigations for "
            "Medical Devices (2013). (성능목표치 설계의 근거 요구)",
        ]


# --------------------------------------------------------------------------
# 13) 2×2 교차설계 (AB/BA)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class CrossoverT(Design):
    """2×2 교차설계 (AB/BA) — 같은 사람이 두 처치를 모두 받는다.

    불면증 디바이스 시험처럼 처치 효과가 빨리 사라지는 경우, 교차설계는 같은
    검정력을 훨씬 적은 인원으로 얻는다. 처치효과 추정량의 분산이
    Var(δ̂) = σ_w²·(1/n_AB + 1/n_BA)/2 이므로, 균등 배정에서 Var = σ_w²/n 이다
    (n = 순서당 인원). 자유도는 순서·시기 효과를 빼고 2n − 2.
    """

    diff: float
    sd_within: float
    alpha: float = 0.05
    sides: int = 2

    key = "crossover"
    supports_cluster = False
    cluster_reason = ("교차설계에서는 모든 대상자가 두 처치를 다 받으므로 평행설계의 "
                      "설계효과 DE = 1+(m−1)ICC가 성립하지 않습니다 "
                      "(군집-교차설계는 별도 공식이 필요합니다)")
    supports_sequential = False
    sequential_reason = ("교차설계의 중간분석은 시기·이월효과와 얽혀 있어 평행설계의 "
                         "경계를 그대로 쓸 수 없습니다")
    name_kr = "2×2 교차설계 (AB/BA)"
    name_en = "2x2 crossover trial"
    test_kr = "교차설계 처치효과 t 검정 (시기·순서 보정)"
    test_en = "t-test for the treatment effect in a 2x2 crossover trial"
    unit_kr = "순서당 n"
    min_unit = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "alpha", _check_alpha(self.alpha))
        object.__setattr__(self, "sides", _check_sides(self.sides))
        if not math.isfinite(self.diff) or self.diff == 0.0:
            raise PowerPlanError("--diff(처치 간 차이)는 0이 아닌 유한한 값이어야 합니다")
        if not (math.isfinite(self.sd_within) and self.sd_within > 0.0):
            raise PowerPlanError(
                f"--sd-within: 0보다 큰 값이어야 합니다 (받은 값: {self.sd_within!r})")

    @property
    def effect_size(self) -> float:
        """개인 내 SD 기준 표준화 효과크기."""
        return abs(self.diff) / self.sd_within

    def power(self, unit: float) -> float:
        n = float(unit)
        df = 2.0 * n - 2.0
        if df < 1.0:
            return 0.0
        ncp = self.effect_size * math.sqrt(n)
        if self.sides == 1:
            return nct_sf(t_ppf(1.0 - self.alpha, df), df, ncp)
        tc = t_ppf(1.0 - self.alpha / 2.0, df)
        return nct_sf(tc, df, ncp) + nct_cdf(-tc, df, ncp)

    def allocation(self, unit: float) -> dict:
        n = max(self.min_unit, math.ceil(unit - 1e-9))
        return {"n_per_sequence": n, "total": 2 * n}

    def power_of_allocation(self, alloc: dict) -> float:
        return self.power(alloc["n_per_sequence"])

    def effect(self) -> dict:
        return {
            "name": "처치 간 차이 / 개인 내 SD",
            "name_en": "treatment difference / within-subject SD",
            "value": self.effect_size,
            "label": f"차이 {self.diff:g}, 개인 내 SD {self.sd_within:g} "
                     f"({label_d(self.effect_size)})",
            "diff": self.diff,
            "sd_within": self.sd_within,
        }

    def scaled(self, factor: float) -> "CrossoverT":
        return CrossoverT(self.diff * factor, self.sd_within, self.alpha, self.sides)

    def plan_lines(self, alloc: dict) -> list[tuple[str, str]]:
        n = alloc["n_per_sequence"]
        return [("순서별 배정", f"AB 순서 {n:,}명 + BA 순서 {n:,}명 = 총 {2 * n:,}명 "
                            "(모두 두 처치를 다 받습니다)")]

    def notes(self) -> list[str]:
        return [
            "**σ_w는 개인 내(within-subject) 표준편차**입니다 — 사람들 사이의 SD가 "
            "아닙니다. 사전연구에서 같은 사람을 두 번 잰 차이의 SD를 σ_diff라 하면 "
            f"σ_w = σ_diff/√2 입니다 (여기 값 기준 σ_diff = "
            f"{self.sd_within * math.sqrt(2):.4g}).",
            "**이월효과(carryover)가 없다**고 가정합니다. 두 처치 사이에 충분한 "
            "휴약기간(washout)을 두고, 그 근거를 프로토콜에 적으세요. 이월효과가 "
            "의심되면 교차설계 자체가 부적절합니다(1기 자료만 쓰게 됩니다).",
            "자유도는 시기(period)와 순서(sequence) 효과를 뺀 2n − 2입니다.",
            "탈락이 생기면 그 사람의 자료를 통째로 못 쓰는 경우가 많아, 평행설계보다 "
            "탈락에 취약합니다 — --dropout을 넉넉히 잡으세요.",
            "검정력은 비중심 t 분포로 정확히 계산했습니다.",
        ]

    def references(self) -> list[str]:
        return [
            "Senn S. Cross-over Trials in Clinical Research. 2nd ed. Wiley; 2002.",
            "Jones B, Kenward MG. Design and Analysis of Cross-Over Trials. "
            "3rd ed. Chapman & Hall/CRC; 2014.",
        ]


# --------------------------------------------------------------------------
# 16) 반복사건 계수(발생률) 비교 — 음이항/포아송 발생률비
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class CountRateRatio(Design):
    """반복사건 계수 결과(연간 악화 횟수·발작 횟수·재입원 횟수)의 발생률비 비교.

    COPD 악화, 천식 악화, 뇌전증 발작, 재입원처럼 **한 사람이 여러 번 겪는 사건**은
    "사건을 겪었는가(이분형)"나 "첫 사건까지의 시간(생존)"으로 바꾸면 정보를 버린다.
    실제 1차 분석은 거의 언제나 **음이항 회귀의 발생률비(rate ratio)** 이고,
    표본수도 거기에 맞춰야 한다.

    분산 모형: Y_ij ~ NB(평균 λ_i·t, 분산 λ_i·t + k(λ_i·t)²).
    log 발생률 추정치의 분산은 대상자 1명당 ``1/(λ_i·t) + k`` 이고(Zhu & Lakkis 2014),
    따라서

        Var(log RR) = (1/n1)(1/(λ1·t) + k) + (1/n2)(1/(λ2·t) + k)

    k = 0이면 포아송이 된다. ``t``는 1인당 **평균 관찰기간**(노출량)이다.
    """

    rate1: float                 # 대조군 사건 발생률 (단위 시간당)
    rate_ratio: float            # 중재/대조 발생률비
    dispersion: float = 0.0      # 음이항 과산포 k (0 = 포아송)
    exposure: float = 1.0        # 1인당 평균 관찰기간 t
    alpha: float = 0.05
    sides: int = 2
    ratio: float = 1.0
    variance: str = "alt"        # alt = 대립가설 하 분산 · null = 귀무가설 하 합동분산
    time_unit: str = "년"

    key = "count"
    name_kr = "반복사건 발생률 비교 (음이항/포아송)"
    name_en = "Event rate ratio (negative binomial / Poisson)"
    unit_kr = "1군 n"
    min_unit = 2

    _VARIANCE_KINDS = ("alt", "null")

    def __post_init__(self) -> None:
        object.__setattr__(self, "alpha", _check_alpha(self.alpha))
        object.__setattr__(self, "sides", _check_sides(self.sides))
        object.__setattr__(self, "ratio", _check_ratio(self.ratio))
        if not (math.isfinite(self.rate1) and self.rate1 > 0.0):
            raise PowerPlanError(
                f"--rate1: 0보다 큰 유한한 발생률이어야 합니다 (받은 값: {self.rate1!r})")
        if not (math.isfinite(self.rate_ratio) and self.rate_ratio > 0.0):
            raise PowerPlanError(
                f"--rr(발생률비): 0보다 큰 유한한 값이어야 합니다 (받은 값: {self.rate_ratio!r})")
        if self.rate_ratio == 1.0:
            raise PowerPlanError(
                "--rr이 1이면(두 군의 발생률이 같으면) 어떤 표본수로도 목표 검정력에 "
                "도달할 수 없습니다")
        if not (math.isfinite(self.dispersion) and self.dispersion >= 0.0):
            raise PowerPlanError(
                "--dispersion(과산포 k): 0 이상의 유한한 값이어야 합니다 "
                f"(0 = 포아송; 받은 값: {self.dispersion!r})")
        if not (math.isfinite(self.exposure) and self.exposure > 0.0):
            raise PowerPlanError(
                f"--exposure(1인당 평균 관찰기간): 0보다 커야 합니다 (받은 값: {self.exposure!r})")
        if self.variance not in self._VARIANCE_KINDS:
            raise PowerPlanError(
                f"--variance: alt 또는 null이어야 합니다 (받은 값: {self.variance!r})")
        # 각 값이 유한하고 0보다 커도 **곱**은 0으로 언더플로하거나 inf로 넘칠 수 있다.
        # 검정력은 λ·t로만 들어가므로 여기서 막지 않으면 0으로 나누게 된다.
        rate2 = self.rate1 * self.rate_ratio
        for label, value in (("--rate1 × --rr(= 중재군 발생률)", rate2),
                             ("--rate1 × --exposure", self.rate1 * self.exposure),
                             ("--rate1 × --rr × --exposure", rate2 * self.exposure)):
            if not (math.isfinite(value) and value > 0.0):
                raise PowerPlanError(
                    f"{label}가 계산할 수 있는 범위를 벗어났습니다 ({value!r}). "
                    "발생률과 관찰기간은 곱으로만 들어가므로, 둘을 같은 시간 단위로 "
                    "맞춰 현실적인 크기로 적어 주세요 (예: --rate1 1.2 --exposure 1)")
        object.__setattr__(self, "time_unit", _clean_label(str(self.time_unit), 12) or "년")

    @property
    def test_kr(self) -> str:
        # k = 0이면 그건 포아송이다. "음이항 회귀, 과산포 k = 0"이라고 쓴 프로토콜
        # 문장은 심사에서 "왜 과산포를 0으로 고정했나"를 부르는 자기모순이다.
        if self.dispersion == 0.0:
            return "포아송 회귀 발생률비 검정 (로그 링크·log 관찰기간 오프셋)"
        return "음이항 회귀 발생률비 검정 (로그 링크·log 관찰기간 오프셋)"

    @property
    def test_en(self) -> str:
        model = "Poisson" if self.dispersion == 0.0 else "negative binomial"
        return (f"{model} regression on the event rate ratio "
                "(log link, log exposure offset)")

    @property
    def rate2(self) -> float:
        return self.rate1 * self.rate_ratio

    @property
    def log_rr(self) -> float:
        return math.log(self.rate_ratio)

    def _unit_var(self, rate: float) -> float:
        """대상자 1명이 기여하는 log 발생률의 분산 (1/(λt) + k)."""
        return 1.0 / (rate * self.exposure) + self.dispersion

    def _power(self, n1: float, n2: float) -> float:
        if n1 <= 0.0 or n2 <= 0.0:
            return 0.0
        var_alt = self._unit_var(self.rate1) / n1 + self._unit_var(self.rate2) / n2
        if self.variance == "null":
            # 귀무가설 하 합동 발생률 — 노출량 가중 평균 (분산 = 두 군 공통)
            pooled = ((n1 * self.rate1 + n2 * self.rate2) / (n1 + n2))
            unit = self._unit_var(pooled)
            var_null = unit / n1 + unit / n2
        else:
            var_null = var_alt
        if var_alt <= 0.0 or var_null <= 0.0:  # pragma: no cover - 방어
            return 0.0
        delta = abs(self.log_rr)
        zc = norm_ppf(1.0 - self.alpha / self.sides)
        bound = zc * math.sqrt(var_null)
        se = math.sqrt(var_alt)
        upper = norm_cdf((delta - bound) / se)
        lower = norm_cdf((-delta - bound) / se) if self.sides == 2 else 0.0
        return min(1.0, upper + lower)

    def power(self, unit: float) -> float:
        return self._power(float(unit), self.ratio * float(unit))

    def allocation(self, unit: float) -> dict:
        n1 = max(self.min_unit, math.ceil(unit - 1e-9))
        n2 = max(self.min_unit, math.ceil(self.ratio * n1 - 1e-9))
        return {"n1": n1, "n2": n2, "total": n1 + n2}

    def power_of_allocation(self, alloc: dict) -> float:
        return self._power(float(alloc["n1"]), float(alloc["n2"]))

    def effect(self) -> dict:
        return {
            "name": "발생률비 (rate ratio)",
            "name_en": "rate ratio",
            "value": self.rate_ratio,
            "label": (f"대조 {self.rate1:g}/{self.time_unit} → 중재 "
                      f"{self.rate2:g}/{self.time_unit}"),
            "rate1": self.rate1,
            "rate2": self.rate2,
            "dispersion": self.dispersion,
            "exposure": self.exposure,
            "time_unit": self.time_unit,
            "variance": self.variance,
        }

    def scaled(self, factor: float) -> "CountRateRatio":
        # log 발생률비를 factor배 — RR = 1(효과 없음)이 되지 않도록 막는다
        try:
            rr = math.exp(self.log_rr * factor)
        except OverflowError:
            raise PowerPlanError(
                "민감도 분석: 발생률비가 계산 범위를 벗어났습니다 — "
                "--rr을 현실적인 크기로 줄이세요") from None
        if abs(rr - 1.0) < 1e-9:
            raise PowerPlanError("민감도 분석: 발생률비가 1이 되어 계산할 수 없습니다")
        return CountRateRatio(self.rate1, rr, self.dispersion, self.exposure,
                              self.alpha, self.sides, self.ratio, self.variance,
                              self.time_unit)

    def sensitivity_value(self, factor: float) -> str:
        try:
            return f"{self.scaled(factor).rate_ratio:.3g}"
        except PowerPlanError:
            return f"×{factor:g}"

    @staticmethod
    def _events(value: float) -> str:
        """사건 수 표기 — 자릿수가 터무니없이 크면 지수 표기로 바꾼다."""
        return f"{value:.1f}" if abs(value) < 1e6 else f"{value:.3g}"

    def plan_lines(self, alloc: dict) -> list[tuple[str, str]]:
        e1 = alloc["n1"] * self.rate1 * self.exposure
        e2 = alloc["n2"] * self.rate2 * self.exposure
        lines = [
            ("기대 사건 수",
             f"{self._events(e1 + e2)}건 (대조 {self._events(e1)} + 중재 "
             f"{self._events(e2)}) · 1인당 관찰 {self.exposure:g}{self.time_unit}"),
            ("1인당 평균 사건 수",
             f"대조 {self.rate1 * self.exposure:.3g}건 · 중재 "
             f"{self.rate2 * self.exposure:.3g}건"),
        ]
        if self.dispersion > 0.0:
            var1 = self.rate1 * self.exposure * (1.0 + self.dispersion
                                                 * self.rate1 * self.exposure)
            lines.append(
                ("과산포 영향",
                 f"k = {self.dispersion:g} → 대조군 개인별 사건 수의 분산/평균 = "
                 f"{var1 / (self.rate1 * self.exposure):.2f}배 (포아송이면 1.00배)"))
        return lines

    def information(self, alloc: dict) -> dict:
        base = Design.information(self, alloc)
        base["caveat"] = (
            "'누적 N'은 **계획된 관찰기간을 마친** 인원입니다. 계수 결과의 정보량은 "
            f"인원 수가 아니라 **누적 관찰량(person-{self.time_unit})** 에 가까우므로, "
            "중간분석 시점은 등록 인원이 아니라 '등록 인원 × 관찰기간'으로 프로토콜에 "
            "적으세요 (짧게 관찰된 사람이 많으면 인원 기준 50%가 정보 50%가 아닙니다).")
        return base

    def notes(self) -> list[str]:
        expected1 = self.rate1 * self.exposure
        out = [
            "음이항(과산포 포아송) 회귀의 log 발생률비에 대한 **정규근사**입니다 "
            "(비중심 t 같은 정확계산이 아닙니다).",
            f"1인당 평균 관찰기간 t = {self.exposure:g}{self.time_unit}로 계산했습니다. "
            "실제로는 사람마다 관찰기간이 다르며, 이 계산은 그 **평균**만 반영합니다 — "
            "관찰기간이 크게 흩어지면(예: 절반이 중도 이탈) 검정력이 더 떨어집니다.",
        ]
        if self.dispersion > 0.0:
            out.append(
                f"과산포 k = {self.dispersion:g} (음이항 분산 = μ + k·μ²). 사전연구가 "
                "있으면 1인당 사건 수의 평균과 분산으로 k = (분산 − 평균) / 평균² 로 "
                "추정하세요. k를 낙관적으로(작게) 잡으면 표본수가 크게 과소해집니다.")
            out.append(
                "⚠ **문헌의 값이 k의 역수일 수 있습니다.** R `glm.nb`의 `theta`, "
                "`rnbinom`의 `size`, SAS `PROC GENMOD`의 표기 등은 분산을 "
                "μ + μ²/θ 로 쓰므로 그 θ는 여기서 넣을 k의 **역수**입니다"
                f" (θ = 0.7이면 --dispersion {1 / 0.7:.2f}). 어느 모수인지 확인하지 않고 "
                "그대로 넣으면 표본수가 수십 % 어긋납니다.")
        else:
            out.append(
                "⚠ --dispersion 0 = **포아송**(분산 = 평균)을 가정했습니다. 임상 계수 "
                "자료는 거의 항상 과산포되어 있어(자주 악화되는 사람이 따로 있다) 이 "
                "가정은 표본수를 크게 과소평가합니다. 사전연구가 있으면 k를 추정해 "
                "--dispersion으로 꼭 넣으세요.")
        if expected1 < 0.5:
            out.append(
                f"⚠ 대조군의 1인당 기대 사건 수가 {expected1:.3g}건으로 매우 적습니다 — "
                "이 영역에서는 정규근사가 낙관적입니다. 관찰기간(--exposure)을 늘리거나 "
                "모의실험으로 확인하세요.")
        if self.variance == "null":
            # 방향을 말로 단정하지 않는다. 1:1에서는 1/λ의 볼록성 때문에 합동 분산이
            # 항상 더 작지만, 배분비가 다르면(큰 군의 발생률이 낮을 때) 뒤집힌다.
            # 실제로 같은 n에서 두 분산을 비교해 **계산된 방향**을 적는다.
            direction = self._null_vs_alt_direction()
            out.append(
                "--variance null: 기각역을 **귀무가설 하 합동 발생률**(총 사건 수 / "
                "총 관찰량 — 포아송에서 H0의 최대우도추정치)로 잡았습니다(점수검정 "
                f"계열). 이 설정에서는 기본값(alt)보다 표본수가 {direction}. "
                "실제 1차 분석이 음이항 회귀의 Wald 검정이면 기본값(alt)이 더 잘 "
                "맞습니다.")
        else:
            direction = CountRateRatio(
                self.rate1, self.rate_ratio, self.dispersion, self.exposure,
                self.alpha, self.sides, self.ratio, "null", self.time_unit,
            )._null_vs_alt_direction()
            out.append(
                "--variance alt(기본): 대립가설 하 분산으로 기각역을 잡았습니다 — "
                "음이항 회귀의 Wald 검정(대부분의 1차 분석)과 맞는 선택입니다. "
                f"--variance null(점수검정 계열)로 바꾸면 표본수가 {direction}")
        out.append(
            "--cluster-size/--cluster-icc의 설계효과 1 + (m−1)·ICC는 **평균·비율**을 "
            "위해 유도된 식입니다. 발생률에 그대로 쓰면 근사의 근사이니, 군집 무작위배정 "
            "계수 결과에는 여유를 두거나 모의실험으로 확인하세요.")
        out.append(
            "--dropout은 '분석 대상에서 빠지는 인원'만 보정합니다. 계수 결과에서는 "
            "중도 이탈이 **관찰기간 단축**으로도 작용하므로(그 사람도 분석에는 남는다), "
            "이탈이 많으면 --exposure를 실제 평균 관찰기간으로 낮춰 잡는 편이 정직합니다.")
        return out

    def _null_vs_alt_direction(self) -> str:
        """같은 n에서 합동(null) 분산과 대립가설(alt) 분산의 크기를 실제로 비교."""
        n1, n2 = 1.0, self.ratio
        var_alt = self._unit_var(self.rate1) / n1 + self._unit_var(self.rate2) / n2
        pooled = (n1 * self.rate1 + n2 * self.rate2) / (n1 + n2)
        unit = self._unit_var(pooled)
        var_null = unit / n1 + unit / n2
        if var_null < var_alt * (1.0 - 1e-12):
            return "**작아집니다**(보수적인 쪽이 아닙니다)"
        if var_null > var_alt * (1.0 + 1e-12):
            return "**커집니다**(더 보수적입니다)"
        return "거의 같습니다"

    def references(self) -> list[str]:
        return [
            "Zhu H, Lakkis H. Sample size calculation for comparing two negative "
            "binomial rates. Stat Med. 2014;33:376-387.",
            "Keene ON, Jones MRK, Lane PW, Anderson J. Analysis of exacerbation "
            "rates in asthma and chronic obstructive pulmonary disease: example "
            "from the TRISTAN study. Pharm Stat. 2007;6:89-97.",
            "Signorini DF. Sample size for Poisson regression. "
            "Biometrika. 1991;78:446-450. (k = 0 포아송 특수경우)",
        ]


# --------------------------------------------------------------------------
# 17) 순서형 결과 — 비례오즈(proportional odds) / Wilcoxon–Mann–Whitney
# --------------------------------------------------------------------------
def po_shift(probs: tuple, odds_ratio: float) -> tuple:
    """비례오즈 모형에서 OR만큼 이동한 두 번째 군의 범주 확률.

    누적확률 γ_i를 로짓 척도에서 log(OR)만큼 옮긴다:
    logit(γ2_i) = logit(γ1_i) + log(OR).
    OR > 1이면 **낮은 범주 쪽으로** 확률이 몰린다 (누적확률이 커진다).
    """
    log_or = math.log(odds_ratio)
    out = []
    prev = 0.0
    cum = 0.0
    for i, p in enumerate(probs[:-1]):
        cum += p
        if not (0.0 < cum < 1.0):
            # 누적확률이 0이나 1에 닿으면 로짓이 ±무한대가 된다. 예전에는 여기서
            # 0으로 나누며 트레이스백으로 죽었다 (--probs 0.2,0.3,0.5,0.0000001).
            raise PowerPlanError(
                f"--probs: {i + 1}번째 범주까지의 누적확률이 {cum:.6g}입니다 — "
                "0이나 1이 되면 오즈비를 정의할 수 없습니다. 사실상 아무도 들어가지 "
                "않는 범주는 이웃 범주와 합쳐서 적으세요")
        # exp/1+exp를 로짓 척도에서 안정적으로 — 오즈를 직접 곱하면 큰 OR에서
        # inf/(1+inf) = nan이 되어 '중재군 분포: nan'이 출력됐다.
        z = math.log(cum / (1.0 - cum)) + log_or
        g = 1.0 / (1.0 + math.exp(-z)) if z >= 0.0 else math.exp(z) / (1.0 + math.exp(z))
        g = min(1.0, max(prev, g))       # 누적확률은 단조 — 반올림으로도 뒤집히지 않게
        out.append(g - prev)
        prev = g
    out.append(max(0.0, 1.0 - prev))
    return tuple(out)


@dataclass(frozen=True)
class OrdinalProportionalOdds(Design):
    """순서형 결과(등급 척도)의 두 군 비교 — 비례오즈 / Wilcoxon–Mann–Whitney.

    mRS, WHO 임상개선척도, NRS 통증등급, Likert 만족도, CTCAE 등급처럼 결과가
    **순서 있는 범주**일 때 쓴다. 실무에서 흔한 "0~2 vs 3~6으로 묶어 비율 비교"는
    정보를 버리는 선택이며, 같은 자료로 순서형 분석을 하면 표본수가 줄어든다.

    Whitehead(1993)의 표본수 공식과 같은 근거를 쓴다 — 비례오즈 모형의 log OR에
    대한 Fisher 정보량은 총 N명·배분비 q1:q2에서

        I = N·q1·q2·(1 − Σ p̄_i³) / 3        (p̄_i = 배분 가중 평균 범주확률)

    이며, 검정력은 Var(log ÔR) = 1/I 의 정규근사로 구한다.
    """

    probs: tuple                 # 대조군의 범주 확률 (합 1)
    odds_ratio: float
    alpha: float = 0.05
    sides: int = 2
    ratio: float = 1.0

    key = "ordinal"
    name_kr = "순서형 결과 두 군 비교 (비례오즈)"
    name_en = "Ordinal outcome, two groups (proportional odds)"
    test_kr = "비례오즈 순서형 로지스틱 회귀 (= Wilcoxon–Mann–Whitney 계열)"
    test_en = ("proportional-odds ordinal logistic regression "
               "(Wilcoxon-Mann-Whitney family)")
    unit_kr = "1군 n"
    min_unit = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "alpha", _check_alpha(self.alpha))
        object.__setattr__(self, "sides", _check_sides(self.sides))
        object.__setattr__(self, "ratio", _check_ratio(self.ratio))
        probs = tuple(float(p) for p in self.probs)
        if len(probs) < 3:
            raise PowerPlanError(
                f"--probs: 순서형 범주가 3개 이상이어야 합니다 (받은 개수: {len(probs)}). "
                "2개면 이분형이므로 prop2를 쓰세요")
        if len(probs) > 30:
            raise PowerPlanError(
                f"--probs: 범주가 너무 많습니다 ({len(probs)}개). 30개 이하로 묶으세요 "
                "— 그 이상이면 연속형으로 보고 ttest2를 쓰는 편이 낫습니다")
        for p in probs:
            if not (math.isfinite(p) and p > 0.0):
                raise PowerPlanError(
                    f"--probs: 모든 범주 확률이 0보다 커야 합니다 (받은 값: {p!r}). "
                    "아무도 들어가지 않는 범주는 이웃 범주와 합치세요")
        total = math.fsum(probs)
        if abs(total - 1.0) > 1e-6:
            if total <= 0.0:  # pragma: no cover - 위에서 걸러짐
                raise PowerPlanError("--probs: 확률의 합이 0입니다")
            if abs(total - 100.0) < 1e-3:
                raise PowerPlanError(
                    "--probs: 합이 100입니다 — 퍼센트가 아니라 비율(합 1)로 적으세요 "
                    "(예: 0.1,0.2,0.7)")
            raise PowerPlanError(
                f"--probs: 확률의 합이 1이어야 합니다 (받은 합: {total:.12g}, "
                f"차이 {total - 1.0:+.3g}). 합이 1이 아니면 정규화 결과가 의도와 "
                "다를 수 있어 그대로 거절합니다")
        # 허용오차(1e-6) 안이어도 **부분** 누적확률이 1에 닿을 수 있다
        # (0.2,0.3,0.5,1e-7). 그러면 로짓이 무한대가 되므로 여기서 막는다.
        cum = 0.0
        for i, p in enumerate(probs[:-1]):
            cum += p
            if not (0.0 < cum < 1.0):
                raise PowerPlanError(
                    f"--probs: {i + 1}번째 범주까지의 누적확률이 {cum:.12g}입니다 — "
                    "0이나 1이 되면 오즈비를 정의할 수 없습니다. 사실상 아무도 "
                    "들어가지 않는 범주는 이웃 범주와 합쳐서 적으세요")
        object.__setattr__(self, "probs", probs)
        if not (math.isfinite(self.odds_ratio) and self.odds_ratio > 0.0):
            raise PowerPlanError(
                f"--or(오즈비): 0보다 큰 유한한 값이어야 합니다 (받은 값: {self.odds_ratio!r})")
        if self.odds_ratio == 1.0:
            raise PowerPlanError(
                "--or이 1이면(두 군의 분포가 같으면) 어떤 표본수로도 목표 검정력에 "
                "도달할 수 없습니다")

    @property
    def probs2(self) -> tuple:
        """비례오즈 가정에서 유도한 중재군 범주 확률."""
        return po_shift(self.probs, self.odds_ratio)

    @property
    def log_or(self) -> float:
        return math.log(self.odds_ratio)

    def mean_probs(self, ratio: float | None = None) -> tuple:
        """두 군의 배분 가중 평균 범주확률 p̄ (1:1이면 단순 평균)."""
        r = self.ratio if ratio is None else ratio
        q1, q2 = 1.0 / (1.0 + r), r / (1.0 + r)
        return tuple(q1 * a + q2 * b for a, b in zip(self.probs, self.probs2))

    @staticmethod
    def tie_factor_of(mean_probs) -> float:
        """1 − Σ p̄_i³ — 범주가 한쪽에 몰릴수록 작아지고 표본수가 커진다.

        검정력 계산과 **화면에 찍히는 값**이 같은 식을 쓰도록 여기 한 곳에만 둔다.
        예전에는 이 식이 두 군데에 복사돼 있어서, 한쪽만 바꾸면 출력된 보정계수와
        실제로 쓰인 보정계수가 조용히 어긋날 수 있었다.
        """
        return 1.0 - math.fsum(p ** 3 for p in mean_probs)

    @property
    def tie_factor(self) -> float:
        return self.tie_factor_of(self.mean_probs())

    def win_probability(self) -> float:
        """P(중재군 > 대조군) + ½P(동점) — Mann–Whitney 확률(공통언어 효과크기).

        범주가 '높을수록 좋다'는 방향은 정하지 않는다. 여기서는 **범주 번호가 클수록**
        큰 값으로 보고 계산하며, OR > 1이면 중재군이 낮은 범주로 몰리므로 0.5보다
        작아진다.
        """
        p1, p2 = self.probs, self.probs2
        greater = 0.0
        for j, b in enumerate(p2):
            greater += b * math.fsum(p1[:j])
        ties = math.fsum(a * b for a, b in zip(p1, p2))
        return greater + 0.5 * ties

    def _power(self, n1: float, n2: float) -> float:
        n = n1 + n2
        if n <= 0.0:
            return 0.0
        # 실제 배분(n1:n2)으로 p̄를 다시 계산한다 — 정수 올림으로 배분비가
        # 조금 달라져도 정보량과 일관되게 유지하기 위해서.
        r = n2 / n1
        tie = self.tie_factor_of(self.mean_probs(r))
        # `tie <= 0.0`은 NaN을 통과시킨다 (NaN 비교는 항상 False) — 그러면 검정력이
        # NaN이 되고 min(1.0, NaN)이 1.0을 돌려주어 "n = 2, 검정력 100%"가 찍혔다.
        if not (tie > 0.0):
            return 0.0
        info = n * (n1 / n) * (n2 / n) * tie / 3.0
        se = math.sqrt(1.0 / info)
        delta = abs(self.log_or)
        zc = norm_ppf(1.0 - self.alpha / self.sides)
        upper = norm_cdf(delta / se - zc)
        lower = norm_cdf(-delta / se - zc) if self.sides == 2 else 0.0
        return min(1.0, upper + lower)

    def power(self, unit: float) -> float:
        return self._power(float(unit), self.ratio * float(unit))

    def allocation(self, unit: float) -> dict:
        n1 = max(self.min_unit, math.ceil(unit - 1e-9))
        n2 = max(self.min_unit, math.ceil(self.ratio * n1 - 1e-9))
        return {"n1": n1, "n2": n2, "total": n1 + n2}

    def power_of_allocation(self, alloc: dict) -> float:
        return self._power(float(alloc["n1"]), float(alloc["n2"]))

    def effect(self) -> dict:
        return {
            "name": "오즈비 (비례오즈)",
            "name_en": "odds ratio (proportional odds)",
            "value": self.odds_ratio,
            "label": (f"범주 {len(self.probs)}개 · 동점보정 1−Σp̄³ = "
                      f"{self.tie_factor:.4f}"),
            "categories": len(self.probs),
            "probs1": self.probs,
            "probs2": self.probs2,
            "mean_probs": self.mean_probs(),
            "tie_factor": self.tie_factor,
            "win_probability": self.win_probability(),
        }

    def scaled(self, factor: float) -> "OrdinalProportionalOdds":
        # log OR을 factor배 (OR = 1은 계산 불가)
        try:
            odds = math.exp(self.log_or * factor)
        except OverflowError:
            raise PowerPlanError(
                "민감도 분석: 오즈비가 계산 범위를 벗어났습니다 — "
                "--or을 현실적인 크기로 줄이세요") from None
        if abs(odds - 1.0) < 1e-9:
            raise PowerPlanError("민감도 분석: 오즈비가 1이 되어 계산할 수 없습니다")
        return OrdinalProportionalOdds(self.probs, odds, self.alpha, self.sides,
                                       self.ratio)

    def sensitivity_value(self, factor: float) -> str:
        try:
            return f"{self.scaled(factor).odds_ratio:.3g}"
        except PowerPlanError:
            return f"×{factor:g}"

    def plan_lines(self, alloc: dict) -> list[tuple[str, str]]:
        fmt = lambda ps: " / ".join(f"{p:.3f}" for p in ps)  # noqa: E731
        lines = [
            ("대조군 범주 분포", fmt(self.probs)),
            ("중재군 범주 분포 (비례오즈 가정)", fmt(self.probs2)),
            ("동점 보정계수 1 − Σp̄³",
             f"{self.tie_factor:.4f} (1에 가까울수록 효율적 — 한 범주에 몰리면 작아짐)"),
            ("Mann–Whitney 확률",
             f"{self.win_probability():.4f} = P(중재 > 대조) + ½P(동점), "
             "범주 번호가 클수록 큰 값이라고 볼 때"),
        ]
        expected_min = min(min(self.probs), min(self.probs2)) * min(alloc["n1"],
                                                                   alloc["n2"])
        lines.append(("가장 드문 범주의 기대 인원", f"군당 약 {expected_min:.1f}명"))
        return lines

    def notes(self) -> list[str]:
        out = [
            "**비례오즈(proportional odds)** 를 가정합니다 — 어느 절단점에서 잘라도 "
            "오즈비가 같다는 뜻입니다. 이 가정이 심하게 깨지면(예: 중간 범주만 움직이면) "
            "이 표본수는 맞지 않습니다.",
            "Whitehead(1993)의 정규근사입니다(비중심 t 같은 정확계산이 아닙니다). "
            "군당 20~30명 미만에서는 여유를 두세요.",
            f"동점 보정계수 1 − Σp̄³ = {self.tie_factor:.4f}입니다. 한 범주에 사람이 "
            "몰릴수록 작아지고 표본수는 그만큼 커집니다 — 범주 확률은 문헌이 아니라 "
            "**우리 기관의 실제 분포**로 넣으세요.",
            "이 검정은 오즈비가 1에 가까울 때 Wilcoxon–Mann–Whitney(순위합) 검정과 "
            "국소적으로 같은 검정력을 가집니다. 1차 분석을 순위합으로 계획해도 이 "
            "표본수를 쓸 수 있습니다.",
            "순서형 그대로 분석하면, 같은 자료를 이분형으로 묶어(예: '0~2 vs 3~6') "
            "비율 비교하는 것보다 대개 표본수가 적게 듭니다. 묶어서 분석할 계획이면 "
            "prop2로 따로 계산해 큰 쪽을 쓰세요.",
        ]
        out.append(
            "--cluster-size/--cluster-icc의 설계효과 1 + (m−1)·ICC는 **평균·비율**을 "
            "위해 유도된 식이고, 순서형의 ICC는 잠재변수 척도에서 정의됩니다. "
            "군집 무작위배정 순서형 연구에는 여유를 두세요.")
        smallest = min(min(self.probs), min(self.probs2))
        if smallest < 0.02:
            out.append(
                f"⚠ 가장 드문 범주의 확률이 {smallest:.3g}입니다 — 기대 인원이 한 자리면 "
                "이웃 범주와 합쳐서 계획하는 편이 안전합니다(근사가 무너집니다).")
        if self.ratio != 1.0:
            out.append(
                f"배분비 1:{self.ratio:g} — 같은 총 N이면 1:1이 가장 높은 검정력을 줍니다.")
        return out

    def references(self) -> list[str]:
        return [
            "Whitehead J. Sample size calculations for ordered categorical data. "
            "Stat Med. 1993;12:2257-2271.",
            "Walters SJ, Campbell MJ, Machin D. Medical Statistics: A Textbook for "
            "the Health Sciences. 5th ed. Wiley; 2021. (순서형 결과의 표본수)",
            "McCullagh P. Regression models for ordinal data. "
            "J R Stat Soc Series B. 1980;42:109-142. (비례오즈 모형)",
        ]


DESIGN_KEYS = (
    TwoSampleT.key,
    PairedT.key,
    OneSampleT.key,
    TwoProportions.key,
    OneWayAnova.key,
    CorrelationTest.key,
    NonInferiorityT.key,
    EquivalenceT.key,
    NonInferiorityProportions.key,
    EquivalenceProportions.key,
    McNemarPaired.key,
    RepeatedMeasuresT.key,
    LogRankSurvival.key,
    OneSampleProportion.key,
    CrossoverT.key,
    CountRateRatio.key,
    OrdinalProportionalOdds.key,
)

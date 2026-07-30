"""설계별 검정력 함수 — 계획 단계에서 실제로 쓰는 8가지 설계.

각 설계는 같은 인터페이스를 따른다:

- ``power(unit)``            : 연속형 unit(군당 n 등)에서의 검정력 — 표본수 탐색용
- ``allocation(unit)``       : 정수 배분 (n1, n2, total …)
- ``power_of_allocation(a)`` : 그 정수 배분에서 실제 달성되는 검정력
- ``scaled(factor)``         : 효과크기를 factor배로 바꾼 같은 설계 (민감도 분석용)

t 검정 계열(ttest2·paired·onesample·noninf·equiv)과 ANOVA는 **정확한 비중심 t/F
분포**로 계산한다(정규근사 아님). 비율 비교는 정규근사 z-검정, 상관은 Fisher z 근사이며,
그 한계를 ``notes()``에 명시한다 — 과대주장하지 않는 것이 이 툴의 원칙이다.

``ttest2``는 ``--analysis``로 세 가지 분석을 지원한다: 추적값만 비교(raw), 기저값을
공변량으로 보정(ancova, 분산 배율 1−r²), 변화량 비교(change, 배율 2(1−r)).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .distributions import chi_expectation, f_ppf, ncf_sf, nct_cdf, nct_sf, t_ppf
from .effects import fisher_z, label_d, label_f, label_r
from .special import norm_cdf, norm_ppf
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
    "DESIGN_KEYS",
]


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
        df = n1 + n2 - 2.0 - (1.0 if self.analysis == "ancova" else 0.0)
        if df < 1.0:
            return 0.0
        ncp = self.effective_d / math.sqrt(1.0 / n1 + 1.0 / n2)
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
        return out

    def references(self) -> list[str]:
        out = ["Cohen J. Statistical Power Analysis for the Behavioral Sciences. 2nd ed. 1988."]
        if self.analysis != "raw":
            out.append("Frison L, Pocock SJ. Repeated measures in clinical trials. "
                       "Stat Med. 1992;11:1685-1704.")
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
        return [
            "Fleiss JL, Levin B, Paik MC. Statistical Methods for Rates and Proportions. 3rd ed. 2003.",
            "Casagrande JT, Pike MC, Smith PG. Biometrics. 1978;34:483-486.",
        ]


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
            "Julious SA. Sample sizes for clinical trials. 2010.",
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
    name_kr = "동등성 검정 (TOST, 두 군 평균)"
    name_en = "Equivalence (TOST, two means)"
    test_kr = "TOST (두 개의 단측 t 검정)"
    test_en = "TOST procedure (two one-sided t-tests)"
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


DESIGN_KEYS = (
    TwoSampleT.key,
    PairedT.key,
    OneSampleT.key,
    TwoProportions.key,
    OneWayAnova.key,
    CorrelationTest.key,
    NonInferiorityT.key,
    EquivalenceT.key,
)

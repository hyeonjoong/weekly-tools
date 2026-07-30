"""정밀도 기반 표본수 — 검정력이 아니라 **신뢰구간 폭**으로 n을 정하는 설계.

타당도/신뢰도 연구(비접촉 호흡센서 vs PSG, 워치 HRV vs ECG 같은 검증 연구)는
"차이가 있다"를 검정하는 게 목적이 아니다. ICC나 Bland–Altman 일치한계(LoA)를
**충분히 좁은 신뢰구간으로 추정**하는 것이 목적이므로, 표본수도 정밀도 기준으로
정해야 한다. 검정력 계산을 억지로 쓰는 흔한 오류를 피하려고 따로 두었다.

- :func:`icc_plan`   : ICC 신뢰구간 폭 목표 (Bonett 2002)
- :func:`loa_plan`   : Bland–Altman LoA 신뢰구간 반폭 목표 (Bland & Altman 1999)
- :func:`kappa_plan` : 범주형 판정 일치도 κ의 신뢰구간 폭 목표 (Fleiss 1969 분산)
- :func:`diagnostic_plan` : 민감도·특이도의 신뢰구간 반폭 목표 (유병률 보정 포함)

연속형 결과(호흡수·심박)는 ICC/LoA, **범주형 판정**(수면단계·이상소견 유무)은
kappa다. 범주형 결과에 ICC를 쓰는 것은 흔한 오류라 따로 두었다.
"""

from __future__ import annotations

import math

from .distributions import t_ppf
from .korean import josa as _josa
from .special import norm_ppf
from .validate import PowerPlanError, alpha_value, as_int, positive, probability

__all__ = ["icc_plan", "loa_plan", "kappa_plan", "diagnostic_plan",
           "icc_ci_width", "loa_half_width", "kappa_variance_unit", "kappa_ci_width",
           "proportion_half_width"]

_MAX_N = 1_000_000
#: 목표 폭의 하한 — 이보다 작으면 제곱이 0으로 언더플로해 나눗셈이 깨진다
_MIN_WIDTH = 1e-6
#: 대상자당 측정(평가자) 수 상한
_MAX_RATERS = 1000


def _check_width(name: str, value: float, upper: float) -> float:
    """목표 신뢰구간 폭 — 언더플로 영역과 무의미한 상한을 함께 거른다."""
    width = positive(name, value)
    if width < _MIN_WIDTH:
        raise PowerPlanError(
            f"{name}: {_MIN_WIDTH:g}보다 좁은 목표는 지원하지 않습니다 "
            f"(받은 값: {width:g}) — 필요한 표본수가 현실적인 범위를 벗어납니다"
        )
    if width >= upper:
        raise PowerPlanError(
            f"{name}: {upper:g} 미만이어야 의미가 있습니다 (받은 값: {width:g})")
    return width


def icc_ci_width(n: int, icc: float, raters: int = 2, alpha: float = 0.05) -> float:
    """대상자 n명·측정 k회일 때 ICC 신뢰구간의 **전체 폭** (Bonett 2002 근사)."""
    if n < 2:
        raise PowerPlanError(f"n: 2 이상이어야 합니다 (받은 값: {n})")
    icc = probability("--icc", icc)
    k = as_int("--raters", raters, minimum=2)
    alpha = alpha_value("--alpha", alpha)
    z = norm_ppf(1.0 - alpha / 2.0)
    num = 8.0 * z * z * (1.0 - icc) ** 2 * (1.0 + (k - 1) * icc) ** 2
    return math.sqrt(num / (k * (k - 1) * (n - 1)))


def _check_given_n(given_n) -> int | None:
    """--n(확보 가능한 대상자 수) 역방향 모드의 입력 검증."""
    if given_n is None:
        return None
    n = as_int("--n", given_n, minimum=2)
    if n > _MAX_N:
        raise PowerPlanError(f"--n: {_MAX_N:,}명을 넘는 값은 지원하지 않습니다 (받은 값: {n:,})")
    return n


def _reverse_note(n: int, target_name: str, target: float, achieved: float) -> list[str]:
    verdict = "충족" if achieved <= target + 1e-12 else "미달"
    return [f"확보 가능한 {n:,}명 기준으로 예상 정밀도를 계산했습니다 "
            f"(목표 {target_name} {target:g} 대비 **{verdict}**)."]


def icc_plan(icc: float, width: float, raters: int = 2, alpha: float = 0.05,
             given_n=None) -> dict:
    """목표 CI 폭을 만족하는 ICC 신뢰도 연구의 대상자 수.

    Bonett(2002): n = 8·z²(1−ρ)²(1+(k−1)ρ)² / (k(k−1)w²) + 1

    Args:
        icc: 예상 ICC (사전연구/문헌값). 보수적으로 낮게 잡는 편이 안전하다.
        width: 목표 **전체** CI 폭 (예: 0.2 → 대략 [0.75, 0.95]).
        raters: 대상자당 측정(평가자) 수 k ≥ 2.
        alpha: 1 − 신뢰수준.
    """
    icc = probability("--icc", icc)
    width = _check_width("--width", width, 1.0)
    raters = as_int("--raters", raters, minimum=2)
    if raters > _MAX_RATERS:
        raise PowerPlanError(
            f"--raters: 대상자당 {_MAX_RATERS}회를 넘는 측정은 지원하지 않습니다 "
            f"(받은 값: {raters:,})")
    alpha = alpha_value("--alpha", alpha)
    z = norm_ppf(1.0 - alpha / 2.0)
    k = raters
    num = 8.0 * z * z * (1.0 - icc) ** 2 * (1.0 + (k - 1) * icc) ** 2
    n_float = num / (k * (k - 1) * width * width) + 1.0
    given = _check_given_n(given_n)
    n = given if given is not None else max(2, math.ceil(n_float - 1e-9))
    if given is None and n > _MAX_N:
        raise PowerPlanError(
            f"필요 표본수가 {n:,}명으로 계산됩니다. 목표 폭(--width)을 넓히거나 "
            "측정 횟수(--raters)를 늘리세요"
        )
    return {
        "kind": "precision",
        "design_key": "icc",
        "name_kr": "ICC 신뢰도 연구 (정밀도 기준)",
        "name_en": "ICC reliability study (precision-based)",
        "target": {"icc": icc, "width": width, "raters": raters, "alpha": alpha},
        "n": n,
        "n_exact": n_float,
        "achieved_width": icc_ci_width(n, icc, raters, alpha),
        "total_measurements": n * raters,
        "given_n": given is not None,
        "notes": (_reverse_note(n, "폭", width, icc_ci_width(n, icc, raters, alpha))
                  if given is not None else []) + [
            "이것은 검정력 계산이 아니라 **정밀도(신뢰구간 폭) 기준** 표본수입니다. "
            "신뢰도 연구에서는 이쪽이 옳은 접근입니다.",
            "예상 ICC를 낙관적으로 잡으면 표본수가 과소해집니다 — 문헌 하한을 쓰세요.",
            "Bonett(2002) 근사식이며 정확법과 소수 인원 차이가 날 수 있습니다.",
            "측정 횟수 k를 2 → 3으로 늘리면 필요한 대상자 수가 크게 줄어듭니다 "
            "(비교해 보세요: --raters 3).",
        ],
        "references": [
            "Bonett DG. Sample size requirements for estimating intraclass correlations "
            "with desired precision. Stat Med. 2002;21:1331-1335.",
            "Shrout PE, Fleiss JL. Psychol Bull. 1979;86:420-428.",
        ],
    }


def loa_half_width(n: int, sd_diff: float, alpha: float = 0.05,
                   z_loa: float = 1.959963984540054) -> float:
    """대상자 n명일 때 Bland–Altman 일치한계(LoA)의 신뢰구간 **반폭**.

    Var(LoA) ≈ s²·(1/n + z²/(2(n−1))), 반폭 = t_{1−α/2, n−1}·√Var.
    """
    if n < 2:
        raise PowerPlanError(f"n: 2 이상이어야 합니다 (받은 값: {n})")
    se = sd_diff * math.sqrt(1.0 / n + z_loa * z_loa / (2.0 * (n - 1)))
    return t_ppf(1.0 - alpha_value("--alpha", alpha) / 2.0, n - 1) * se


def loa_plan(sd_diff: float, half_width: float, alpha: float = 0.05,
             given_n=None) -> dict:
    """Bland–Altman 일치한계의 CI 반폭 목표를 만족하는 대상자 수.

    Args:
        sd_diff: 예상되는 **차이의 표준편차** s (두 방법 차이의 SD).
        half_width: 목표 LoA 신뢰구간 반폭 (원래 단위).
        alpha: 1 − 신뢰수준.
    """
    sd_diff = positive("--sd-diff", sd_diff)
    half_width = positive("--half-width", half_width)
    alpha = alpha_value("--alpha", alpha)
    given = _check_given_n(given_n)
    if given is not None:
        return _loa_result(given, sd_diff, half_width, alpha, given_n=True)
    if loa_half_width(2, sd_diff, alpha) <= half_width:
        n = 2                     # 2명으로 이미 충분 (목표 반폭이 매우 넓은 경우)
        return _loa_result(n, sd_diff, half_width, alpha)
    # 반폭은 n에 대해 단조감소 → 위로 배로 늘려 잡고 이분 탐색
    hi = 4
    while hi < _MAX_N and loa_half_width(hi, sd_diff, alpha) > half_width:
        hi *= 2
    if loa_half_width(min(hi, _MAX_N), sd_diff, alpha) > half_width:
        raise PowerPlanError(
            f"필요 표본수가 {_MAX_N:,}명을 넘습니다. 목표 반폭(--half-width)을 넓히세요 "
            f"(현재 목표 {half_width:g}, 차이의 SD {sd_diff:g})"
        )
    lo = 2
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if loa_half_width(mid, sd_diff, alpha) <= half_width:
            hi = mid
        else:
            lo = mid
    n = hi
    return _loa_result(n, sd_diff, half_width, alpha)


def kappa_variance_unit(kappa: float, prevalence: float) -> float:
    """n·Var(κ̂) — 대상자 1명당 분산 (2평가자·이분형·동일 주변확률 가정).

    Fleiss·Cohen·Everitt(1969)의 델타법 분산을 "두 평가자가 같은 유병률 π를 가지고
    일치도가 κ"인 모형에 대입하면 다음 닫힌 형태가 된다:

        n·Var(κ̂) = (1−κ)·[ (1−κ)(1−2κ) + κ(2−κ) / (2π(1−π)) ]

    유병률이 극단적일수록(π → 0 또는 1) 분산이 급격히 커진다 — 드문 소견의
    일치도를 재려면 표본이 훨씬 많이 필요하다는 잘 알려진 사실이다.
    """
    kappa = probability("--kappa", kappa)
    prevalence = probability("--prevalence", prevalence)
    var = (1.0 - kappa) * ((1.0 - kappa) * (1.0 - 2.0 * kappa)
                           + kappa * (2.0 - kappa) / (2.0 * prevalence * (1.0 - prevalence)))
    if var <= 0.0:  # pragma: no cover - κ,π ∈ (0,1)에서는 항상 양수
        raise PowerPlanError("kappa 분산이 0 이하로 계산됩니다 — 입력을 확인하세요")
    return var


def kappa_ci_width(n: int, kappa: float, prevalence: float, alpha: float = 0.05) -> float:
    """대상자 n명일 때 κ 신뢰구간의 **전체 폭**."""
    if n < 2:
        raise PowerPlanError(f"n: 2 이상이어야 합니다 (받은 값: {n})")
    z = norm_ppf(1.0 - alpha_value("--alpha", alpha) / 2.0)
    return 2.0 * z * math.sqrt(kappa_variance_unit(kappa, prevalence) / n)


def kappa_plan(kappa: float, width: float, prevalence: float = 0.5,
               alpha: float = 0.05, given_n=None) -> dict:
    """목표 CI 폭을 만족하는 κ(일치도) 연구의 대상자 수.

        n = 4·z²·[n·Var(κ̂)] / w²        (w = 목표 **전체** 폭)

    Args:
        kappa: 예상 κ (보수적으로 낮게 잡는 편이 안전하다 — 낮을수록 n이 커진다).
        width: 목표 전체 CI 폭 (예: 0.2 → ±0.1).
        prevalence: 관심 범주의 유병률 π (예: 이상소견 비율). 0.5에서 가장 효율적이다.
        alpha: 1 − 신뢰수준.
    """
    kappa = probability("--kappa", kappa)
    width = _check_width("--width", width, 2.0)
    prevalence = probability("--prevalence", prevalence)
    alpha = alpha_value("--alpha", alpha)
    z = norm_ppf(1.0 - alpha / 2.0)
    unit_var = kappa_variance_unit(kappa, prevalence)
    n_float = 4.0 * z * z * unit_var / (width * width)
    given = _check_given_n(given_n)
    n = given if given is not None else max(2, math.ceil(n_float - 1e-9))
    if given is None and n > _MAX_N:
        raise PowerPlanError(
            f"필요 표본수가 {n:,}명으로 계산됩니다. 목표 폭(--width)을 넓히거나 "
            "유병률이 0.5에 가까운 범주로 나누세요"
        )
    achieved = kappa_ci_width(n, kappa, prevalence, alpha)
    return {
        "kind": "precision",
        "design_key": "kappa",
        "name_kr": "범주형 일치도 κ 연구 (정밀도 기준)",
        "name_en": "Cohen's kappa agreement study (precision-based)",
        "target": {"kappa": kappa, "width": width, "prevalence": prevalence, "alpha": alpha},
        "n": n,
        "n_exact": n_float,
        "achieved_width": achieved,
        "expected_ci": (max(-1.0, kappa - achieved / 2.0), min(1.0, kappa + achieved / 2.0)),
        "variance_unit": unit_var,
        "expected_positive": n * prevalence,
        "given_n": given is not None,
        "notes": (_reverse_note(n, "폭", width, achieved) if given is not None else []) + [
            "검정력이 아니라 **정밀도(신뢰구간 폭) 기준**입니다 — 일치도 연구의 표준 접근입니다.",
            "평가자 2명·**이분형 판정**(있음/없음)·두 평가자의 유병률이 같다는 가정의 "
            "대표본 분산식(Fleiss 1969)입니다.",
            "**3범주 이상(예: 수면단계 W/N1/N2/N3/R)의 κ는 이 식으로 계산할 수 없습니다.** "
            "관심 범주를 하나 골라 이분형으로 줄이거나(예: N3 vs 그 외), 가중 κ라면 "
            "시뮬레이션 기반 계산이 필요합니다.",
            f"유병률 π = {_josa(f'{prevalence:g}', '으로', '로')} 가정했습니다. "
            "π가 0이나 1에 가까울수록 필요한 "
            "표본수가 급격히 늘어납니다 — 드문 소견이면 π를 실제값으로 꼭 확인하세요.",
            "예상 κ를 낙관적으로 잡으면(높게) 표본수가 과소해집니다 — 문헌 하한을 쓰세요.",
        ],
        "references": [
            "Fleiss JL, Cohen J, Everitt BS. Large sample standard errors of kappa and "
            "weighted kappa. Psychol Bull. 1969;72:323-327.",
            "Donner A, Eliasziw M. A goodness-of-fit approach to inference procedures "
            "for the kappa statistic: confidence interval construction, "
            "significance-testing and sample size estimation. "
            "Stat Med. 1992;11:1511-1519.",
            "Sim J, Wright CC. The kappa statistic in reliability studies: use, "
            "interpretation, and sample size requirements. "
            "Phys Ther. 2005;85:257-268.",
        ],
    }


def proportion_half_width(n: float, p: float, alpha: float = 0.05) -> float:
    """비율 p를 n명에서 추정할 때의 신뢰구간 **반폭** (Wald)."""
    if n < 1:
        raise PowerPlanError(f"n: 1 이상이어야 합니다 (받은 값: {n})")
    z = norm_ppf(1.0 - alpha_value("--alpha", alpha) / 2.0)
    return z * math.sqrt(p * (1.0 - p) / n)


def wilson_half_width(n: float, p: float, alpha: float = 0.05) -> float:
    """Wilson(score) 신뢰구간의 **최대 반폭** — 점추정치에서 먼 쪽 끝까지의 거리.

    Wald 구간은 p가 1에 가까울수록 실제보다 좁게 나온다. 민감도 0.95~0.99를
    다루는 진단정확도 연구에서 이 차이는 두 배가 넘을 수 있으므로, "예상 반폭"을
    Wald로만 보고하면 실제로 보고될 구간보다 훨씬 낙관적인 약속을 하게 된다.
    실제 논문은 대부분 Wilson이나 Clopper–Pearson으로 보고한다.
    """
    if n < 1:
        raise PowerPlanError(f"n: 1 이상이어야 합니다 (받은 값: {n})")
    z = norm_ppf(1.0 - alpha_value("--alpha", alpha) / 2.0)
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    spread = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    lo, hi = max(0.0, centre - spread), min(1.0, centre + spread)
    return max(p - lo, hi - p)


def _n_for_proportion(p: float, half_width: float, alpha: float, method: str) -> float:
    """비율 p를 목표 반폭으로 추정하는 데 필요한 인원."""
    z = norm_ppf(1.0 - alpha / 2.0)
    wald = z * z * p * (1.0 - p) / (half_width * half_width)
    if method == "wald":
        return wald
    # Wilson 반폭은 n에 대해 단조감소 → 필요한 n을 위로 훑어 찾는다
    n = max(1.0, wald)
    for _ in range(100_000):
        if wilson_half_width(n, p, alpha) <= half_width:
            return n
        n = n * 1.02 + 1.0
        if n > _MAX_N:
            break
    return n


def diagnostic_plan(sens: float, spec: float, prevalence: float, half_width: float,
                    alpha: float = 0.05, given_n=None, method: str = "wilson") -> dict:
    """진단정확도 연구 — 민감도·특이도를 목표 정밀도로 추정할 대상자 수.

    **여기서 사람들이 틀리는 지점**: 민감도는 질환이 **있는** 사람에서만 추정되므로,
    필요한 것은 "전체 n"이 아니라 "질환자 n"이다. 유병률 10%짜리 코호트에서
    민감도 CI 반폭 0.05를 얻으려면 질환자 139명이 필요하고, 그러려면 전체
    1,390명을 등록해야 한다. 이 나눗셈을 빼먹으면 표본수가 10배 모자란다.

        n_질환 = z²·Se(1−Se)/w²,   n_비질환 = z²·Sp(1−Sp)/w²
        n_전체 = max(n_질환/유병률, n_비질환/(1−유병률))

    Args:
        sens: 예상 민감도, spec: 예상 특이도 (둘 다 0~1).
        prevalence: 대상 집단의 유병률 (연속 등록 코호트 기준).
        half_width: 목표 신뢰구간 **반폭** (예: 0.05 = ±5%p).
        alpha: 1 − 신뢰수준.
    """
    sens = probability("--sens", sens)
    spec = probability("--spec", spec)
    prevalence = probability("--prevalence", prevalence)
    half_width = _check_width("--half-width", half_width, 1.0)
    alpha = alpha_value("--alpha", alpha)
    if method not in ("wilson", "wald"):
        raise PowerPlanError(
            f"--method: wilson 또는 wald여야 합니다 (받은 값: {method!r})")
    z = norm_ppf(1.0 - alpha / 2.0)
    n_disease = _n_for_proportion(sens, half_width, alpha, method)
    n_healthy = _n_for_proportion(spec, half_width, alpha, method)
    wald_disease = z * z * sens * (1.0 - sens) / (half_width * half_width)
    total = max(n_disease / prevalence, n_healthy / (1.0 - prevalence))
    given = _check_given_n(given_n)
    if given is not None:
        n = given
    else:
        # 실제로 확보되는 질환자 수는 **정수**(floor)다. 소수 그대로 반올림하면
        # 반폭이 목표를 아슬아슬하게 넘는 일이 생기므로, 정수 기준으로 목표를
        # 만족할 때까지 올린다.
        need_disease, need_healthy = math.ceil(n_disease), math.ceil(n_healthy)
        n = max(2, math.ceil(total - 1e-9))
        for _ in range(1000):
            if (math.floor(n * prevalence + 1e-9) >= need_disease
                    and math.floor(n * (1.0 - prevalence) + 1e-9) >= need_healthy):
                break
            n += 1
    if given is None and n > _MAX_N:
        raise PowerPlanError(
            f"필요 표본수가 {n:,}명으로 계산됩니다. 목표 반폭(--half-width)을 넓히거나, "
            "질환자를 따로 모으는 사례-대조 설계를 고려하세요"
        )
    # 표시·정밀도 계산 모두 **실제로 손에 쥐는 정수 인원**으로 한다
    got_disease = max(1.0, float(math.floor(n * prevalence + 1e-9)))
    got_healthy = max(1.0, float(math.floor(n * (1.0 - prevalence) + 1e-9)))
    binding = "민감도" if n_disease / prevalence >= n_healthy / (1.0 - prevalence) else "특이도"
    return {
        "kind": "precision",
        "design_key": "diag",
        "name_kr": "진단정확도 연구 (민감도·특이도 정밀도 기준)",
        "name_en": "Diagnostic accuracy study (precision-based)",
        "target": {"sens": sens, "spec": spec, "prevalence": prevalence,
                   "half_width": half_width, "alpha": alpha},
        "n": n,
        "n_exact": total,
        "n_disease": got_disease,
        "n_healthy": got_healthy,
        "required_disease": n_disease,
        "required_healthy": n_healthy,
        "binding": binding,
        "method": method,
        "required_disease_wald": wald_disease,
        "achieved_half_width": (wilson_half_width(got_disease, sens, alpha)
                                if method == "wilson"
                                else proportion_half_width(got_disease, sens, alpha)),
        "achieved_half_width_spec": (wilson_half_width(got_healthy, spec, alpha)
                                     if method == "wilson"
                                     else proportion_half_width(got_healthy, spec, alpha)),
        "achieved_half_width_wald": proportion_half_width(got_disease, sens, alpha),
        "given_n": given is not None,
        "notes": (_reverse_note(
            n, "반폭", half_width,
            wilson_half_width(got_disease, sens, alpha) if method == "wilson"
            else proportion_half_width(got_disease, sens, alpha))
            if given is not None else []) + [
            "검정력이 아니라 **정밀도(신뢰구간 반폭)** 기준입니다 — 진단정확도 연구의 "
            "표준 접근입니다.",
            f"필요한 것은 전체 인원이 아니라 **질환자 수**입니다: 민감도에 "
            f"{math.ceil(n_disease):,}명, 특이도에 {math.ceil(n_healthy):,}명이 필요하고, "
            f"유병률 {prevalence:.1%}에서 이를 채우려면 전체 "
            f"{math.ceil(total - 1e-9):,}명을 등록해야 합니다 "
            f"(제약이 되는 쪽: **{binding}**)."
            + (f" 지금 지정한 {n:,}명으로는 질환자가 약 {got_disease:.0f}명뿐입니다."
               if given is not None else ""),
            "질환자를 따로 모으는 **사례-대조 설계**라면 전체 인원이 아니라 위의 "
            "질환자·비질환자 수를 직접 목표로 잡으면 됩니다 — 훨씬 적게 듭니다. "
            "다만 그 설계에서는 예측도(PPV/NPV)를 추정할 수 없습니다.",
            (f"**Wilson(score) 구간** 기준입니다(기본값). 흔히 인용되는 Buderer 공식은 "
             f"Wald 구간 기준이라 질환자 {math.ceil(wald_disease):,}명이면 된다고 하지만, "
             "민감도가 0.9를 넘으면 Wald 구간은 실제로 보고되는 Wilson·"
             "Clopper–Pearson 구간보다 **좁게** 나옵니다 — 그대로 쓰면 목표 정밀도를 "
             "달성하지 못합니다. 문헌값과 맞춰야 하면 --method wald를 쓰세요."
             if method == "wilson" else
             "**Wald 구간** 기준입니다(고전 Buderer 공식). 민감도가 0.9를 넘으면 실제로 "
             "보고되는 Wilson·Clopper–Pearson 구간이 이보다 **넓어** 목표 반폭을 "
             "넘습니다 — --method wilson으로 확인하세요."),
            "참조표준(gold standard)이 완전하다고 가정합니다. 참조표준 자체에 오차가 "
            "있으면(예: PSG 판독자 간 불일치) 관측 민감도가 낮게 나옵니다.",
        ],
        "references": [
            "Buderer NM. Statistical methodology: I. Incorporating the prevalence of "
            "disease into the sample size calculation for sensitivity and specificity. "
            "Acad Emerg Med. 1996;3:895-900.",
            "Bossuyt PM, et al. STARD 2015: an updated list of essential items for "
            "reporting diagnostic accuracy studies. BMJ. 2015;351:h5527.",
        ],
    }


def _loa_result(n: int, sd_diff: float, half_width: float, alpha: float,
                given_n: bool = False) -> dict:
    return {
        "kind": "precision",
        "design_key": "loa",
        "name_kr": "Bland–Altman 일치한계(LoA) 정밀도 기준",
        "name_en": "Bland–Altman limits of agreement (precision-based)",
        "target": {"sd_diff": sd_diff, "half_width": half_width, "alpha": alpha},
        "n": n,
        "achieved_half_width": loa_half_width(n, sd_diff, alpha),
        "expected_loa": (-1.959963984540054 * sd_diff, 1.959963984540054 * sd_diff),
        "ratio_to_sd": half_width / sd_diff,
        "given_n": given_n,
        "notes": (_reverse_note(n, "반폭", half_width,
                                loa_half_width(n, sd_diff, alpha)) if given_n else []) + [
            "검정력이 아니라 **LoA 신뢰구간의 정밀도** 기준입니다 (방법 비교 연구의 표준).",
            "차이의 SD(s)는 사전연구에서 추정하세요. s를 과소평가하면 표본수가 부족해집니다.",
            "표시된 예상 LoA(±1.96s)는 편향(bias)이 0이라는 가정하의 폭입니다.",
            "차이가 측정값 크기에 비례하면(비례편향) 로그변환 후 계획하세요.",
            "정규성(차이의 정규분포)을 가정한 근사식입니다.",
        ],
        "references": [
            "Bland JM, Altman DG. Measuring agreement in method comparison studies. "
            "Stat Methods Med Res. 1999;8:135-160.",
            "Bland JM, Altman DG. Lancet. 1986;1:307-310.",
        ],
    }

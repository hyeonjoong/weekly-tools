"""정밀도 기반 표본수 — 검정력이 아니라 **신뢰구간 폭**으로 n을 정하는 설계.

타당도/신뢰도 연구(비접촉 호흡센서 vs PSG, 워치 HRV vs ECG 같은 검증 연구)는
"차이가 있다"를 검정하는 게 목적이 아니다. ICC나 Bland–Altman 일치한계(LoA)를
**충분히 좁은 신뢰구간으로 추정**하는 것이 목적이므로, 표본수도 정밀도 기준으로
정해야 한다. 검정력 계산을 억지로 쓰는 흔한 오류를 피하려고 따로 두었다.

- :func:`icc_plan` : ICC 신뢰구간 폭 목표 (Bonett 2002)
- :func:`loa_plan` : Bland–Altman LoA 신뢰구간 반폭 목표 (Bland & Altman 1999)
"""

from __future__ import annotations

import math

from .distributions import t_ppf
from .special import norm_ppf
from .validate import PowerPlanError, as_int, positive, probability

__all__ = ["icc_plan", "loa_plan", "icc_ci_width", "loa_half_width"]

_MAX_N = 1_000_000


def icc_ci_width(n: int, icc: float, raters: int = 2, alpha: float = 0.05) -> float:
    """대상자 n명·측정 k회일 때 ICC 신뢰구간의 **전체 폭** (Bonett 2002 근사)."""
    if n < 2:
        raise PowerPlanError(f"n: 2 이상이어야 합니다 (받은 값: {n})")
    icc = probability("--icc", icc)
    k = as_int("--raters", raters, minimum=2)
    alpha = probability("--alpha", alpha)
    z = norm_ppf(1.0 - alpha / 2.0)
    num = 8.0 * z * z * (1.0 - icc) ** 2 * (1.0 + (k - 1) * icc) ** 2
    return math.sqrt(num / (k * (k - 1) * (n - 1)))


def icc_plan(icc: float, width: float, raters: int = 2, alpha: float = 0.05) -> dict:
    """목표 CI 폭을 만족하는 ICC 신뢰도 연구의 대상자 수.

    Bonett(2002): n = 8·z²(1−ρ)²(1+(k−1)ρ)² / (k(k−1)w²) + 1

    Args:
        icc: 예상 ICC (사전연구/문헌값). 보수적으로 낮게 잡는 편이 안전하다.
        width: 목표 **전체** CI 폭 (예: 0.2 → 대략 [0.75, 0.95]).
        raters: 대상자당 측정(평가자) 수 k ≥ 2.
        alpha: 1 − 신뢰수준.
    """
    icc = probability("--icc", icc)
    width = positive("--width", width)
    raters = as_int("--raters", raters, minimum=2)
    alpha = probability("--alpha", alpha)
    if width >= 1.0:
        raise PowerPlanError(f"--width: 1 미만이어야 의미가 있습니다 (받은 값: {width:g})")
    z = norm_ppf(1.0 - alpha / 2.0)
    k = raters
    num = 8.0 * z * z * (1.0 - icc) ** 2 * (1.0 + (k - 1) * icc) ** 2
    n_float = num / (k * (k - 1) * width * width) + 1.0
    n = max(2, math.ceil(n_float - 1e-9))
    if n > _MAX_N:
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
        "notes": [
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
    return t_ppf(1.0 - alpha / 2.0, n - 1) * se


def loa_plan(sd_diff: float, half_width: float, alpha: float = 0.05) -> dict:
    """Bland–Altman 일치한계의 CI 반폭 목표를 만족하는 대상자 수.

    Args:
        sd_diff: 예상되는 **차이의 표준편차** s (두 방법 차이의 SD).
        half_width: 목표 LoA 신뢰구간 반폭 (원래 단위).
        alpha: 1 − 신뢰수준.
    """
    sd_diff = positive("--sd-diff", sd_diff)
    half_width = positive("--half-width", half_width)
    alpha = probability("--alpha", alpha)
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


def _loa_result(n: int, sd_diff: float, half_width: float, alpha: float) -> dict:
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
        "notes": [
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

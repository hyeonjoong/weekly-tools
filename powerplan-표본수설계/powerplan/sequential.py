"""군차별설계(group-sequential design) — 중간분석을 표본수 계획에 반영한다.

**왜 필요한가**: 임상시험은 안전성·윤리 때문에 중간분석을 한다. 그런데 같은 α로
여러 번 들여다보면 1종오류가 부풀어(2회면 0.05 → 0.083, 5회면 0.14) 규제기관이
받아들이지 않는다. 표준 해법은 Lan & DeMets(1983)의 **α 소비함수**로 각 시점에
쓸 유의수준을 나누는 것이고, 그 대가로 최종 임계값이 커지므로 같은 검정력을
유지하려면 표본수를 조금 늘려야 한다 — 그 배율이 **표본수 팽창계수**(inflation
factor)다. 프로토콜에는 (1) 최대 표본수, (2) 각 시점의 임계값·명목 유의수준,
(3) 조기중단을 감안한 기대 표본수가 모두 들어가야 한다. 이 모듈이 그 셋을 만든다.

**계산 방식**: Armitage–McPherson–Rowe(1969)의 재귀적 수치적분. 정보량
t₁ < … < t_K = 1 시점의 누적 통계량 S_k = Z_k·√t_k 는 독립증분을 가지므로,
"아직 경계를 넘지 않은" 부분밀도를 다음 시점으로 합성곱하며 전파한다.

    f₁(s)  = φ(s; μt₁, t₁)                         (|s| < c₁)
    f_k(s) = ∫_{|u|<c_{k−1}} f_{k−1}(u)·φ(s−u; μΔ_k, Δ_k) du

경계 c_k 는 귀무가설(μ=0)에서의 누적 이탈확률이 소비함수 α*(t_k)와 같아지도록
정한다. 그 다음 대립가설의 이동모수 μ를 목표 검정력이 나오도록 역산하면
팽창계수 R = (μ_GS / (z_{1−α/s} + z_{1−β}))² 를 얻는다.

정규근사(Z 통계량) 기반이다 — gsDesign·EAST를 포함한 표준 도구가 모두 같다.
t 검정의 정확 비중심 t 계산은 **고정설계 표본수**에 그대로 쓰고, 여기서 얻은
R만 곱한다.
"""

from __future__ import annotations

import math
from functools import lru_cache

from .special import bisect_increasing, norm_cdf, norm_ppf
from .validate import PowerPlanError

__all__ = ["SPENDING_KINDS", "MAX_INTERIM", "sequential_plan", "spending_label",
           "check_interim", "check_timing", "power_from_fixed"]

#: 지원하는 α 소비함수
SPENDING_KINDS = ("obf", "pocock", "linear")
#: 중간분석 횟수 상한 (이보다 많은 계획은 현실에 없다)
MAX_INTERIM = 10
#: 부분밀도 적분 격자점 수 (Simpson — 홀수여야 한다)
_GRID = 81
#: 단측 설계에서 아래쪽을 잘라내는 지점 (표준편차 배수). 이 바깥 질량은 < 1e-23
_TAIL_SIGMAS = 10.0
#: z 경계 탐색 상한 (명목 p ~ 1e-88)
_MAX_BOUND = 20.0
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)

_SPENDING_KR = {
    "obf": "O'Brien–Fleming 형 (Lan–DeMets)",
    "pocock": "Pocock 형 (Lan–DeMets)",
    "linear": "선형(균등) 소비",
}
_SPENDING_EN = {
    "obf": "O'Brien-Fleming-type (Lan-DeMets) alpha spending",
    "pocock": "Pocock-type (Lan-DeMets) alpha spending",
    "linear": "linear alpha spending",
}


def spending_label(kind: str, korean: bool = True) -> str:
    return (_SPENDING_KR if korean else _SPENDING_EN).get(kind, kind)


# --------------------------------------------------------------------------
# α 소비함수
# --------------------------------------------------------------------------
def _spent(t: float, alpha: float, kind: str) -> float:
    """정보비율 t까지 **누적**으로 쓰는 α (α*(0)=0, α*(1)=α)."""
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return alpha
    if kind == "obf":
        # α*(t) = 2{1 − Φ(z_{1−α/2}/√t)} — gsDesign의 sfLDOF과 동일.
        # 여기서 α는 **소비 대상이 되는 전체 오류율**이며, 단측 설계에서도 같은 식을
        # 그대로 쓴다(단측 α를 반으로 나눈 양측식이 아니다). gsDesign도 동일 규약이다.
        return 2.0 * (1.0 - norm_cdf(norm_ppf(1.0 - alpha / 2.0) / math.sqrt(t)))
    if kind == "pocock":
        return alpha * math.log1p((math.e - 1.0) * t)
    if kind == "linear":
        return alpha * t
    raise PowerPlanError(  # pragma: no cover - 호출 전에 검증한다
        f"--spending: {', '.join(SPENDING_KINDS)} 중 하나여야 합니다 (받은 값: {kind!r})"
    )


# --------------------------------------------------------------------------
# 재귀적 수치적분
# --------------------------------------------------------------------------
def _simpson_grid(lo: float, hi: float, n: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """[lo, hi] 위의 Simpson 격자점과 가중치 (n은 홀수)."""
    h = (hi - lo) / (n - 1)
    xs = tuple(lo + h * i for i in range(n))
    ws = tuple(h / 3.0 * (1.0 if i in (0, n - 1) else (4.0 if i % 2 else 2.0))
               for i in range(n))
    return xs, ws


#: 첫 시점 이전의 "부분밀도" = 0에 놓인 점질량 (재귀를 한 줄로 통일해 준다)
_INITIAL_STATE = ((0.0,), (1.0,), (1.0,))


def _advance(state, upper_c: float, lower_c: float, sd: float, mu_step: float,
             two_sided: bool, npts: int):
    """한 시점 전진 — (상측 이탈확률, 하측 이탈확률, 다음 부분밀도).

    `lower_c`는 양측이면 하측 경계, 단측이면 (이탈이 아니라) 적분 절단점이다.
    """
    xs, ws, fs = state
    inv = 1.0 / sd
    # 가중치×밀도를 한 번만 곱해 두고 재사용한다 (합성곱이 이 함수의 병목이다)
    weighted = [w * f for w, f in zip(ws, fs)]
    up = math.fsum(v * (1.0 - norm_cdf((upper_c - x - mu_step) * inv))
                   for x, v in zip(xs, weighted))
    down = math.fsum(v * norm_cdf((lower_c - x - mu_step) * inv)
                     for x, v in zip(xs, weighted))
    nxs, nws = _simpson_grid(lower_c, upper_c, npts)
    # 정규밀도를 직접 전개한다 — 함수 호출과 fsum을 없애면 3배쯤 빨라지고,
    # 항이 모두 양수라 단순 누적의 오차는 n·eps(~1e-14) 수준이다.
    shifted = [x + mu_step for x in xs]
    scale = inv * _INV_SQRT_2PI
    exp_ = math.exp
    nfs = []
    for y in nxs:
        acc = 0.0
        for xm, v in zip(shifted, weighted):
            z = (y - xm) * inv
            if -38.0 < z < 38.0:          # 그 밖은 exp(-722) 미만이라 무시해도 된다
                acc += v * exp_(-0.5 * z * z)
        nfs.append(acc * scale)
    return up, (down if two_sided else 0.0), (nxs, nws, tuple(nfs))


def _lower_limit(upper_c: float, two_sided: bool, mu_total: float, sd_total: float) -> float:
    if two_sided:
        return -upper_c
    return min(-upper_c, mu_total - _TAIL_SIGMAS * sd_total)


def _crossing(bounds, timing, drift: float, two_sided: bool, npts: int = _GRID):
    """이동모수 `drift`에서 시점별 (상측, 하측) 이탈확률."""
    state = _INITIAL_STATE
    prev_t = 0.0
    ups: list[float] = []
    downs: list[float] = []
    for b, t in zip(bounds, timing):
        step = t - prev_t
        sd = math.sqrt(step)
        c = b * math.sqrt(t)
        lo = _lower_limit(c, two_sided, drift * t, math.sqrt(t))
        up, down, state = _advance(state, c, lo, sd, drift * step, two_sided, npts)
        ups.append(up)
        downs.append(down)
        prev_t = t
    return ups, downs


def _solve_bounds(timing, alpha: float, kind: str, two_sided: bool,
                  npts: int = _GRID) -> tuple[float, ...]:
    """귀무가설에서 누적 이탈확률 = α*(t_k)가 되도록 각 시점의 z 경계를 정한다."""
    state = _INITIAL_STATE
    bounds: list[float] = []
    prev_t = 0.0
    prev_spend = 0.0
    for t in timing:
        step = t - prev_t
        sd = math.sqrt(step)
        root_t = math.sqrt(t)
        spend = _spent(t, alpha, kind)
        target = max(spend - prev_spend, 0.0)
        xs, ws, fs = state

        def exit_at(b: float) -> float:
            c = b * root_t
            total = math.fsum(w * f * (1.0 - norm_cdf((c - x) / sd))
                              for x, w, f in zip(xs, ws, fs))
            if two_sided:
                total += math.fsum(w * f * norm_cdf((-c - x) / sd)
                                   for x, w, f in zip(xs, ws, fs))
            return total

        # exit_at은 b에 대해 감소 → 부호를 뒤집어 증가함수로 만들고 이분법
        b = bisect_increasing(lambda z: -exit_at(z), -target, 1e-6, _MAX_BOUND, tol=1e-12)
        b = min(max(b, 1e-6), _MAX_BOUND)
        bounds.append(b)
        c = b * root_t
        lo = _lower_limit(c, two_sided, 0.0, root_t)
        _up, _down, state = _advance(state, c, lo, sd, 0.0, two_sided, npts)
        prev_t = t
        prev_spend = spend
    return tuple(bounds)


def _drift_for_power(bounds, timing, target_power: float, two_sided: bool,
                     npts: int = _GRID) -> float:
    """목표 검정력을 주는 이동모수 μ (검정력 = 상측 경계를 넘을 확률)."""
    def power_at(mu: float) -> float:
        ups, _ = _crossing(bounds, timing, mu, two_sided, npts)
        return math.fsum(ups)

    return bisect_increasing(power_at, target_power, 0.0, 25.0, tol=1e-7, max_iter=60)


# --------------------------------------------------------------------------
# 공개 API
# --------------------------------------------------------------------------
def check_interim(interim) -> int:
    """--interim 검증 — **정보비율 튜플을 만들기 전에** 부른다.

    예전에는 상한 검사가 sequential_plan 안에만 있어서, --interim 2147483647 이
    먼저 20억 개짜리 튜플을 만들며 메모리를 다 먹었다.
    """
    if isinstance(interim, bool) or not isinstance(interim, int):
        raise PowerPlanError(f"--interim: 정수여야 합니다 (받은 값: {interim!r})")
    if interim < 1:
        raise PowerPlanError(f"--interim: 1 이상이어야 합니다 (받은 값: {interim})")
    if interim > MAX_INTERIM:
        raise PowerPlanError(
            f"--interim: 중간분석은 {MAX_INTERIM}회까지만 지원합니다 (받은 값: {interim:,}). "
            "실제 임상시험에서 중간분석은 보통 1~3회입니다"
        )
    return interim


def check_timing(interim: int, timing) -> tuple[float, ...]:
    """--timing 검증 → 정보비율 튜플 (마지막은 항상 1.0)."""
    interim = check_interim(interim)
    looks = interim + 1
    if timing is None:
        return tuple((i + 1) / looks for i in range(looks))
    values = list(timing)
    if len(values) == looks - 1:
        values.append(1.0)
    if len(values) != looks:
        raise PowerPlanError(
            f"--timing: 중간분석 {interim}회면 정보비율을 {looks - 1}개(중간분석 시점만) "
            f"또는 {looks}개(마지막은 1.0) 적어야 합니다 (받은 값 {len(values)}개)"
        )
    out = []
    for v in values:
        f = float(v)
        if not (math.isfinite(f) and 0.0 < f <= 1.0):
            raise PowerPlanError(
                f"--timing: 0보다 크고 1 이하인 정보비율이어야 합니다 (받은 값: {v!r})"
            )
        out.append(f)
    if abs(out[-1] - 1.0) > 1e-12:
        raise PowerPlanError(
            f"--timing: 마지막 값은 1.0(최종분석)이어야 합니다 (받은 값: {out[-1]:g})"
        )
    for a, b in zip(out, out[1:]):
        if b <= a:
            raise PowerPlanError(
                "--timing: 정보비율은 커지는 순서여야 합니다 (예: 0.5,1.0)"
            )
    return tuple(out)


@lru_cache(maxsize=128)
def sequential_plan(interim: int, alpha: float, sides: int, target_power: float,
                    spending: str = "obf", timing: tuple | None = None) -> dict:
    """중간분석 계획 — 경계, 팽창계수, 기대 표본수.

    Args:
        interim: **중간분석 횟수** (최종분석 제외). 총 분석 횟수 K = interim + 1.
        alpha: 전체 1종오류율 (다중비교 보정 후 값을 넣는다).
        sides: 2면 양측 대칭경계, 1이면 단측.
        target_power: 목표 검정력.
        spending: ``obf`` · ``pocock`` · ``linear``.
        timing: 정보비율 튜플 (없으면 균등 배치).
    """
    interim = check_interim(interim)
    if spending not in SPENDING_KINDS:
        raise PowerPlanError(
            f"--spending: {', '.join(SPENDING_KINDS)} 중 하나여야 합니다 (받은 값: {spending!r})"
        )
    if not (0.0 < alpha < 0.5):
        raise PowerPlanError(f"중간분석 계획: α가 유효 범위를 벗어났습니다 ({alpha:g})")
    if not (0.0 < target_power < 1.0):
        raise PowerPlanError(f"중간분석 계획: 검정력이 유효 범위를 벗어났습니다 ({target_power:g})")
    if target_power < alpha:
        raise PowerPlanError("중간분석 계획: 목표 검정력이 α보다 작습니다 — 입력을 확인하세요")

    two_sided = sides == 2
    fractions = check_timing(interim, timing)
    bounds = _solve_bounds(fractions, alpha, spending, two_sided)
    drift = _drift_for_power(bounds, fractions, target_power, two_sided)
    z_alpha = norm_ppf(1.0 - alpha / (2.0 if two_sided else 1.0))
    drift_fixed = z_alpha + norm_ppf(target_power)
    # 경계가 탐색 상한(_MAX_BOUND)에 걸리는 극단적 --timing에서는 수치적으로 1을
    # 아주 살짝 밑돌 수 있다. "중간분석을 넣었더니 표본수가 줄었다"는 결과는
    # 이론적으로 불가능하므로 1 미만으로는 내려가지 않게 한다.
    inflation = max(1.0, (drift / drift_fixed) ** 2) if drift_fixed > 0 else 1.0

    ups_h1, downs_h1 = _crossing(bounds, fractions, drift, two_sided)
    ups_h0, downs_h0 = _crossing(bounds, fractions, 0.0, two_sided)
    stop_h1 = [u + d for u, d in zip(ups_h1, downs_h1)]
    stop_h0 = [u + d for u, d in zip(ups_h0, downs_h0)]

    def expected_fraction(stop) -> float:
        """조기중단을 감안한 기대 정보비율 (마지막 시점은 반드시 도달)."""
        acc = 0.0
        remaining = 1.0
        for t, p in zip(fractions[:-1], stop[:-1]):
            acc += t * p
            remaining -= p
        return acc + fractions[-1] * max(remaining, 0.0)

    nominal_p = tuple((1.0 - norm_cdf(b)) * (2.0 if two_sided else 1.0) for b in bounds)
    cumulative = tuple(_spent(t, alpha, spending) for t in fractions)
    return {
        "interim": interim,
        "looks": interim + 1,
        "alpha": alpha,
        "sides": sides,
        "spending": spending,
        "spending_kr": spending_label(spending),
        "spending_en": spending_label(spending, korean=False),
        "timing": fractions,
        "bounds": bounds,
        "nominal_p": nominal_p,
        "cumulative_alpha": cumulative,
        "incremental_alpha": tuple(
            c - p for c, p in zip(cumulative, (0.0,) + cumulative[:-1])),
        "target_power": target_power,
        "drift": drift,
        "drift_fixed": drift_fixed,
        "inflation": inflation,
        "stop_prob_h1": tuple(stop_h1),
        "stop_prob_h0": tuple(stop_h0),
        "cumulative_stop_h1": tuple(
            math.fsum(stop_h1[: i + 1]) for i in range(len(stop_h1))),
        "expected_fraction_h1": expected_fraction(stop_h1),
        "expected_fraction_h0": expected_fraction(stop_h0),
        "achieved_alpha": math.fsum(stop_h0),
        "achieved_power": math.fsum(ups_h1),
    }


def drift_from_fixed_power(fixed_power: float, alpha: float, sides: int) -> float:
    """고정설계 검정력 → 그것을 만드는 이동모수 μ.

    양측검정의 검정력은 Φ(μ − z) + Φ(−μ − z)이므로 **아래쪽 꼬리까지 포함해서**
    역산해야 한다. 위쪽만 보고 μ = z + Φ⁻¹(power)로 풀면 검정력이 낮은 구간에서
    μ를 과대추정한다(검정력 = α일 때 μ가 0이 아니라 양수로 나온다).
    """
    z_alpha = norm_ppf(1.0 - alpha / (2.0 if sides == 2 else 1.0))
    p = min(max(float(fixed_power), 0.0), 1.0 - 1e-15)
    if sides != 2:
        return z_alpha + norm_ppf(p) if p > 0.0 else -math.inf
    # 양측: 검정력은 μ ≥ 0에서 단조증가하고 μ=0에서 정확히 α다
    if p <= alpha:
        return 0.0

    def two_sided_power(mu: float) -> float:
        return norm_cdf(mu - z_alpha) + norm_cdf(-mu - z_alpha)

    return bisect_increasing(two_sided_power, p, 0.0, 40.0, tol=1e-12)


def power_from_fixed(seq: dict, fixed_power: float) -> float:
    """같은 표본수의 **고정설계 검정력**을 군차별설계 검정력으로 옮긴다.

    고정설계 검정력에서 이동모수 μ를 역산한 뒤, 그 μ에서 상측 경계를 넘을
    확률(= 군차별설계의 검정력)을 계산한다.
    """
    mu = drift_from_fixed_power(fixed_power, seq["alpha"], seq["sides"])
    if not math.isfinite(mu) or mu <= 0.0:
        # 효과가 없는 자리 — 상측 경계 통과확률은 α/양측수다
        return seq["alpha"] / (2.0 if seq["sides"] == 2 else 1.0)
    ups, _ = _crossing(seq["bounds"], seq["timing"], mu, seq["sides"] == 2)
    return min(1.0, math.fsum(ups))


def sequential_notes(seq: dict) -> list[str]:
    """프로토콜에 함께 적어야 하는 가정·한계."""
    looks = seq["looks"]
    return [
        f"중간분석 {seq['interim']}회 + 최종분석 1회 (총 {looks}회)를 "
        f"{seq['spending_kr']} α 소비함수로 계획했습니다. 전체 1종오류율은 "
        f"{seq['alpha']:.4g}로 유지되며, 각 시점의 명목 유의수준은 아래 경계표와 같습니다.",
        f"최대 표본수는 고정설계의 {seq['inflation']:.4f}배입니다(팽창계수). 대신 조기중단 "
        f"덕에 **기대 정보량**은 대립가설에서 최대치의 "
        f"{seq['expected_fraction_h1']:.1%}, 귀무가설에서 "
        f"{seq['expected_fraction_h0']:.1%} 수준입니다 (정보량이 사건 수인 설계에서는 "
        "줄어드는 것이 등록 인원이 아니라 추적기간입니다 — 모집 목표는 최대 표본수 "
        "그대로 잡으세요).",
        "정보비율(--timing)은 계획일 뿐이며, 실제 중간분석 시점이 어긋나도 α 소비함수는 "
        "그 시점의 실제 정보량으로 다시 계산합니다(Lan–DeMets의 장점). 프로토콜에는 "
        "'실제 정보량에 따라 소비함수로 경계를 재계산한다'고 적으세요.",
        "이 계산은 **효능 조기중단(상측 경계)만** 반영합니다. 무익성 중단(futility) 경계를 "
        "함께 쓰면 검정력이 조금 내려가므로 별도 계획이 필요합니다.",
        "경계·팽창계수는 Z 통계량(정규근사) 기반입니다 — gsDesign·EAST 등 표준 도구와 "
        "같은 방식이며, 고정설계 표본수 자체는 이 툴의 정확 계산(비중심 t/F)을 씁니다. "
        "다만 실제 분석에서 **t 통계량**을 이 Z 경계와 비교하면 자유도가 작은 초기 "
        "중간분석에서 1종오류가 약간 새어 나옵니다(모의실험 기준 0.05 → 0.052 수준). "
        "규제 제출용이라면 중간분석 경계를 t 분위수로 조정하거나 첫 중간분석을 "
        "정보비율 0.4 이후로 잡으세요.",
    ]


def sequential_references() -> list[str]:
    return [
        "Lan KKG, DeMets DL. Discrete sequential boundaries for clinical trials. "
        "Biometrika. 1983;70:659-663.",
        "O'Brien PC, Fleming TR. A multiple testing procedure for clinical trials. "
        "Biometrics. 1979;35:549-556.",
        "Jennison C, Turnbull BW. Group Sequential Methods with Applications to "
        "Clinical Trials. Chapman & Hall/CRC; 2000.",
    ]

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

from .korean import josa
from .special import bisect_increasing, norm_cdf, norm_ppf
from .validate import PowerPlanError

__all__ = ["SPENDING_KINDS", "FUTILITY_KINDS", "MAX_INTERIM", "sequential_plan",
           "spending_label", "futility_label", "check_interim", "check_timing",
           "power_from_fixed"]

#: 지원하는 α 소비함수
SPENDING_KINDS = ("obf", "pocock", "linear")
#: 지원하는 β(무익성) 소비함수 — α 소비함수와 같은 함수형을 쓴다
FUTILITY_KINDS = ("obf", "pocock", "linear")
#: 중간분석 횟수 상한 (이보다 많은 계획은 현실에 없다)
MAX_INTERIM = 10
#: 부분밀도 적분 격자점 수 (Simpson — 홀수여야 한다)
_GRID = 81
#: 단측 설계에서 아래쪽을 잘라내는 지점 (표준편차 배수). 이 바깥 질량은 < 1e-23
_TAIL_SIGMAS = 10.0
#: z 경계 탐색 상한 (명목 p ~ 1e-88)
_MAX_BOUND = 20.0
#: 허용하는 최소 정보비율 간격. 이보다 촘촘하면 증분의 표준편차(√Δt)가 Simpson
#: 격자 간격보다 작아져 합성곱이 앨리어싱을 일으킨다 — 확률이 1을 넘고 표본수가
#: 고정설계보다 작아지는 결과가 나온다. 실제 시험에서 정보량 1% 차이의 중간분석은
#: 의미가 없으므로 계산을 시도하는 대신 거절한다.
_MIN_TIMING_STEP = 0.01
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


_FUTILITY_KR = {
    "obf": "O'Brien–Fleming 형",
    "pocock": "Pocock 형",
    "linear": "선형(균등)",
}
_FUTILITY_EN = {
    "obf": "O'Brien-Fleming-type beta spending",
    "pocock": "Pocock-type beta spending",
    "linear": "linear beta spending",
}


def spending_label(kind: str, korean: bool = True) -> str:
    return (_SPENDING_KR if korean else _SPENDING_EN).get(kind, kind)


def futility_label(kind: str, korean: bool = True) -> str:
    return (_FUTILITY_KR if korean else _FUTILITY_EN).get(kind, kind)


def check_futility(kind):
    """--futility 검증 → 소비함수 이름 또는 None."""
    if kind is None:
        return None
    if not isinstance(kind, str) or kind not in FUTILITY_KINDS:
        raise PowerPlanError(
            f"--futility: {', '.join(FUTILITY_KINDS)} 중 하나여야 합니다 (받은 값: {kind!r})"
        )
    return kind


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
             npts: int):
    """한 시점 전진 — (상측 이탈확률, 하측 이탈확률, 다음 부분밀도).

    하측 확률이 **실제 이탈**인지(양측 경계·무익성 경계) 단순한 수치 절단인지는
    호출부가 판단한다 — 여기서는 언제나 그대로 돌려준다.
    """
    xs, ws, fs = state
    if not (upper_c > lower_c):
        # 마지막 시점에서 무익성 경계와 효능 경계가 만나면 계속 구간이 비어 있다.
        # 다음 시점이 없으므로 부분밀도는 0으로 두고 하측에 남은 질량을 모두 넘긴다.
        weighted = [w * f for w, f in zip(ws, fs)]
        inv0 = 1.0 / sd
        up0 = math.fsum(v * (1.0 - norm_cdf((upper_c - x - mu_step) * inv0))
                        for x, v in zip(xs, weighted))
        total = math.fsum(weighted)
        return up0, max(total - up0, 0.0), ((lower_c,), (0.0,), (0.0,))
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
    return up, down, (nxs, nws, tuple(nfs))


def _lower_limit(upper_c: float, two_sided: bool, mu_total: float, sd_total: float) -> float:
    if two_sided:
        return -upper_c
    return min(-upper_c, mu_total - _TAIL_SIGMAS * sd_total)


def _lower_edge(upper_c: float, root_t: float, two_sided: bool, mu_total: float,
                fut_z) -> tuple[float, bool]:
    """계속 구간의 하단과 "그 아래로 나가면 중단인가" 여부.

    무익성 경계가 있으면 그 경계가 하단이고 넘어가면 중단이다. 없으면 예전 규칙:
    양측은 −c(해를 뜻하는 효능 경계, 중단), 단측은 수치 절단점(중단 아님).
    """
    if fut_z is None:
        return _lower_limit(upper_c, two_sided, mu_total, root_t), two_sided
    lo = fut_z * root_t
    # 아주 낮은 무익성 경계는 수치 격자를 헛되이 넓히기만 한다. 질량이 1e-23 미만인
    # 지점에서 잘라도 확률은 그대로다.
    trunc = mu_total - _TAIL_SIGMAS * root_t
    return min(max(lo, trunc), upper_c), True


def _crossing(bounds, timing, drift: float, two_sided: bool, futility=None,
              npts: int = _GRID):
    """이동모수 `drift`에서 시점별 (상측, 하측) 이탈확률.

    `futility`가 주어지면 하측은 무익성 중단확률이 된다(효능 기각이 아니다).
    """
    state = _INITIAL_STATE
    prev_t = 0.0
    ups: list[float] = []
    downs: list[float] = []
    for i, (b, t) in enumerate(zip(bounds, timing)):
        step = t - prev_t
        sd = math.sqrt(step)
        root_t = math.sqrt(t)
        c = b * root_t
        lo, counts = _lower_edge(c, root_t, two_sided, drift * t,
                                 futility[i] if futility is not None else None)
        up, down, state = _advance(state, c, lo, sd, drift * step, npts)
        ups.append(up)
        downs.append(down if counts else 0.0)
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
        _up, _down, state = _advance(state, c, lo, sd, 0.0, npts)
        prev_t = t
        prev_spend = spend
    return tuple(bounds)


def _futility_bounds(eff_bounds, timing, drift: float, two_sided: bool, kind: str,
                     beta: float, npts: int = _GRID) -> tuple[float, ...]:
    """β 소비함수로 무익성 경계를 정한다 (대립가설 `drift`에서).

    시점 k의 경계 b_k 는 "μ = drift 인데도 그 시점에 무익성으로 멈출 누적확률"이
    β*(t_k)가 되도록 잡는다 — α 소비함수의 거울상이다. 마지막 시점은 효능 경계와
    만나므로 b_K = a_K 다(넘지 못하면 곧 실패).

    **누적 기준은 이미 쓴 양(실제 중단확률)** 이다. 경계가 −a_k(양측의 해 방향
    경계)에 걸려 계획만큼 못 쓴 시점이 있으면 그 몫은 다음 시점으로 넘어간다.
    """
    state = _INITIAL_STATE
    prev_t = 0.0
    spent = 0.0
    last = len(timing) - 1
    out: list[float] = []
    for i, (a, t) in enumerate(zip(eff_bounds, timing)):
        step = t - prev_t
        sd = math.sqrt(step)
        root_t = math.sqrt(t)
        c = a * root_t
        mu_step = drift * step
        xs, ws, fs = state
        if i == last:
            b = a
        else:
            target = max(_spent(t, beta, kind) - spent, 0.0)
            # 양측설계에서는 −a_k 아래가 이미 "해(harm)" 효능 경계다. 그보다 낮은
            # 무익성 경계는 아무것도 더하지 않으므로 거기서 멈춘다.
            floor_z = -a if two_sided else -_MAX_BOUND
            weighted = [w * f for w, f in zip(ws, fs)]

            def below(z: float) -> float:
                s = z * root_t
                return math.fsum(v * norm_cdf((s - x - mu_step) / sd)
                                 for x, v in zip(xs, weighted))

            b = bisect_increasing(below, target, floor_z, a, tol=1e-12)
            b = min(max(b, floor_z), a)
        out.append(b)
        lo, _counts = _lower_edge(c, root_t, two_sided, drift * t, b)
        _up, down, state = _advance(state, c, lo, sd, mu_step, npts)
        spent += down
        prev_t = t
    return tuple(out)


def conditional_power(final_bound: float, t: float, z: float, drift: float) -> float:
    """중간분석에서 Z_k = z 를 봤을 때 **최종분석에서 유의해질 확률**.

    DSMB가 실제로 이야기하는 숫자다 — "Z 경계 0.38"보다 "이대로 가면 성공확률
    12%"가 훨씬 잘 읽힌다. 남은 정보량 1−t 구간의 증분이 평균 μ(1−t),
    분산 1−t 인 정규라는 사실만 쓴다.

        CP = 1 − Φ( (a_K − z√t − μ(1−t)) / √(1−t) )

    `drift`에 설계 대립가설의 μ를 넣으면 "가정이 맞다면", 관측 추세
    ẑ = z/√t 를 넣으면 "지금 추세대로라면"의 조건부 검정력이다.

    **중간에 남은 다른 중간분석에서 멈출 가능성은 무시한다** — 조건부 검정력의
    표준 정의가 그렇고(최종 경계만 본다), 그래서 약간 낙관적이다.
    """
    if t >= 1.0:
        return 1.0 if z >= final_bound else 0.0
    rest = 1.0 - t
    return 1.0 - norm_cdf((final_bound - z * math.sqrt(t) - drift * rest)
                          / math.sqrt(rest))


def _drift_for_power(bounds, timing, target_power: float, two_sided: bool,
                     futility: str | None = None, npts: int = _GRID):
    """목표 검정력을 주는 이동모수 μ (검정력 = 상측 경계를 넘을 확률).

    무익성 경계가 있으면 경계 자체가 μ에 의존하므로 매 반복에서 다시 계산한다.
    돌려주는 값은 (μ, 무익성 경계 튜플 또는 None).
    """
    beta = 1.0 - target_power

    def power_at(mu: float) -> float:
        fut = (_futility_bounds(bounds, timing, mu, two_sided, futility, beta, npts)
               if futility else None)
        ups, _ = _crossing(bounds, timing, mu, two_sided, fut, npts)
        return math.fsum(ups)

    mu = bisect_increasing(power_at, target_power, 0.0, 25.0, tol=1e-7, max_iter=60)
    fut = (_futility_bounds(bounds, timing, mu, two_sided, futility, beta, npts)
           if futility else None)
    return mu, fut


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
    # 첫 시점도 0에서부터의 간격을 본다
    for a, b in zip((0.0,) + tuple(out), out):
        if b - a < _MIN_TIMING_STEP:
            raise PowerPlanError(
                f"--timing: 정보비율 간격이 너무 촘촘합니다 ({a:g} → {b:g}). "
                f"분석 사이에 최소 {_MIN_TIMING_STEP:.0%}의 정보량 차이가 필요합니다 "
                "— 그보다 촘촘한 중간분석은 실제로 의미가 없고, 수치적으로도 "
                "믿을 수 없는 경계가 나옵니다"
            )
    return tuple(out)


@lru_cache(maxsize=128)
def sequential_plan(interim: int, alpha: float, sides: int, target_power: float,
                    spending: str = "obf", timing: tuple | None = None,
                    futility: str | None = None) -> dict:
    """중간분석 계획 — 경계, 팽창계수, 기대 표본수.

    Args:
        interim: **중간분석 횟수** (최종분석 제외). 총 분석 횟수 K = interim + 1.
        alpha: 전체 1종오류율 (다중비교 보정 후 값을 넣는다).
        sides: 2면 양측 대칭경계, 1이면 단측.
        target_power: 목표 검정력.
        spending: ``obf`` · ``pocock`` · ``linear``.
        timing: 정보비율 튜플 (없으면 균등 배치).
        futility: β 소비함수 이름을 주면 **비구속적 무익성 경계**를 함께 계산한다.
            효능 경계는 무익성을 무시하고 정하므로(비구속), DSMB가 무익성 경계를
            넘고도 계속 진행하기로 해도 전체 α는 그대로 유지된다. 대신 무익성으로
            멈출 확률만큼 검정력이 깎이므로 목표 검정력을 되찾도록 표본수를
            더 늘린다(팽창계수가 커진다).
    """
    interim = check_interim(interim)
    futility = check_futility(futility)
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
    drift, fut_bounds = _drift_for_power(bounds, fractions, target_power, two_sided,
                                         futility)
    z_alpha = norm_ppf(1.0 - alpha / (2.0 if two_sided else 1.0))
    drift_fixed = z_alpha + norm_ppf(target_power)
    # 경계가 탐색 상한(_MAX_BOUND)에 걸리는 극단적 --timing에서는 수치적으로 1을
    # 아주 살짝 밑돌 수 있다. "중간분석을 넣었더니 표본수가 줄었다"는 결과는
    # 이론적으로 불가능하므로 1 미만으로는 내려가지 않게 한다.
    inflation = max(1.0, (drift / drift_fixed) ** 2) if drift_fixed > 0 else 1.0

    ups_h1, downs_h1 = _crossing(bounds, fractions, drift, two_sided, fut_bounds)
    ups_h0, downs_h0 = _crossing(bounds, fractions, 0.0, two_sided, fut_bounds)
    stop_h1 = [u + d for u, d in zip(ups_h1, downs_h1)]
    stop_h0 = [u + d for u, d in zip(ups_h0, downs_h0)]
    # 비구속(non-binding) 보증: 무익성 경계를 **무시하고** 계속 갔을 때의 1종오류율.
    # 효능 경계를 무익성과 무관하게 정했으므로 이 값이 α를 넘지 않아야 한다.
    ups_free, downs_free = (_crossing(bounds, fractions, 0.0, two_sided)
                            if fut_bounds is not None else (ups_h0, downs_h0))
    alpha_nonbinding = math.fsum(ups_free) + math.fsum(downs_free)

    for label, probs in (("H1", stop_h1), ("H0", stop_h0)):
        total = math.fsum(probs)
        if not (0.0 <= total <= 1.0 + 1e-6) or any(p < -1e-9 for p in probs):
            raise PowerPlanError(  # pragma: no cover - _MIN_TIMING_STEP이 막는다
                f"중간분석 계획: 수치적으로 불안정한 설정입니다 ({label} 중단확률 합 "
                f"{total:.4g}). --timing 간격을 넓히거나 --interim을 줄이세요"
            )

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
    beta = 1.0 - target_power
    futility_extra = {}
    if fut_bounds is not None:
        # **실제 검정력 손실**을 따로 잰다. β 소비량(= 중간에 무익성으로 멈출 확률)을
        # 검정력 손실이라고 부르면 4배쯤 과장된다 — 그렇게 멈춘 시험 대부분은
        # 어차피 최종분석에서도 실패했을 것이기 때문이다. 정직한 수치는 "효능만
        # 보는 설계의 표본수를 그대로 두고 무익성 규칙만 얹었을 때 남는 검정력"이다.
        drift_eff_only, _ = _drift_for_power(bounds, fractions, target_power, two_sided)
        ups_same_n, _ = _crossing(bounds, fractions, drift_eff_only, two_sided, fut_bounds)
        power_same_n = math.fsum(ups_same_n)
        futility_extra = {
            "futility": futility,
            "futility_kr": futility_label(futility),
            "futility_en": futility_label(futility, korean=False),
            "futility_bounds": fut_bounds,
            "futility_nominal_p": tuple(1.0 - norm_cdf(b) for b in fut_bounds),
            "beta": beta,
            "cumulative_beta": tuple(_spent(t, beta, futility) for t in fractions[:-1])
                               + (beta,),
            "futility_stop_h1": tuple(downs_h1),
            "futility_stop_h0": tuple(downs_h0),
            "cumulative_futility_h1": tuple(
                math.fsum(downs_h1[: i + 1]) for i in range(len(downs_h1))),
            "cumulative_futility_h0": tuple(
                math.fsum(downs_h0[: i + 1]) for i in range(len(downs_h0))),
            # 무익성 경계를 실제로 지키면 효능 방향 1종오류율은 이만큼으로 내려간다
            # 무익성 경계에서의 조건부 검정력 — DSMB가 실제로 읽는 숫자
            "cp_at_futility_alt": tuple(
                conditional_power(bounds[-1], t, b, drift)
                for t, b in zip(fractions[:-1], fut_bounds[:-1])),
            "cp_at_futility_trend": tuple(
                conditional_power(bounds[-1], t, b, b / math.sqrt(t))
                for t, b in zip(fractions[:-1], fut_bounds[:-1])),
            "alpha_if_honored": math.fsum(ups_h0),
            #: 비교 대상 — 무익성이 없을 때의 **효능 방향**(상측) 1종오류율
            "alpha_upper_nominal": math.fsum(ups_free),
            "alpha_nonbinding": alpha_nonbinding,
            "binding": False,
            #: 무익성 경계가 해(harm) 방향 효능 경계에 딱 걸린 시점 (추가 규칙 없음)
            "futility_at_harm_bound": tuple(
                two_sided and abs(f + e) < 1e-6
                for f, e in zip(fut_bounds[:-1], bounds[:-1])) + (False,),
            #: 중간분석에서 소비한 2종오류 β* (= 무익성으로 멈출 확률). 검정력 손실이 아니다.
            "beta_spent_interim": math.fsum(downs_h1[:-1]),
            #: 효능만 보는 설계의 표본수를 그대로 두고 무익성 규칙만 얹었을 때의 검정력
            "power_same_n": power_same_n,
            #: 그래서 실제로 깎이는 검정력 (이만큼을 되찾으려고 표본수를 늘린다)
            "power_loss": max(target_power - power_same_n, 0.0),
        }
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
        "achieved_alpha": alpha_nonbinding,
        "achieved_power": math.fsum(ups_h1),
        "futility": None,
        "futility_bounds": None,
        **futility_extra,
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
    fut = seq.get("futility_bounds")
    if not math.isfinite(mu) or mu <= 0.0:
        if fut is None:
            # 효과가 없는 자리 — 상측 경계 통과확률은 α/양측수다
            return seq["alpha"] / (2.0 if seq["sides"] == 2 else 1.0)
        # 무익성 경계가 있으면 μ=0에서도 그 경계가 먼저 멈춰 세우므로 α/2보다 작다
        mu = 0.0
    ups, _ = _crossing(seq["bounds"], seq["timing"], mu, seq["sides"] == 2, fut)
    return min(1.0, math.fsum(ups))


def sequential_notes(seq: dict) -> list[str]:
    """프로토콜에 함께 적어야 하는 가정·한계."""
    looks = seq["looks"]
    if seq.get("futility_bounds") is not None:
        futility_note = (
            f"무익성(futility) 중단 경계를 {seq['futility_kr']} β 소비함수로 함께 "
            f"넣었습니다(**비구속적**). 효능 경계는 무익성을 무시하고 정했으므로, "
            f"DSMB가 무익성 경계를 넘고도 계속 진행하기로 해도 전체 1종오류율은 "
            f"{seq['alpha']:.4g}를 넘지 않습니다 — 규제기관이 요구하는 성질입니다. "
            f"반대로 경계를 그대로 지키면 **효능 방향** 1종오류율이 "
            f"{seq['alpha_upper_nominal']:.4g} → "
            + josa(f"{seq['alpha_if_honored']:.4g}", "으로", "로")
            + f" 내려갑니다(양측 α {seq['alpha']:.4g}의 절반과 비교한 값입니다 — "
            f"0.05와 직접 비교하면 안 됩니다). "
            f"효과가 정말 없을 때 중간에 멈출 확률은 "
            f"{seq['cumulative_futility_h0'][-2]:.1%}, 효과가 있는데 잘못 멈출 확률은 "
            f"{seq['cumulative_futility_h1'][-2]:.1%}(= 중간분석에서 소비한 2종오류 β*)입니다. "
            f"이 확률이 곧 검정력 손실은 아닙니다 — 그렇게 멈춘 시험 대부분은 어차피 "
            f"최종분석에서도 실패했을 것이기 때문입니다. **실제 검정력 손실**은 "
            f"{seq['target_power']:.1%} → {seq['power_same_n']:.1%} "
            f"({seq['power_loss'] * 100:.1f}%p)이며, 이를 되찾도록 표본수를 늘렸습니다"
            f"(팽창계수 {seq['inflation']:.4f}에 포함).")
        if seq["sides"] == 2:
            futility_note += (
                " 양측설계에서 무익성 경계는 **해(harm) 방향 효능 경계를 덮습니다** — "
                f"Z가 −{seq['bounds'][0]:.3f} 아래인 경우(통계적으로 유의한 해)도 위 "
                "무익성 중단확률에 함께 세어져 있습니다. DSMB에는 '무익'과 '해'를 "
                "구분해 보고하도록 헌장에 적으세요.")
    else:
        futility_note = (
            "이 계산은 **효능 조기중단(상측 경계)만** 반영합니다. 무익성 중단(futility) "
            "경계를 함께 쓰면 검정력이 조금 내려가므로 `--futility obf`처럼 함께 "
            "계획하세요.")
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
        futility_note,
        "경계·팽창계수는 Z 통계량(정규근사) 기반입니다 — gsDesign·EAST 등 표준 도구와 "
        "같은 방식이며, 고정설계 표본수 자체는 이 툴의 정확 계산(비중심 t/F)을 씁니다. "
        "다만 실제 분석에서 **t 통계량**을 이 Z 경계와 비교하면 자유도가 작은 초기 "
        "중간분석에서 1종오류가 약간 새어 나옵니다(모의실험 기준 0.05 → 0.052 수준). "
        "규제 제출용이라면 중간분석 경계를 t 분위수로 조정하거나 첫 중간분석을 "
        "정보비율 0.4 이후로 잡으세요.",
    ]


def sequential_references(seq: dict | None = None) -> list[str]:
    refs = [
        "Lan KKG, DeMets DL. Discrete sequential boundaries for clinical trials. "
        "Biometrika. 1983;70:659-663.",
        "O'Brien PC, Fleming TR. A multiple testing procedure for clinical trials. "
        "Biometrics. 1979;35:549-556.",
        "Jennison C, Turnbull BW. Group Sequential Methods with Applications to "
        "Clinical Trials. Chapman & Hall/CRC; 2000.",
    ]
    if seq is not None and seq.get("futility_bounds") is not None:
        refs += [
            "Pampallona S, Tsiatis AA, Kim K. Interim monitoring of group sequential "
            "trials using spending functions for the type I and type II error "
            "probabilities. Drug Information Journal. 2001;35:1113-1121. (β 소비함수의 표준 참고문헌. 다만 이 툴이 쓰는 β 소비함수의 **함수형**은 Lan–DeMets의 OBF/Pocock/선형 형태를 β에 그대로 적용한 것이며, 이 논문의 검정력족 경계와는 값이 다릅니다)",
            "US FDA. Adaptive Designs for Clinical Trials of Drugs and Biologics: "
            "Guidance for Industry. 2019. (구속적 무익성 경계는 규칙을 실제로 지킬 때만 α를 통제한다는 점을 지적)",
        ]
    return refs

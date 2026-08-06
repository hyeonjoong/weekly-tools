"""폴리코릭(polychoric) 상관 — 순서형(리커트) 문항용 상관행렬(scipy 불필요).

리커트 문항을 연속형으로 보고 계산하는 피어슨 상관은, 관측 범주가 잠재 정규변수를
'구간 절단'한 것임을 무시해 상관을 하향편의(attenuation)시킨다. 폴리코릭 상관은 두 문항이
각각 잠재 이변량 정규분포를 절단한 결과라고 보고, 절단점(threshold)과 잠재 상관 ρ를 추정한다.
임상 자가진단(예: 1~5 리커트)의 요인분석에서 방법 리뷰어가 흔히 요구하는 표준 절차다.

구현: 2단계 추정. (1) 각 문항의 절단점을 주변 누적비율의 정규분위수(probit)로 고정,
(2) 절단점을 고정한 채 다항우도를 최대화하는 ρ를 1차원 최적화(황금분할)로 찾는다.
이변량 정규 CDF는 Genz(2004) 알고리즘(Gauss-Legendre)으로 계산해 scipy 없이도 정밀하다.
"""
from __future__ import annotations

import math
from typing import List, Tuple

import numpy as np

_SQRT2 = math.sqrt(2.0)
_TWO_PI = 2.0 * math.pi
_INV_GOLDEN = (math.sqrt(5.0) - 1.0) / 2.0   # 0.618...


def norm_cdf(x: float) -> float:
    """표준정규 누적분포 Φ(x) = ½ erfc(−x/√2). (stdlib erfc, scipy 불필요)."""
    return 0.5 * math.erfc(-x / _SQRT2)


# Acklam의 역정규(probit) 유리함수 근사 — 절대오차 ~1.15e-9(꼬리 포함).
_A = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
      1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
_B = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
      6.680131188771972e+01, -1.328068155288572e+01]
_C = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
      -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
_D = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
      3.754408661907416e+00]


def norm_ppf(p: float) -> float:
    """표준정규 분위수 Φ⁻¹(p). p→0/1 극단은 ∓∞ 반환(절단점 경계 처리용)."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
               ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
               ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    q = p - 0.5
    r = q * q
    return (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / \
           (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1.0)


# Genz bvnu용 Gauss-Legendre 노드/가중치(|r| 구간별 정밀도 상향).
_GL = {
    3: (np.array([0.9324695142031522, 0.6612093864662647, 0.2386191860831970]),
        np.array([0.1713244923791705, 0.3607615730481384, 0.4679139345726904])),
    6: (np.array([0.9815606342467191, 0.9041172563704750, 0.7699026741943050,
                  0.5873179542866171, 0.3678314989981802, 0.1252334085114692]),
        np.array([0.04717533638651177, 0.1069393259953183, 0.1600783285433464,
                  0.2031674267230659, 0.2334925365383547, 0.2491470458134029])),
    10: (np.array([0.9931285991850949, 0.9639719272779138, 0.9122344282513259,
                   0.8391169718222188, 0.7463319064601508, 0.6360536807265150,
                   0.5108670019508271, 0.3737060887154196, 0.2277858511416451,
                   0.07652652113349733]),
         np.array([0.01761400713915212, 0.04060142980038694, 0.06267204833410906,
                   0.08327674157670475, 0.1019301198172404, 0.1181945319615184,
                   0.1316886384491766, 0.1420961093183821, 0.1491729864726037,
                   0.1527533871307259])),
}


def _bvnu(dh: float, dk: float, r: float) -> float:
    """표준 이변량 정규의 상측 확률 P(X≥dh, Y≥dk), 상관 r (Genz 2004)."""
    if math.isinf(dh) or math.isinf(dk):
        if dh == -math.inf and dk == -math.inf:
            return 1.0
        if dh == -math.inf:
            return norm_cdf(-dk)
        if dk == -math.inf:
            return norm_cdf(-dh)
        return 0.0   # +inf 하한 → 0
    if r == 0.0:
        return norm_cdf(-dh) * norm_cdf(-dk)
    ar = abs(r)
    lg = 3 if ar < 0.3 else (6 if ar < 0.75 else 10)
    x, w = _GL[lg]
    xs = np.concatenate([1.0 - x, 1.0 + x])
    ws = np.concatenate([w, w])
    h, k = dh, dk
    hk = h * k
    hs = (h * h + k * k) / 2.0
    asr = math.asin(r) / 2.0
    sn = np.sin(asr * xs)
    bvn = float(np.dot(np.exp((sn * hk - hs) / (1.0 - sn ** 2)), ws))
    bvn = bvn * asr / _TWO_PI + norm_cdf(-h) * norm_cdf(-k)
    return min(1.0, max(0.0, bvn))


def bvn_cdf(a: float, b: float, r: float) -> float:
    """표준 이변량 정규 CDF Φ₂(a,b;r) = P(X≤a, Y≤b). 무한 경계도 처리."""
    if a == -math.inf or b == -math.inf:
        return 0.0
    if a == math.inf:
        return 1.0 if b == math.inf else norm_cdf(b)
    if b == math.inf:
        return norm_cdf(a)
    return _bvnu(-a, -b, r)


def _thresholds(counts: np.ndarray) -> List[float]:
    """범주 도수 → 절단점 [-inf, a1, ..., a_{c-1}, +inf] (누적비율의 probit)."""
    n = counts.sum()
    cum = np.cumsum(counts) / n
    th = [-math.inf]
    for m in range(len(counts) - 1):
        th.append(norm_ppf(float(cum[m])))
    th.append(math.inf)
    return th


def _cell_prob(a_lo, a_hi, b_lo, b_hi, r):
    """절단점 구간 [a_lo,a_hi]×[b_lo,b_hi]에 잠재쌍이 들 확률(포함배제)."""
    return (bvn_cdf(a_hi, b_hi, r) - bvn_cdf(a_lo, b_hi, r)
            - bvn_cdf(a_hi, b_lo, r) + bvn_cdf(a_lo, b_lo, r))


def _neg_loglik(table, tha, thb, r):
    ci, cj = table.shape
    ll = 0.0
    for s in range(ci):
        for t in range(cj):
            c = table[s, t]
            if c == 0:
                continue
            pi = _cell_prob(tha[s], tha[s + 1], thb[t], thb[t + 1], r)
            ll += c * math.log(max(pi, 1e-12))
    return -ll


def _golden_min(f, lo, hi, tol=1e-6, max_iter=200):
    """[lo,hi]에서 단봉 함수 f 최소화(황금분할). scipy 불필요."""
    c = hi - _INV_GOLDEN * (hi - lo)
    d = lo + _INV_GOLDEN * (hi - lo)
    fc, fd = f(c), f(d)
    for _ in range(max_iter):
        if abs(hi - lo) < tol:
            break
        if fc < fd:
            hi, d, fd = d, c, fc
            c = hi - _INV_GOLDEN * (hi - lo)
            fc = f(c)
        else:
            lo, c, fc = c, d, fd
            d = lo + _INV_GOLDEN * (hi - lo)
            fd = f(d)
    return (lo + hi) / 2.0


def polychoric_corr(xi: np.ndarray, xj: np.ndarray) -> float:
    """두 순서형 벡터의 폴리코릭 상관 ρ 추정(2단계 ML). 결측 없는 정수형 가정."""
    ui = np.unique(xi)
    uj = np.unique(xj)
    if ui.size < 2 or uj.size < 2:
        return float("nan")
    mi = {v: k for k, v in enumerate(ui)}
    mj = {v: k for k, v in enumerate(uj)}
    ci, cj = ui.size, uj.size
    table = np.zeros((ci, cj))
    for a, b in zip(xi, xj):
        table[mi[a], mj[b]] += 1
    tha = _thresholds(table.sum(axis=1))
    thb = _thresholds(table.sum(axis=0))
    # 시작 구간을 넓게 잡고 황금분할(음의 로그우도는 ρ에 대해 사실상 단봉).
    r = _golden_min(lambda rho: _neg_loglik(table, tha, thb, rho),
                    -0.999, 0.999)
    return float(max(-1.0, min(1.0, r)))


def polychoric_matrix(x: np.ndarray, pairwise: bool = False):
    """순서형 응답행렬 (n,p)의 폴리코릭 상관행렬 (p,p). 대각 1, 대칭.

    pairwise=True 면 결측(NaN)이 섞인 원자료를 받아 **문항쌍마다 둘 다 응답한 행**으로
    ρ를 추정하고, (r, counts) 튜플을 돌려준다(counts는 쌍별 유효 표본 수).
    """
    p = x.shape[1]
    r = np.eye(p)
    # int64로 캐스팅하기 전에 범위를 확인한다. 1e19 같은 값은 조용히 포화(saturate)해
    # 모든 응답이 INT64_MAX라는 단일 '범주'가 되고, 그러면 ρ가 추정 불가가 되어
    # (예전 코드에서는) 0.0으로 메워졌다 — '무상관'이라는 강한 거짓 주장이었다.
    finite_vals = x[np.isfinite(x)]
    if finite_vals.size and float(np.max(np.abs(finite_vals))) > 2 ** 53:
        raise ValueError(
            "polychoric 상관은 정수 코드 순서형(리커트) 문항을 가정하는데, 범주 코드로 "
            "쓰기에 값이 너무 큰 문항이 있습니다(|값| > 2^53). 단위·자릿수 입력 오류가 "
            "아닌지 확인하거나 --correlation pearson 을 쓰세요.")
    if not pairwise:
        xi_int = np.rint(x).astype(np.int64)   # 범주 라벨(정수) 기준으로 집계
        for i in range(p):
            for j in range(i + 1, p):
                # 추정 불가(범주가 1개뿐 등)는 0.0으로 메우지 않고 NaN으로 남긴다 —
                # 0은 '무상관'이라는 주장이라 요인해를 실제로 바꾼다. 호출 쪽에서
                # 원인 문항을 짚어 거절한다(재표본·집단 경로는 자체 실패 처리를 쓴다).
                r[i, j] = r[j, i] = polychoric_corr(xi_int[:, i], xi_int[:, j])
        return r

    obs = np.isfinite(x)
    xi_int = np.rint(np.where(obs, x, 0.0)).astype(np.int64)
    counts = np.zeros((p, p), dtype=np.int64)
    for i in range(p):
        counts[i, i] = int(obs[:, i].sum())
    for i in range(p):
        for j in range(i + 1, p):
            m = obs[:, i] & obs[:, j]
            counts[i, j] = counts[j, i] = int(m.sum())
            rho = (polychoric_corr(xi_int[m, i], xi_int[m, j])
                   if int(m.sum()) >= 3 else float("nan"))
            r[i, j] = r[j, i] = rho if math.isfinite(rho) else np.nan
    return r, counts


def max_categories(x: np.ndarray) -> int:
    """열별 고유값 개수의 최댓값(폴리코릭 적정성 진단용). 결측은 무시한다."""
    best = 0
    for j in range(x.shape[1]):
        col = x[:, j]
        col = col[np.isfinite(col)]
        best = max(best, int(np.unique(col).size))
    return best

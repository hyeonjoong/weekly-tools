"""집단 간 하위척도 점수 비교 — Welch t / Welch ANOVA · 효과크기 · Holm 보정.

임상·제약 연구에서 설문 점수는 거의 항상 **집단별로** 본다(치료군 vs 대조군, 남/여,
기관별, 방문시점별). 이 모듈은 `--group-col` 로 지정한 컬럼을 기준으로 하위척도
점수를 나눠 기술통계·검정·효과크기를 낸다.

설계 원칙
- **Welch 를 기본으로 쓴다.** 집단 크기와 분산이 다른 것이 실제 임상자료의 기본값이고,
  등분산을 가정하는 Student t / 일반 ANOVA 는 그럴 때 1종 오류율이 무너진다.
  Welch 는 등분산일 때도 검정력 손실이 거의 없어 기본값으로 권고된다
  (Delacre, Lakens & Leys 2017; Ruxton 2006).
- **효과크기를 항상 함께 낸다.** p 값만으로는 임상적 크기를 말할 수 없다.
  두 집단이면 Hedges' g(소표본 편향 보정된 Cohen's d)와 그 CI를 낸다.
- **다중비교를 숨기지 않는다.** 하위척도가 여러 개면 검정도 여러 번이므로
  Holm-Bonferroni 보정 p 를 함께 표기한다(보정 전 p 도 그대로 남긴다).
- **탐색적 분석임을 명시한다.** 이 도구의 집단비교는 사전에 정의된 1차 분석이 아니라
  자료 점검·기술 목적이다. 리포트에 그렇게 적는다.

계산 불가(분산 0, 집단당 N<2 등)일 때는 **틀린 숫자 대신 사유(reason)를 담은 None**
구조를 돌려주고 리포트가 그 사유를 그대로 보여준다.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from . import special, stats

# 집단 수 상한. 이보다 많으면 사실상 연속형 변수를 그룹으로 지정한 것이라
# 비교표가 의미를 잃는다(그리고 화면을 뒤덮는다).
MAX_GROUPS = 20


def welch_ttest(
    xs: Sequence[float], ys: Sequence[float], conf: float = 0.95
) -> Optional[Dict[str, object]]:
    """Welch 의 이표본 t 검정(등분산 가정 없음).

      t  = (m₁−m₂) / √(s₁²/n₁ + s₂²/n₂)
      df = (s₁²/n₁ + s₂²/n₂)² / [ (s₁²/n₁)²/(n₁−1) + (s₂²/n₂)²/(n₂−1) ]   (Welch–Satterthwaite)
      p  = 2·(1 − F_t(|t|; df))   (양측)

    평균차의 CI 도 같은 df 의 t 분위수로 낸다. 각 집단 N<2 이거나 두 집단 모두
    분산이 0(=SE 0)이면 None.
    """
    n1, n2 = len(xs), len(ys)
    if n1 < 2 or n2 < 2:
        return None
    m1, m2 = stats.mean(xs), stats.mean(ys)
    v1, v2 = stats.variance(xs), stats.variance(ys)
    if v1 is None or v2 is None:
        return None
    a1, a2 = v1 / n1, v2 / n2
    se2 = a1 + a2
    if se2 <= 0.0:
        # 두 집단 모두 값이 완전히 동일 — t 가 정의되지 않는다(0/0 또는 ±inf).
        return None
    se = math.sqrt(se2)
    denom = (a1 * a1) / (n1 - 1) + (a2 * a2) / (n2 - 1)
    if denom <= 0.0:
        return None
    df = (se2 * se2) / denom
    diff = m1 - m2
    t = diff / se
    # 꼬리를 직접 계산한다(1-CDF 는 |t|≳9 에서 p 를 정확히 0.0 으로 만든다).
    p = min(max(special.t_sf_two_sided(t, df), 0.0), 1.0)
    tcrit = special.t_ppf(1.0 - (1.0 - conf) / 2.0, df)
    return {
        "test": "welch_t",
        "t": t,
        "df": df,
        "p": p,
        "mean_diff": diff,
        "diff_ci": (diff - tcrit * se, diff + tcrit * se),
    }


def hedges_g(
    xs: Sequence[float], ys: Sequence[float], conf: float = 0.95
) -> Optional[Dict[str, object]]:
    """Hedges' g — 소표본 편향을 보정한 표준화 평균차.

      d = (m₁−m₂) / s_pooled,   s_pooled = √[((n₁−1)s₁² + (n₂−1)s₂²)/(n₁+n₂−2)]
      J = 1 − 3/(4(n₁+n₂)−9),   g = J·d
      SE_g = J·√( (n₁+n₂)/(n₁n₂) + d²/(2(n₁+n₂)) )    (대표본 근사)
      CI = g ± z_{1−a/2}·SE_g

    합동 SD 가 0이면(양쪽 모두 상수) 표준화 자체가 불가 → None.
    해석 관례: |g| 0.2 작음 / 0.5 중간 / 0.8 큼 (Cohen 1988) — 어디까지나 관례다.
    """
    n1, n2 = len(xs), len(ys)
    if n1 < 2 or n2 < 2:
        return None
    m1, m2 = stats.mean(xs), stats.mean(ys)
    v1, v2 = stats.variance(xs), stats.variance(ys)
    if v1 is None or v2 is None:
        return None
    sp2 = ((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2)
    if sp2 <= 0.0:
        return None
    d = (m1 - m2) / math.sqrt(sp2)
    j = 1.0 - 3.0 / (4.0 * (n1 + n2) - 9.0)
    g = j * d
    se_d = math.sqrt((n1 + n2) / (n1 * n2) + (d * d) / (2.0 * (n1 + n2)))
    se_g = j * se_d
    z = special.norm_ppf(1.0 - (1.0 - conf) / 2.0)
    return {"g": g, "se": se_g, "ci": (g - z * se_g, g + z * se_g)}


def welch_anova(groups: Sequence[Sequence[float]]) -> Optional[Dict[str, object]]:
    """Welch 의 일원배치 이분산 ANOVA(3집단 이상).

      w_i = n_i/s_i²,  W = Σw_i,  m̄ = Σw_i·m_i / W
      A   = Σ w_i (m_i − m̄)² / (k−1)
      Λ   = Σ (1 − w_i/W)² / (n_i − 1)
      B   = 1 + 2(k−2)/(k²−1) · Λ
      F   = A / B,   df₁ = k−1,   df₂ = (k²−1) / (3Λ)

    집단이 3개 미만이거나, 어떤 집단이 N<2 이거나 분산이 0이면(w_i 가 발산) None.
    """
    k = len(groups)
    if k < 3:
        return None
    ws: List[float] = []
    ms: List[float] = []
    ns: List[int] = []
    for g in groups:
        n = len(g)
        if n < 2:
            return None
        v = stats.variance(g)
        if v is None or v <= 0.0:
            return None
        ns.append(n)
        ms.append(stats.mean(g))
        ws.append(n / v)
    W = sum(ws)
    mbar = sum(w * m for w, m in zip(ws, ms)) / W
    a = sum(w * (m - mbar) ** 2 for w, m in zip(ws, ms)) / (k - 1)
    lam = sum((1.0 - w / W) ** 2 / (n - 1) for w, n in zip(ws, ns))
    if lam <= 0.0:
        return None
    b = 1.0 + (2.0 * (k - 2) / (k * k - 1.0)) * lam
    f = a / b
    df1 = float(k - 1)
    df2 = (k * k - 1.0) / (3.0 * lam)
    p = special.f_sf(f, df1, df2)  # 1-CDF 대신 상측 꼬리 직접 계산(자리수 손실 방지)
    return {
        "test": "welch_anova",
        "F": f,
        "df1": df1,
        "df2": df2,
        "p": min(max(p, 0.0), 1.0),
    }


def holm_adjust(pvals: Sequence[Optional[float]]) -> List[Optional[float]]:
    """Holm-Bonferroni 보정 p (단계적 하강). None 은 보정 대상에서 제외하고 None 유지.

    p_(1)≤…≤p_(m) 에 대해 adj_(i) = max_{j≤i} min(1, (m−j+1)·p_(j)) — 단조성 보장.
    """
    idx = [i for i, p in enumerate(pvals) if p is not None]
    m = len(idx)
    out: List[Optional[float]] = [None] * len(pvals)
    if m == 0:
        return out
    order = sorted(idx, key=lambda i: pvals[i])  # type: ignore[index]
    running = 0.0
    for rank, i in enumerate(order):
        adj = (m - rank) * float(pvals[i])  # type: ignore[arg-type]
        running = max(running, min(1.0, adj))
        out[i] = running
    return out


def _describe(values: Sequence[float]) -> Dict[str, object]:
    return {
        "n": len(values),
        "mean": stats.mean(values),
        "sd": stats.stdev(values),
        "median": stats.median(values),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def group_scores(
    scores: Sequence[Optional[float]], group_values: Sequence[str]
) -> Tuple[Dict[str, List[float]], int]:
    """점수를 집단 라벨별로 모은다.

    반환: ({라벨: [점수…]}, 집단 라벨이 비어있어 제외된 응답자 수).
    점수가 None(무응답 과다)인 응답자는 그 하위척도에서만 빠진다.
    """
    buckets: Dict[str, List[float]] = {}
    n_no_label = 0
    for s, gv in zip(scores, group_values):
        label = (gv or "").strip()
        if label == "":
            if s is not None:
                n_no_label += 1
            continue
        buckets.setdefault(label, [])
        if s is not None:
            buckets[label].append(s)
    return buckets, n_no_label


def compare_subscales(
    subscales: Sequence[Dict[str, object]],
    group_values: Sequence[str],
    column: str,
    conf: float = 0.95,
    group_alphas: Optional[Dict[str, Dict[str, object]]] = None,
) -> Optional[Dict[str, object]]:
    """하위척도별 집단 비교표를 만든다.

    subscales   : analyze_subscale 결과들(‘scores’ 포함, 응답자 순서 유지).
    group_values: 응답자별 집단 라벨(원문 문자열). 빈 값은 '미분류'로 제외.
    group_alphas: {하위척도명: {집단라벨: α}} — 집단별 신뢰도(있으면 표에 함께 표기).

    집단이 2개 미만이거나 MAX_GROUPS 초과면 None(사유 포함 dict)을 돌려준다.
    """
    # 라벨 수집은 집합으로 중복을 제거한다. 리스트 선형탐색(`not in list`)으로 하면
    # 환자 ID 컬럼을 실수로 지정했을 때 O(N²) 가 되어 수십만 행에서 수 분간 멈춘다
    # (상한 초과라 어차피 비교하지 않을 자료인데도).
    seen_labels = set()
    for gv in group_values:
        lab = (gv or "").strip()
        if lab:
            seen_labels.add(lab)
    labels_all = sorted(seen_labels)

    if len(labels_all) < 2:
        return {
            "column": column,
            "labels": labels_all,
            "usable": False,
            "reason": (
                f"'{column}' 에 서로 다른 집단이 2개 이상 있어야 비교할 수 있습니다"
                f"(발견된 집단: {len(labels_all)}개)."
            ),
            "subscales": [],
            "n_no_label": sum(1 for gv in group_values if not (gv or "").strip()),
        }
    if len(labels_all) > MAX_GROUPS:
        return {
            "column": column,
            # 라벨을 돌려주지 않는다. 이 분기는 '환자 ID 컬럼을 그룹으로 지정했다'는
            # 뜻이고, 그 라벨을 리포트/JSON에 실으면 공유 산출물에 식별자가 새어나간다.
            "labels": [],
            "usable": False,
            "reason": (
                f"'{column}' 의 집단이 {len(labels_all)}개로 너무 많습니다"
                f"(상한 {MAX_GROUPS}). 연속형 변수나 ID 컬럼을 지정하지 않았는지 확인하세요."
            ),
            "subscales": [],
            "n_no_label": sum(1 for gv in group_values if not (gv or "").strip()),
        }

    rows: List[Dict[str, object]] = []
    # 집단 라벨이 비어 있어 어떤 비교에서도 빠지는 응답자 수(전체 기준).
    n_no_label = sum(1 for gv in group_values if not (gv or "").strip())
    for s in subscales:
        scores = s.get("scores") or []
        buckets, _ = group_scores(scores, group_values)
        alphas = (group_alphas or {}).get(str(s["name"]), {})
        gstats = []
        for lab in labels_all:
            vals = buckets.get(lab, [])
            d = _describe(vals)
            d["label"] = lab
            d["alpha"] = alphas.get(lab)
            gstats.append(d)

        usable = [g for g in gstats if int(g["n"]) >= 2]
        test: Optional[Dict[str, object]] = None
        effect: Optional[Dict[str, object]] = None
        reason: Optional[str] = None
        # 평균차·g 의 부호가 어느 집단 기준인지 리포트가 명시할 수 있도록 남긴다
        # (부호만 보고 반대로 읽는 것이 가장 흔한 해석 오류다).
        diff_labels: Optional[List[str]] = None
        if len(usable) < 2:
            reason = "점수가 2명 이상인 집단이 2개 미만이라 검정할 수 없습니다."
        elif len(usable) == 2:
            diff_labels = [str(usable[0]["label"]), str(usable[1]["label"])]
            xs = buckets[str(usable[0]["label"])]
            ys = buckets[str(usable[1]["label"])]
            test = welch_ttest(xs, ys, conf)
            effect = hedges_g(xs, ys, conf)
            if test is None:
                reason = "두 집단의 점수 분산이 0이라 t 검정을 계산할 수 없습니다."
        else:
            test = welch_anova([buckets[str(g["label"])] for g in usable])
            if test is None:
                reason = "일부 집단의 점수 분산이 0이라 Welch ANOVA를 계산할 수 없습니다."
        # 점수가 2명 미만인 집단은 검정에서 빠진다 — 조용히 빼면 '전체 비교'로 오해되므로
        # 어떤 집단이 빠졌는지 이름으로 남긴다.
        excluded = [
            str(g["label"]) for g in gstats if int(g["n"]) < 2
        ] if test is not None else []
        rows.append(
            {
                "name": s["name"],
                "excluded_groups": excluded,
                "score_method": s.get("score_method", "mean"),
                "groups": gstats,
                "n_groups_tested": len(usable),
                "diff_labels": diff_labels,
                "test": test,
                "effect": effect,
                "reason": reason,
                "p": (test or {}).get("p"),
                "p_holm": None,
            }
        )

    adj = holm_adjust([r["p"] for r in rows])  # type: ignore[arg-type]
    for r, a in zip(rows, adj):
        r["p_holm"] = a

    return {
        "column": column,
        "labels": labels_all,
        "usable": True,
        "reason": None,
        "n_no_label": n_no_label,
        "n_tests": sum(1 for r in rows if r["p"] is not None),
        "subscales": rows,
    }

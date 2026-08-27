"""사전-사후(반복측정) 분석 — 대응표본 t · Wilcoxon · 변화량 효과크기 · 검사-재검사 ICC
· 반응자(responder) 분석 · 집단별 변화량 비교.

**왜 필요한가.** 임상연구에서 설문은 거의 항상 **두 번 이상** 잰다(기저 → 12주). 연구자가
정말 알고 싶은 값은 한 시점의 평균이 아니라 **변화량**과 "몇 명이 의미 있게 좋아졌는가"다.
이 모듈은 긴 형식(long format: 한 사람이 시점마다 한 행) CSV에서 같은 ID를 시점 간에 짝지어
그 계산을 한다.

이 모듈이 내는 값
- 시점별 평균±SD, **변화량(사후−사전) 평균±SD 와 CI**
- **대응표본 t 검정**(t, df=n−1, p)과 **Cohen's dz**(+CI) — dz 는 변화량 SD 로 나눈 값이라
  독립표본 d 와 크기를 직접 비교하면 안 된다(Lakens 2013).
- (옵션) **Wilcoxon 부호순위** — 소표본·치우친 분포에서의 민감도 분석
- **검사-재검사 ICC(2,1, 절대일치)** 와 그로부터의 SEM·MDC₉₅ — 단, 두 시점 사이에 **개입이
  없을 때만** '신뢰도'로 해석할 수 있다(개입이 있으면 낮은 ICC 는 치료효과의 반영이다).
- **반응자 분석** — 임계값(config 의 `mcid`, 없으면 α 기반 MDC₉₅) 이상 변한 인원수.
  '개선/악화' 라는 방향 라벨을 붙이지 않고 **감소/증가**로만 센다(척도마다 좋은 방향이 다르다).
- `--group-col` 이 함께 있으면 **집단별 변화량 비교**(Welch t/ANOVA + Hedges g) — 임상시험의
  기본 분석인 '기저 대비 변화의 군간 차이'에 해당한다(공변량 보정은 하지 않는 탐색적 계산).

설계 원칙
- **짝을 못 지은 사람을 조용히 버리지 않는다.** 한 시점에만 나온 ID, 같은 (ID, 시점)이 두 번
  나온 ID의 수를 모두 리포트에 남긴다.
- 계산 불가는 틀린 숫자 대신 사유(reason)를 담아 돌려준다.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from . import compare, nonparam, special, stats

# 시점 라벨이 이보다 많으면 시점 컬럼이 아니라 날짜/ID 컬럼을 지정한 것으로 본다.
MAX_TIMEPOINTS = 20


def paired_ttest(diffs: Sequence[float], conf: float = 0.95) -> Optional[Dict[str, object]]:
    """대응표본 t 검정(차이의 평균이 0인지). 쌍이 2개 미만이거나 차이 분산 0이면 None.

      t = mean(d) / (sd(d)/√n),  df = n−1,  p = P(|T| ≥ |t|)
    """
    n = len(diffs)
    if n < 2:
        return None
    m = stats.mean(diffs)
    sd = stats.stdev(diffs)
    if sd is None or sd <= 0.0:
        return None
    se = sd / math.sqrt(n)
    t = m / se
    df = float(n - 1)
    p = min(max(special.t_sf_two_sided(t, df), 0.0), 1.0)
    tcrit = special.t_ppf(1.0 - (1.0 - conf) / 2.0, df)
    return {
        "test": "paired_t",
        "t": t,
        "df": df,
        "p": p,
        "mean_diff": m,
        "sd_diff": sd,
        "diff_ci": (m - tcrit * se, m + tcrit * se),
    }


def cohen_dz(diffs: Sequence[float], conf: float = 0.95) -> Optional[Dict[str, object]]:
    """대응표본 효과크기 Cohen's dz = mean(d)/sd(d) (+ 대표본 근사 CI).

      SE_dz ≈ √(1/n + dz²/(2n)),  CI = dz ± z·SE

    dz 는 **변화량의 SD** 로 표준화한 값이라 독립표본 Hedges g 와 스케일이 다르다.
    (상관이 높을수록 dz 는 커진다 — 두 값을 같은 표에 놓고 비교하지 말 것; Lakens 2013.)
    """
    n = len(diffs)
    if n < 2:
        return None
    m = stats.mean(diffs)
    sd = stats.stdev(diffs)
    if sd is None or sd <= 0.0:
        return None
    dz = m / sd
    se = math.sqrt(1.0 / n + (dz * dz) / (2.0 * n))
    z = special.norm_ppf(1.0 - (1.0 - conf) / 2.0)
    return {"dz": dz, "se": se, "ci": (dz - z * se, dz + z * se)}


def icc_agreement(
    rows: Sequence[Sequence[float]], conf: float = 0.95
) -> Optional[Dict[str, object]]:
    """ICC(2,1) — 이원 확률효과, **절대일치**, 단일측정 급내상관 (McGraw & Wong 1996 ICC(A,1)).

    rows[i] = 대상 i 의 k 회 측정(시점). 여기서는 k=2(사전·사후)로 쓰지만 일반 k 를 받는다.

      ICC = (MSR − MSE) / [MSR + (k−1)MSE + k(MSC − MSE)/n]

    함께 내는 SEM 은 두 정의를 모두 준다: `sem`(일치형 √MSE) 과 `sem_agreement`
    (절대일치 √(MSE+(MSC−MSE)/n)) — 후자가 ICC(2,1) 과 SEM=SD·√(1−ICC) 로 맞아떨어진다.

    CI 는 McGraw & Wong(1996) Table 7 의 F 분포 기반 구간. 대상 2명 미만·측정 2회 미만이거나
    분산이 0 이어서 정의되지 않으면 None.

    ⚠ 해석: 두 시점 사이에 **개입이 없을 때만** 검사-재검사 신뢰도다. 치료가 들어간 전후에
    ICC 가 낮은 것은 신뢰도가 나쁜 것이 아니라 사람마다 반응이 달랐다는 뜻이다.
    """
    n = len(rows)
    if n < 2:
        return None
    k = len(rows[0])
    if k < 2 or any(len(r) != k for r in rows):
        return None
    grand = sum(sum(r) for r in rows) / (n * k)
    row_means = [sum(r) / k for r in rows]
    col_means = [sum(rows[i][j] for i in range(n)) / n for j in range(k)]
    ss_r = k * sum((rm - grand) ** 2 for rm in row_means)
    ss_c = n * sum((cm - grand) ** 2 for cm in col_means)
    ss_t = sum((v - grand) ** 2 for r in rows for v in r)
    ss_e = ss_t - ss_r - ss_c
    df_e = (n - 1) * (k - 1)
    if df_e <= 0:
        return None
    ms_r = ss_r / (n - 1)
    ms_c = ss_c / (k - 1)
    ms_e = max(ss_e / df_e, 0.0)
    denom = ms_r + (k - 1) * ms_e + k * (ms_c - ms_e) / n
    if denom <= 0.0:
        return None
    icc = (ms_r - ms_e) / denom
    ci: Optional[Tuple[float, float]] = None
    if 0.0 < icc < 1.0 and ms_e > 0.0:
        a = k * icc / (n * (1.0 - icc))
        b = 1.0 + k * icc * (n - 1.0) / (n * (1.0 - icc))
        num = (a * ms_c + b * ms_e) ** 2
        den = (a * ms_c) ** 2 / (k - 1) + (b * ms_e) ** 2 / df_e
        if den > 0.0:
            v = num / den
            alpha = 1.0 - conf
            f_l = special.f_ppf(1.0 - alpha / 2.0, n - 1, v)
            f_u = special.f_ppf(1.0 - alpha / 2.0, v, n - 1)
            c_term = k * ms_c + (k * n - k - n) * ms_e
            lo_den = f_l * c_term + n * ms_r
            up_den = c_term + n * f_u * ms_r
            if lo_den > 0.0 and up_den > 0.0:
                lo = n * (ms_r - f_l * ms_e) / lo_den
                up = n * (f_u * ms_r - ms_e) / up_den
                ci = (lo, min(up, 1.0))
    # 측정오차의 표준편차(SEM)는 두 가지 정의가 있고 값이 크게 다르다 — 하나만 내면
    # 반드시 오해가 생기므로 둘 다 낸다.
    #  - 일치형(consistency, Weir 2005): √MSE. 시점 간 **평균 이동(체계적 변화)을 오차로
    #    보지 않는다**. 개입 전후 자료에서 '측정오차'를 말할 때 보통 이 값을 쓴다.
    #  - 절대일치(agreement, de Vet 2006): √(MSE + (MSC−MSE)/n). 평균 이동까지 오차에
    #    포함하며, 함께 내는 ICC(2,1) 과 SEM = SD·√(1−ICC) 항등식이 성립하는 쪽이다.
    sem = math.sqrt(ms_e) if ms_e > 0 else 0.0
    var_agree = ms_e + (ms_c - ms_e) / n
    sem_agreement = math.sqrt(var_agree) if var_agree > 0 else 0.0
    k95 = 1.959963984540054 * math.sqrt(2.0)
    return {
        "icc": icc,
        "ci": ci,
        "n": n,
        "k": k,
        "ms_r": ms_r,
        "ms_c": ms_c,
        "ms_e": ms_e,
        "sem": sem,
        "sem_agreement": sem_agreement,
        "mdc95": k95 * sem,
        "mdc95_agreement": k95 * sem_agreement,
    }


# 사유 메시지에 그대로 실을 시점 라벨 수의 상한. 시점 컬럼이 사실은 방문일자면 라벨이
# 곧 진료일이라 공유 산출물(리포트·JSON)에 식별정보가 실린다 — 집단 라벨 상한과 같은 방침.
REASON_LABEL_PREVIEW = 5


def _label_preview(labels: Sequence[str]) -> str:
    shown = list(labels[:REASON_LABEL_PREVIEW])
    more = len(labels) - len(shown)
    return ", ".join(shown) + (f" 외 {more}개" if more > 0 else "")


def resolve_timepoints(
    labels: Sequence[str], pre: Optional[str], post: Optional[str]
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """사전·사후 라벨을 정한다. 반환 (pre, post, 오류사유).

    - 사용자가 --time-pre/--time-post 로 지정했으면 그대로 쓰되 자료에 있는지 확인한다.
    - 지정이 없고 시점이 정확히 2개면 정렬 순서(숫자면 숫자 순)로 앞을 사전으로 본다.
    - 시점이 3개 이상인데 지정이 없으면 오류(무엇을 비교할지 도구가 정할 수 없다).
    """
    uniq = list(labels)
    if pre is not None or post is not None:
        if pre is None or post is None:
            return None, None, "--time-pre 와 --time-post 는 함께 지정해야 합니다."
        if pre == post:
            return None, None, "--time-pre 와 --time-post 가 같습니다."
        missing = [x for x in (pre, post) if x not in uniq]
        if missing:
            shown = _label_preview(uniq)
            return None, None, (
                "시점 컬럼에 없는 값입니다: " + ", ".join(missing)
                + f" (자료의 시점: {shown})"
            )
        return pre, post, None
    if len(uniq) < 2:
        return None, None, (
            f"시점이 {len(uniq)}개뿐이라 사전-사후 비교를 할 수 없습니다."
        )
    if len(uniq) > 2:
        shown = _label_preview(uniq)
        return None, None, (
            f"시점이 {len(uniq)}개입니다 — --time-pre 와 --time-post 로 비교할 두 시점을 "
            f"지정하세요 (자료의 시점: {shown})"
        )
    return uniq[0], uniq[1], None


def order_labels(labels: Sequence[str]) -> Tuple[List[str], str]:
    """시점 라벨의 순서를 정한다. 반환 (정렬된 라벨, 규칙 이름).

    - 전부 숫자로 읽히면 **숫자 순**('0','4','12' 이 문자열 정렬로 0,12,4 가 되는 것을 막는다).
    - 아니면 **자료에 처음 나온 순서**. 한글 라벨('기저','12주')을 문자열 정렬하면
      '12주'가 앞에 와서 변화량의 부호가 통째로 뒤집힌다 — 가나다순보다 파일 순서가
      연구자의 의도에 훨씬 가깝다. 어느 쪽이든 리포트에 사전/사후를 명시하고
      --time-pre/--time-post 로 덮어쓸 수 있게 한다.
    """
    seen: List[str] = []
    for lab in labels:
        if lab and lab not in seen:
            seen.append(lab)
    try:
        vals = [float(x) for x in seen]
    except (TypeError, ValueError):
        return seen, "appearance"
    # 'nan'/'inf' 는 float() 를 통과하지만 정렬 순서가 정의되지 않는다(사전/사후가 조용히
    # 뒤바뀔 수 있다) → 숫자 규칙을 쓰지 않는다.
    if not all(math.isfinite(v) for v in vals):
        return seen, "appearance"
    return [x for _, x in sorted(zip(vals, seen), key=lambda t: t[0])], "numeric"


def build_pairs(
    keys: Sequence[str], time_values: Sequence[str], pre: str, post: str
) -> Dict[str, object]:
    """(ID, 시점) 으로 행을 짝짓는다.

    반환: {"pairs": [(key, i_pre, i_post)…], "n_dup": 같은 (ID,시점)이 2번 이상이라 통째로
    제외한 ID 수, "n_unpaired": 한 시점에만 있는 ID 수, "n_no_id": ID 가 빈 행 수,
    "n_other_time": 비교 대상이 아닌 시점(또는 시점 없음)의 행 수}
    """
    at: Dict[str, Dict[str, List[int]]] = {}
    n_no_id = 0
    n_other_time = 0
    for i, (k, t) in enumerate(zip(keys, time_values)):
        if t not in (pre, post):
            # 비교하지 않는 시점(예: 3시점 자료의 4주)이나 시점이 빈 행. 짝짓기에서는
            # 빠지지만 리포트 상단의 문항 통계에는 그대로 들어가 있으므로 수를 남긴다.
            n_other_time += 1
            continue
        if not k:
            n_no_id += 1
            continue
        at.setdefault(k, {}).setdefault(t, []).append(i)
    pairs: List[Tuple[str, int, int]] = []
    n_dup = 0
    n_unpaired = 0
    for k, byt in at.items():
        ip = byt.get(pre, [])
        iq = byt.get(post, [])
        if len(ip) > 1 or len(iq) > 1:
            # 같은 사람이 같은 시점에 두 번 — 어느 행이 맞는지 도구가 고를 수 없다.
            # 아무거나 고르면 조용히 틀린 변화량이 나오므로 통째로 뺀다.
            n_dup += 1
            continue
        if len(ip) == 1 and len(iq) == 1:
            pairs.append((k, ip[0], iq[0]))
        else:
            n_unpaired += 1
    pairs.sort(key=lambda p: p[0])
    return {
        "pairs": pairs,
        "n_dup": n_dup,
        "n_unpaired": n_unpaired,
        "n_no_id": n_no_id,
        "n_other_time": n_other_time,
    }


def _describe(values: Sequence[float]) -> Dict[str, object]:
    return {
        "n": len(values),
        "mean": stats.mean(values),
        "sd": stats.stdev(values),
        "median": stats.median(values),
    }


def _group_change_compare(
    changes: Sequence[float],
    labels: Sequence[str],
    conf: float,
    use_nonparam: bool,
) -> Optional[Dict[str, object]]:
    """집단별 변화량 비교(2집단 Welch t + Hedges g, 3집단 이상 Welch ANOVA)."""
    buckets: Dict[str, List[float]] = {}
    for ch, lab in zip(changes, labels):
        if not lab:
            continue
        buckets.setdefault(lab, []).append(ch)
    usable = {k: v for k, v in buckets.items() if len(v) >= 2}
    if len(usable) < 2:
        return None
    names = sorted(usable)
    groups = [{"label": nm, **_describe(usable[nm])} for nm in names]
    test = effect = np_test = None
    if len(names) == 2:
        xs, ys = usable[names[0]], usable[names[1]]
        test = compare.welch_ttest(xs, ys, conf)
        effect = compare.hedges_g(xs, ys, conf)
        if use_nonparam:
            np_test = nonparam.mannwhitney_u(xs, ys)
    else:
        test = compare.welch_anova([usable[nm] for nm in names])
        if use_nonparam:
            np_test = nonparam.kruskal_wallis([usable[nm] for nm in names])
    return {
        "groups": groups,
        "diff_labels": names if len(names) == 2 else None,
        "excluded_groups": sorted(k for k in buckets if len(buckets[k]) < 2),
        "test": test,
        "effect": effect,
        "nonparam": np_test,
    }


def compare_prepost(
    subscales: Sequence[Dict[str, object]],
    keys: Sequence[str],
    time_values: Sequence[str],
    column: str,
    pre: Optional[str] = None,
    post: Optional[str] = None,
    conf: float = 0.95,
    mcid: Optional[Dict[str, float]] = None,
    group_values: Optional[Sequence[str]] = None,
    group_column: Optional[str] = None,
    use_nonparam: bool = False,
    id_label: str = "",
) -> Dict[str, object]:
    """하위척도별 사전-사후 비교표를 만든다.

    keys        : 응답자 행별 짝짓기 키(ID 컬럼 값). 빈 문자열이면 짝짓기 불가.
    time_values : 응답자 행별 시점 라벨(정규화된 문자열).
    mcid        : {하위척도명: 임상적 최소중요차이} — 반응자 판정 임계값(없으면 MDC₉₅).
    """
    labels_all, order_rule = order_labels(time_values)
    base = {
        "column": column,
        "labels": labels_all[:MAX_TIMEPOINTS],
        "n_labels": len(labels_all),
        "order_rule": order_rule,
        "id_label": id_label,
        "pre": None,
        "post": None,
        "usable": False,
        "reason": None,
        "subscales": [],
        "nonparam": use_nonparam,
        "group_column": group_column,
    }
    if len(labels_all) > MAX_TIMEPOINTS:
        # 시점이 수십 개면 사실상 날짜·ID 컬럼이다. 라벨을 그대로 실으면 식별정보가 샐 수 있어
        # 개수만 알린다(집단 비교의 MAX_GROUPS 와 같은 방침).
        base["labels"] = []
        base["reason"] = (
            f"'{column}' 의 시점이 {len(labels_all)}개로 너무 많습니다(상한 {MAX_TIMEPOINTS}). "
            "날짜나 ID 컬럼을 지정하지 않았는지 확인하세요."
        )
        return base
    if not any(keys):
        base["reason"] = (
            "짝짓기에 쓸 ID 값이 없습니다 — --id-col 로 응답자 ID 컬럼을 지정하세요"
            "(여러 개면 --pair-id 로 어느 것을 쓸지 고를 수 있습니다)."
        )
        return base

    p, q, err = resolve_timepoints(labels_all, pre, post)
    if err:
        base["reason"] = err
        return base
    base["pre"], base["post"] = p, q
    if pre is not None or post is not None:
        base["order_rule"] = "explicit"

    info = build_pairs(keys, time_values, str(p), str(q))
    pairs = info["pairs"]
    base.update(
        {
            "n_pairs_total": len(pairs),
            "n_dup_excluded": info["n_dup"],
            "n_unpaired": info["n_unpaired"],
            "n_no_id": info["n_no_id"],
            "n_other_time": info["n_other_time"],
        }
    )
    if not pairs:
        # 사유는 **실제 원인**을 짚어야 한다. 중복 키로 전원이 빠졌는데 "ID 표기가 다른가"
        # 라고 안내하면 사용자는 엉뚱한 곳을 뒤진다.
        if info["n_dup"]:
            base["reason"] = (
                f"'{p}'/'{q}' 두 시점에 모두 나온 ID 가 있지만, 짝짓기 키가 같은 행이 "
                f"두 번 이상 나와 {info['n_dup']}명을 모두 제외했습니다 — --pair-id 로 지정한 "
                "ID 조합이 응답자를 유일하게 구분하는지(예: 기관+환자번호) 확인하세요."
            )
        else:
            base["reason"] = (
                f"'{p}' 와 '{q}' 두 시점에 모두 나온 ID 가 없습니다 — ID 표기가 시점마다 "
                "다르지 않은지 확인하세요."
            )
        return base

    gvals = list(group_values or [])
    rows: List[Dict[str, object]] = []
    for s in subscales:
        scores = list(s.get("scores") or [])
        pre_vals: List[float] = []
        post_vals: List[float] = []
        changes: List[float] = []
        glabels: List[str] = []
        n_missing_score = 0
        n_group_conflict = 0
        for key, i, j in pairs:
            a = scores[i] if i < len(scores) else None
            b = scores[j] if j < len(scores) else None
            if a is None or b is None:
                n_missing_score += 1
                continue
            pre_vals.append(float(a))
            post_vals.append(float(b))
            changes.append(float(b) - float(a))
            if gvals:
                ga = gvals[i] if i < len(gvals) else ""
                gb = gvals[j] if j < len(gvals) else ""
                if ga and gb and ga != gb:
                    # 같은 사람이 시점마다 다른 군으로 적혀 있다 — 자료 오류다.
                    # 아무 쪽이나 고르면 조용히 틀린 군간 비교가 되므로 뺀다.
                    n_group_conflict += 1
                    glabels.append("")
                else:
                    glabels.append(ga or gb)
        n = len(changes)
        test = paired_ttest(changes, conf) if n >= 2 else None
        effect = cohen_dz(changes, conf) if n >= 2 else None
        # 모수 검정과 같은 하한(n≥2)을 쓴다. 쌍 1개짜리 Wilcoxon 은 p=1.0 인데 효과크기는
        # ±1.00('큼')로 찍혀, 바로 아래의 '검정할 수 없습니다' 경고와 정면으로 어긋난다.
        wil = nonparam.wilcoxon_signed_rank(changes) if (use_nonparam and n >= 2) else None
        icc = icc_agreement([[a, b] for a, b in zip(pre_vals, post_vals)], conf) if n >= 2 else None
        r_pp = stats.pearson(pre_vals, post_vals) if n >= 2 else None

        # 반응자 분석 임계값. 우선순위와 그 이유:
        #  1) config 의 mcid — 원 논문의 임상적 최소중요차이. 가장 신뢰할 값.
        #  2) 이 짝 자료에서 나온 MDC₉₅(√MSE 기반) — **짝지어진 사람들만**으로 계산되고
        #     반복측정 오차에서 직접 나온다.
        #  3) α 기반 MDC₉₅ — 마지막 수단. --time-col 자료에서는 모든 시점을 합친 표본의
        #     α·SD 라 같은 사람이 여러 번 들어가고 개입 효과로 SD 가 부풀려진다.
        name = str(s["name"])
        thr = (mcid or {}).get(name)
        thr_source = "mcid" if thr is not None else None
        if thr is not None and not (float(thr) > 0 and math.isfinite(float(thr))):
            # 0 이하 임계값은 감소·증가를 동시에 만족시켜 인원이 이중집계된다(합계 > N).
            thr, thr_source = None, None
        if thr is None and icc is not None:
            m = icc.get("mdc95")
            if m is not None and math.isfinite(float(m)) and float(m) > 0:
                thr = float(m)
                thr_source = "mdc95_retest"
        if thr is None:
            m = s.get("mdc95")
            if m is not None and math.isfinite(float(m)) and float(m) > 0:
                thr = float(m)
                thr_source = "mdc95_alpha"
        responders = None
        if thr is not None and n >= 1:
            dec = sum(1 for c in changes if c <= -thr)
            inc = sum(1 for c in changes if c >= thr)
            responders = {
                "threshold": float(thr),
                "source": thr_source,
                "n": n,
                "decreased": dec,
                "increased": inc,
                "unchanged": n - dec - inc,
                "decreased_pct": round(100.0 * dec / n, 1),
                "increased_pct": round(100.0 * inc / n, 1),
                "unchanged_pct": round(100.0 * (n - dec - inc) / n, 1),
            }
        gcmp = (
            _group_change_compare(changes, glabels, conf, use_nonparam)
            if gvals and n >= 4 else None
        )
        reason = None
        if n < 2:
            reason = f"두 시점 점수가 모두 있는 사람이 {n}명뿐이라 검정할 수 없습니다."
        elif test is None:
            reason = "변화량이 모든 사람에서 같아(분산 0) t 검정을 계산할 수 없습니다."
        rows.append(
            {
                "name": name,
                "score_method": s.get("score_method", "mean"),
                "n_pairs": n,
                "n_missing_score": n_missing_score,
                "n_group_conflict": n_group_conflict,
                "pre": _describe(pre_vals),
                "post": _describe(post_vals),
                "change": _describe(changes),
                "change_ci": list(test["diff_ci"]) if test else None,
                "test": test,
                "effect": effect,
                "wilcoxon": wil,
                "r_prepost": r_pp,
                "icc": icc,
                "responders": responders,
                "group_change": gcmp,
                "reason": reason,
                "p": (test or {}).get("p"),
                "p_holm": None,
            }
        )

    adj = compare.holm_adjust([r["p"] for r in rows])  # type: ignore[arg-type]
    for r, a in zip(rows, adj):
        r["p_holm"] = a

    base["usable"] = True
    base["subscales"] = rows
    base["n_tests"] = sum(1 for r in rows if r["p"] is not None)
    return base

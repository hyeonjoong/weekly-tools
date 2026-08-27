"""응답 품질(부주의 응답, careless/insufficient-effort responding) 점검.

설문 데이터에는 문항을 읽지 않고 한 열로 찍은 응답(straightlining), 대충 채운 응답이
섞인다. 이런 응답은 신뢰도·상관을 왜곡하므로 분석 전에 걸러내는 것이 표준 절차다
(Meade & Craig 2012; Curran 2016). 이 모듈은 **원자료(raw, 역코딩 전)** 기준으로
응답자별 지표를 계산한다.

지표
- **longstring** — 문항 순서상 **연속으로 같은 값**을 답한 최대 길이. 결측은 연속을
  끊는다(값이 없으므로 '같다'고 볼 수 없다).
- **IRV** (intra-individual response variability) — 응답자 내 응답의 표준편차(ddof=1).
  0 이면 모든 답이 같음. 낮을수록 한 열로 찍었을 가능성.
- **결측률** — 응답자별 무응답 비율.

**중요한 한계(과잉해석 금지).** 단방향 임상척도(ISI·PHQ-9 등)에서 '모두 0'은
**증상이 없는 실제 응답**일 수 있다 — 부주의 응답이 아니다. longstring/IRV 는
**역문항이 섞여 있을 때** 진단력이 높다. 따라서 이 지표는 '자동 제외 기준'이 아니라
**원자료를 눈으로 확인할 대상을 좁히는 선별(screening) 도구**다. 리포트도 그렇게 표기한다.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

from . import stats
from .dataio import SurveyData


def longstring(values: Sequence[Optional[float]]) -> int:
    """문항 순서상 연속으로 같은 값을 답한 최대 길이(결측은 연속을 끊음).

    응답이 하나도 없으면 0.
    """
    best = 0
    run = 0
    prev: Optional[float] = None
    for v in values:
        if v is None:
            run = 0
            prev = None
            continue
        if prev is not None and v == prev:
            run += 1
        else:
            run = 1
        prev = v
        if run > best:
            best = run
    return best


def respondent_quality(
    data: SurveyData, items: List[str], longstring_min: Optional[int] = None
) -> Dict[str, object]:
    """응답자별 품질 지표 + 요약.

    longstring_min: longstring 플래그 기준(이 값 이상이면 플래그). None이면
      기본 휴리스틱 max(3, ceil(k/2)) 를 쓴다. 보편적 컷오프는 존재하지 않으므로
      이 값은 '선별 기준'일 뿐이며 리포트에 기준값을 함께 표기한다.
    """
    k = len(items)
    if longstring_min is None:
        longstring_min = max(3, math.ceil(k / 2)) if k else 3

    rows: List[Dict[str, object]] = []
    for idx, row in enumerate(data.rows):
        vals = [row.get(it) for it in items]
        answered = [v for v in vals if v is not None]
        n_ans = len(answered)
        ls = longstring(vals)
        irv = stats.stdev(answered) if n_ans >= 2 else None
        # straightline: 답한 문항이 3개 이상인데 값이 전부 동일.
        straight = n_ans >= 3 and irv is not None and irv == 0.0
        long_run = k > 0 and ls >= longstring_min and n_ans >= 3
        rows.append(
            {
                "row": idx + 1,  # 1-기반 데이터 행 번호(헤더 제외)
                "ids": dict(data.id_values[idx]) if idx < len(data.id_values) else {},
                "n_answered": n_ans,
                "n_missing": k - n_ans,
                "missing_pct": round(100.0 * (k - n_ans) / k, 1) if k else 0.0,
                "longstring": ls,
                "irv": irv,
                "straightline": straight,
                "long_run": long_run,
                "flagged": bool(straight or long_run),
            }
        )

    ls_all = [int(r["longstring"]) for r in rows]
    irvs = [r["irv"] for r in rows if r["irv"] is not None]
    n_flag = sum(1 for r in rows if r["flagged"])
    # 결측률이 높은 응답자(절반 넘게 무응답) — 별도 집계.
    n_high_missing = sum(1 for r in rows if k and (r["n_missing"] / k) > 0.5)
    return {
        "longstring_min": longstring_min,
        "n_respondents": len(rows),
        "n_flagged": n_flag,
        "n_straightline": sum(1 for r in rows if r["straightline"]),
        "n_high_missing": n_high_missing,
        "max_longstring": max(ls_all) if ls_all else 0,
        "median_longstring": stats.median([float(x) for x in ls_all]) if ls_all else None,
        "median_irv": stats.median(irvs) if irvs else None,
        "respondents": rows,
    }


def duplicate_ids(
    data: SurveyData, time_values: Optional[Sequence[str]] = None
) -> List[Dict[str, object]]:
    """ID 컬럼 값이 중복된 응답자 그룹(이중입력·병합오류 탐지).

    ID 컬럼이 여러 개면 그 조합(튜플)을 하나의 식별자로 본다. 빈 ID 값은 무시한다
    (결측 ID 는 '중복'이 아니라 '없음'이므로 별도 문제).

    time_values 가 주어지면(=반복측정 자료) **(ID, 시점)** 조합으로 판정한다. 같은 사람이
    시점마다 한 줄씩 있는 것은 정상이므로, 그것까지 '중복'이라 하면 모든 ID가 경고로
    뜨면서 진짜 이중입력이 묻힌다.
    반환: [{"id": "...", "rows": [행번호...], "count": n}, ...] (중복만).
    """
    if not data.id_columns:
        return []
    tvals = list(time_values or [])
    seen: Dict[tuple, List[int]] = {}
    for idx, ids in enumerate(data.id_values):
        key = tuple(ids.get(c, "") for c in data.id_columns)
        if all(v == "" for v in key):
            continue  # ID 전부 비어있으면 중복 판정 대상 아님
        if tvals:
            key = key + (tvals[idx] if idx < len(tvals) else "",)
        seen.setdefault(key, []).append(idx + 1)
    out: List[Dict[str, object]] = []
    for key, rows in seen.items():
        if len(rows) > 1:
            shown = key[:-1] if tvals else key
            label = " / ".join(shown)
            if tvals:
                label += f" (시점: {key[-1] or '없음'})"
            out.append(
                {"id": label, "rows": rows, "count": len(rows)}
            )
    out.sort(key=lambda d: (-int(d["count"]), str(d["id"])))
    return out


def subscale_correlations(
    subscales: List[Dict[str, object]], conf: float = 0.95
) -> Optional[Dict[str, object]]:
    """하위척도 점수 간 상관(쌍별 완전응답, pairwise deletion).

    변별타당도(discriminant validity) 점검용. 하위척도가 2개 미만이면 None.
    각 쌍마다 상관 r 과 그 쌍에 쓰인 N 을 함께 낸다(쌍마다 N이 다를 수 있음).
    """
    named = [(str(s["name"]), s["scores"]) for s in subscales]
    k = len(named)
    if k < 2:
        return None
    names = [n for n, _ in named]
    pairs: List[Dict[str, object]] = []
    for i in range(k):
        for j in range(i + 1, k):
            xs_raw, ys_raw = named[i][1], named[j][1]
            xs: List[float] = []
            ys: List[float] = []
            for a, b in zip(xs_raw, ys_raw):
                if a is not None and b is not None:
                    xs.append(a)
                    ys.append(b)
            r = stats.pearson(xs, ys) if len(xs) >= 2 else None
            pairs.append(
                {"a": names[i], "b": names[j], "r": r, "n": len(xs)}
            )
    if not pairs:
        return None
    return {"names": names, "pairs": pairs}

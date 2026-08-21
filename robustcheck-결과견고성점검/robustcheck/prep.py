"""흔드는 축 B·C·E 의 실제 적용 — 결측 처리 → 로그변환 → 이상치 제거.

**순서가 결과를 바꾼다.** 이 툴은 순서를 하나로 고정하고 리포트에 인쇄한다:

    ① 결측 처리(C) → ② 로그변환(E) → ③ 이상치 제거(B) → ④ 검정(D)

로그 척도로 분석할 거면 이상치도 로그 척도에서 판정하는 것이 일관되기
때문이고, 결측을 채운 뒤에야 채운 값도 이상치 판정 대상이 되기 때문이다.
다른 순서를 원하면 그건 다른 툴이다 — 여기서는 **말없이 바꾸지 않는다.**
"""

import math
from typing import Dict, List, Optional, Sequence, Tuple

from .inference import mean, stdev
from .spec import Spec, Subject

__all__ = [
    "SkipScenario",
    "Prepared",
    "OUTLIER_LEVELS",
    "MISSING_LEVELS",
    "TEST_LEVELS",
    "LOG_LEVELS",
    "PIPELINE_ORDER",
    "prepare",
    "quantile",
    "max_possible_z",
    "sd_rule_note",
]

OUTLIER_LEVELS = ("없음", "±3SD", "IQR1.5")
MISSING_LEVELS = ("완결자만", "LOCF", "평균대체")
TEST_LEVELS = ("모수", "비모수")
LOG_LEVELS = ("미적용", "적용")

PIPELINE_ORDER = "결측 처리(C) → 로그변환(E) → 이상치 제거(B) → 검정(D)"

# 이 값 미만이면 그 군에서 이상치 규칙을 적용하지 않는다(적용해도 의미가 없다).
MIN_N_FOR_OUTLIER_RULE = 4
# 검정을 돌리기 위한 군별 최소 인원.
MIN_N_PER_GROUP = 3


class SkipScenario(Exception):
    """이 시나리오는 계산할 수 없다. `reason` 이 커버리지 자백에 그대로 들어간다."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason if not detail else "%s (%s)" % (reason, detail))
        self.reason = reason
        self.detail = detail


class Prepared:
    """한 시나리오의 분석 직전 상태."""

    __slots__ = ("design", "a", "b", "cov_a", "cov_b", "pre", "post", "x", "y",
                 "ids_a", "ids_b", "ids", "excluded", "notes", "imputed")

    def __init__(self, design: str) -> None:
        self.design = design
        self.a: List[float] = []
        self.b: List[float] = []
        self.cov_a: List[float] = []
        self.cov_b: List[float] = []
        self.pre: List[float] = []
        self.post: List[float] = []
        self.x: List[float] = []
        self.y: List[float] = []
        self.ids_a: List[str] = []
        self.ids_b: List[str] = []
        self.ids: List[str] = []
        self.excluded: List[Tuple[str, str]] = []   # (subject_id, 사유)
        self.notes: List[str] = []
        self.imputed = 0

    @property
    def n(self) -> int:
        if self.design == "two-group":
            return len(self.a) + len(self.b)
        if self.design == "paired":
            return len(self.pre)
        return len(self.x)


def quantile(sorted_values: Sequence[float], q: float) -> float:
    """선형보간 분위수(numpy 기본 `linear`, R type 7)."""
    n = len(sorted_values)
    if n == 0:
        raise ValueError("quantile: 빈 표본")
    if n == 1:
        return float(sorted_values[0])
    pos = (n - 1) * q
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def max_possible_z(n: int) -> float:
    """표본 하나가 가질 수 있는 최대 |z| = (n−1)/√n.

    n ≤ 10 이면 이 값이 3 미만이라 **±3SD 규칙은 수학적으로 아무도 배제할 수
    없다.** 그런 시나리오를 조용히 '이상치 0명'으로 넘기면 사용자는 규칙이
    작동했다고 오해한다. 그래서 사유를 남긴다.
    """
    if n < 2:
        return 0.0
    return (n - 1) / math.sqrt(n)


def sd_rule_note(n: int, label: str = "") -> Optional[str]:
    if max_possible_z(n) >= 3.0:
        return None
    where = " (%s)" % label if label else ""
    return ("n=%d%s 에서는 ±3SD 가 아무도 배제할 수 없다 — 최대 |z| = %.2f < 3"
            % (n, where, max_possible_z(n)))


def _outlier_mask(values: Sequence[float], rule: str) -> List[bool]:
    """True = 이상치. 규칙이 '없음' 이거나 표본이 너무 작으면 전부 False."""
    if rule == "없음" or len(values) < MIN_N_FOR_OUTLIER_RULE:
        return [False] * len(values)
    if rule == "±3SD":
        m = mean(values)
        sd = stdev(values)
        if sd <= 0.0:
            return [False] * len(values)
        return [abs(v - m) > 3.0 * sd for v in values]
    if rule == "IQR1.5":
        ordered = sorted(values)
        q1 = quantile(ordered, 0.25)
        q3 = quantile(ordered, 0.75)
        iqr = q3 - q1
        if iqr <= 0.0:
            return [False] * len(values)
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        return [v < lo or v > hi for v in values]
    raise ValueError("알 수 없는 이상치 규칙: %r" % rule)


# ------------------------------------------------------- ① 결측 처리 (축 C)


def _resolve_missing(
    subjects: Sequence[Subject], spec: Spec, rule: str
) -> Tuple[List[Tuple[Subject, Dict[str, float]]], List[Tuple[str, str]], int]:
    """결측 규칙을 적용해 (피험자, 값) 목록을 만든다."""
    cols = spec.numeric_columns
    kept: List[Tuple[Subject, Dict[str, float]]] = []
    dropped: List[Tuple[str, str]] = []
    imputed = 0

    if spec.design != "paired" and rule != "완결자만":
        raise SkipScenario(
            "설계상 미적용",
            "결측 처리 축(C)은 paired 설계 전용 — %s 는 완결자만 적용" % spec.design,
        )

    if rule == "완결자만":
        for s in subjects:
            values = {c: s.get(c) for c in cols}
            if any(v is None for v in values.values()):
                miss = [c for c, v in values.items() if v is None]
                dropped.append((s.sid, "결측(%s)" % ",".join(miss)))
                continue
            kept.append((s, {c: float(v) for c, v in values.items()}))
        return kept, dropped, imputed

    if rule == "LOCF":
        for s in subjects:
            pre, post = s.get(spec.pre), s.get(spec.post)
            if pre is None:
                dropped.append((s.sid, "LOCF 불가(기저값 결측)"))
                continue
            if post is None:
                post = pre
                imputed += 1
            kept.append((s, {spec.pre: float(pre), spec.post: float(post)}))
        return kept, dropped, imputed

    if rule == "평균대체":
        pre_obs = [s.get(spec.pre) for s in subjects if s.get(spec.pre) is not None]
        post_obs = [s.get(spec.post) for s in subjects if s.get(spec.post) is not None]
        if not pre_obs or not post_obs:
            raise SkipScenario("결측 100%", "평균대체에 쓸 관측값이 없는 열이 있음")
        pre_mean, post_mean = mean(pre_obs), mean(post_obs)
        for s in subjects:
            pre, post = s.get(spec.pre), s.get(spec.post)
            if pre is None and post is None:
                dropped.append((s.sid, "두 시점 모두 결측"))
                continue
            if pre is None:
                pre = pre_mean
                imputed += 1
            if post is None:
                post = post_mean
                imputed += 1
            kept.append((s, {spec.pre: float(pre), spec.post: float(post)}))
        return kept, dropped, imputed

    raise ValueError("알 수 없는 결측 규칙: %r" % rule)


# ------------------------------------------------------ ② 로그변환 (축 E)


def _apply_log(
    rows: List[Tuple[Subject, Dict[str, float]]], cols: Sequence[str], rule: str
) -> None:
    if rule == "미적용":
        return
    for _, values in rows:
        for c in cols:
            if values[c] <= 0.0:
                raise SkipScenario("로그변환 불가", "0 이하 값 포함")
    for _, values in rows:
        for c in cols:
            values[c] = math.log(values[c])


# ------------------------------------------------------ ③ 이상치 제거 (축 B)


def _drop_two_group(
    rows: List[Tuple[Subject, Dict[str, float]]], spec: Spec,
    levels: Tuple[str, ...], rule: str,
) -> Tuple[List[Tuple[Subject, Dict[str, float]]], List[Tuple[str, str]], List[str]]:
    """군 **안에서** 결과변수 기준으로 판정한다(군 간 차이를 이상치로 오인하지 않도록)."""
    notes: List[str] = []
    excluded: List[Tuple[str, str]] = []
    keep: List[Tuple[Subject, Dict[str, float]]] = []
    for level in levels:
        members = [(s, v) for s, v in rows if s.group == level]
        values = [v[spec.value] for _, v in members]
        if rule != "없음" and len(values) < MIN_N_FOR_OUTLIER_RULE:
            notes.append("군 '%s' n=%d — 이상치 규칙 미적용(n<%d)"
                         % (level, len(values), MIN_N_FOR_OUTLIER_RULE))
        elif rule == "±3SD":
            note = sd_rule_note(len(values), "군 %s" % level)
            if note:
                notes.append(note)
        mask = _outlier_mask(values, rule)
        for (s, v), bad in zip(members, mask):
            if bad:
                excluded.append((s.sid, "이상치(%s, 군 %s)" % (rule, level)))
            else:
                keep.append((s, v))
    # 어느 군에도 속하지 않는 행(군 라벨이 비었거나 오타)은 **소리 없이 사라지면
    # 안 된다.** n + 제외인원 이 전체 N 과 맞지 않게 되고, 사용자는 자기 피험자가
    # 빠진 줄도 모른다. 사유를 붙여 제외 목록에 넣는다.
    matched = {id(s) for s, _ in keep} | {sid for sid, _ in excluded}
    for s, _ in rows:
        if id(s) not in matched and s.group not in levels:
            excluded.append((s.sid, "군 라벨 없음/불일치(%r)" % (s.group or "")))
    # 입력 순서를 보존해야 같은 입력 → 같은 출력이 된다.
    order = {id(s): i for i, (s, _) in enumerate(rows)}
    keep.sort(key=lambda pair: order[id(pair[0])])
    return keep, excluded, notes


def _drop_by_series(
    rows: List[Tuple[Subject, Dict[str, float]]], series: Sequence[float],
    rule: str, label: str,
) -> Tuple[List[Tuple[Subject, Dict[str, float]]], List[Tuple[str, str]], List[str]]:
    notes: List[str] = []
    if rule != "없음" and len(series) < MIN_N_FOR_OUTLIER_RULE:
        notes.append("n=%d — 이상치 규칙 미적용(n<%d)"
                     % (len(series), MIN_N_FOR_OUTLIER_RULE))
    elif rule == "±3SD":
        note = sd_rule_note(len(series), label)
        if note:
            notes.append(note)
    mask = _outlier_mask(series, rule)
    keep, excluded = [], []
    for (s, v), bad in zip(rows, mask):
        if bad:
            excluded.append((s.sid, "이상치(%s, %s)" % (rule, label)))
        else:
            keep.append((s, v))
    return keep, excluded, notes


# --------------------------------------------------------------- 조립


def prepare(
    subjects: Sequence[Subject],
    spec: Spec,
    group_levels: Tuple[str, ...],
    outlier: str,
    missing: str,
    log: str,
) -> Prepared:
    """축 B·C·E 를 적용해 검정 직전 상태를 만든다. 못 하면 SkipScenario."""
    rows, dropped, imputed = _resolve_missing(subjects, spec, missing)
    if not rows:
        raise SkipScenario("결측 100%", "결측 처리 후 남은 피험자 0명")

    _apply_log(rows, spec.numeric_columns, log)

    prepared = Prepared(spec.design)
    prepared.excluded.extend(dropped)
    prepared.imputed = imputed

    if spec.design == "two-group":
        rows, excluded, notes = _drop_two_group(rows, spec, group_levels, outlier)
        prepared.excluded.extend(excluded)
        prepared.notes.extend(notes)
        for s, v in rows:
            if s.group == group_levels[0]:
                prepared.a.append(v[spec.value])
                prepared.ids_a.append(s.sid)
                if spec.covariate:
                    prepared.cov_a.append(v[spec.covariate])
            elif s.group == group_levels[1]:
                prepared.b.append(v[spec.value])
                prepared.ids_b.append(s.sid)
                if spec.covariate:
                    prepared.cov_b.append(v[spec.covariate])
        prepared.ids = prepared.ids_a + prepared.ids_b
        if len(prepared.a) < MIN_N_PER_GROUP or len(prepared.b) < MIN_N_PER_GROUP:
            raise SkipScenario(
                "군 n<%d" % MIN_N_PER_GROUP,
                "%s n=%d · %s n=%d"
                % (group_levels[0], len(prepared.a), group_levels[1], len(prepared.b)),
            )
        if spec.covariate and len(prepared.a) + len(prepared.b) < 4:
            raise SkipScenario("공변량 모형 자유도 부족", "n<4")
        return prepared

    if spec.design == "paired":
        diffs = [v[spec.post] - v[spec.pre] for _, v in rows]
        rows, excluded, notes = _drop_by_series(rows, diffs, outlier, "차이점수")
        prepared.excluded.extend(excluded)
        prepared.notes.extend(notes)
        for s, v in rows:
            prepared.pre.append(v[spec.pre])
            prepared.post.append(v[spec.post])
            prepared.ids.append(s.sid)
        if len(prepared.pre) < MIN_N_PER_GROUP:
            raise SkipScenario("잔여 N<%d" % MIN_N_PER_GROUP,
                               "대응 쌍 %d개" % len(prepared.pre))
        return prepared

    # corr — x·y 각각 판정하고 합집합을 뺀다.
    xs = [v[spec.x] for _, v in rows]
    ys = [v[spec.y] for _, v in rows]
    mask_x = _outlier_mask(xs, outlier)
    mask_y = _outlier_mask(ys, outlier)
    if outlier != "없음" and len(xs) < MIN_N_FOR_OUTLIER_RULE:
        prepared.notes.append("n=%d — 이상치 규칙 미적용(n<%d)"
                              % (len(xs), MIN_N_FOR_OUTLIER_RULE))
    elif outlier == "±3SD":
        note = sd_rule_note(len(xs))
        if note:
            prepared.notes.append(note)
    for (s, v), bx, by in zip(rows, mask_x, mask_y):
        if bx or by:
            which = "+".join([n for n, f in ((spec.x, bx), (spec.y, by)) if f])
            prepared.excluded.append((s.sid, "이상치(%s, %s)" % (outlier, which)))
            continue
        prepared.x.append(v[spec.x])
        prepared.y.append(v[spec.y])
        prepared.ids.append(s.sid)
    if len(prepared.x) < 4:
        raise SkipScenario("잔여 N<4", "상관 계산에 필요한 최소 인원 미달")
    return prepared

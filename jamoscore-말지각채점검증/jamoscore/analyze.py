"""문항에서 다시 계산하고, 갈리는 곳만 골라낸다.

여기의 모든 함수는 :class:`~jamoscore.findings.Finding` 목록을 돌려주고,
**아무것도 고치지 않는다.** 입력 객체를 제자리에서 바꾸는 곳은 채점 결과를
``Item`` 에 채워 넣는 :func:`score_items` 하나뿐이다.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from . import binom
from .findings import CRITICAL, INFO, WARNING, Finding
from .hangul import NO_JONG, hangul_syllables, is_syllable, normalize
from .reader import Pass, parse_number
from .xlsx import column_name
from .spec import UNDECIDABLE

#: 응답 칸에 관찰 메모가 섞였다고 보는 신호.
#: 여는 괄호 안쪽 문자 집합에서 **여는 괄호도 제외**해야 한다 — 그러지 않으면
#: 닫히지 않은 괄호가 길게 이어질 때 역추적이 2차식으로 폭발한다(한 칸에 26초).
_MEMO_RE = re.compile(r"[（(\[{][^)）\]}（(\[{]{2,}[)）\]}]|같[았았]|듯|말하는|것\s*같")
#: 정규식을 적용하기 전에 자르는 길이. 전사 칸이 이보다 길면 그 자체가 이상 신호다.
MAX_RESPONSE_SCAN = 512
#: 전사가 아니라 '반응 없음'을 적어 둔 칸. 자리별 자모 대조를 하면 안 된다.
#:
#: 실측: 한 피험자가 응답 칸에 `모름` 이라고 9번 적었는데, 그것을 자리별로
#: 대조하면 `ㅟ→ㅗ`·`ㅚ→ㅗ`·`ㅛ→ㅗ`… 처럼 **모든 모음이 ㅗ로 간다**는 있지도
#: 않은 패턴이 혼동 행렬에 실린다(`모름` 의 첫 음절이 `모` 이기 때문이다).
NON_RESPONSE_MARKERS = (
    "모름", "모르", "몰라", "무응답", "무반응", "없음", "못들", "못 들",
    "안들", "안 들", "패스", "거부", "묵묵",
)
#: 한글 음절·공백 외 문자.
_NON_HANGUL_RE = re.compile(r"[^가-힣\s]")


# ---------------------------------------------------------------- 채점
def is_non_response(text: str, markers: Sequence[str] = ()) -> bool:
    """전사가 아니라 '반응 없음' 기록인가."""
    cell = normalize(text).replace(" ", "")
    if not cell:
        return False
    for marker in tuple(NON_RESPONSE_MARKERS) + tuple(markers):
        if marker.replace(" ", "") in cell:
            return True
    return False


def score_items(passes: Sequence[Pass]) -> None:
    """사람 점수를 숫자로 읽고, 목표음소 규칙 점수를 계산해 ``Item`` 에 채운다."""
    for p in passes:
        rule = p.spec.rule
        for item in p.items:
            value = parse_number(item.human_raw)
            if value is not None and 0 <= value <= p.spec.max_points:
                # 3.0 처럼 소수로 저장된 정수 점수를 정수로 정리한다.
                item.human = float(value)
            else:
                item.human = None
            item.rule, item.rule_reason = rule.score(item.stimulus, item.response)


# ---------------------------------------------------------- 점수 칸 위생
def check_score_cells(passes: Sequence[Pass]) -> List[Finding]:
    out: List[Finding] = []
    bad_text: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    out_of_range: List[Finding] = []
    for p in passes:
        for item in p.items:
            raw = item.human_raw
            value = parse_number(raw)
            if raw and value is None:
                bad_text[raw].append((item.subject, item.where))
            elif value is not None and not (0 <= value <= p.spec.max_points):
                out_of_range.append(Finding(
                    CRITICAL, "점수 범위 밖",
                    "점수 %s 는 %s 블록의 만점 %d 을 벗어납니다."
                    % (raw, p.spec.label, p.spec.max_points),
                    subject=item.subject, location=item.where,
                    evidence="%s → %s" % (item.stimulus, item.response)))
    for raw, places in sorted(bad_text.items()):
        shown = ", ".join(w for _, w in places[:4])
        more = "" if len(places) <= 4 else " 외 %d곳" % (len(places) - 4)
        out.append(Finding(
            WARNING, "점수 칸에 숫자 아닌 값",
            "점수 칸에 %r 가 들어 있어 숫자로 읽지 못했습니다(%d곳)." % (raw, len(places)),
            subject=places[0][0], location=shown + more,
            evidence="숫자가 아니므로 정답수에서 제외했습니다."))
    return out + out_of_range


# -------------------------------------------------------- 응답 칸 위생
def check_response_cells(passes: Sequence[Pass],
                         non_response: Sequence[str] = ()) -> List[Finding]:
    out: List[Finding] = []
    memo, foreign, blank_scored, scored_blank, refused = [], [], [], [], []
    for p in passes:
        for item in p.items:
            resp = item.response[:MAX_RESPONSE_SCAN]
            if is_non_response(resp, non_response):
                refused.append(item)
                continue
            if resp and _MEMO_RE.search(resp):
                memo.append(item)
            elif resp and _NON_HANGUL_RE.search(resp):
                foreign.append(item)
            if not resp and item.human is not None:
                blank_scored.append(item)
            if resp and item.human is None and item.human_raw == "":
                scored_blank.append(item)
    if refused:
        out.append(Finding(
            INFO, "전사가 아닌 '반응 없음' 기록",
            "응답 칸에 전사 대신 '모름' 류가 적힌 칸이 %d곳 있습니다 — 규칙 대조와 "
            "혼동 행렬에서 뺐습니다(자리별 자모 대조가 성립하지 않습니다)."
            % len(refused),
            subject=refused[0].subject,
            location=", ".join(i.where for i in refused[:5]),
            evidence="예: %s → %s" % (refused[0].stimulus,
                                      _clip(refused[0].response)),
            extra={"items": refused}))
    if memo:
        out.append(Finding(
            WARNING, "응답 칸에 관찰 메모 혼입",
            "응답 칸에 전사 이외의 관찰 메모로 보이는 글이 섞여 있습니다 — %d곳." % len(memo),
            subject=memo[0].subject,
            location=", ".join(i.where for i in memo[:5]),
            evidence=" / ".join("%s → %s" % (i.stimulus, _clip(i.response))
                                for i in memo[:3]),
            extra={"items": memo}))
    if foreign:
        out.append(Finding(
            WARNING, "응답 칸에 한글 외 문자",
            "응답 칸에 한글 음절이 아닌 문자가 섞여 있습니다 — %d곳." % len(foreign),
            subject=foreign[0].subject,
            location=", ".join(i.where for i in foreign[:5]),
            evidence=" / ".join("%s → %s" % (i.stimulus, _clip(i.response))
                                for i in foreign[:3]),
            extra={"items": foreign}))
    if blank_scored:
        out.append(Finding(
            WARNING, "응답이 비었는데 점수가 있음",
            "응답 칸이 비었는데 점수 칸에는 값이 있습니다 — %d곳. 무응답인지 "
            "전사를 빠뜨린 것인지 원 기록으로 확인이 필요합니다." % len(blank_scored),
            subject=blank_scored[0].subject,
            location=", ".join(i.where for i in blank_scored[:5]),
            evidence="예: %s (점수 %s)" % (blank_scored[0].stimulus,
                                          blank_scored[0].human_raw),
            extra={"items": blank_scored}))
    if scored_blank:
        out.append(Finding(
            WARNING, "응답은 있는데 점수가 비었음",
            "응답은 적혀 있는데 점수 칸이 비어 있습니다 — %d곳. 정답수에서 "
            "제외했습니다." % len(scored_blank),
            subject=scored_blank[0].subject,
            location=", ".join(i.where for i in scored_blank[:5]),
            evidence="예: %s → %s" % (scored_blank[0].stimulus,
                                      _clip(scored_blank[0].response)),
            extra={"items": scored_blank}))
    return out


def _clip(text: str, width: int = 18) -> str:
    text = normalize(text)
    return text if len(text) <= width else text[:width] + "…"


# ------------------------------------------------- 사람 채점 vs 규칙 채점
def compare_rule(passes: Sequence[Pass], non_response: Sequence[str] = ()
                 ) -> Tuple[List[Finding], List[dict], dict]:
    """사람 점수와 규칙 점수가 갈리는 문항만 골라낸다.

    돌려주는 세 번째 값은 대조 통계 — ``{비교, 일치, 불일치, 대조불가}``.
    """
    rows: List[dict] = []
    stats = Counter()
    for p in passes:
        for item in p.items:
            if item.human is None:
                stats["대조불가"] += 1
                continue
            if is_non_response(item.response, non_response):
                stats["대조불가"] += 1
                stats["사유:비전사 응답(모름 등)"] += 1
                continue
            if item.rule == UNDECIDABLE:
                stats["대조불가"] += 1
                stats["사유:" + item.rule_reason] += 1
                continue
            stats["비교"] += 1
            if float(item.rule) == float(item.human):
                stats["일치"] += 1
                continue
            stats["불일치"] += 1
            rows.append({
                "피험자": item.subject, "검사": item.test, "시점": item.timepoint,
                "단위": item.unit, "문항": item.seq, "위치": item.where,
                "제시": item.stimulus, "응답": item.response,
                "사람": _fmt_score(item.human), "규칙": _fmt_score(item.rule),
                "규칙근거": item.rule_reason,
            })
    findings: List[Finding] = []
    per_block = Counter()
    per_block_bad = Counter()
    for p in passes:
        for item in p.items:
            if item.human is None or item.rule == UNDECIDABLE:
                continue
            if is_non_response(item.response, non_response):
                continue
            per_block[(p.test, p.unit)] += 1
            if float(item.rule) != float(item.human):
                per_block_bad[(p.test, p.unit)] += 1
    suspect = []
    for key, total in sorted(per_block.items()):
        if total >= 20 and per_block_bad[key] >= total * 0.8:
            suspect.append((key, total, per_block_bad[key]))
    for (test, unit), total, bad in suspect:
        observed = _observed_max(passes, test, unit)
        declared = _declared_max(passes, test, unit)
        findings.append(Finding(
            CRITICAL, "채점 선언 의심",
            "%s(%s) 는 %d문항 중 %d문항(%.0f%%)이 규칙 채점과 어긋납니다 — 자료 "
            "오류보다 **선언이 틀렸을** 가능성이 큽니다. 점수 칸의 실제 최댓값은 "
            "%s인데 --subtest 의 만점은 %s이고, 목표 규칙이 이 검사에 맞는지도 "
            "확인하세요." % (test, unit, total, bad, 100.0 * bad / total,
                            observed, declared),
            subject="", location="--subtest %s" % test,
            evidence="이 비율이면 규칙대조.csv 전체가 잡음입니다."))
    if rows and not suspect:
        pct = 100.0 * len(rows) / max(1, stats["비교"])
        findings.append(Finding(
            WARNING, "사람 채점과 규칙 채점이 다름(확인 필요)",
            "%d/%d 문항 (%.1f%%) 에서 사람 점수와 목표음소 규칙 점수가 갈립니다. "
            "어느 쪽이 맞는지는 이 툴이 판정하지 않습니다 — 원 기록으로 확인하세요."
            % (len(rows), stats["비교"], pct),
            subject=rows[0]["피험자"],
            location=", ".join(r["위치"] for r in rows[:5]),
            evidence=" / ".join("%s %s→%s 사람=%s 규칙=%s"
                                % (r["검사"], r["제시"], _clip(r["응답"]),
                                   r["사람"], r["규칙"]) for r in rows[:4]),
            extra={"rows": rows, "rate": pct}))
    return findings, rows, dict(stats)


def _observed_max(passes, test, unit) -> str:
    values = [it.human for p in passes if p.test == test and p.unit == unit
              for it in p.items if it.human is not None]
    return _fmt_score(max(values)) if values else "(없음)"


def _declared_max(passes, test, unit) -> str:
    for p in passes:
        if p.test == test and p.unit == unit:
            return str(p.spec.max_points)
    return "(없음)"


def _fmt_score(value) -> str:
    if value is None or value == UNDECIDABLE:
        return str(value)
    f = float(value)
    return str(int(f)) if f == int(f) else ("%.1f" % f)


# --------------------------------------- 동일 (제시, 응답) 쌍 다른 채점
def inconsistent_pairs(passes: Sequence[Pass]) -> Tuple[List[Finding], List[dict]]:
    """같은 검사·같은 (제시, 응답) 인데 점수가 다른 사례.

    채점 규칙이 무엇이든 **반박할 수 없는** 소견이라 규칙 대조와 독립적으로 낸다.
    """
    table: Dict[Tuple[str, str, str, str], Dict[float, List]] = defaultdict(
        lambda: defaultdict(list))
    for p in passes:
        for item in p.items:
            if item.human is None or not item.stimulus or not item.response:
                continue
            key = (item.test, item.unit, item.stimulus, item.response)
            table[key][float(item.human)].append(item)
    rows, findings = [], []
    for key in sorted(table, key=lambda k: (k[0], k[1], k[2], k[3])):
        scores = table[key]
        if len(scores) < 2:
            continue
        test, unit, stim, resp = key
        detail = []
        for score in sorted(scores):
            items = scores[score]
            detail.append("%s점: %s" % (
                _fmt_score(score),
                _names(sorted({i.subject for i in items}), 6)))
            for i in items:
                rows.append({
                    "검사": test, "단위": unit, "제시": stim, "응답": resp,
                    "피험자": i.subject, "시점": i.timepoint, "위치": i.where,
                    "점수": _fmt_score(score),
                })
        findings.append(Finding(
            WARNING, "동일 쌍 다른 채점",
            "%s 검사에서 같은 (제시, 응답) = (%s → %s) 가 서로 다른 점수를 "
            "받았습니다 — %s." % (test, stim, _clip(resp), " / ".join(detail)),
            subject=sorted({i.subject for lst in scores.values() for i in lst})[0],
            location=", ".join(sorted({i.where for lst in scores.values()
                                       for i in lst})[:6]),
            evidence="채점 규칙이 무엇이든 두 문항이 같은 점수를 받아야 합니다."))
    return findings, rows


# -------------------------------------- 같은 문항을 두 패스가 다르게 전사
def cross_pass_transcripts(passes: Sequence[Pass]) -> List[Finding]:
    """한 블록을 두 번 채점했는데 **같은 자극의 응답 전사가 서로 다른** 경우."""
    grouped: Dict[Tuple[str, str, str], List[Pass]] = defaultdict(list)
    for p in passes:
        grouped[(p.subject, p.test, p.timepoint)].append(p)
    out = []
    for key, plist in sorted(grouped.items()):
        if len(plist) < 2:
            continue
        base = plist[0]
        base_map = {}
        for item in base.items:
            base_map.setdefault(item.stimulus, item)
        for other in plist[1:]:
            for item in other.items:
                ref = base_map.get(item.stimulus)
                if ref is None or not ref.response or not item.response:
                    continue
                if ref.response != item.response:
                    out.append(Finding(
                        WARNING, "같은 문항을 두 번 다르게 전사",
                        "%s %s-%s 의 자극 '%s' 가 %s 패스에서는 '%s', %s 패스에서는 "
                        "'%s' 로 적혀 있습니다. 한 번의 발화를 두 번 옮긴 것이라면 "
                        "둘 중 하나가 원 기록과 다릅니다."
                        % (key[0], key[1], key[2], item.stimulus, base.unit,
                           _clip(ref.response), other.unit, _clip(item.response)),
                        subject=key[0],
                        location="%s / %s" % (ref.where, item.where),
                        evidence="자극 %s" % item.stimulus))
    return out


# ------------------------------------------------------ 시트 안의 요약 %
def check_inhsheet_summary(passes: Sequence[Pass]) -> Tuple[List[Finding], int, int]:
    out, compared, mismatched = [], 0, 0
    uncomparable = []
    for p in passes:
        if p.reported is None:
            continue
        reported = parse_number(p.reported)
        recomputed = p.percent
        if reported is None:
            continue
        compared += 1
        if recomputed is None:
            uncomparable.append(p)
            continue
        if abs(reported - recomputed) > 0.051:
            mismatched += 1
            out.append(Finding(
                CRITICAL, "시트 요약값 불일치",
                "%s %s-%s(%s): 시트에 적힌 %s%% 와 문항 재계산 %.1f%% "
                "(%s/%d) 가 다릅니다."
                % (p.subject, p.test, p.timepoint, p.unit, p.reported,
                   recomputed, _fmt_score(p.earned), p.denominator),
                subject=p.subject,
                location="%s %d행" % (p.sheet, p.reported_row or 0),
                evidence="분모 %d = 문항 %d × 만점 %d"
                         % (p.denominator, len(p.items), p.spec.max_points)))
    if uncomparable:
        out.append(Finding(
            WARNING, "시트 요약과 대조 불가",
            "시트에 적힌 요약 %% %d개는 문항에서 다시 계산할 수 없었습니다 — "
            "해당 블록의 점수 칸에 읽지 못한 값이 있습니다." % len(uncomparable),
            subject=uncomparable[0].subject,
            location=_names(["%s %s-%s(%s)" % (p.subject, p.test, p.timepoint, p.unit)
                             for p in uncomparable], 6)))
    return out, compared, mismatched


# --------------------------------------------------------- 불가능한 %
def impossible_percent(passes: Sequence[Pass]) -> List[Finding]:
    """어떤 정수 정답수로도 만들 수 없는 % 는 옮겨 적는 과정에서 생긴 값이다."""
    out = []
    for p in passes:
        if p.reported is None:
            continue
        reported = parse_number(p.reported)
        if reported is None or p.denominator == 0:
            continue
        if not reachable_percent(reported, p.denominator):
            out.append(Finding(
                CRITICAL, "불가능한 백분율",
                "%s %s-%s(%s) 의 시트 값 %s%% 는 분모 %d 로는 어떤 정답수로도 "
                "나올 수 없는 값입니다."
                % (p.subject, p.test, p.timepoint, p.unit, p.reported,
                   p.denominator),
                subject=p.subject,
                location="%s %d행" % (p.sheet, p.reported_row or 0),
                evidence="가능한 이웃 값: %s" % nearest_percents(reported, p.denominator)))
    return out


def reachable_percent(value: float, denominator: int, tol: float = 0.51) -> bool:
    """``value`` 가 ``k/denominator`` 에서 나올 수 있는 백분율인가.

    **반올림된 값이 아니라 원값 ``100k/n`` 과 비교한다.** 파이썬의 ``round`` 는
    짝수 반올림이라 6.25 → 6.2 이지만 엑셀·손계산은 6.3 이다. 반올림된 값끼리
    비교하면 정상적인 6.3 을 '불가능한 백분율'이라고 말하게 된다.

    기본 허용오차 0.51 은 "소수 첫째 자리까지 적었거나 정수로 반올림했을 수 있다"
    를 함께 받아들이기 위한 값이다 — 이 검사는 **명백히 불가능한 값만** 잡는다.
    """
    if denominator <= 0:
        return False
    for k in range(denominator + 1):
        if abs(100.0 * k / denominator - value) <= tol:
            return True
    return False


def nearest_percents(value: float, denominator: int, count: int = 2) -> str:
    cands = sorted((abs(100.0 * k / denominator - value), k)
                   for k in range(denominator + 1))
    return " · ".join("%.1f%%(%d/%d)" % (round(100.0 * k / denominator, 1), k,
                                         denominator)
                      for _, k in cands[:count])


# --------------------------------------------------------- 바닥·천장
def floor_ceiling(passes: Sequence[Pass]) -> List[Finding]:
    """바닥·천장 도달을 **검사별로 묶어** 한 건씩만 낸다(피험자마다 한 줄이면 못 읽는다)."""
    ceiling = defaultdict(list)
    floor = defaultdict(list)
    for p in sorted(passes, key=lambda x: (x.test, x.timepoint, x.unit, x.subject)):
        pct = p.percent
        if pct is None:
            continue
        if is_blank_pass(p):
            # 응답이 전부 비어 있는데 0점이 찍힌 블록은 '바닥 수행'이 아니라
            # 미실시일 가능성이 크다. 별도 소견으로 따로 낸다.
            continue
        if pct >= 100.0:
            ceiling[(p.test, p.timepoint, p.unit)].append(p.subject)
        elif pct <= 0.0:
            floor[(p.test, p.timepoint, p.unit)].append(p.subject)
    out = []
    for label, table, direction in (
        ("천장 도달", ceiling, "더 좋아질"),
        ("바닥 도달", floor, "더 나빠질"),
    ):
        if not table:
            continue
        blocks = ["%s-%s(%s) %s" % (k[0], k[1], k[2], _names(v, 6))
                  for k, v in sorted(table.items())]
        total = sum(len(v) for v in table.values())
        out.append(Finding(
            INFO, label,
            "%d블록 %d명 — 이 블록에서는 %s 폭이 구조적으로 없습니다. %s"
            % (len(blocks), total, direction, " / ".join(blocks)),
            location="문항점수.csv"))
    return out


def is_blank_pass(p: Pass) -> bool:
    """응답이 한 칸도 없는데 점수는 전부 0 인 블록 — 미실시·탈락일 수 있다."""
    if not p.items:
        return False
    if any(it.response for it in p.items):
        return False
    scored = [it for it in p.items if it.human is not None]
    return bool(scored) and all(it.human == 0 for it in scored)


def blank_passes(passes: Sequence[Pass]) -> Tuple[List[Finding], set]:
    """미실시로 보이는 블록을 찾아낸다.

    이 툴의 하류(`statwise`·`longistat`)에 0% 로 흘려보내면 그 0 이 실제 수행으로
    분석에 들어간다. 그래서 **소견으로 내고, 넘겨주는 CSV 에서도 뺀다.**
    """
    blank = [p for p in passes if is_blank_pass(p)]
    if not blank:
        return [], set()
    grouped = defaultdict(list)
    for p in blank:
        grouped[(p.test, p.timepoint, p.unit)].append(p.subject)
    out = []
    for key, subjects in sorted(grouped.items()):
        out.append(Finding(
            WARNING, "응답이 전부 비었는데 0점",
            "%s-%s(%s): 응답 칸이 하나도 없는데 점수가 전부 0 인 피험자 %d명(%s) — "
            "미실시·탈락이 0점으로 기록된 것인지 확인하세요. 이 블록은 "
            "statwise·longistat 로 넘기는 CSV 에서 뺐습니다."
            % (key[0], key[1], key[2], len(subjects), _names(subjects)),
            subject=subjects[0], location=", ".join(subjects[:8]),
            evidence="0% 를 '바닥 수행'으로 세지 않았습니다."))
    return out, {p.key for p in blank}


def duplicate_blocks(passes: Sequence[Pass]) -> List[Finding]:
    """같은 (피험자·검사·시점·단위) 블록이 한 시트에 두 번 나온 경우.

    뒤에 읽은 것이 앞엣것을 덮어써서, 리포트가 스스로 모순되는 말을 하게 된다.
    """
    seen = defaultdict(list)
    for p in passes:
        seen[p.key].append(p)
    out = []
    for key, group in sorted(seen.items()):
        if len(group) < 2:
            continue
        out.append(Finding(
            CRITICAL, "같은 블록이 두 번 나옴",
            "%s %s-%s(%s) 블록이 시트에 %d번 나옵니다(%s열). 어느 것을 쓸지 "
            "정할 수 없어 계산에서 뒤엣것만 남습니다 — 시트를 정리하거나 "
            "--subtest 로 패스를 나눠 선언하세요."
            % (key[0], key[1], key[2], key[3], len(group),
               "·".join(column_name(p.col) for p in group)),
            subject=key[0], location=group[0].sheet))
    return out


def _names(subjects: Sequence[str], limit: int = 8) -> str:
    shown = "·".join(subjects[:limit])
    return shown if len(subjects) <= limit else "%s 외 %d명" % (shown, len(subjects) - limit)


# ------------------------------------------------------------- 혼동행렬
def confusion(passes: Sequence[Pass], min_count: int = 3,
              non_response: Sequence[str] = ()):
    """``{자모위치: Counter[(목표, 응답)]}``, 게이트로 잘라낸 칸 수, 제외한 문항 수.

    **음절 수가 다른 응답은 집계에서 뺀다.** 전사 칸에는 `모름`·`휴지` 같은
    비전사 응답이 섞여 들어오는데, 그걸 자리별로 대조하면 있지도 않은 혼동 패턴
    (예: '모든 모음이 ㅗ로 간다')을 만들어 낸다. 뺀 개수는 자백에 적는다.
    """
    tables: Dict[str, Counter] = defaultdict(Counter)
    skipped = 0
    for p in passes:
        rule = p.spec.rule
        for item in p.items:
            if not item.stimulus or not item.response:
                continue
            if is_non_response(item.response, non_response):
                skipped += 1
                continue
            if len(hangul_syllables(item.stimulus)) != len(
                    hangul_syllables(item.response)):
                skipped += 1
                continue
            for pos, target, got in rule.targets(item.stimulus, item.response):
                tables[pos][(target, got)] += 1
    gated, dropped = {}, 0
    for pos, counter in tables.items():
        keep = Counter()
        for key, n in counter.items():
            if key[0] == key[1]:
                keep[key] = n            # 정답 칸은 항상 남긴다(대각선)
            elif n >= min_count:
                keep[key] = n
            else:
                dropped += 1
        gated[pos] = keep
    return tables, gated, dropped, skipped


def top_confusions(gated: Dict[str, Counter], limit: int = 6):
    """대각선(정답)을 뺀 상위 혼동 목록."""
    out = {}
    for pos, counter in gated.items():
        errs = [(k, n) for k, n in counter.items() if k[0] != k[1]]
        errs.sort(key=lambda kv: (-kv[1], kv[0]))
        out[pos] = errs[:limit]
    return out


# ----------------------------------------------------------- 임계차
def critical_differences(passes: Sequence[Pass], timepoints: Sequence[str],
                         alpha: float = 0.05) -> Tuple[List[dict], List[Finding]]:
    """사전→사후 변화가 검사 재검사 오차 범위를 벗어나는지.

    ``단어`` 단위(문항이 서로 독립인 0/1 채점)에서만 계산한다. 음소 점수는 한
    단어 안의 세 음소가 서로 독립이 아니라 이항 가정이 성립하지 않는다.
    """
    if len(timepoints) < 2:
        return [], []
    pre_name, post_name = timepoints[0], timepoints[1]
    by = {}
    for p in passes:
        by[(p.subject, p.test, p.timepoint, p.unit)] = p
    rows, findings = [], []
    exceeded = Counter()
    total = Counter()
    fractional = []
    for (subject, test, tp, unit), p in sorted(by.items()):
        if tp != pre_name or unit != "단어" or p.spec.max_points != 1:
            # 이항 모형은 문항이 서로 독립인 0/1 채점에서만 성립한다.
            continue
        post = by.get((subject, test, post_name, unit))
        pre_pct, post_pct = p.percent, (post.percent if post else None)
        n = len(p.items)
        pre_k = _exact_count(p) if pre_pct is not None else None
        post_k = _exact_count(post) if (post and post_pct is not None) else None
        if pre_pct is not None and pre_k is None:
            fractional.append(p)
        same_n = bool(post and len(post.items) == n)
        if pre_k is not None and not (0 <= pre_k <= n):
            pre_k = None                      # 만점 선언이 어긋난 경우 — 판정하지 않는다
        result = binom.verdict(pre_k, post_k if same_n else None, n, alpha)
        lo = hi = None
        crit_text = ""
        if pre_k is not None and n and 0 <= pre_k <= n:
            lo, hi = binom.score_interval(pre_k, n, alpha)
            crit_text = binom.format_critical_difference(pre_k, n, alpha)
        rows.append({
            "피험자": subject, "검사": test, "단위": unit, "문항수": n,
            "사전정답": "" if pre_k is None else pre_k,
            "사전%": "" if pre_pct is None else "%.1f" % pre_pct,
            "사후정답": "" if post_k is None else post_k,
            "사후%": "" if post_pct is None else "%.1f" % post_pct,
            "변화%p": ("" if (pre_pct is None or post_pct is None)
                       else "%.1f" % (post_pct - pre_pct)),
            "오차범위정답수": "" if lo is None else "%d~%d" % (lo, hi),
            "95%임계차%p(하락/상승)": crit_text,
            "판정": result,
        })
        total[test] += 1
        if result == "초과":
            exceeded[test] += 1
    if fractional:
        findings.append(Finding(
            WARNING, "정답수가 정수가 아님",
            "0/1 채점이라고 선언한 블록에 소수 점수가 있어 이항 임계차를 계산하지 "
            "않았습니다 — %d블록(%s)." % (
                len(fractional),
                _names(["%s %s-%s" % (p.subject, p.test, p.timepoint)
                        for p in fractional], 5)),
            subject=fractional[0].subject,
            evidence="이항 모형은 소수 정답수를 받아들이지 않습니다."))
    for test in sorted(total):
        findings.append(Finding(
            INFO, "개인 내 변화와 검사 오차 범위",
            "%s: 개인 내 변화가 검사 재검사 오차 범위를 벗어난 피험자 %d/%d명."
            % (test, exceeded[test], total[test]),
            subject="", location="임계차.csv",
            evidence="사전 점수의 정확 이항(Clopper–Pearson) %g%% 구간 기준. "
                     "사후 점수의 표본오차는 넣지 않은 **한 점수 구간**이라, "
                     "두 점수 차의 임계차 표보다 좁습니다(더 쉽게 '초과'가 됩니다)."
                     % ((1 - alpha) * 100)))
    return rows, findings


def _exact_count(p) -> Optional[int]:
    """정수 정답수. 소수(0.5 등)가 섞여 있으면 None — 반올림해 숨기지 않는다."""
    if p is None:
        return None
    value = p.earned
    if abs(value - round(value)) > 1e-9:
        return None
    return int(round(value))


# ------------------------------------------------------- 자극 리스트 정합
class ListAssignment:
    """한 블록이 정답 시트의 **어느 리스트**를 썼는가."""

    __slots__ = ("column", "order_same")

    def __init__(self, column: str, order_same: bool):
        self.column = column
        self.order_same = order_same


def check_answer_key(passes: Sequence[Pass], key_lists: Dict[str, Dict[str, List[str]]],
                     key_sheet: str):
    """제시 자극을 정답 시트의 리스트들과 대조한다.

    **어느 리스트를 썼는지는 고정하지 않는다.** 사전·사후에 리스트를 맞바꾸는
    상쇄균형(counterbalancing) 설계가 흔하고, 그걸 모르고 '1번=사전'으로 박으면
    정상 자료의 절반을 불일치로 토해낸다.

    돌려주는 값: ``(소견목록, 대조못한블록목록, {패스키: ListAssignment})``
    """
    out: List[Finding] = []
    unmatched: List[str] = []
    seen_unmatched = set()
    assignment: Dict[Tuple[str, str, str, str], ListAssignment] = {}
    order_diff = Counter()
    used = defaultdict(Counter)
    for p in sorted(passes, key=lambda x: (x.subject, x.test, x.timepoint, x.unit)):
        candidates = key_lists.get(p.test)
        if not candidates:
            token = p.test
            if token not in seen_unmatched:
                seen_unmatched.add(token)
                unmatched.append(token)
            continue
        if not p.items:
            continue          # 빈 패스는 '레이아웃 이상'에서 이미 보고했다
        actual = [it.stimulus for it in p.items]
        exact = [name for name, lst in sorted(candidates.items()) if lst == actual]
        same_set = [name for name, lst in sorted(candidates.items())
                    if sorted(lst) == sorted(actual)]
        if exact:
            assignment[p.key] = ListAssignment(exact[0], True)
            used[(p.test, p.timepoint, p.unit)][exact[0]] += 1
            continue
        if same_set:
            assignment[p.key] = ListAssignment(same_set[0], False)
            used[(p.test, p.timepoint, p.unit)][same_set[0]] += 1
            order_diff[(p.test, p.unit)] += 1
            continue
        best, score = None, -1
        for name, lst in sorted(candidates.items()):
            overlap = len(set(lst) & set(actual))
            if overlap > score:
                best, score = name, overlap
        expected = candidates.get(best, [])
        missing = [x for x in expected if x not in actual]
        extra = [x for x in actual if x not in expected]
        dup = [x for x, n in Counter(actual).items() if n > 1]
        parts = []
        if len(actual) != len(expected):
            parts.append("자극 개수가 다름(%d vs %d)" % (len(actual), len(expected)))
        if missing:
            parts.append("빠진 자극 %d개(%s)" % (len(missing), ", ".join(missing[:4])))
        if extra:
            parts.append("정답 시트에 없는 자극 %d개(%s)" % (len(extra), ", ".join(extra[:4])))
        if dup:
            parts.append("중복 자극 %d개(%s)" % (len(dup), ", ".join(dup[:4])))
        out.append(Finding(
            CRITICAL, "자극 리스트 불일치",
            "%s %s-%s(%s): 정답 시트의 어느 리스트와도 맞지 않습니다 — %s "
            "(가장 가까운 리스트: %s)"
            % (p.subject, p.test, p.timepoint, p.unit,
               " · ".join(parts) or "목록이 다름", best),
            subject=p.subject, location=p.sheet,
            evidence="정답 시트 '%s' 대조" % key_sheet))
    by_test = defaultdict(list)
    for (test, timepoint, unit), counter in sorted(used.items()):
        by_test[test].append("%s(%s) %s" % (
            timepoint, unit,
            "·".join("%s %d명" % (k, v) for k, v in sorted(counter.items()))))
    for test, parts in sorted(by_test.items()):
        out.append(Finding(
            INFO, "사용한 자극 리스트",
            "%s: %s" % (test, " / ".join(parts)),
            location=key_sheet,
            evidence="어느 리스트를 썼는지는 자료가 말하게 두었습니다"
                     "(상쇄균형 설계를 깨뜨리지 않기 위해)."))
    for (test, unit), n in sorted(order_diff.items()):
        out.append(Finding(
            INFO, "제시 순서가 정답 시트와 다름",
            "%s(%s): %d개 블록에서 자극 목록은 같고 제시 순서만 다릅니다 — "
            "무작위 제시라면 정상입니다." % (test, unit, n),
            location=key_sheet))
    return out, unmatched, assignment


def check_same_list_across_time(passes: Sequence[Pass], timepoints: Sequence[str],
                                assignment=None) -> List[Finding]:
    """사전·사후에 **같은 자극 목록**을 쓴 경우 — 변화량에 학습효과가 섞인다.

    정답 시트가 있으면 '어느 리스트를 썼는가'로, 없으면 자극 집합이 같은지로 본다.
    똑같은 순서까지 같으면 치명(옮겨 적기 사고 가능), 집합만 같으면 경고.
    """
    if len(timepoints) < 2:
        return []
    stim = {}
    for p in passes:
        if p.items:
            stim[p.key] = [i.stimulus for i in p.items]
    resp = {}
    for p in passes:
        if p.items:
            resp[p.key] = [i.response for i in p.items]
    same_order = defaultdict(list)
    same_set = defaultdict(list)
    same_responses = defaultdict(list)
    for key, first in sorted(stim.items()):
        subject, test, tp, unit = key
        if tp != timepoints[0]:
            continue
        second = stim.get((subject, test, timepoints[1], unit))
        if not second:
            continue
        if assignment:
            a = assignment.get(key)
            b = assignment.get((subject, test, timepoints[1], unit))
            if a is None or b is None:
                continue
            if a.column != b.column:
                continue
        elif sorted(first) != sorted(second):
            continue
        if first == second:
            other_key = (subject, test, timepoints[1], unit)
            if resp.get(key) and resp.get(key) == resp.get(other_key):
                # 자극도 응답도 완전히 같다 — 한쪽을 복사해 넣었을 가능성이 높다.
                same_responses[(test, unit)].append(subject)
            else:
                same_order[(test, unit)].append(subject)
        else:
            same_set[(test, unit)].append(subject)
    out = []
    for (test, unit), subjects in sorted(same_responses.items()):
        out.append(Finding(
            CRITICAL, "사전·사후가 자극도 응답도 동일",
            "%s(%s): 사전과 사후의 자극과 응답이 **한 글자도 다르지 않은** 피험자 "
            "%d명(%s) — 한쪽을 복사해 넣었는지 확인하세요."
            % (test, unit, len(subjects), _names(subjects)),
            subject=subjects[0], location=", ".join(subjects[:8]),
            evidence="응답까지 전부 같을 확률은 사실상 없습니다."))
    for (test, unit), subjects in sorted(same_order.items()):
        out.append(Finding(
            WARNING, "사전·사후 자극이 순서까지 동일",
            "%s(%s): 사전과 사후의 자극 목록과 순서가 같은 피험자 %d명(%s) — "
            "단일 목록 검사(K-CNC 계열)에서는 정상일 수 있지만, 변화량에 "
            "학습효과가 섞입니다."
            % (test, unit, len(subjects), _names(subjects)),
            subject=subjects[0], location=", ".join(subjects[:8]),
            evidence="자극 문자열만 봤습니다. 난이도 동등성은 보지 않았습니다."))
    for (test, unit), subjects in sorted(same_set.items()):
        out.append(Finding(
            WARNING, "사전·사후 같은 자극 목록",
            "%s(%s): %d명에서 사전과 사후가 같은 자극 목록입니다(순서는 다름) — "
            "검사 설계상 의도한 것일 수 있지만, 변화량에 학습효과가 섞입니다."
            % (test, unit, len(subjects)),
            subject=subjects[0], location=", ".join(subjects[:8]),
            evidence="목록이 같은지만 봤습니다. 난이도 동등성은 보지 않았습니다."))
    return out

"""기본 위생 점검 — **매번 우는 체커는 두 번 다시 안 열린다.**

여기 있는 항목은 전부 "병합 결과를 그대로 믿기 전에 눈으로 확인할 값어치가
있는가"를 기준으로 골랐고, 거짓양성이 날 만한 것은 심각도를 `정보`로 내려
종료코드에 영향을 주지 않게 했다. 깨끗한 자료에서 경고가 하나라도 뜨면 그
항목은 잘못 설계된 것이다(그 성질을 테스트가 지킨다).

* **완전 결측 열 / 상수 열** — 정보. 내보내기 설정이 틀렸을 때 나온다.
* **범위 이탈** — 경고. `--spec` 에 사람이 적어 넣은 범위에서만 본다.
  범위를 안 적으면 이 검사는 아예 돌지 않는다(임의의 정상범위를 지어내지 않는다).
* **단위 의심** — 정보. 같은 이름의 변수가 두 파일에 있고 중앙값이 약 60배
  또는 1000배 차이면 분↔시간, ms↔s 를 의심한다. **자동 변환은 하지 않는다.**
* **타임존 혼재** — 심각(병합 불가). 오프셋이 섞이면 결과가 조용히 몇 시간
  밀린다. 변환하지 않고 멈춘다.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

from .dataio import Frame, is_missing, parse_number
from .detect import norm_name
from .issues import CRITICAL, INFO, WARNING, Issue, IssueLog
from .keys import common_head
from .merge import FilePlan

__all__ = ["check_columns", "check_ranges", "check_units", "check_timezones",
           "check_key_overlap", "check_prefix_conflict", "check_yield"]

# 두 파일이 이 비율보다 적게 겹치면 "정상적인 결측"이 아니라 "ID 체계가 다른
# 것 아닌가"를 의심한다. 임상 자료에서 파일 간 피험자 결측은 흔하지만,
# 절반 이상이 어긋나는 것은 대개 표기 문제다.
_LOW_OVERLAP = 0.5

# 값 목록을 문제목록에 나열할 때의 상한(리포트가 소음이 되지 않도록).
_MAX_LISTED = 10
# 원본 셀을 리포트에 옮길 때의 길이 상한. 자유기재 칸에는 이름·연락처가 들어
# 있을 수 있으므로 통째로 옮기지 않는다.
_MAX_SNIPPET = 16


def _snippet(value: str) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= _MAX_SNIPPET else text[:_MAX_SNIPPET] + "…"


# 변수 이름에서 단위를 뜻하는 꼬리표. 같은 변수인지 비교할 때만 떼어 낸다.
_UNIT_SUFFIX_RE = re.compile(
    r"(_?(?:min|mins|minute|minutes|분|sec|secs|s|초|ms|msec|h|hr|hrs|hour|hours|시간)|"
    r"\(.*\))$")


def _base_name(column: str) -> str:
    probe = norm_name(column)
    prev = None
    while probe != prev:
        prev = probe
        probe = _UNIT_SUFFIX_RE.sub("", probe)
    return probe


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def check_columns(plans: Sequence[FilePlan], issues: IssueLog) -> None:
    """완전 결측 열과 상수 열을 정보로 보고한다."""
    for plan in plans:
        frame = plan.frame
        if frame.nrows == 0:
            continue
        for column in plan.value_columns:
            cells = frame.column(column)
            present = [c.strip() for c in cells if not is_missing(c)]
            if not present:
                issues.add(Issue(
                    file=frame.label, kind="완전결측열", severity=INFO,
                    key=column,
                    message=f"'{column}' 열의 {len(cells)}행이 모두 비어 있습니다",
                    advice="내보내기 설정이나 열 이름을 확인하세요. 병합에는 그대로 넘깁니다."))
            elif len(set(present)) == 1 and frame.nrows >= 3:
                issues.add(Issue(
                    file=frame.label, kind="상수열", severity=INFO, key=column,
                    message=(f"'{column}' 열의 값이 전부 "
                             f"'{_snippet(present[0])}' 입니다"),
                    advice="의도한 것이면 무시하세요(분석에서는 쓸 수 없는 열입니다)."))


def check_ranges(plans: Sequence[FilePlan], ranges: Dict[str, Sequence[float]],
                 issues: IssueLog) -> None:
    """`--spec` 의 `ranges` 에 적힌 변수만 범위를 본다."""
    if not ranges:
        return
    lookup = {norm_name(k): (float(v[0]), float(v[1]))
              for k, v in ranges.items()
              if isinstance(v, (list, tuple)) and len(v) == 2}
    if not lookup:
        return

    for plan in plans:
        frame = plan.frame
        for column in plan.value_columns:
            bounds = lookup.get(norm_name(column))
            if bounds is None:
                continue
            low, high = bounds
            offenders: List[Tuple[int, str]] = []
            for i, cell in enumerate(frame.column(column)):
                if is_missing(cell):
                    continue
                value = parse_number(cell)
                if value is None:
                    continue
                if value < low or value > high:
                    offenders.append((i, cell.strip()))
            if not offenders:
                continue
            shown = offenders[:_MAX_LISTED]
            detail = ", ".join(f"{frame.source_line(i)}행={_snippet(v)}"
                               for i, v in shown)
            issues.add(Issue(
                file=frame.label, kind="범위이탈", severity=WARNING, key=column,
                line=str(frame.source_line(offenders[0][0])),
                message=(f"'{column}' 에 범위 [{low:g}, {high:g}] 를 벗어난 값이 "
                         f"{len(offenders)}건 있습니다 ({detail}"
                         f"{' 외' if len(offenders) > len(shown) else ''})"),
                advice="입력 오류인지, 단위가 다른지 원본에서 확인하세요. 값은 그대로 넘깁니다."))


def check_units(plans: Sequence[FilePlan], issues: IssueLog) -> None:
    """같은 변수가 파일마다 다른 단위로 들어왔을 가능성을 **정보로만** 알린다."""
    profiles: Dict[str, List[Tuple[str, str, float, int]]] = {}
    for plan in plans:
        frame = plan.frame
        for column in plan.value_columns:
            numbers = [n for n in (parse_number(c) for c in frame.column(column))
                       if n is not None and n > 0]
            if len(numbers) < 5:
                continue
            profiles.setdefault(_base_name(column), []).append(
                (frame.label, column, _median(numbers), len(numbers)))

    for base, entries in sorted(profiles.items()):
        if len(entries) < 2 or not base:
            continue
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                (fa, ca, ma, _), (fb, cb, mb, _) = entries[i], entries[j]
                if fa == fb or ma <= 0 or mb <= 0:
                    continue
                ratio = max(ma, mb) / min(ma, mb)
                for factor, meaning in ((60.0, "분 ↔ 시간 또는 초 ↔ 분"),
                                        (1000.0, "ms ↔ s 또는 g ↔ kg"),
                                        (3600.0, "초 ↔ 시간")):
                    if 0.8 * factor <= ratio <= 1.25 * factor:
                        issues.add(Issue(
                            file=f"{fa} ↔ {fb}", kind="단위의심", severity=INFO,
                            key=base,
                            message=(f"'{ca}'({fa}, 중앙값 {ma:.4g}) 와 "
                                     f"'{cb}'({fb}, 중앙값 {mb:.4g}) 가 약 "
                                     f"{ratio:.0f}배 차이입니다 — {meaning} 가능성"),
                            advice="자동 변환은 하지 않았습니다. 단위를 확인하세요."))
                        break


def check_prefix_conflict(prefixes: Dict[str, str], issues: IssueLog) -> bool:
    """파일마다 **다른** 접두어가 자동 제거되려 하면 그 제거를 취소한다.

    `BELL-001-01..04` 와 `BELL-002-01..04` 는 서로 다른 사이트의 다른 사람들인데,
    각각 자기 파일 안에서는 접두어가 상수라 둘 다 `01..04` 로 줄어든다. 그러면
    남남이 조용히 한 사람으로 병합된다 — `--unify-id-heads` 가 경고하는 바로 그
    위험이 기본 동작에서 일어나는 것이다.

    그래서 **보고하고 넘어가지 않고, 자동 제거를 아예 하지 않는다.** 정말 떼야
    한다면 `--spec` 의 `id_prefixes` 로 사람이 하나를 지정하면 된다.
    반환값: 자동 접두어 제거를 계속해도 되는가.
    """
    found = {label: value for label, value in prefixes.items() if value}
    if len(set(found.values())) <= 1:
        return True
    detail = "; ".join(f"{label}: '{value}'"
                       for label, value in sorted(found.items()))
    issues.add(Issue(
        file="(전체)", kind="접두어불일치", severity=CRITICAL,
        message=(f"파일마다 서로 다른 ID 접두어가 자동 제거될 뻔했습니다 — "
                 f"{detail}. 떼고 나면 양쪽이 같은 번호로 줄어들어 **서로 다른 "
                 "사람이 한 사람으로 병합**됩니다. 자동 제거를 취소했습니다."),
        advice=("접두어가 사이트/코호트를 뜻한다면 그대로 두는 것이 맞습니다. "
                "정말 떼야 한다면 `--spec` 의 `id_prefixes` 에 뗄 접두어 하나를 "
                "직접 적으세요.")))
    return False


def check_yield(plans: Sequence[FilePlan], issues: IssueLog,
                final_rows: int, unmatched: int, total_rows: int) -> None:
    """병합 결과가 비었거나 대부분이 짝을 못 찾았으면 알린다.

    표가 0행인데 "문제 없음"으로 끝나는 것은 이 툴이 낼 수 있는 가장 나쁜
    결과다 — 사람은 병합이 됐다고 믿고 다음 단계로 간다.
    """
    if total_rows and final_rows == 0:
        issues.add(Issue(
            file="(전체)", kind="결과없음", severity=CRITICAL,
            message=(f"입력 {total_rows}행을 읽었지만 최종 표가 **0행**입니다. "
                     "조건을 만족하는 피험자·시점이 하나도 없었습니다."),
            advice=("`--how inner` 를 `outer` 로 바꾸거나, 먼저 `--inspect` 로 "
                    "각 파일의 키·시점이 실제로 같은 것을 가리키는지 확인하세요.")))
        return
    if total_rows and unmatched > total_rows * 0.5:
        issues.add(Issue(
            file="(전체)", kind="미매칭과다", severity=WARNING,
            message=(f"입력 {total_rows}행 중 {unmatched}행"
                     f"({unmatched / total_rows:.0%})이 최종 표에 들지 못했습니다"),
            advice=("정상적인 결측일 수도 있지만, 키·시점 체계가 파일마다 다를 "
                    "때도 이렇게 됩니다. `키매칭표.csv` 를 확인하세요.")))


def check_key_overlap(plans: Sequence[FilePlan], issues: IssueLog,
                      unify_heads: bool = False) -> None:
    """파일 간 피험자 키가 실제로 겹치는지 본다 — **이 툴에서 가장 중요한 검사.**

    병합은 "성공"했는데 겹치는 피험자가 하나도 없는 상태가 최악이다. 표는
    나오고 통계도 돌아가지만, 실제로는 두 파일이 나란히 붙어 있을 뿐 아무것도
    합쳐지지 않았다. pandas 도 엑셀도 이때 아무 말을 하지 않는다.

    겹침이 0이면 **머리말을 통일하면 붙는지**까지 직접 계산해 보고, 붙는다면
    `--unify-id-heads` 를 쓰라고 이름까지 알려 준다(자동으로 적용하지는 않는다 —
    `S01..S16` 과 `C01..C16` 이 서로 다른 코호트일 수 있고 그 판단은 사람 몫이다).
    """
    if len(plans) < 2:
        return
    sets = {p.label: p.subjects() for p in plans}
    labels = [p.label for p in plans]

    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            sa, sb = sets[a], sets[b]
            if not sa or not sb:
                continue
            shared = sa & sb
            smaller = min(len(sa), len(sb))
            ratio = len(shared) / smaller if smaller else 0.0

            if shared and ratio >= _LOW_OVERLAP:
                continue

            advice = ["`--alias` 표로 두 파일의 ID 대응을 직접 적어 줄 수 있습니다."]
            if not unify_heads:
                head_a, head_b = common_head(sorted(sa)), common_head(sorted(sb))
                if head_a is not None and head_b is not None and head_a != head_b:
                    ua = {k[len(head_a):] for k in sa}
                    ub = {k[len(head_b):] for k in sb}
                    gained = ua & ub
                    if len(gained) > len(shared):
                        advice.insert(0, (
                            f"두 파일의 ID 머리말이 각각 '{head_a}' / '{head_b}' 로 "
                            f"파일 안에서는 상수입니다. `--unify-id-heads` 를 붙이면 "
                            f"{len(gained)}명이 맞물립니다 — 다만 두 머리말이 서로 "
                            "다른 코호트를 뜻하는 것은 아닌지 먼저 확인하세요."))

            sample_a = ", ".join(sorted(sa)[:4])
            sample_b = ", ".join(sorted(sb)[:4])
            if not shared:
                issues.add(Issue(
                    file=f"{a} ↔ {b}", kind="키겹침없음", severity=CRITICAL,
                    message=(f"두 파일에 공통으로 존재하는 피험자가 "
                             f"**한 명도 없습니다** ({a}: {len(sa)}명 예 {sample_a} / "
                             f"{b}: {len(sb)}명 예 {sample_b}). 병합 표는 만들어지지만 "
                             "두 파일의 값이 같은 행에서 만나지 않습니다."),
                    advice=" ".join(advice)))
            else:
                issues.add(Issue(
                    file=f"{a} ↔ {b}", kind="키겹침낮음", severity=WARNING,
                    message=(f"공통 피험자가 {len(shared)}명뿐입니다 "
                             f"({a} {len(sa)}명 / {b} {len(sb)}명, "
                             f"작은 쪽 기준 {ratio:.0%})."),
                    advice=" ".join(advice)))


def check_timezones(plans: Sequence[FilePlan], issues: IssueLog) -> None:
    """타임존 오프셋이 섞여 있으면 변환하지 않고 병합을 막는다.

    한 열 안에서 섞인 경우뿐 아니라 **파일 사이에서 다른 경우**도 막는다.
    파일 A가 전부 `+09:00`, 파일 B가 전부 `+00:00` 이면 각 열은 일관돼 보이지만,
    같은 순간이 서로 다른 날짜로 귀속되어 두 파일이 한 행에서 만나지 못한다.
    피험자는 100% 겹치므로 키 겹침 검사도 이것을 잡지 못한다.
    """
    seen: Dict[str, List[str]] = {}
    for plan in plans:
        date_plan = plan.date_plan
        if date_plan is None or not date_plan.parsed:
            continue
        # 이 파일이 쓰는 오프셋 표기(없으면 '(표기 없음)').
        stamp = ", ".join(sorted(date_plan.offsets)) or "(표기 없음)"
        seen.setdefault(stamp, []).append(plan.label)

    if len(seen) > 1:
        detail = "; ".join(f"{stamp}: {', '.join(files)}"
                           for stamp, files in sorted(seen.items()))
        issues.add(Issue(
            file="(전체)", kind="파일간타임존불일치", severity=CRITICAL,
            blocking=True,
            message=(f"파일마다 시간대 표기가 다릅니다 — {detail}. 같은 순간이 "
                     "서로 다른 날짜로 귀속되어 두 파일이 한 행에서 만나지 "
                     "못합니다."),
            advice=("이 툴은 타임존을 변환하지 않습니다. 원본을 한 시간대로 "
                    "통일하거나, 모든 파일에서 오프셋 표기를 빼고 다시 "
                    "실행하세요.")))

    for plan in plans:
        date_plan = plan.date_plan
        if date_plan is None or not date_plan.mixed_timezone:
            continue
        offsets = ", ".join(sorted(date_plan.offsets)) or "(없음)"
        issues.add(Issue(
            file=plan.label, kind="타임존혼재", severity=CRITICAL, blocking=True,
            key=plan.time_col or "",
            message=(f"'{plan.time_col}' 열에 서로 다른 시간대 표기가 섞여 있습니다"
                     f"(오프셋: {offsets}, 오프셋 없는 행 {date_plan.naive_count}건)"),
            advice=("이 툴은 타임존을 변환하지 않습니다. 원본을 한 시간대로 통일한 뒤 "
                    "다시 실행하세요.")))

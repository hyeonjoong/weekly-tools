"""병합 엔진 — 이 툴의 두 가지 불변식이 사는 곳.

**불변식 1 — 카테시안 조인은 일어나지 않는다.**
병합에 들어가기 전에 각 파일은 키당 정확히 한 행으로 축약된다(중복은 정책에
따라 제외되거나 first/last/mean 으로 해소된다). 그러므로 출력 행 수는 어떤
입력에 대해서도 **최종 키 집합의 크기**를 넘을 수 없다. pandas `merge` 가
경고 없이 행을 곱하는 바로 그 자리를 막는다.

**불변식 2 — 어떤 입력 행도 사유 없이 사라지지 않는다.**
모든 입력 행은 정확히 하나의 처분(`사용` 또는 드롭 사유)을 배정받는다.
`Ledger.verify()` 가 `입력 = Σ처분` 을 검사하고, 어긋나면 조용히 넘어가지 않고
내부 오류로 크게 보고한다. 리포트의 N-흐름은 이 원장을 그대로 옮긴 것이다.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .dataio import Frame, is_missing, parse_number
from .detect import Detection
from .issues import CRITICAL, INFO, WARNING, Issue, IssueLog
from .keys import KeyNormalizer, NormalizationStats
from .timeline import DatePlan, ParsedTime, VisitNormalizer, night_of, parse_date_cell

__all__ = [
    "USED", "DUP_MERGED", "DROP_NO_KEY", "DROP_DATE_PARSE", "DROP_DUPLICATE",
    "DROP_TIME_CONFLICT", "DROP_SUBJECT_UNMATCHED", "DROP_TIME_UNMATCHED",
    "DISPOSITIONS", "Ledger", "FilePlan", "MergeResult", "merge_files",
]

USED = "사용"
DUP_MERGED = "중복키(평균에 반영)"
DROP_NO_KEY = "키없음"
DROP_DATE_PARSE = "날짜파싱실패"
DROP_DUPLICATE = "중복키"
DROP_TIME_CONFLICT = "시점충돌"
DROP_SUBJECT_UNMATCHED = "피험자미매칭"
DROP_TIME_UNMATCHED = "시점미매칭"

# 리포트에 이 순서로 나간다.
DISPOSITIONS = (USED, DUP_MERGED, DROP_NO_KEY, DROP_DATE_PARSE, DROP_DUPLICATE,
                DROP_TIME_CONFLICT, DROP_SUBJECT_UNMATCHED, DROP_TIME_UNMATCHED)

_PREFIX_SAFE_RE = re.compile(r"[^0-9A-Za-z가-힣]+")

# 원본 셀을 리포트에 옮길 때의 길이 상한. 자유기재 칸에는 이름·연락처가 들어
# 있을 수 있으므로 통째로 옮기지 않는다.
_MAX_SNIPPET = 16


def _snippet(value: str) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= _MAX_SNIPPET else text[:_MAX_SNIPPET] + "…"


def make_prefix(name: str) -> str:
    """파일 이름 -> 열 접두어. 열 이름에 안전한 문자만 남긴다."""
    stem = os.path.splitext(os.path.basename(name))[0]
    safe = _PREFIX_SAFE_RE.sub("_", stem).strip("_")
    return safe or "file"


class Ledger:
    """입력 행 하나하나의 처분을 기록하는 원장."""

    def __init__(self, sizes: Sequence[int]) -> None:
        self._rows: List[List[Optional[str]]] = [[None] * n for n in sizes]
        self._detail: List[List[str]] = [[""] * n for n in sizes]

    def set(self, file_index: int, row_index: int, disposition: str,
            detail: str = "") -> None:
        """처분을 배정한다. **한 행에 두 번 배정하지 않는다** — 먼저 배정된
        사유가 이긴다(더 이른 단계에서 걸러진 것이 실제 이유이므로)."""
        if self._rows[file_index][row_index] is None:
            self._rows[file_index][row_index] = disposition
            self._detail[file_index][row_index] = detail

    def reassign(self, file_index: int, row_index: int, disposition: str,
                 detail: str = "") -> None:
        """이미 배정된 처분을 덮어쓴다.

        `set()` 이 선착순인 것은 "더 이른 단계에서 걸러진 이유가 진짜 이유"이기
        때문이지만, 딱 한 경우 예외가 필요하다: `--dup-policy mean` 으로 평균에
        반영된 행은 그 그룹이 최종 키 집합에 들어갈 때만 실제로 기여한다.
        그룹이 통째로 빠지면 '평균에 반영'은 거짓말이 되므로 바로잡는다.
        """
        self._rows[file_index][row_index] = disposition
        self._detail[file_index][row_index] = detail

    def get(self, file_index: int, row_index: int) -> Optional[str]:
        return self._rows[file_index][row_index]

    def detail(self, file_index: int, row_index: int) -> str:
        return self._detail[file_index][row_index]

    def unassigned(self, file_index: int) -> List[int]:
        return [i for i, d in enumerate(self._rows[file_index]) if d is None]

    def rows_with(self, file_index: int, disposition: str) -> List[int]:
        """그 파일에서 특정 처분을 받은 행 번호."""
        return [i for i, d in enumerate(self._rows[file_index])
                if d == disposition]

    def counts(self, file_index: Optional[int] = None) -> Dict[str, int]:
        out: Dict[str, int] = {}
        blocks = (self._rows if file_index is None
                  else [self._rows[file_index]])
        for block in blocks:
            for disp in block:
                key = disp or "미배정"
                out[key] = out.get(key, 0) + 1
        return out

    @property
    def total(self) -> int:
        return sum(len(block) for block in self._rows)

    def verify(self, plans: Sequence["FilePlan"] = (),
               final_set: Optional[set] = None,
               final_subjects: Optional[set] = None) -> Optional[str]:
        """원장을 **실제 산출물과 대조**한다. 문제가 있으면 설명 문자열.

        `입력 = Σ처분` 만 세면 아무것도 검증하지 못한다(모든 칸을 세니 언제나
        참이다). 그래서 여기서는 조작할 수 없는 두 목록을 맞춰 본다:

        * `기여(사용 + 평균반영)로 표시된 행`  과
        * `최종 표에 들어간 값을 실제로 뒷받침한 행`(`FilePlan.backing`)

        둘이 어긋나면 둘 중 하나다 — 버려진 행이 '사용'으로 둔갑했거나(그러면
        논문의 N이 부풀고), 표에 있는 값이 어떤 입력 행에도 근거가 없거나
        (그러면 값이 어디선가 조용히 덮어써졌다). 두 경우 모두 이 툴이 존재하는
        이유 그 자체이므로 크게 보고한다.
        """
        counts = self.counts()
        if counts.get("미배정"):
            return (f"처분이 배정되지 않은 입력 행이 {counts['미배정']}건 있습니다 "
                    "(내부 오류 — 이 결과를 신뢰하지 마세요).")
        unknown = set(counts) - set(DISPOSITIONS)
        if unknown:
            return f"알 수 없는 처분이 있습니다: {', '.join(sorted(unknown))} (내부 오류)."
        if final_set is None:
            return None

        contributed = {(p.index, i) for p in plans
                       for disp in (USED, DUP_MERGED)
                       for i in self.rows_with(p.index, disp)}
        backed = set()
        for plan in plans:
            for gkey, rows in plan.backing.items():
                key, _tp = gkey
                in_final = (gkey in final_set or
                            (plan.subject_level and
                             key in (final_subjects or set())))
                if in_final:
                    backed.update((plan.index, i) for i in rows)

        if contributed != backed:
            phantom = len(backed - contributed)
            inflated = len(contributed - backed)
            parts = []
            if inflated:
                parts.append(f"기여로 표시됐지만 최종 표를 뒷받침하지 않는 행 "
                             f"{inflated}건")
            if phantom:
                parts.append(f"최종 표에 값이 있는데 근거 행이 없는 경우 "
                             f"{phantom}건")
            return ("원장과 산출물이 어긋납니다 — " + ", ".join(parts) +
                    " (내부 오류 — 이 결과의 N을 쓰지 마세요).")
        return None


@dataclass
class FilePlan:
    """파일 하나에 대해 확정된 병합 계획과 중간 산출물."""

    index: int
    frame: Frame
    prefix: str
    key_det: Detection
    time_kind: str = "none"            # 'date' | 'visit' | 'none'
    time_col: Optional[str] = None
    date_plan: Optional[DatePlan] = None
    keys: List[str] = field(default_factory=list)
    timepoints: List[Optional[str]] = field(default_factory=list)
    key_stats: Optional[NormalizationStats] = None
    value_columns: List[str] = field(default_factory=list)
    # (키, 시점) -> 이 파일이 기여하는 값 한 줄
    resolved: Dict[Tuple[str, Optional[str]], List[str]] = field(default_factory=dict)
    # (키, 시점) -> 그 값을 실제로 뒷받침한 입력 행 번호들.
    # 원장 검증이 "사용으로 표시된 행"과 이 목록을 대조하기 위해 필요하다.
    backing: Dict[Tuple[str, Optional[str]], List[int]] = field(default_factory=dict)
    unknown_visits: Dict[str, int] = field(default_factory=dict)
    no_time_rows: int = 0              # 시각 없이 날짜만 있던 행
    fixed_label: Optional[str] = None  # --visit-label 로 파일 전체에 준 시점

    @property
    def label(self) -> str:
        return self.frame.label

    @property
    def subject_level(self) -> bool:
        """이 파일은 시점 열이 없어 피험자 단위로 모든 시점에 붙는가."""
        return self.time_kind == "none"

    def subjects(self) -> set:
        return {k for k in self.keys if k}


@dataclass
class MergeResult:
    """병합 산출물 전체."""

    plans: List[FilePlan]
    ledger: Ledger
    header: List[str]
    rows: List[List[str]]
    final_keys: List[Tuple[str, Optional[str]]]
    how: str
    align: str
    base_index: int
    coverage: Dict[str, Dict[str, int]] = field(default_factory=dict)
    # 정규화 덕분에 **서로 다른 원본 표기가 한 사람으로 합쳐진** 경우만.
    # "제로패딩 189건" 같은 내부 계산 건수와 달리, 이것이 사람에게 의미 있는 수다.
    merged_spellings: Dict[str, List[str]] = field(default_factory=dict)
    ledger_error: Optional[str] = None

    @property
    def subjects(self) -> List[str]:
        seen: List[str] = []
        marked = set()
        for key, _ in self.final_keys:
            if key not in marked:
                marked.add(key)
                seen.append(key)
        return seen


# --------------------------------------------------------------------------
# 1단계 — 키와 시점 배정
# --------------------------------------------------------------------------

def assign_keys_and_times(plan: FilePlan, normalizer: KeyNormalizer,
                          ledger: Ledger, issues: IssueLog,
                          align: str, cutoff: _dt.time,
                          visits: VisitNormalizer) -> None:
    """파일의 각 행에 정규 키와 시점을 배정하고, 실패한 행을 원장에 기록한다."""
    frame = plan.frame
    raw_keys = frame.column(plan.key_det.column or frame.header[0])
    plan.keys, plan.key_stats = normalizer.normalize_column(frame.label, raw_keys)

    for i, key in enumerate(plan.keys):
        if not key:
            ledger.set(plan.index, i, DROP_NO_KEY, "피험자 ID가 비어 있음")

    if plan.key_stats and plan.key_stats.collisions:
        for key, raws in sorted(plan.key_stats.collisions.items())[:20]:
            issues.add(Issue(
                file=frame.label, kind="키정규화충돌", severity=WARNING,
                key=key,
                message=("같은 파일 안의 서로 다른 표기 " +
                         " / ".join(f"'{_snippet(r)}'" for r in raws[:6]) +
                         f" 가 같은 키 '{_snippet(key)}' 로 합쳐집니다"),
                advice=("같은 사람이면 그대로 두어도 되지만, 다른 사람이면 "
                        "`--alias` 로 구분하거나 `--no-key-normalize` 를 쓰세요.")))

    plan.timepoints = [None] * frame.nrows
    if plan.time_kind == "none":
        return

    if plan.time_kind == "fixed":
        # `--visit-label 설문_4주.csv=W4` — 파일 하나가 곧 한 시점인 자료.
        # 라벨도 사전 정의표를 거치므로 다른 파일의 `week4` 와 맞물린다.
        label, _known = visits(plan.fixed_label or "")
        plan.timepoints = [label] * frame.nrows
        return

    if plan.time_kind == "visit":
        column = frame.column(plan.time_col or "")
        for i, raw in enumerate(column):
            label, known = visits(raw)
            if not label:
                ledger.set(plan.index, i, DROP_DATE_PARSE, "시점 라벨이 비어 있음")
                continue
            if not known:
                plan.unknown_visits[label] = plan.unknown_visits.get(label, 0) + 1
            plan.timepoints[i] = label
        return

    # 날짜 기반
    date_plan = plan.date_plan
    column = frame.column(plan.time_col or "")
    for i, raw in enumerate(column):
        if ledger.get(plan.index, i) is not None:
            continue
        parsed: Optional[ParsedTime] = (
            parse_date_cell(raw, date_plan) if date_plan else None)
        if parsed is None:
            ledger.set(plan.index, i, DROP_DATE_PARSE,
                       "날짜를 해석할 수 없음: "
                       f"'{' '.join(raw.split())[:40]}'")
            continue
        if parsed.time is None:
            plan.no_time_rows += 1
        day = night_of(parsed, cutoff) if align == "night" else parsed.date
        plan.timepoints[i] = day.isoformat()


# --------------------------------------------------------------------------
# 2단계 — 중복 해소 (카테시안 조인 차단)
# --------------------------------------------------------------------------

def resolve_duplicates(plan: FilePlan, ledger: Ledger, issues: IssueLog,
                       policy: str, align: str = "date") -> None:
    """파일을 (키, 시점)당 정확히 한 행으로 축약한다.

    `policy == 'error'` 이면 중복 그룹은 **통째로 제외**한다. 하나를 골라
    진행하는 것은 사람이 명시적으로 정책을 지정했을 때만 한다.
    """
    frame = plan.frame
    groups: Dict[Tuple[str, Optional[str]], List[int]] = {}
    for i, key in enumerate(plan.keys):
        if ledger.get(plan.index, i) is not None:
            continue
        groups.setdefault((key, plan.timepoints[i]), []).append(i)

    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    value_idx = [frame.index[c] for c in plan.value_columns]

    # 수면 자료를 `--align date` 로 돌리면 한 밤이 두 날짜로 갈라져 **정상 자료가
    # 통째로 중복 키로 잡힌다.** 이때 `--dup-policy first` 를 권하면 사람이 멀쩡한
    # 측정치를 절반 버리게 되므로, 진짜 원인을 먼저 말해 준다.
    night_hint = bool(dup_groups and align != "night" and plan.date_plan
                      and plan.date_plan.has_time)
    hint = ("이 파일의 날짜 열에는 **시각이 들어 있습니다**. 수면 자료라면 자정을 "
            "넘긴 기록이 다음 날로 잡혀 정상 자료가 중복으로 보일 수 있습니다 — "
            "`--dup-policy` 를 정하기 전에 먼저 `--align night` 로 다시 돌려 보세요. "
            if night_hint else "")
    if night_hint:
        issues.add(Issue(
            file=plan.label, kind="야간귀속권고", severity=WARNING,
            key=plan.time_col or "",
            message=(f"'{plan.time_col}' 에 시각이 포함된 행이 "
                     f"{plan.date_plan.has_time}건 있는데 `--align date` 로 돌렸고, "
                     f"중복 키가 {len(dup_groups)}개 그룹 나왔습니다"),
            advice=("`--align night` 를 먼저 시도하세요. 자정을 넘긴 기록이 앞선 "
                    "밤으로 귀속되면 이 중복이 대부분 사라집니다.")))

    for gkey, rows in groups.items():
        if len(rows) == 1:
            plan.resolved[gkey] = [frame.rows[rows[0]][j] for j in value_idx]
            plan.backing[gkey] = list(rows)
            continue

        subject, timepoint = gkey
        where = ", ".join(str(frame.source_line(r)) for r in rows[:6])
        detail = (f"{plan.label} 의 {len(rows)}개 행이 같은 키를 가집니다"
                  f"(행 {where}{' 외' if len(rows) > 6 else ''})")
        issue_key = subject + (f" / {timepoint}" if timepoint else "")

        if policy == "error":
            for r in rows:
                ledger.set(plan.index, r, DROP_DUPLICATE, detail)
            issues.add(Issue(
                file=plan.label, kind="중복키", severity=CRITICAL,
                key=issue_key, line=where, message=detail,
                advice=(hint + "이 키는 병합에서 제외했습니다. 원본에서 "
                        "재측정/재업로드를 확인하고, 그래도 진행하려면 "
                        "`--dup-policy first|last|mean` 을 명시하세요.")))
            continue

        if policy in ("first", "last"):
            keep = rows[0] if policy == "first" else rows[-1]
            plan.resolved[gkey] = [frame.rows[keep][j] for j in value_idx]
            plan.backing[gkey] = [keep]
            for r in rows:
                if r != keep:
                    ledger.set(plan.index, r, DROP_DUPLICATE,
                               f"--dup-policy {policy} 로 제외")
            issues.add(Issue(
                file=plan.label, kind="중복키", severity=WARNING,
                key=issue_key, line=where,
                message=detail + f" → --dup-policy {policy} 적용",
                advice=f"{frame.source_line(keep)}행만 남기고 나머지를 버렸습니다."))
            continue

        # mean — 숫자 열은 평균, 그 외는 값이 모두 같을 때만 유지
        merged: List[str] = []
        disagree: List[str] = []
        for col_pos, col in enumerate(plan.value_columns):
            j = value_idx[col_pos]
            cells = [frame.rows[r][j] for r in rows]
            present = [c for c in cells if not is_missing(c)]
            numbers = [parse_number(c) for c in present]
            if present and all(n is not None for n in numbers):
                mean = sum(n for n in numbers if n is not None) / len(numbers)
                merged.append(_format_number(mean))
            elif not present:
                merged.append("")
            elif len(set(c.strip() for c in present)) == 1:
                merged.append(present[0].strip())
            else:
                merged.append("")
                disagree.append(col)
        plan.resolved[gkey] = merged
        plan.backing[gkey] = list(rows)
        # 대표 행의 처분은 조립 단계에서 정한다(최종 키 집합에 못 들 수도 있다).
        for r in rows[1:]:
            ledger.set(plan.index, r, DUP_MERGED, "--dup-policy mean 으로 평균에 반영")
        issues.add(Issue(
            file=plan.label, kind="중복키", severity=WARNING,
            key=issue_key, line=where,
            message=detail + " → --dup-policy mean 적용",
            advice=("숫자 열은 평균으로 합쳤습니다."
                    + (f" 값이 서로 달라 비운 열: {', '.join(disagree)}"
                       if disagree else ""))))

    if dup_groups and policy == "error":
        issues.add(Issue(
            file=plan.label, kind="중복키요약", severity=CRITICAL,
            message=(f"중복 키 {len(dup_groups)}개 그룹"
                     f"({sum(len(v) for v in dup_groups.values())}행)을 병합에서 "
                     "제외했습니다"),
            advice="문제목록.csv 의 '중복키' 행을 확인하세요."))


def _format_number(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.6g}"


# --------------------------------------------------------------------------
# 3단계 — 시점 허용오차 스냅
# --------------------------------------------------------------------------

def snap_to_base(plans: Sequence[FilePlan], base_index: int, tolerance: int,
                 ledger: Ledger, issues: IssueLog) -> None:
    """비기준 파일의 날짜 시점을 기준 파일의 가장 가까운 시점으로 옮긴다.

    동률(같은 거리의 후보가 둘)이면 **옮기지 않고 충돌로 보고한 뒤 그 행을
    병합에서 뺀다.** 둘 중 아무거나 고르는 것이 이 툴이 해서는 안 되는 일이다.
    """
    if tolerance <= 0:
        return
    base = plans[base_index]
    if base.time_kind != "date":
        return

    anchors: Dict[str, List[_dt.date]] = {}
    for (key, tp) in base.resolved:
        if tp:
            try:
                anchors.setdefault(key, []).append(_dt.date.fromisoformat(tp))
            except ValueError:
                continue
    for key in anchors:
        anchors[key].sort()

    for plan in plans:
        if plan.index == base_index or plan.time_kind != "date":
            continue
        remapped: Dict[Tuple[str, Optional[str]], List[str]] = {}
        remapped_backing: Dict[Tuple[str, Optional[str]], List[int]] = {}
        # 충돌로 이미 버린 목적지. 세 번째 시점이 같은 날로 끌려와도 되살아나면
        # 안 되므로 기억해 둔다.
        blocked: set = set()
        moved_keys: set = set()
        moved = 0
        for (key, tp), values in plan.resolved.items():
            if not tp or key not in anchors:
                remapped[(key, tp)] = values
                remapped_backing[(key, tp)] = plan.backing.get((key, tp), [])
                continue
            try:
                day = _dt.date.fromisoformat(tp)
            except ValueError:
                remapped[(key, tp)] = values
                remapped_backing[(key, tp)] = plan.backing.get((key, tp), [])
                continue
            scored = sorted(
                ((abs((a - day).days), a) for a in anchors[key]
                 if abs((a - day).days) <= tolerance),
                key=lambda pair: (pair[0], pair[1]))
            if not scored:
                remapped[(key, tp)] = values
                remapped_backing[(key, tp)] = plan.backing.get((key, tp), [])
                continue
            if len(scored) > 1 and scored[0][0] == scored[1][0]:
                issues.add(Issue(
                    file=plan.label, kind="시점충돌", severity=WARNING, key=key,
                    message=(f"{tp} 이(가) 기준 파일의 {scored[0][1].isoformat()} 와 "
                             f"{scored[1][1].isoformat()} 에 같은 거리"
                             f"({scored[0][0]}일)로 걸립니다"),
                    advice="어느 쪽인지 정할 수 없어 이 행은 병합하지 않았습니다."))
                _mark_rows(plan, key, tp, ledger, DROP_TIME_CONFLICT,
                           "허용오차 안에 같은 거리의 후보가 둘")
                continue
            target = scored[0][1].isoformat()
            if (key, target) in blocked:
                _mark_rows(plan, key, tp, ledger, DROP_TIME_CONFLICT,
                           f"다른 시점과 함께 {target} 로 스냅되어 충돌")
                continue
            if (key, target) in remapped:
                # 두 시점이 같은 기준 날짜로 끌려왔다. 어느 쪽이 그 날인지 정할
                # 수 없으므로 **덮어쓰지 않고** 둘 다 병합에서 뺀다. (덮어쓰면
                # 한 줄이 조용히 사라지고 원장과 출력이 어긋난다.)
                issues.add(Issue(
                    file=plan.label, kind="시점충돌", severity=WARNING, key=key,
                    message=(f"{tp} 와(과) 다른 시점이 모두 기준 파일의 {target} 로 "
                             f"허용오차 {tolerance}일 안에 걸립니다"),
                    advice=("어느 쪽이 그 시점인지 정할 수 없어 두 시점을 모두 "
                            "병합하지 않았습니다. `--tolerance-days` 를 줄이세요.")))
                _mark_rows(plan, key, tp, ledger, DROP_TIME_CONFLICT,
                           f"다른 시점과 함께 {target} 로 스냅되어 충돌")
                _mark_rows(plan, key, target, ledger, DROP_TIME_CONFLICT,
                           f"다른 시점과 함께 {target} 로 스냅되어 충돌")
                del remapped[(key, target)]
                remapped_backing.pop((key, target), None)
                blocked.add((key, target))
                if (key, target) in moved_keys:
                    moved -= 1          # 옮겼다가 도로 버렸으니 세지 않는다
                continue
            if target != tp:
                moved += 1
                moved_keys.add((key, target))
                _retag_rows(plan, key, tp, target)
            remapped[(key, target)] = values
            remapped_backing[(key, target)] = plan.backing.get((key, tp), [])
        plan.resolved = remapped
        plan.backing = remapped_backing
        if moved:
            issues.add(Issue(
                file=plan.label, kind="시점스냅", severity=INFO,
                message=(f"허용오차 {tolerance}일 안에서 {moved}개 시점을 기준 파일"
                         f"('{base.label}')의 날짜로 맞췄습니다"),
                advice="merged.csv 의 원본 날짜 열에서 실제 측정일을 확인할 수 있습니다."))


def _mark_rows(plan: FilePlan, key: str, timepoint: Optional[str],
               ledger: Ledger, disposition: str, detail: str) -> None:
    for i, (k, tp) in enumerate(zip(plan.keys, plan.timepoints)):
        if k == key and tp == timepoint:
            ledger.set(plan.index, i, disposition, detail)


def _retag_rows(plan: FilePlan, key: str, old: Optional[str],
                new: str) -> None:
    for i, (k, tp) in enumerate(zip(plan.keys, plan.timepoints)):
        if k == key and tp == old:
            plan.timepoints[i] = new


# --------------------------------------------------------------------------
# 4단계 — 키 집합 확정 & 조립
# --------------------------------------------------------------------------

def _final_key_set(plans: Sequence[FilePlan], how: str, base_index: int
                   ) -> List[Tuple[str, Optional[str]]]:
    timed = [p for p in plans if not p.subject_level]
    if how == "left":
        base = plans[base_index]
        if base.subject_level and timed:
            # 기준 파일에 시점이 없으면 그 키는 전부 (피험자, None) 이라 시점 있는
            # 어떤 파일과도 만나지 못한다. 그대로 두면 값이 하나도 없는 표가
            # 나오므로, 기준 파일의 피험자를 시점 있는 파일들의 시점으로 펼친다.
            subjects = {k for k, _ in base.resolved}
            keys = {(k, tp) for p in timed for (k, tp) in p.resolved
                    if k in subjects}
            covered = {k for k, _ in keys}
            keys |= {(k, None) for k in subjects if k not in covered}
        else:
            keys = set(base.resolved)
    elif how == "inner":
        if timed:
            sets = [set(p.resolved) for p in timed]
            keys = set.intersection(*sets)
        else:
            sets = [set(p.resolved) for p in plans]
            keys = set.intersection(*sets) if sets else set()
        for p in plans:
            if p.subject_level:
                subjects = {k for k, _ in p.resolved}
                keys = {(k, tp) for (k, tp) in keys if k in subjects}
    else:                                     # outer
        keys = set()
        for p in timed:
            keys |= set(p.resolved)
        if not timed:
            for p in plans:
                keys |= set(p.resolved)
        else:
            # 시점 있는 파일에 전혀 없는 피험자가 피험자 단위 파일에만 있으면,
            # 그 피험자도 시점 없는 행으로 남긴다(outer 의 뜻).
            known = {k for k, _ in keys}
            for p in plans:
                if p.subject_level:
                    for (k, _tp) in p.resolved:
                        if k not in known:
                            keys.add((k, None))
    return sorted(keys, key=lambda kt: (kt[0], kt[1] or ""))


def merge_files(plans: List[FilePlan], ledger: Ledger, issues: IssueLog,
                normalizer: KeyNormalizer, how: str, align: str,
                base_index: int) -> MergeResult:
    """축약된 파일들을 하나의 표로 합치고, 남은 행의 처분을 확정한다."""
    final_keys = _final_key_set(plans, how, base_index)
    final_set = set(final_keys)
    final_subjects = {k for k, _ in final_keys}

    # `subject_id` / `timepoint` 는 이 표의 예약 열이다. 어떤 파일의
    # `접두어_원본열` 이 우연히 같은 이름이 되면 열 이름이 중복된 표가 나오고,
    # pandas 는 그중 하나만 조용히 남긴다. 겹치면 빈 자리를 찾아 붙인다.
    header = ["subject_id", "timepoint"]
    taken = set(header)
    for plan in plans:
        for col in plan.value_columns:
            name = f"{plan.prefix}_{col}"
            if name in taken:
                n = 1
                while f"{name}.{n}" in taken:
                    n += 1
                name = f"{name}.{n}"
            taken.add(name)
            header.append(name)

    rows: List[List[str]] = []
    for key, timepoint in final_keys:
        row = [normalizer.display_id(key), timepoint or ""]
        for plan in plans:
            if plan.subject_level:
                values = plan.resolved.get((key, None))
            else:
                values = plan.resolved.get((key, timepoint))
            row.extend(values if values is not None
                       else [""] * len(plan.value_columns))
        rows.append(row)

    # 아직 처분이 없는 행 = 축약까지는 살아남았지만 최종 키 집합에 못 든 행.
    for plan in plans:
        for i in ledger.unassigned(plan.index):
            key, timepoint = plan.keys[i], plan.timepoints[i]
            lookup = (key, None) if plan.subject_level else (key, timepoint)
            if lookup in final_set or (plan.subject_level
                                       and key in final_subjects):
                ledger.set(plan.index, i, USED)
            elif key not in final_subjects:
                ledger.set(plan.index, i, DROP_SUBJECT_UNMATCHED,
                           f"'{normalizer.display_id(key)}' 이(가) 최종 표에 없음")
            else:
                ledger.set(plan.index, i, DROP_TIME_UNMATCHED,
                           f"시점 {timepoint} 이(가) 최종 표에 없음")

    # `--dup-policy mean` 으로 '평균에 반영' 표시가 붙었지만 그 그룹이 최종 키
    # 집합에 못 든 행은, 실제로는 아무 데도 기여하지 않았다. 그대로 두면 N-흐름이
    # 기여하지 않은 행을 기여한 것으로 셈한다.
    for plan in plans:
        for i in ledger.rows_with(plan.index, DUP_MERGED):
            key, timepoint = plan.keys[i], plan.timepoints[i]
            lookup = (key, None) if plan.subject_level else (key, timepoint)
            if lookup in final_set or (plan.subject_level
                                       and key in final_subjects):
                continue
            if key not in final_subjects:
                ledger.reassign(plan.index, i, DROP_SUBJECT_UNMATCHED,
                                f"'{normalizer.display_id(key)}' 이(가) 최종 표에 없음")
            else:
                ledger.reassign(plan.index, i, DROP_TIME_UNMATCHED,
                                f"시점 {timepoint} 이(가) 최종 표에 없음")

    coverage: Dict[str, Dict[str, int]] = {}
    for subject in sorted(final_subjects):
        coverage[subject] = {p.label: int(subject in p.subjects())
                             for p in plans}

    spellings: Dict[str, List[str]] = {}
    for subject in sorted(final_subjects):
        raws = normalizer.display_candidates.get(subject) or set()
        if len(raws) > 1:
            spellings[subject] = sorted(raws)

    result = MergeResult(plans=plans, ledger=ledger, header=header, rows=rows,
                         final_keys=final_keys, how=how, align=align,
                         base_index=base_index, coverage=coverage,
                         merged_spellings=spellings)
    result.ledger_error = ledger.verify(plans, final_set, final_subjects)
    if result.ledger_error:
        issues.add(Issue(
            file="(전체)", kind="내부오류", severity=CRITICAL,
            message=result.ledger_error,
            advice="이 결과를 논문/보고서에 쓰지 마세요. 개발자에게 알려 주세요."))
    return result

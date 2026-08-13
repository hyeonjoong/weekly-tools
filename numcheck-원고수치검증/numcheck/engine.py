"""검사 규칙들을 한 번에 돌려 :class:`Report` 를 만든다."""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from .counts import check_counts
from .deltas import check_deltas
from .docio import Manuscript, read_manuscript
from .grimcheck import check_grim
from .intervals import check_intervals
from .language import check_language
from .model import Claim, Finding, Report
from .options import Options
from .proportions import check_proportions
from .pvalues import check_pvalues
from .sections import assign_sections

__all__ = ["CHECKS", "analyze", "analyze_manuscript"]

CheckFn = Callable[[list, Options], List[Tuple[Claim, Optional[Finding]]]]

# 실행 순서 = 리포트에서 항목이 묶이는 순서
CHECKS: Tuple[Tuple[str, CheckFn], ...] = (
    ("비율 재계산", check_proportions),
    ("p 재계산", check_pvalues),
    ("N 합계", check_counts),
    ("GRIM", check_grim),
    ("변화량", check_deltas),
    ("신뢰구간", check_intervals),
    ("유의성 문구", check_language),
)


def analyze_manuscript(ms: Manuscript, opts: Optional[Options] = None) -> Report:
    """이미 읽어 둔 원고를 검사한다."""
    opts = opts or Options()
    opts.line_label = ms.line_label
    assign_sections(ms)
    report = Report(
        path=str(ms.path),
        fmt=ms.fmt,
        line_label=ms.line_label,
        word_count=ms.word_count,
        table_rows=ms.table_rows,
        notes=list(ms.notes),
        truncated=ms.truncated,
    )
    for _name, fn in CHECKS:
        for claim, finding in fn(ms.lines, opts):
            report.add(claim, finding)
    report.findings = _merge_duplicates(report.findings)
    report.claims.sort(key=lambda c: (c.line_no, c.item))
    return report


def _merge_duplicates(findings: List[Finding]) -> List[Finding]:
    """같은 오류가 본문과 표에 함께 적혀 있으면 한 건으로 합친다.

    실제 원고에서는 핵심 숫자가 본문·초록·표에 모두 나오므로, 합치지 않으면
    치명 건수가 두세 배로 부풀어 심각도를 오해하게 된다. 나머지 위치는 한 줄로
    덧붙여 어디를 고쳐야 하는지 잃지 않는다.
    """
    seen: dict = {}
    order = []
    for f in findings:
        key = (f.level, f.item, f.reported, f.recomputed, f.message)
        group = seen.get(key)
        # **같은 줄** 안의 두 지적은 서로 다른 문제다(표 한 행에 두 통계량이
        # 나란히 틀린 경우). 줄이 다를 때만 "같은 내용이 반복됐다"로 본다.
        if group is not None and all(g.line_no != f.line_no for g in group):
            group.append(f)
            continue
        if group is None:
            seen[key] = [f]
            order.append(key)
        else:
            merged_key = (key, f.line_no, len(order))
            seen[merged_key] = [f]
            order.append(merged_key)
    merged = []
    for key in order:
        group = seen[key]
        first = group[0]
        others = [g for g in group[1:] if g.line_no != first.line_no]
        if others:
            where = ", ".join(f"L{g.line_no}" for g in others)
            first.message += f" (같은 내용이 {where} 에도 있습니다.)"
            if first.message_en:
                first.message_en += f" (The same claim also appears at {where}.)"
        merged.append(first)
    return merged


def analyze(path, opts: Optional[Options] = None) -> Report:
    """파일 경로를 받아 읽고 검사한다(읽기 전용)."""
    return analyze_manuscript(read_manuscript(path), opts)

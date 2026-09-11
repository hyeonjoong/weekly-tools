"""리포트 조립. `[커버리지 자백]` 없이는 리포트가 나가지 못한다.

'리포트에 안 나온 항목 = 이상 없음' 이라는 착각이 이 부류 툴의 가장 큰 위험이다.
그래서 무엇을 못 봤는지 적는 블록을 **코드로 강제**한다.
"""

from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional, Sequence

from .findings import CRITICAL, INFO, WARNING, Finding, count_by_severity, sort_findings
from .hangul import NO_JONG
from .safeio import sanitize_line

COVERAGE_HEADER = "[커버리지 자백]"
_TAIL = "리포트에 안 나온 항목이 \"이상 없음\"이라는 뜻이 아닙니다."


class ReportIntegrityError(RuntimeError):
    """자백 블록이 빠진 리포트를 만들려 했을 때. 출력 대신 예외를 낸다."""


class Coverage:
    """무엇을 얼마나 봤는가 — 자백 블록의 재료."""

    def __init__(self):
        self.sheets_total = 0
        self.sheets_subject = 0
        self.sheets_pii_unopened: List[str] = []
        self.sheets_pii_dropped: List[str] = []
        self.sheets_skipped: List[tuple] = []
        self.sheets_incomplete: List[tuple] = []
        self.sheets_unreadable: List[tuple] = []
        self.answer_key: Optional[str] = None
        self.summary_sheet: Optional[str] = None
        self.items_total = 0
        self.items_compared = 0
        self.skip_reasons: Counter = Counter()
        self.summary_cells_total = 0
        self.summary_cells_compared = 0
        self.summary_unmapped: List[str] = []
        self.answer_key_unmatched: List[str] = []
        self.confusion_dropped = 0
        self.min_count = 3
        self.notes: List[str] = []

    @property
    def item_rate(self) -> float:
        if not self.items_total:
            return 0.0
        return 100.0 * self.items_compared / self.items_total

    def render(self) -> List[str]:
        lines = [COVERAGE_HEADER]
        parts = ["시트 %d개 중 %d개를 피험자로 인식" % (self.sheets_total,
                                                    self.sheets_subject)]
        if self.answer_key:
            parts.append("정답 시트 1개(%s)" % sanitize_line(self.answer_key))
        if self.summary_sheet:
            parts.append("요약 시트 1개(%s)" % sanitize_line(self.summary_sheet))
        if self.sheets_skipped:
            parts.append("%d개는 검사 블록이 없어 건너뜀" % len(self.sheets_skipped))
        if self.sheets_incomplete:
            parts.append("%d개는 헤더가 불완전해 제외" % len(self.sheets_incomplete))
        if self.sheets_pii_unopened:
            parts.append("%d개는 식별정보로 보여 **열지 않음**(%s)"
                         % (len(self.sheets_pii_unopened),
                            ", ".join(sanitize_line(s) for s in self.sheets_pii_unopened)))
        if self.sheets_pii_dropped:
            parts.append("%d개는 열 이름이 식별정보라 읽자마자 버림(%s)"
                         % (len(self.sheets_pii_dropped),
                            ", ".join(sanitize_line(s) for s in self.sheets_pii_dropped)))
        if self.sheets_unreadable:
            parts.append("%d개는 읽지 못함" % len(self.sheets_unreadable))
        lines.append("   " + ", ".join(parts) + ".")

        skipped = self.items_total - self.items_compared
        detail = ""
        if self.skip_reasons:
            detail = "(" + " · ".join(
                "%s %d" % (sanitize_line(k), v)
                for k, v in self.skip_reasons.most_common(6)) + ")"
        lines.append("   문항 %d개 중 %d개 대조(%.1f%%) · 건너뜀 %d개 %s"
                     % (self.items_total, self.items_compared, self.item_rate,
                        skipped, detail))
        if self.summary_cells_total or self.summary_cells_compared:
            lines.append("   요약 셀 %d개 중 %d개 대조."
                         % (self.summary_cells_total, self.summary_cells_compared))
        if self.summary_unmapped:
            lines.append("   요약표에서 열 이름을 해석하지 못해 대조하지 못한 열 %d개: %s"
                         % (len(self.summary_unmapped),
                            ", ".join(sanitize_line(c) for c in self.summary_unmapped[:8])))
        if self.answer_key_unmatched:
            lines.append("   정답 시트에 대응 열이 없어 자극 대조를 못한 블록: %s"
                         % ", ".join(sanitize_line(c) for c in self.answer_key_unmatched))
        if self.confusion_dropped:
            lines.append("   혼동 행렬에서 관측 %d회 미만이라 표에서 뺀 칸 %d개."
                         % (self.min_count, self.confusion_dropped))
        for note in self.notes:
            lines.append("   " + sanitize_line(note))
        lines.append("   " + _TAIL)
        return lines


def _fmt_finding(f: Finding) -> List[str]:
    head = "[%s] %s" % (f.severity, sanitize_line(f.kind))
    if f.subject:
        head += " — %s" % sanitize_line(f.subject)
    lines = [head, "   " + sanitize_line(f.message)]
    if f.location:
        lines.append("   위치: " + sanitize_line(f.location))
    if f.evidence:
        lines.append("   근거: " + sanitize_line(f.evidence))
    return lines


def render_confusion(top: Dict[str, list], min_count: int, features_used: bool,
                     analysed: bool = True) -> List[str]:
    """혼동 표. **게이트를 넘은 칸이 하나도 없어도 블록을 지우지 않는다** —
    표가 비어 있다는 사실 자체가 정보이고, 자질 미분류 자백도 같이 나가야 한다."""
    if not analysed:
        return []
    lines = ["[정보] 음소 혼동 상위 (최소 %d회 이상)" % min_count]
    any_row = False
    for pos in ("초성", "중성", "종성"):
        pairs = top.get(pos) or []
        if not pairs:
            continue
        any_row = True
        shown = " · ".join("%s→%s %d" % (a if a else NO_JONG, b if b else NO_JONG, n)
                           for (a, b), n in pairs)
        lines.append("   %s  %s" % (pos, shown))
    if not any_row:
        lines.append("   최소 관측수 %d회를 넘는 혼동 칸이 없습니다 — 관측이 적거나 "
                     "오답이 드뭅니다." % min_count)
    if features_used:
        lines.append("   ※ 조음위치·조음방법 묶음은 --features 로 받은 자질표로만 "
                     "집계했습니다(혼동행렬_*_자질.csv). 이 툴은 음운론적 해석을 "
                     "스스로 만들지 않습니다.")
    else:
        lines.append("   ※ 조음위치·조음방법 묶음은 --features 미지정으로 분류하지 "
                     "않았습니다. 이 툴은 음운론적 해석을 스스로 만들지 않습니다.")
    return lines


def build(header_lines: Sequence[str], findings: Sequence[Finding],
          confusion_lines: Sequence[str], coverage: Coverage,
          verdict_line: str, exit_code: int) -> str:
    """리포트 전문을 만든다. 자백 블록이 없으면 :class:`ReportIntegrityError`."""
    out: List[str] = []
    out.extend(sanitize_line(x) for x in header_lines)
    out.append("")
    ordered = sort_findings(list(findings))
    if not ordered:
        out.append("[정보] 치명·경고로 볼 소견이 없습니다.")
        out.append("   아래 자백 블록에서 **무엇을 보지 못했는지** 먼저 확인하세요.")
        out.append("")
    for f in ordered:
        out.extend(_fmt_finding(f))
        out.append("")
    if confusion_lines:
        out.extend(sanitize_line(x) for x in confusion_lines)
        out.append("")
    out.extend(coverage.render())
    out.append("")
    out.append(sanitize_line(verdict_line))
    out.append("종료코드 %d" % exit_code)
    text = "\n".join(out) + "\n"
    if COVERAGE_HEADER not in text or _TAIL not in text:
        raise ReportIntegrityError(
            "커버리지 자백 블록이 없는 리포트는 출력하지 않습니다.")
    return text


def verdict(counts: Dict[str, int], undetermined: bool) -> tuple:
    """(판정 문장, 종료코드). **판정 불가(3)가 치명(1)보다 우선한다.**"""
    if undetermined:
        return ("판정: 판정 불가 — 읽지 못한 부분이 있어 '치명 %d건'이라고 "
                "말할 수 없습니다.  (치명 %d건 · 경고 %d건)"
                % (counts[CRITICAL], counts[CRITICAL], counts[WARNING]), 3)
    if counts[CRITICAL]:
        return ("판정: 확인 필요  (치명 %d건 · 경고 %d건)"
                % (counts[CRITICAL], counts[WARNING]), 1)
    return ("판정: 치명 소견 없음  (경고 %d건 · 정보 %d건)"
            % (counts[WARNING], counts[INFO]), 0)

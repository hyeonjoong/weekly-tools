"""리포트 — 콘솔 · 정합점검.md · CSV 3종.

지켜야 할 것 하나: **[커버리지 자백] 없이는 리포트가 나가지 않는다.**
못 본 것을 말하지 않는 점검표는 "다 봤다"로 읽히고, 그게 이 툴이 만들 수 있는
가장 큰 사고다. 그래서 자백 블록이 빠지면 파일을 쓰지 않고 예외를 던진다.
"""

from . import __version__
from .items import FACETS, ITEM_LABEL, ITEM_KEYS
from .normalize import RULES
from .safeio import (ReportIntegrityError, mask_then_truncate, safe_line, safe_name,
                     write_csv, write_text)

COVERAGE_HEADER = "[커버리지 자백]"
CONSOLE_TITLE = "irbpack %s — 제출서류 정합점검" % __version__

_STATUS_MARK = {
    "기준": "  ← 기준",
    "다름": "  ← 다름",
    "동일": "",
    "없음": "  ← 이 항목에 대응하는 값이 없습니다",
    "값못찾음": "  ← 이 항목을 말하지만 값을 추출하지 못했습니다(직접 확인 필요)",
}
# 결론 줄에 붙는 문서 수는 '값을 말한 문서' 기준이다.
MAX_SAME_ROWS = 1          # 기준과 같은 문서는 하나만 펼치고 나머지는 이름만 한 줄로 접는다


class Report(object):
    """한 번의 점검 결과 전체."""

    def __init__(self, input_label, documents, excluded, result, baseline_findings=None,
                 baseline_label="", role_option_count=0, all_rows=False):
        self.input_label = input_label
        self.documents = documents
        self.excluded = excluded
        self.result = result
        self.baseline_findings = baseline_findings or []
        self.baseline_label = baseline_label
        self.role_option_count = role_option_count
        self.all_rows = all_rows
        self.baseline_notes = []
        self.scan_notes = []

    # ------------------------------------------------------------ 집계

    @property
    def readable(self):
        return [doc for doc in self.documents if doc.read_ok]

    @property
    def unreadable(self):
        return [doc for doc in self.documents if not doc.read_ok]

    @property
    def all_findings(self):
        from .compare import merge_findings
        return merge_findings(list(self.result.findings), list(self.baseline_findings))

    def counts(self):
        counts = {"치명": 0, "경고": 0}
        for finding in self.all_findings:
            if finding.severity in counts:
                counts[finding.severity] += 1
        return counts

    def compared_count(self):
        return len(self.result.compared_items)

    # ------------------------------------------------------------ 블록

    def _roles_block(self):
        lines = ["[문서 역할]"]
        for doc in self.documents:
            if not doc.read_ok:
                continue
            reason = doc.role_reason if doc.role_source == "자동" else doc.role_source
            lines.append("  %-10s %s" % (doc.role or "?", safe_name(doc.name)))
            lines.append("             근거: %s" % safe_line(reason))
            for note in doc.notes:
                lines.append("             주의: %s" % safe_line(note))
        for doc in self.unreadable:
            lines.append("  %-10s %s" % ("읽지 못함", safe_name(doc.name)))
            lines.append("             사유: %s" % safe_line(doc.error))
        for doc in self.excluded:
            lines.append("  %-10s %s (--role 제외)" % ("제외", safe_name(doc.name)))
        return lines

    def _finding_lines(self, findings, header):
        lines = ["%s %d건" % (header, len(findings))]
        if not findings:
            lines.append("  (없음)")
            return lines
        for number, finding in enumerate(findings, 1):
            for order, (summary, rows) in enumerate(finding.blocks):
                head = ("  %d. %s — %s" % (number, finding.label, safe_line(summary)) if order == 0
                        else "     %s— %s" % (" " * len(str(number)), safe_line(summary)))
                lines.append(head)
                lines.extend(self._row_lines(rows))
            for note in finding.notes:
                lines.append("       · %s" % safe_line(note))
        return lines

    def _row_lines(self, rows):
        """한 대조단위의 근거 줄. 기준과 같은 문서는 이름만 한 줄로 접는다."""
        lines = []
        same_docs = []
        for row in rows:
            if row.status == "동일" and row.doc not in same_docs:
                same_docs.append(row.doc)
        hidden = set(same_docs[MAX_SAME_ROWS:]) if not self.all_rows else set()
        for row in rows:
            if row.status == "동일" and row.doc in hidden:
                continue
            values = " · ".join('"%s"' % safe_line(value) for value in row.values) or "(값 없음)"
            lines.append("       %-12s %s%s" % (
                safe_line(row.role or "?"), values, _STATUS_MARK.get(row.status, "")))
            lines.append("       %-12s   %s%s" % (
                "", safe_name(row.doc), " / %s" % safe_line(row.where) if row.where else ""))
            # 원문이 값과 같으면 한 줄 더 쓸 이유가 없다 (라운드2 A-10)
            if row.quote and row.quote != row.doc and row.quote not in row.values:
                lines.append("       %-12s   원문: %s" % ("", safe_line(row.quote)))
        if hidden:
            lines.append("       %-12s (기준과 같은 값을 말한 문서 %d곳은 접었습니다: %s — 전부 보려면 --all-rows)"
                         % ("", len(hidden), " · ".join(safe_name(name) for name in hidden)))
        return lines

    def _agreement_lines(self):
        agreements = self.result.agreements
        lines = ["[정보] 일치 확인 %d건" % len(agreements)]
        if not agreements:
            lines.append("  (없음)")
            return lines
        for item, facet, display, doc_count, silent in agreements:
            line = "  %s / %s — %s (값을 말한 문서 %d곳 일치)" % (
                ITEM_LABEL[item], facet, safe_line(display), doc_count)
            if silent:
                line += " / %s 에서는 값을 찾지 못했습니다 — 일치가 아닙니다" % (
                    " · ".join(mask_then_truncate(safe_name(name), 22) for name in silent))
            lines.append(line)
        if any(entry[4] for entry in agreements):
            lines.append("  (※ '값을 찾지 못함' 이 붙은 줄은 그 문서를 직접 열어 확인하세요 — "
                         "표현이 달라 추출에 실패했을 수 있습니다)")
        return lines

    def _coverage_lines(self):
        result = self.result
        total_docs = len(self.documents)
        lines = [COVERAGE_HEADER]
        lines.append("  문서       %d개 중 %d개 읽음 (읽지 못한 문서 %d개)" % (
            total_docs, len(self.readable), len(self.unreadable)))
        for doc in self.unreadable:
            lines.append("             · %s — %s" % (safe_name(doc.name), safe_line(doc.error)))
        comparable = [key for key in ITEM_KEYS if FACETS[key]]
        lines.append("  대조한 항목 %d개 중 %d개를 2개 이상 문서에서 찾아 대조 (값 대조 대상은 %d개 — "
                     "평가·검사 항목은 CRF 전용 폼 존재 여부만 봅니다)" % (
                         len(ITEM_KEYS), self.compared_count(), len(comparable)))
        not_compared = [key for key in comparable if key not in result.compared_items]
        if not_compared:
            lines.append("  값 못 찾은 항목 %s — 어느 문서에서도 값을 찾지 못했거나 한 문서에만 있습니다"
                         % ", ".join(ITEM_LABEL[key] for key in not_compared))
        lines.append("  대조 불가  %d개 단위 (아래 사유 참조 — '일치'가 아니라 '판단 못 함'입니다)"
                     % len(result.uncomparable))
        for entry in result.uncomparable:
            docs = (" [%s]" % " · ".join(safe_name(name) for name in entry.docs)) if entry.docs else ""
            lines.append("             · %s / %s — %s%s" % (
                ITEM_LABEL[entry.item], entry.facet, safe_line(entry.reason), docs))
        lines.append("  정규화     표기가 달랐지만 같은 값으로 본 경우 %d건" % result.normalized_pairs)
        for example in result.normalized_examples:
            lines.append("             · %s" % safe_line(example))
        lines.append("             규칙 전문은 정합점검.md 부록에 인쇄됩니다")
        for note in self.scan_notes:
            lines.append("  안 본 것   %s" % safe_line(note))
        for note in self.baseline_notes:
            lines.append("  개정 축     %s" % safe_line(note))
        auto = len([doc for doc in self.documents if doc.role_source == "자동"])
        lines.append("  자동판별   문서 %d개 자동, --role 지정 %d개" % (auto, self.role_option_count))
        lines.append("  안 보는 것 문서 하나의 품질·규정 준수 여부·표본수 적정성은 판정하지 않습니다")
        return lines

    # ------------------------------------------------------------ 콘솔

    def console(self):
        critical = [f for f in self.all_findings if f.severity == "치명"]
        warnings = [f for f in self.all_findings if f.severity == "경고"]
        lines = [CONSOLE_TITLE]
        lines.append("입력: %s  (문서 %d개)" % (safe_name(self.input_label), len(self.documents)))
        if self.baseline_label:
            lines.append("이전 패킷(--baseline): %s" % safe_name(self.baseline_label))
        lines.append("")
        lines.extend(self._roles_block())
        lines.append("")
        lines.append("(기준 = 프로토콜. 프로토콜이 그 값을 말하지 않으면 값이 가장 많은 문서를 기준으로 씁니다.")
        lines.append(" 기준에 없는 값을 말한 문서만 '다름'으로 찍고, 일부만 적은 것은 넘어갑니다.)")
        lines.append("")
        lines.extend(self._finding_lines(critical, "[치명]"))
        lines.append("")
        lines.extend(self._finding_lines(warnings, "[경고]"))
        lines.append("")
        lines.extend(self._agreement_lines())
        lines.append("")
        lines.extend(self._coverage_lines())
        text = "\n".join(lines)
        _require_coverage(text)
        return text

    # ------------------------------------------------------------ 마크다운

    def markdown(self, exit_code):
        counts = self.counts()
        lines = ["# 제출서류 정합점검 결과", ""]
        lines.append("- 도구: `irbpack %s` (외부 의존성 0 · 네트워크 0 · 원본 읽기 전용)" % __version__)
        lines.append("- 입력: `%s` — 문서 %d개(읽음 %d개)" % (
            safe_name(self.input_label), len(self.documents), len(self.readable)))
        if self.baseline_label:
            lines.append("- 이전 패킷: `%s`" % safe_name(self.baseline_label))
        lines.append("- 결과: **치명 %d건 · 경고 %d건** (종료코드 %d)" % (counts["치명"], counts["경고"], exit_code))
        for doc in self.readable:
            for note in doc.notes:
                lines.append("- 주의: `%s` — %s" % (safe_name(doc.name), safe_line(note)))
        for doc in self.unreadable:
            lines.append("- 읽지 못함: `%s` — %s" % (safe_name(doc.name), safe_line(doc.error)))
        lines.append("")
        lines.append("> 이 툴은 **서류들 사이**의 값이 서로 다른 곳만 찍습니다. 어느 값이 옳은지,")
        lines.append("> IRB 가 승인할지는 판정하지 않습니다.")
        lines.append("")
        lines.append("## 콘솔 출력 전문")
        lines.append("")
        console = self.console()
        fence = _fence_for(console)      # 본문에 ``` 가 들어 있어도 펜스가 깨지지 않게
        lines.append(fence)
        lines.append(console)
        lines.append(fence)
        lines.append("")
        lines.extend(self._matrix_markdown())
        lines.append("")
        lines.extend(self._appendix_markdown())
        text = "\n".join(lines) + "\n"
        _require_coverage(text)
        return text

    def _matrix_markdown(self):
        """항목 12종 × 문서 전체 대조표."""
        lines = ["## 항목 12종 × 문서 대조표", ""]
        docs = [doc for doc in self.documents if doc.read_ok]
        header = ["항목 / 대조단위"] + [
            "%s<br>(%s)" % (_md_cell(mask_then_truncate(safe_name(doc.name), 22)), doc.role or "?")
            for doc in docs]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "---|" * len(header))
        table = {}
        for mention in self.all_mentions():
            table.setdefault((mention.item, mention.facet), {}).setdefault(mention.doc, set()).add(mention.display)
        for item in ITEM_KEYS:
            facets = FACETS[item] or ["(값 대조 안 함)"]
            for facet in facets:
                cells = []
                for doc in docs:
                    values = table.get((item, facet), {}).get(doc.name)
                    cells.append(_md_cell(" · ".join(sorted(values))) if values else "—")
                lines.append("| %s / %s | %s |" % (ITEM_LABEL[item], facet, " | ".join(cells)))
        return lines

    def all_mentions(self):
        return getattr(self, "_mentions", [])

    def set_mentions(self, mentions):
        self._mentions = mentions

    def _appendix_markdown(self):
        lines = ["## 부록 — 적용한 정규화 규칙", ""]
        lines.append("| 규칙 | 내용 |")
        lines.append("|---|---|")
        for name, description in RULES:
            lines.append("| `%s` | %s |" % (name, description))
        lines.append("")
        lines.append("정규화는 **표기 차이만** 흡수합니다. `만 5~12세` 와 `만 6~12세` 는 절대 같지 않습니다.")
        return lines

    # ------------------------------------------------------------ CSV

    def csv_conflicts(self):
        header = ["항목", "심각도", "유형", "상태", "문서", "역할", "값", "근거위치", "원문", "요약"]
        rows = []
        for finding in self.all_findings:
            for row in finding.rows:
                rows.append([
                    finding.label, finding.severity, "/".join(finding.kinds), row.status,
                    safe_name(row.doc), row.role, " · ".join(row.values), row.where, row.quote,
                    finding.summary,
                ])
            if not finding.rows:
                rows.append([finding.label, finding.severity, "/".join(finding.kinds), "",
                             "", "", "", "", " ".join(finding.notes), finding.summary])
        return header, rows

    def csv_extractions(self):
        header = ["대조결과", "문서", "역할", "항목", "대조단위", "추출값", "근거위치", "원문문장"]
        rows = []
        seen = set()
        roles = dict((doc.name, doc.role) for doc in self.documents)
        status = self._row_status_index()
        for mention in self.all_mentions():
            key = (mention.doc, mention.item, mention.facet, mention.value, mention.where)
            if key in seen:
                continue                                   # 같은 값이 같은 자리에서 두 번 잡힌 경우
            seen.add(key)
            rows.append([
                status.get((mention.doc, mention.item), ""),
                safe_name(mention.doc), roles.get(mention.doc, ""), ITEM_LABEL[mention.item],
                mention.facet, mention.display, mention.where, mention.quote,
            ])
        rows.sort(key=lambda row: ({"다름": 0, "값못찾음": 1, "기준": 2}.get(row[0], 3), row[1], row[3]))
        return header, rows

    def _row_status_index(self):
        """(문서, 항목) → 이 문서가 그 항목에서 어떤 판정을 받았는가."""
        index = {}
        for finding in self.all_findings:
            for row in finding.rows:
                current = index.get((row.doc, finding.item))
                if current != "다름":
                    index[(row.doc, finding.item)] = row.status
        return index

    def csv_uncomparable(self):
        header = ["항목", "대조단위", "사유", "관련문서"]   # 고정 항목은 compare.py 가 넣는다
        rows = []
        for entry in self.result.uncomparable:
            rows.append([ITEM_LABEL[entry.item], entry.facet, entry.reason,
                         " · ".join(safe_name(name) for name in entry.docs)])
        for doc in self.unreadable:
            rows.append(["(문서 전체)", "-", "읽지 못했습니다: %s" % doc.error, safe_name(doc.name)])
        return header, rows

    def write_all(self, out_dir, exit_code):
        written = [write_text(out_dir, "정합점검.md", self.markdown(exit_code))]
        written.append(write_csv(out_dir, "불일치목록.csv", *self.csv_conflicts()))
        written.append(write_csv(out_dir, "항목추출표.csv", *self.csv_extractions()))
        written.append(write_csv(out_dir, "대조불가.csv", *self.csv_uncomparable()))
        return written


def _fence_for(text):
    """본문보다 긴 백틱 울타리를 만든다.

    문서에 ``` 가 들어 있으면 코드블록이 그 자리에서 닫혀, 그 뒤의 리포트가 통째로
    본문으로 렌더링된다 — 문서가 리포트에 가짜 소견을 심을 수 있다(라운드2 B-2).
    """
    longest = 0
    run = 0
    for char in text:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def _md_cell(value):
    """마크다운 표 칸 — 값 안의 '|' 가 열을 늘려 표를 통째로 어긋나게 하는 것을 막는다."""
    return safe_line(value).replace("|", "/")


def _require_coverage(text):
    if COVERAGE_HEADER not in text:
        raise ReportIntegrityError(
            "커버리지 자백 블록이 없는 리포트는 내보내지 않습니다 — 못 본 것을 말하지 않는 점검표는 "
            "'다 봤다'로 읽힙니다"
        )

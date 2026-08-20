"""리포트 — 커버리지 자백이 항상 맨 위에 옵니다.

**커버리지 자백 블록이 없으면 리포트를 내보내지 않습니다.** 이건 스타일이
아니라 안전장치입니다. 몇 개를 봤고 몇 개를 못 봤는지 말하지 않는 체커는
'이상 없음'이 무슨 뜻인지 알 수 없고, 그런 통과는 없느니만 못합니다.
`ReportIntegrityError` 는 그 상태로 출력이 나가는 것을 코드 레벨에서 막습니다.
"""

import csv
import io
import os
import unicodedata
from typing import Dict, List, Optional, Tuple

from . import __version__
from .analyze import Analysis, percent
from .judge import GRADE_CRITICAL, GRADE_SKIP, Judgement, VERDICT_STALE
from .manuscript import SECTION_LABEL
from .numbers import Number, SKIP_ORDER
from .safety import InputError, csv_safe, redact, safe_out_path

COVERAGE_MARKER = "── 커버리지 자백"
WIDTH = 62
RULE = "─" * WIDTH

OUT_REPORT = "출처대조.md"
OUT_ISSUES = "문제목록.csv"
OUT_TABLE = "대조표.csv"
OUT_SUMMARY = "요약.txt"

SECTION_ORDER = ["abstract", "results", "tables", "captions", "methods",
                 "introduction", "discussion", "references", "other"]


class ReportIntegrityError(Exception):
    """커버리지 자백이 빠진 리포트를 내보내려 할 때."""


def _require_coverage(text: str) -> str:
    if COVERAGE_MARKER not in text:
        raise ReportIntegrityError(
            "커버리지 자백 블록이 없어 리포트를 출력하지 않습니다. "
            "(몇 개를 대조하고 몇 개를 건너뛰었는지 밝히지 않는 리포트는 내보내지 않습니다.)")
    return text


def line_tag(analysis: Analysis, number: Number) -> str:
    if analysis.manuscript.line_kind == "문단 번호":
        return "문단%d" % number.line
    return "L%d" % number.line


def _num(value: int) -> str:
    return "{:,}".format(value)


def display_width(text: str) -> int:
    """한글·한자는 터미널에서 두 칸을 먹습니다 — 표를 맞추려면 세어야 합니다."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in text)


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


# --------------------------------------------------------------------------- #
# 커버리지 자백
# --------------------------------------------------------------------------- #

def coverage_lines(analysis: Analysis) -> List[str]:
    cov = analysis.coverage
    head = COVERAGE_MARKER + " "
    lines = [head + "─" * max(0, WIDTH - display_width(head))]
    lines.append("%s%s개   (대조 대상 절에서 뽑은 숫자)"
                 % (pad("추출 숫자", 17), _num(cov.extracted)))
    parts = []
    for key in SECTION_ORDER:
        count = cov.by_section.get(key)
        if count:
            parts.append("%s %d" % (SECTION_LABEL.get(key, key), count))
    detail = ("  (%s)" % " · ".join(parts)) if parts else ""
    lines.append("%s%s개%s" % (pad("  대조 대상", 17), _num(cov.compared), detail))
    lines.append("%s%s개" % (pad("  건너뜀", 17), _num(cov.skipped)))
    for reason in SKIP_ORDER:
        count = cov.skip_counts.get(reason)
        if count:
            lines.append("      %s%s" % (pad(reason, 24), _num(count)))
    for reason, count in sorted(cov.skip_counts.items()):
        if reason not in SKIP_ORDER:
            lines.append("      %s%s" % (pad(reason, 24), _num(count)))
    if cov.compared:
        rate = cov.unmatched_rate
        verdict = ("임계 %.0f%% 이내" % analysis.max_unmatched
                   if rate <= analysis.max_unmatched
                   else "임계 %.0f%% 초과" % analysis.max_unmatched)
        lines.append("%s매칭 %s · 미매칭 %s  (미매칭율 %.1f%%, %s)"
                     % (pad("  대조 결과", 17), _num(cov.matched),
                        _num(cov.unmatched), rate, verdict))
    else:
        lines.append("%s대조할 숫자가 없습니다" % pad("  대조 결과", 17))
    lines.append("%s%s개  (%s — `--sections` 로 조정)"
                 % (pad("대조 제외 절", 17), _num(cov.off_section),
                    _excluded_label(analysis)))
    unread = len(analysis.current.unread) + \
        (len(analysis.previous.unread) if analysis.previous else 0)
    lines.append("%s%s개%s"
                 % (pad("읽지 못한 파일", 17), _num(unread),
                    "" if unread == 0 else "  ↓ 아래 목록"))
    lines.append(RULE)
    return lines


def _excluded_label(analysis: Analysis) -> str:
    excluded = [SECTION_LABEL.get(s, s) for s in SECTION_ORDER
                if s not in analysis.sections]
    return "·".join(excluded) if excluded else "없음"


# --------------------------------------------------------------------------- #
# 콘솔
# --------------------------------------------------------------------------- #

def render_console(analysis: Analysis, *, max_items: int = 40) -> str:
    lines: List[str] = []
    lines.append("tracecheck %s — 원고 수치 출처 대조" % __version__)
    lines.append("원고: %s  (문단 %d개 · 표 %d개 · 위치는 %s)"
                 % (os.path.basename(analysis.manuscript.path),
                    analysis.manuscript.paragraph_count,
                    analysis.manuscript.table_count,
                    analysis.manuscript.line_kind))
    lines.append("현재 번들: %s  (파일 %d개 · 수치 셀 %s개)"
                 % (_roots_label(analysis.current.roots),
                    analysis.current.file_count, _num(analysis.current.cell_count)))
    if analysis.previous is not None:
        lines.append("이전 번들: %s  (파일 %d개 · 수치 셀 %s개)"
                     % (_roots_label(analysis.previous.roots),
                        analysis.previous.file_count,
                        _num(analysis.previous.cell_count)))
    else:
        lines.append("이전 번들: 없음")
    lines.append("")
    lines.extend(coverage_lines(analysis))
    lines.append("")

    unread = list(analysis.current.unread)
    if analysis.previous is not None:
        unread += [(f + " (이전)", why) for f, why in analysis.previous.unread]
    if unread:
        lines.append("[읽지 못한 파일] %d건 — 이 파일들에 있는 값은 '출처 없음'으로 잡힐 수 있습니다"
                     % len(unread))
        for name, why in unread[:15]:
            lines.append("  · %s — %s" % (name, why))
        if len(unread) > 15:
            lines.append("  · … 외 %d건 (대조표.csv 옆의 출처대조.md 참조)" % (len(unread) - 15))
        lines.append("")

    for note in analysis.warnings:
        lines.append("※ %s" % note)
    if analysis.warnings:
        lines.append("")

    if analysis.undecidable:
        lines.append("[판정불가] %s" % analysis.undecidable)
        lines.append("")
        lines.append("판정: 판정불가. 종료 코드 3")
        return _require_coverage("\n".join(lines))

    criticals = analysis.criticals
    warns = analysis.warns
    lines.append("[치명] %d건%s" % (len(criticals), "" if criticals else " — 없음"))
    for judgement in criticals[:max_items]:
        lines.extend(_finding_lines(analysis, judgement))
    if len(criticals) > max_items:
        lines.append("  … 외 %d건 (문제목록.csv 참조)" % (len(criticals) - max_items))
    lines.append("")
    lines.append("[경고] %d건%s" % (len(warns), "" if warns else " — 없음"))
    for judgement in warns[:max_items]:
        lines.extend(_finding_lines(analysis, judgement))
    if len(warns) > max_items:
        lines.append("  … 외 %d건 (문제목록.csv 참조)" % (len(warns) - max_items))
    lines.append("")
    lines.append("[정보] 매칭 %d건 → 대조표.csv 참조" % len(analysis.infos))
    lines.append("")
    lines.append("판정: %s" % _verdict_sentence(analysis))
    return _require_coverage("\n".join(lines))


def _roots_label(roots: List[str]) -> str:
    return " + ".join(os.path.basename(r.rstrip(os.sep)) or r for r in roots)


def _finding_lines(analysis: Analysis, judgement: Judgement) -> List[str]:
    number = judgement.number
    head = "  %s %s  \"%s\"" % (line_tag(analysis, number),
                                number.loc, number.context)
    body = "      → %s: %s" % (number.text, judgement.note)
    out = [head, body]
    if judgement.grade != GRADE_CRITICAL and judgement.matches:
        out.append("        출처: %s" % judgement.source_locs)
    if judgement.advice:
        out.append("        권고: %s" % judgement.advice)
    return out


def _verdict_sentence(analysis: Analysis) -> str:
    if analysis.undecidable:
        return "판정불가. 종료 코드 3"
    criticals = len(analysis.criticals)
    warns = len(analysis.warns)
    if criticals:
        stale = sum(1 for j in analysis.criticals if j.verdict == VERDICT_STALE)
        extra = " (구버전 잔존 %d건)" % stale if stale else ""
        return "치명 %d건%s. 종료 코드 1" % (criticals, extra)
    if warns:
        return "경고 %d건, 치명 없음. 종료 코드 2" % warns
    return "대조 대상 %d개 전부 출처 확인됨. 종료 코드 0" % analysis.coverage.compared


# --------------------------------------------------------------------------- #
# 문장 초안
# --------------------------------------------------------------------------- #

def sentences(analysis: Analysis) -> List[str]:
    cov = analysis.coverage
    bundle = _roots_label(analysis.current.roots)
    confirmed = len(analysis.infos)
    flagged = len(analysis.warns)
    missing = len(analysis.criticals)
    kr_missing = ("" if not missing else
                  " %d개는 현재 산출물에서 출처를 확인하지 못하였다." % missing)
    en_missing = ("" if not missing else
                  " %d value(s) could not be located in the current outputs."
                  % missing)
    kr = ("본 원고의 Abstract·Results·표에 보고된 수치 %d개를 분석 산출물"
          "(%s, 파일 %d개·수치 셀 %s개)과 자동 대조하여 %d개(%.1f%%)에서 출처를 확인하였고, "
          "%d개는 단위·자릿수 확인이 필요한 것으로 표시하였다. "
          "대조는 반올림 자릿수를 고려한 값 일치 기준으로 수행하였으며, "
          "값이 가리키는 지표의 의미(라벨) 일치는 검증 대상이 아니다.%s"
          % (cov.compared, bundle, analysis.current.file_count,
             _num(analysis.current.cell_count), confirmed,
             percent(confirmed, cov.compared), flagged, kr_missing))
    en = ("All %d numeric values reported in the Abstract, Results and tables were "
          "programmatically traced back to the analysis output files (%s; %d files, "
          "%s numeric cells); %d (%.1f%%) were located unambiguously and %d were "
          "flagged for unit or precision review. Matching was performed on values "
          "only, allowing for the reported number of decimal places; semantic "
          "(label) correspondence was not verified.%s"
          % (cov.compared, bundle, analysis.current.file_count,
             _num(analysis.current.cell_count), confirmed,
             percent(confirmed, cov.compared), flagged, en_missing))
    if analysis.previous is not None:
        kr += (" 또한 재분석 이전 산출물(%s)과 대조하여, 이전 결과에만 존재하는 값이 "
               "원고에 남아 있는지 확인하였다."
               % _roots_label(analysis.previous.roots))
        en += (" Values present only in the pre-reanalysis outputs (%s) were "
               "additionally flagged as potentially stale."
               % _roots_label(analysis.previous.roots))
    return [kr, en]


# --------------------------------------------------------------------------- #
# 파일 산출물
# --------------------------------------------------------------------------- #

def render_markdown(analysis: Analysis) -> str:
    lines = ["# 원고 수치 출처 대조 (tracecheck %s)" % __version__, ""]
    lines.append("- 원고: `%s`" % os.path.basename(analysis.manuscript.path))
    lines.append("- 현재 번들: `%s` — 파일 %d개 · 수치 셀 %s개"
                 % (_roots_label(analysis.current.roots),
                    analysis.current.file_count, _num(analysis.current.cell_count)))
    if analysis.previous is not None:
        lines.append("- 이전 번들: `%s` — 파일 %d개 · 수치 셀 %s개"
                     % (_roots_label(analysis.previous.roots),
                        analysis.previous.file_count,
                        _num(analysis.previous.cell_count)))
    else:
        lines.append("- 이전 번들: **미지정** — 구버전 잔존 검사는 수행되지 않았습니다.")
    lines.append("- 대조 대상 절: %s"
                 % ", ".join(SECTION_LABEL.get(s, s) for s in analysis.sections))
    lines.append("")
    lines.append("## 커버리지 자백")
    lines.append("")
    lines.append("```")
    lines.extend(coverage_lines(analysis))
    lines.append("```")
    lines.append("")

    unread = [(f, w) for f, w in analysis.current.unread]
    if analysis.previous is not None:
        unread += [(f + " (이전 번들)", w) for f, w in analysis.previous.unread]
    lines.append("## 읽지 못한 파일 (%d건)" % len(unread))
    lines.append("")
    if unread:
        lines.append("| 파일 | 사유 |")
        lines.append("|---|---|")
        for name, why in unread:
            lines.append("| `%s` | %s |" % (name, why))
    else:
        lines.append("없음 — 번들의 모든 대상 파일을 읽었습니다.")
    lines.append("")

    if analysis.warnings:
        lines.append("## 주의")
        lines.append("")
        for note in analysis.warnings:
            lines.append("- %s" % note)
        lines.append("")

    if analysis.undecidable:
        lines.append("## 판정불가")
        lines.append("")
        lines.append(analysis.undecidable)
        lines.append("")
        lines.append("판정을 내리지 않았습니다(종료 코드 3). "
                     "대조율이 낮은 상태에서 낸 '치명'은 신뢰할 수 없기 때문입니다.")
        lines.append("")
        return _require_coverage("\n".join(lines) + "\n")

    for grade, items in (("치명", analysis.criticals), ("경고", analysis.warns)):
        lines.append("## %s (%d건)" % (grade, len(items)))
        lines.append("")
        if not items:
            lines.append("없음.")
            lines.append("")
            continue
        for judgement in items:
            number = judgement.number
            lines.append("### %s · %s · `%s`"
                         % (line_tag(analysis, number), number.loc, number.text))
            lines.append("")
            lines.append("> %s" % number.context)
            lines.append("")
            lines.append("- 판정: **%s** — %s" % (judgement.verdict, judgement.note))
            if judgement.matches or judgement.prev_matches:
                lines.append("- 출처: %s (매칭 %d곳, 방식 %s)"
                             % (judgement.source_locs, judgement.match_count,
                                judgement.method or "-"))
            if judgement.advice:
                lines.append("- 권고: %s" % judgement.advice)
            lines.append("")

    lines.append("## 정보 (매칭 %d건)" % len(analysis.infos))
    lines.append("")
    lines.append("출처가 확인된 값은 `%s` 에 파일·행·열과 함께 전부 들어 있습니다." % OUT_TABLE)
    lines.append("")
    lines.append("## 재현성 문단 초안 (KR / EN)")
    lines.append("")
    for sentence in sentences(analysis):
        lines.append("> %s" % sentence)
        lines.append("")
    if analysis.criticals:
        lines.append("※ 치명 %d건이 남아 있는 상태의 초안입니다. "
                     "고치기 전에 이 문장을 논문에 붙이지 마세요."
                     % len(analysis.criticals))
        lines.append("")
    lines.append("## 이 리포트가 말하지 않는 것")
    lines.append("")
    lines.append("- 값의 **의미**는 보지 않습니다. `12.4` 가 ISI 평균인지 나이 평균인지 "
                 "확인하지 않습니다 — 값이 번들 어딘가에 있다는 사실만 말합니다.")
    lines.append("- 산술을 **재계산하지 않습니다**(비율·p·GRIM 은 `numcheck` 영역).")
    lines.append("- 단위를 환산하지 않습니다(min↔h, ms↔s 는 매칭 실패로 둡니다).")
    lines.append("- 어느 값이 맞는지 **고쳐 주지 않습니다.** 원고는 읽기만 합니다.")
    lines.append("")
    return _require_coverage("\n".join(lines) + "\n")


def render_summary(analysis: Analysis) -> str:
    lines = ["tracecheck %s — 요약" % __version__,
             "원고: %s" % os.path.basename(analysis.manuscript.path),
             "번들: %s" % _roots_label(analysis.current.roots)]
    lines.extend(coverage_lines(analysis))
    if analysis.undecidable:
        lines.append("판정불가: %s" % analysis.undecidable)
    else:
        lines.append("치명 %d건 · 경고 %d건 · 정보 %d건"
                     % (len(analysis.criticals), len(analysis.warns),
                        len(analysis.infos)))
    lines.append("판정: %s" % _verdict_sentence(analysis))
    return _require_coverage("\n".join(lines) + "\n")


TABLE_HEADER = ["줄번호", "절", "원문", "추출값", "정규화값", "판정", "매칭수",
                "매칭방식", "출처파일", "출처위치", "출처원문값", "반올림자릿수",
                "등급", "설명"]
ISSUE_HEADER = ["줄번호", "절", "등급", "원문", "추출값", "판정", "설명", "권고"]


def render_table_csv(analysis: Analysis) -> str:
    by_number: Dict[int, Judgement] = {id(j.number): j for j in analysis.judgements}
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(TABLE_HEADER)
    for number in analysis.numbers:
        judgement = by_number.get(id(number))
        if number.skip:
            row = [line_tag(analysis, number), number.loc, number.context,
                   number.raw, number.text, number.skip, 0, "", "", "", "",
                   number.decimals, GRADE_SKIP, "건너뜀 사유: %s" % number.skip]
        elif judgement is None:
            continue
        else:
            row = [line_tag(analysis, number), number.loc, number.context,
                   number.raw, number.text, judgement.verdict,
                   judgement.match_count, judgement.method,
                   judgement.source_files, judgement.source_locs,
                   judgement.source_raws, number.decimals, judgement.grade,
                   judgement.note]
        writer.writerow([csv_safe(cell) for cell in row])
    return buffer.getvalue()


def render_issues_csv(analysis: Analysis) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(ISSUE_HEADER)
    for judgement in analysis.criticals + analysis.warns:
        number = judgement.number
        writer.writerow([csv_safe(cell) for cell in [
            line_tag(analysis, number), number.loc, judgement.grade,
            number.context, number.text, judgement.verdict, judgement.note,
            judgement.advice]])
    return buffer.getvalue()


def write_outputs(analysis: Analysis, out_dir: str,
                  protected: Optional[List[str]] = None) -> List[str]:
    """산출물 4종을 `--out-dir` 안에만 만듭니다."""
    protected = protected or []
    payloads = [
        (OUT_REPORT, render_markdown(analysis)),
        (OUT_ISSUES, render_issues_csv(analysis)),
        (OUT_TABLE, render_table_csv(analysis)),
        (OUT_SUMMARY, render_summary(analysis)),
    ]
    # 4개를 **먼저 전부 검사**하고, 임시 파일에 다 쓴 다음, 마지막에 한꺼번에
    # 자리를 바꿉니다. 중간에 실패하면 옛 리포트가 그대로 남아야 합니다 —
    # 새 리포트 2개와 옛 리포트 2개가 섞이면 "종료 코드 0"이라고 적힌 옛
    # 요약을 보고 안심하게 됩니다(2라운드에서 실제로 재현됐습니다).
    targets = [(safe_out_path(out_dir, name, protected), text)
               for name, text in payloads]
    staged: List[Tuple[str, str]] = []
    try:
        for path, text in targets:
            temp = path + ".작성중"
            try:
                with open(temp, "w", encoding="utf-8-sig", newline="") as handle:
                    handle.write(text)
            except OSError as exc:
                raise InputError("산출물을 쓸 수 없습니다: %s (%s)"
                                 % (redact(path), exc.__class__.__name__))
            staged.append((temp, path))
        written: List[str] = []
        for temp, path in staged:
            try:
                os.replace(temp, path)
            except OSError as exc:
                raise InputError("산출물을 바꿔 넣을 수 없습니다: %s (%s)"
                                 % (redact(path), exc.__class__.__name__))
            written.append(path)
        staged = []
        return written
    finally:
        for temp, _path in staged:
            try:
                os.remove(temp)
            except OSError:
                pass

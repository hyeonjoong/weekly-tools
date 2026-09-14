"""리포트 출력 — 콘솔 · 봉투점검.md · CSV 3종.

**커버리지 자백이 없으면 리포트를 내지 않는다.** 무엇을 못 봤는지 적지
않은 점검표는 '리포트에 없는 항목 = 일치'로 읽히고, 그 오해가 이 툴이
막으려는 사고를 그대로 다시 만든다. 그래서 렌더링 단계에서 강제한다.
"""

from typing import List

from . import __version__
from .analysis import Report, mb
from .findings import CRITICAL, INFO, WARNING
import re

from .safeio import csv_cell, sanitize

CONFESSION_HEADING = "[커버리지 자백]"

#: 한 건의 근거로 찍는 최대 줄 수. 미디어가 10만 개인 문서에서 콘솔이
#: 10만 줄로 폭발하는 것을 막는다.
MAX_DETAIL_LINES = 30


#: 리포트 파일에서 가려야 하는 사람 이름 줄.
_PERSON_LINE = re.compile(r"^(작성자|최종수정자):\s*(.+)$")
#: 표제 불일치 근거 줄에 실리는 저자 목록 (`… · 저자순서: 원고 '...' ↔ …`).
_AUTHOR_LINE = re.compile(r"·\s*저자순서:")
_QUOTED = re.compile(r"'([^']*)'")


def _mask_person(line: str) -> str:
    """리포트 **파일**에 실릴 줄에서 사람 이름을 가린다.

    `봉투점검.md` 는 공저자에게 그대로 전달되는 파일이다. 콘솔에는
    본인만 보므로 이름을 그대로 둔다.
    """
    match = _PERSON_LINE.match(line.strip())
    if match:
        return f"{match.group(1)}: {mask_name(match.group(2))}"
    if _AUTHOR_LINE.search(line):
        def _mask_group(found):
            names = [mask_name(n) for n in found.group(1).split(",") if n.strip()]
            return "'" + ", ".join(names) + "'" if names else "''"
        return _QUOTED.sub(_mask_group, line)
    return line


def _detail_lines(finding, indent: str, limit=MAX_DETAIL_LINES):
    """근거 줄. 콘솔에서만 자른다 — 리포트 파일은 전부 싣는다.

    파일까지 자르면서 "전체는 리포트에 있습니다"라고 적으면 그 말 자체가
    거짓이 되고, 치명 근거가 **어디에도 없는** 상태가 된다.
    """
    if limit is None or len(finding.detail) <= limit:
        return [indent + sanitize(d, 300) for d in finding.detail]
    shown = finding.detail[:limit]
    lines = [indent + sanitize(d, 300) for d in shown]
    lines.append(f"{indent}… 외 {len(finding.detail) - limit}건 "
                 "(전체는 --out-dir 의 봉투점검.md 에 있습니다)")
    return lines


class ReportIntegrityError(Exception):
    """자백 없는 리포트를 만들려고 했다. 출력 대신 실패한다."""


def md_cell(value, limit: int = 300) -> str:
    """마크다운 표 칸에 넣을 문자열.

    파일명에 `|` 를 넣으면 칸이 쪼개져 **고아를 '본문앵커'로 보이게** 위조할
    수 있다. 이 툴이 잡으려는 거짓말과 같은 종류라 표 문자를 막는다.
    """
    text = sanitize(value, limit)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("`", "\u02cb")


def mask_name(value: str) -> str:
    """사람 이름을 가린다. 리포트는 공저자에게 그대로 전달되는 파일이다."""
    text = sanitize(value).strip()
    if not text or text == "(없음)":
        return "(없음)"
    parts = [p for p in re.split(r"\s+", text) if p]
    masked = []
    for part in parts:
        masked.append(part[0] + "*" * (len(part) - 1) if len(part) > 1 else part)
    return " ".join(masked)


def _require_confession(report: Report) -> None:
    cov = report.coverage
    if not cov.parsed:
        raise ReportIntegrityError("커버리지 자백에 '읽음' 항목이 없습니다 — 리포트를 내지 않습니다")
    if not cov.unchecked:
        raise ReportIntegrityError("커버리지 자백에 '검사 안 함' 항목이 없습니다 — 리포트를 내지 않습니다")


def _confession_lines(report: Report) -> List[str]:
    cov = report.coverage
    lines = [CONFESSION_HEADING]
    lines.append("  읽음:      " + ", ".join(sanitize(n) for n in cov.parsed))
    lines.append("  목록만:    " + (", ".join(sanitize(n) for n in cov.listed_only) or "(없음)"))
    excluded = [f"{sanitize(n)}/" for n in cov.excluded_dirs]
    excluded += [f"{sanitize(n)} (링크)" for n in cov.excluded_links]
    lines.append("  제외:      " + (", ".join(excluded) or "(하위폴더·링크 없음)")
                 + ("  ← 봉투로 보지 않았습니다" if excluded else ""))
    if cov.unreadable:
        lines.append("  못 읽음:   " + ", ".join(sanitize(n) for n in cov.unreadable))
    for index, item in enumerate(cov.unchecked):
        prefix = "  검사 안 함: " if index == 0 else "              · "
        lines.append(prefix + sanitize(item, 200))
    lines.append("  리포트에 안 나온 항목이 \"일치\"라는 뜻이 아닙니다.")
    return lines


def console_lines(report: Report) -> List[str]:
    _require_confession(report)
    info = report.manuscript
    env = report.envelope
    lines = [f"packlist {__version__} — 투고 봉투 점검"]
    subdir_note = f"   (하위폴더 {len(env.subdirs)}개는 제외 — 자백 참조)" if env.subdirs else ""
    lines.append(f"봉투: {env.label}/   파일 {len(env.visible_files)}개{subdir_note}")
    lines.append(f"원고: {sanitize(info.filename)}  "
                 f"(문단 {info.raw_paragraph_count} · 코멘트 {info.comment_count} · "
                 f"변경내용 {info.insertions + info.deletions})")
    if report.baseline_label:
        lines.append(f"기준 판본: {sanitize(report.baseline_label)}")
    lines.append("")
    for finding in report.findings:
        lines.append(f"[{finding.severity}] {sanitize(finding.title, 200)}")
        lines.extend(_detail_lines(finding, "       "))
    lines.append("")
    lines.extend(_confession_lines(report))
    lines.append("")
    lines.append(f"치명 {report.critical_count}건 · 경고 {report.warning_count}건"
                 f"  → 종료코드 {report.exit_code}")
    return lines


def markdown(report: Report) -> str:
    _require_confession(report)
    info = report.manuscript
    env = report.envelope
    out = [f"# 투고 봉투 점검 — {env.label}", "",
           f"- packlist {__version__}",
           f"- 원고: `{sanitize(info.filename)}`",
           f"- 문단 {info.raw_paragraph_count} · 코멘트 {info.comment_count} · "
           f"변경내용 {info.insertions + info.deletions}",
           f"- 봉투 총 {mb(env.total_bytes)} · 파일 {len(env.visible_files)}개"]
    if report.baseline_label:
        out.append(f"- 기준 판본: `{sanitize(report.baseline_label)}`")
    out += ["", f"**치명 {report.critical_count}건 · 경고 {report.warning_count}건 "
                f"→ 종료코드 {report.exit_code}**", ""]

    for severity, heading in ((CRITICAL, "치명"), (WARNING, "경고"), (INFO, "정보")):
        group = [f for f in report.findings if f.severity == severity]
        if not group:
            continue
        out.append(f"## {heading} ({len(group)}건)")
        out.append("")
        for finding in group:
            out.append(f"- **{sanitize(finding.title, 200)}**")
            for line in _detail_lines(finding, "", limit=None):
                out.append("  - " + _mask_person(line))
        out.append("")

    out.append("## 근거 — 원고 안 미디어")
    out.append("")
    out.append("| 미디어 | 바이트 | 해시앞8 | 앵커 | 중복그룹 |")
    out.append("|---|---:|---|---|---|")
    for row in report.assets:
        if row.kind != "미디어":
            continue
        out.append(f"| `{md_cell(row.name)}` | {row.size} | {md_cell(row.digest8)} | "
                   f"{md_cell(row.anchored)} | {md_cell(row.dup_group) or '-'} |")
    out.append("")

    out.append("## 근거 — 약속 ↔ 실물")
    out.append("")
    if report.promises:
        out.append("| 약속 토큰 | 본문 등장 | 봉투 안 파일 | 판정 |")
        out.append("|---|---:|---|---|")
        for row in report.promises:
            out.append(f"| {md_cell(row.token)} | {row.mentions} | "
                       f"{md_cell(row.matched) or '-'} | {md_cell(row.verdict)} |")
    else:
        out.append("본문이 부르는 보충자료 토큰이 없습니다.")
    out.append("")

    if report.frontmatter and report.frontmatter.checks:
        out.append("## 근거 — 표제 3종 (제목 · 저자순서 · 원고번호)")
        out.append("")
        out.append("| 문서 | 항목 | 판정 | 비고 |")
        out.append("|---|---|---|---|")
        for filename, checks in report.frontmatter.checks.items():
            for check in checks:
                out.append(f"| `{md_cell(filename)}` | {md_cell(check.field)} | "
                           f"{md_cell(check.verdict)} | "
                           f"{md_cell(check.note or check.found, 120) or '-'} |")
        out.append("")

    out.append("## " + CONFESSION_HEADING)
    out.append("")
    for line in _confession_lines(report)[1:]:
        out.append("- " + _mask_person(line.strip()))
    out.append("")
    return "\n".join(out)


def _csv(rows: List[List[str]]) -> str:
    return "\n".join(",".join(f'"{csv_cell(c).replace(chr(34), chr(34) * 2)}"' for c in row)
                     for row in rows) + "\n"


def assets_csv(report: Report) -> str:
    rows = [["파일", "종류", "바이트", "해시앞8", "앵커여부", "중복그룹", "본문참조수"]]
    for row in report.assets:
        rows.append([row.name, row.kind, str(row.size), row.digest8,
                     row.anchored, row.dup_group, str(row.mentions)])
    return _csv(rows)


def promises_csv(report: Report) -> str:
    rows = [["약속토큰", "본문등장수", "실물파일", "판정"]]
    for row in report.promises:
        rows.append([row.token, str(row.mentions), row.matched, row.verdict])
    return _csv(rows)


def ledger_csv(report: Report) -> str:
    rows = [["판본", "문단", "코멘트", "변경내용", "미디어수", "중복", "고아", "바이트"]]
    for row in report.ledger:
        rows.append([row.label, str(row.paragraphs), str(row.comments), str(row.revisions),
                     str(row.media), str(row.duplicates), str(row.orphans), str(row.total_bytes)])
    return _csv(rows)


ARTIFACTS = (
    ("봉투점검.md", markdown),
    ("자산목록.csv", assets_csv),
    ("약속대조.csv", promises_csv),
    ("회차대장.csv", ledger_csv),
)

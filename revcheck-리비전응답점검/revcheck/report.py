"""리포트 렌더링과 산출 파일 저장.

원칙 두 가지.
    1. **커버리지 자백 없이는 리포트를 내지 않는다.** 무엇을 못 봤는지 말하지 않는
       체커는 '이상 없음'과 '검사 못 함'을 구분해 주지 못한다.
    2. 원고에서 나온 글자는 그대로 화면·CSV 로 나간다 — 그래서 제어문자를 지우고
       (터미널 조작 방지), CSV 셀은 수식으로 실행되지 않게 막는다.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import List, Sequence

from .inventory import CITECHECK_HEADER
from .model import CRITICAL, INFO, SEVERITY_ORDER, WARNING, Result
from .normalize import strip_control

__all__ = [
    "REPORT_MD",
    "ISSUES_CSV",
    "CHANGES_CSV",
    "ADDED_REFS_CSV",
    "render_text",
    "render_markdown",
    "write_outputs",
    "csv_safe",
]

REPORT_MD = "리비전점검.md"
ISSUES_CSV = "문제목록.csv"
CHANGES_CSV = "변경목록.csv"
ADDED_REFS_CSV = "추가문헌.csv"

ISSUE_HEADER = ["등급", "유형", "대상", "설명", "상세", "권고"]
# 판정불가로 일찍 끝나도 **문서화된 열 이름 그대로** 빈 CSV 를 남긴다 —
# 열이 달라지면 다음 단계(citecheck·엑셀 필터)가 조용히 깨진다.
CHANGE_HEADER = [
    "제출본문단", "개정본문단", "줄범위", "절", "유형", "숫자변경", "신고여부",
    "신고근거", "제출본텍스트", "개정본텍스트",
]

_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")
_PLAIN_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")

_TAIL_NOTE = (
    "※ 개정본 자체의 숫자·인용↔참고문헌·그림표 번호는 검사하지 않았습니다. "
    "numcheck 와 draftcheck 를 따로 돌리세요. 새로 추가된 문헌의 DOI 는 "
    "추가문헌.csv 를 citecheck 에 넣으면 확인됩니다."
)


def csv_safe(value) -> str:
    """엑셀/구글시트가 셀을 **수식으로 실행**하지 못하게 만든다.

    ``=HYPERLINK(...)`` ``@SUM`` ``+cmd`` ``-cmd`` 로 시작하는 셀은 스프레드시트가
    수식으로 해석한다. 원고 문장이 그대로 셀에 들어가므로 반드시 막는다.
    순수한 숫자(``-3``)는 수식이 될 수 없으므로 그대로 둔다.
    """
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ")
    text = strip_control(text).replace("\t", " ")
    if text[:1] in _FORMULA_LEAD and not _PLAIN_NUMBER.fullmatch(text.strip()):
        text = "'" + text
    return text


def _severity_counts(result: Result) -> str:
    return (
        f"치명 {len(result.criticals)}건 / 경고 {len(result.warnings)}건 / "
        f"정보 {len(result.by_severity(INFO))}건"
    )


def _sorted_findings(result: Result):
    """등급 → 자리(요약은 뒤) 순. 그 안에서는 검사 순서를 유지한다."""
    return sorted(
        result.findings, key=lambda f: (SEVERITY_ORDER.index(f.severity), f.order)
    )


def render_text(result: Result, exit_code: int, out_dir: Path = None) -> str:
    lines: List[str] = ["revcheck — 리비전 응답 점검"]
    lines.extend(result.header_lines)
    for note in result.notes:
        lines.append(f"      · {note}")
    lines.append("")

    if result.undecidable:
        lines.append("[판정불가]")
        lines.append(f"  {result.undecidable}")
        lines.append("")
        lines.append(_coverage_block(result))
        lines.append("")
        lines.append("종료코드 3 (판정불가) — 아무것도 '이상 없음'으로 표시하지 않았습니다.")
        return "\n".join(lines)

    number = 1
    for severity in (CRITICAL, WARNING):
        group = [f for f in _sorted_findings(result) if f.severity == severity]
        lines.append(f"[{severity} {len(group)}건]")
        if not group:
            lines.append("  없음")
        for finding in group:
            lines.append(f"{number}. {finding.message}")
            for detail in finding.detail:
                lines.append(f"   {detail}")
            if finding.advice:
                lines.append(f"   → {finding.advice}")
            number += 1
        lines.append("")

    infos = [f for f in result.findings if f.severity == INFO]
    lines.append("[정보]")
    for line in result.info_lines:
        lines.append(f"- {line}")
    for finding in infos:
        lines.append(f"- [{finding.target}] {finding.message}")
        for detail in finding.detail:
            lines.append(f"    {detail}")
    if not result.info_lines and not infos:
        lines.append("- (없음)")
    lines.append("")

    lines.append(_coverage_block(result))
    lines.append("")
    lines.append(_TAIL_NOTE)
    lines.append("")
    label = {0: "정상", 1: "치명 있음", 2: "경고 있음", 3: "판정불가"}[exit_code]
    lines.append(f"종료코드 {exit_code} ({label})")
    if out_dir is not None:
        lines.append(
            f"{out_dir}/{REPORT_MD}, {ISSUES_CSV}, {CHANGES_CSV}, {ADDED_REFS_CSV} 저장"
        )
    return "\n".join(strip_control(line) for line in lines)


def _coverage_block(result: Result) -> str:
    lines = ["[커버리지 자백]"]
    if not result.coverage:  # pragma: no cover - 방어: 여기에 오면 버그다
        lines.append("- (커버리지를 계산하지 못했습니다 — 결과를 신뢰하지 마세요)")
    for item in result.coverage:
        lines.append(item.render())
    return "\n".join(lines)


def render_markdown(result: Result, exit_code: int, generated: str = "") -> str:
    lines = ["# revcheck — 리비전 응답 점검", ""]
    for line in result.header_lines:
        lines.append(f"- {line}")
    for note in result.notes:
        lines.append(f"- {note}")
    if generated:
        lines.append(f"- 생성: {generated}")
    lines.append("")
    if result.undecidable:
        lines.append("## 판정불가")
        lines.append("")
        lines.append(result.undecidable)
        lines.append("")
        lines.append("## 커버리지 자백")
        lines.append("")
        for item in result.coverage:
            lines.append(item.render())
        return "\n".join(lines) + "\n"

    lines.append(f"**{_severity_counts(result)} — 종료코드 {exit_code}**")
    lines.append("")
    for severity in SEVERITY_ORDER:
        group = [f for f in _sorted_findings(result) if f.severity == severity]
        if severity == INFO and not group and not result.info_lines:
            continue
        lines.append(f"## {severity}")
        lines.append("")
        if severity == INFO:
            for line in result.info_lines:
                lines.append(f"- {line}")
        if not group and severity != INFO:
            lines.append("- 없음")
        for finding in group:
            lines.append(f"- **[{finding.kind}] {finding.target}** — {finding.message}")
            for detail in finding.detail:
                lines.append(f"  - {detail}")
            if finding.advice:
                lines.append(f"  - 권고: {finding.advice}")
        lines.append("")
    lines.append("## 커버리지 자백")
    lines.append("")
    for item in result.coverage:
        lines.append(item.render())
    lines.append("")
    lines.append(_TAIL_NOTE)
    lines.append("")
    return "\n".join(strip_control(line) for line in lines)


def issue_rows(result: Result) -> List[List[str]]:
    rows = [list(ISSUE_HEADER)]
    for finding in _sorted_findings(result):
        rows.append([
            csv_safe(finding.severity),
            csv_safe(finding.kind),
            csv_safe(finding.target),
            csv_safe(finding.message),
            csv_safe(" / ".join(finding.detail)),
            csv_safe(finding.advice),
        ])
    return rows


def _write_csv(path: Path, rows: Sequence[Sequence[str]]) -> None:
    safe = [[csv_safe(cell) for cell in row] for row in rows]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(safe)


class OutputError(Exception):
    """산출 폴더에 쓸 수 없을 때."""


def _same_file(path: Path, source_ids) -> bool:
    """이미 있는 파일이 입력 원고와 같은 실체(inode)인가."""
    try:
        stat = path.stat()
    except OSError:
        return False
    return (stat.st_dev, stat.st_ino) in source_ids


def _reject_symlinked_dir(target: Path) -> None:
    """결과 폴더가 **그 자체로** 심볼릭 링크면 거부한다.

    링크를 따라가면 사용자가 보고 있는 곳이 아닌 데에 미공개 원고 문장이 적힌다.
    상위 경로의 링크(macOS 의 ``/tmp`` → ``/private/tmp`` 같은 시스템 링크)까지
    막으면 정상적인 첫 실행이 거절되므로, 사용자가 지정한 마지막 요소만 본다.
    """
    if target.is_symlink():
        raise OutputError(
            f"결과 폴더가 심볼릭 링크입니다: {target} — 실제 폴더 경로를 지정하세요."
        )


def write_outputs(
    result: Result, out_dir, exit_code: int, sources: Sequence[Path] = (), generated: str = ""
) -> List[Path]:
    """리포트와 CSV 세 개를 저장한다. 입력 파일은 절대 건드리지 않는다."""
    target = Path(out_dir).expanduser()
    _reject_symlinked_dir(target)
    try:
        target.mkdir(parents=True, exist_ok=True)
        resolved = target.resolve()
    except OSError as exc:
        raise OutputError(f"결과 폴더를 만들 수 없습니다: {target} ({exc.strerror})") from exc
    if not resolved.is_dir():  # pragma: no cover - mkdir 이 성공했으면 폴더다
        raise OutputError(f"결과 폴더가 아닙니다: {resolved}")

    # 입력 원고와 **같은 파일인지**는 경로 문자열이 아니라 inode 로 본다.
    # macOS 는 자모 분리(NFD) 파일명을 쓰고, 하드링크도 있다 — 문자열 비교만
    # 하면 미공개 원고를 리포트로 덮어쓰는 사고가 난다.
    source_ids = set()
    for src in sources:
        try:
            stat = Path(src).stat()
            source_ids.add((stat.st_dev, stat.st_ino))
        except OSError:  # pragma: no cover - 이미 읽은 파일이다
            continue

    written: List[Path] = []
    payloads = [
        (REPORT_MD, render_markdown(result, exit_code, generated), None),
        (ISSUES_CSV, None, issue_rows(result)),
        (CHANGES_CSV, None, result.change_rows or [CHANGE_HEADER]),
        (ADDED_REFS_CSV, None, result.added_ref_rows or [list(CITECHECK_HEADER)]),
    ]
    for name, text, rows in payloads:
        path = resolved / name
        if path.is_symlink():
            raise OutputError(
                f"출력 위치에 심볼릭 링크가 있습니다: {path} — 링크를 지우거나 "
                "--out-dir 을 다른 폴더로 지정하세요."
            )
        if _same_file(path, source_ids):
            raise OutputError(
                f"출력 파일이 입력 원고와 같습니다({name}) — "
                "원고를 덮어쓰지 않도록 --out-dir 을 다른 폴더로 지정하세요."
            )
        try:
            if text is not None:
                path.write_text(text, encoding="utf-8")
            else:
                _write_csv(path, rows)
        except OSError as exc:
            leftover = (
                f" 이미 쓴 {len(written)}개 파일과 옛 파일이 섞여 있으니 폴더를 비우고 "
                "다시 실행하세요."
                if written
                else ""
            )
            raise OutputError(
                f"결과 파일을 쓸 수 없습니다: {path} ({exc.strerror})." + leftover
            ) from exc
        written.append(path)
    return written

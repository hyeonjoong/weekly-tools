"""점검 결과를 사람이 읽는 콘솔 요약 / 마크다운 / CSV 로 바꾼다.

출력은 항상 ``--out-dir`` 안에만 만들어지고, 파일 이름은 이 모듈에 하드코딩된
세 개뿐이다(원고 내용이 파일 경로에 관여하지 않으므로 경로 순회가 불가능하다).
CSV 셀은 Excel 수식 인젝션을 막기 위해 이스케이프한다.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path
from typing import List, Optional, Sequence

from .checks import CRITICAL, INFO, WARNING, Finding, Result
from .docio import ManuscriptError

REPORT_MD = "점검결과.md"
ISSUES_CSV = "문제목록.csv"
REFERENCES_CSV = "references.csv"

_SEVERITY_ORDER = (CRITICAL, WARNING, INFO)
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")
_PLAIN_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


# 원고에서 뽑은 글자를 터미널에 그대로 뱉으면, 원고 안의 ESC 시퀀스가 화면을 지우거나
# 터미널 제목을 바꿀 수 있다(악의적 .docx뿐 아니라 변환 사고로도 섞여 들어온다).
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏  ‪-‮]")


def sanitize(text: str) -> str:
    """출력에 들어갈 원고 유래 문자열에서 제어문자를 지운다(탭은 공백으로)."""
    return _CONTROL_CHARS.sub("", (text or "").replace("\t", " "))


def csv_safe(value) -> str:
    """엑셀/구글시트가 셀을 **수식으로 실행**하지 못하게 만든다.

    ``=HYPERLINK(...)``, ``@SUM``, ``+cmd``, ``-cmd`` 로 시작하는 셀은 스프레드시트가
    수식으로 해석한다. 원고에서 뽑은 문장이 그대로 셀에 들어가므로 반드시 막아야 한다.
    순수한 숫자(``-3``)는 수식이 될 수 없으므로 그대로 둔다.
    """
    text = "" if value is None else str(value)
    # 탭/개행이 셀을 깨뜨리므로 먼저 공백으로 바꾼다. 그다음 선행 문자를 판정해야
    # "\t=1+1" 처럼 제어문자로 가린 수식이 통과하지 못한다.
    text = text.replace("\r", " ").replace("\n", " ")
    text = sanitize(text).strip(" ") if text.strip(" ") else text
    if text[:1] in _FORMULA_LEAD and not _PLAIN_NUMBER.fullmatch(text):
        text = "'" + text
    return text


def display_width(text: str) -> int:
    """터미널이 실제로 차지하는 칸 수. 한글·CJK는 두 칸이다."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def _line_ref(result: Result, finding: Finding) -> str:
    if finding.line is None:
        return "—"
    return f"L{finding.line}"


def _real(result: Result, severity: str) -> int:
    """'점검 불가'를 뺀 실제 원고 결함 수 (불가는 따로 센다)."""
    return len([f for f in result.findings if f.severity == severity and f.kind != "점검불가"])


def summary_line(result: Result) -> str:
    text = (
        f"치명 {_real(result, CRITICAL)} · 경고 {_real(result, WARNING)} · "
        f"정보 {_real(result, INFO)}"
    )
    if result.blockers:
        text += f"  ※ 점검 불가 {len(result.blockers)}건 (이상 없음이 아닙니다)"
    return text


# ── 콘솔 ─────────────────────────────────────────────────────────────────────


def console_report(result: Result, width: int = 78) -> str:
    ms = result.ms
    out: List[str] = []
    counts = result.counts
    style_label = {
        "numeric": "numeric(번호형)",
        "author-year": "author-year(저자-연도)",
        "cite-key": r"cite-key(\cite/\bibitem)",
        "판별불가": "판별 불가",
    }.get(result.style, result.style)
    out.append("")
    out.append(f"draftcheck — {ms.path.name}  ({ms.fmt}, {ms.line_label} 번호 기준)")
    out.append(
        f"본문 {counts.get('body_words', 0):,}단어 / 초록 {counts.get('abstract_words', 0):,}단어"
        f" / 인용 스타일: {style_label} ({result.style_source})"
    )
    out.append("-" * width)

    if result.blockers:
        out.append("")
        out.append("┏" + "━" * (width - 2) + "┓")
        out.append(_box_line("★ 점검 불가 — 아래 항목은 '이상 없음'이 아닙니다", width))
        for reason in result.blockers:
            for chunk in _wrap(sanitize(reason), width - 6):
                out.append(_box_line("  " + chunk.strip(), width))
        out.append(_box_line("  → 해당 부분은 반드시 눈으로 확인하세요.", width))
        out.append("┗" + "━" * (width - 2) + "┛")

    for severity in _SEVERITY_ORDER:
        # '점검 불가'는 위의 상자에서 이미 크게 보여 줬다. 여기서 또 세면
        # 원고의 결함이 아닌 것이 '치명 1건'으로 잡혀 숫자가 부풀려진다.
        items = [f for f in result.findings if f.severity == severity and f.kind != "점검불가"]
        if not items:
            continue
        out.append("")
        out.append(f"■ {severity} {len(items)}건")
        for finding in items:
            head = f"  {_line_ref(result, finding):>6}  {sanitize(finding.message)}"
            out.extend(_wrap(head, width, subsequent="        "))
            if finding.advice and severity != INFO:
                out.extend(_wrap("          → " + sanitize(finding.advice), width))

    if result.limit_rows:
        out.append("")
        title = f"■ 분량 ({result.limits_name} 기준)" if result.limits_name else "■ 분량"
        out.append(title)
        for label, actual, limit, ok in result.limit_rows:
            if limit is None:
                out.append(f"  {label:<14} {actual:>7,}       (한도 미지정)")
            else:
                mark = "✓" if ok else f"✗ {actual - limit:,} 초과"
                out.append(f"  {label:<14} {actual:>7,} / 한도 {limit:<7,} {mark}")

    if result.coverage:
        out.append("")
        out.append("■ 이 점검이 실제로 본 것 (자기 보고)")
        for note in result.coverage:
            out.extend(_wrap("  · " + sanitize(note), width, subsequent="  "))
    if ms.notes:
        out.append("")
        out.append("■ 파일 읽기 메모")
        for note in ms.notes:
            out.extend(_wrap("  · " + sanitize(note), width, subsequent="  "))

    out.append("")
    out.append("-" * width)
    out.append(summary_line(result))
    out.append("")
    return "\n".join(out)


def _box_line(text: str, width: int) -> str:
    """상자 한 줄. 한글은 두 칸을 차지하므로 **표시 폭**으로 채워야 선이 맞는다."""
    inner = width - 2
    while display_width(text) > inner and text:
        text = text[:-1]
    return "┃" + _pad(text, inner) + "┃"


def _wrap(text: str, width: int, subsequent: str = "  ") -> List[str]:
    """한글이 섞인 문자열을 표시 폭 기준으로 접는다.

    들여쓰기를 보존한다. 예전 구현은 첫 조각의 공백을 잘라 버려서, 애써 맞춘
    줄번호 정렬(``  L106  …``)이 화면에서는 전부 왼쪽에 붙어 나왔다.
    """
    indent = text[: len(text) - len(text.lstrip(" "))]
    words = text.strip(" ").split(" ")
    if not words or words == [""]:
        return [text.rstrip()]
    lines: List[str] = []
    current = indent + words[0]
    for word in words[1:]:
        candidate = current + " " + word
        if display_width(candidate) > width:
            lines.append(current.rstrip())
            current = indent + subsequent + word
        else:
            current = candidate
    lines.append(current.rstrip())
    return lines


# ── 마크다운 ─────────────────────────────────────────────────────────────────


def markdown_report(result: Result, generated: str = "") -> str:
    ms = result.ms
    counts = result.counts
    lines: List[str] = []
    lines.append(f"# 투고 전 정합성 점검 — {ms.path.name}")
    lines.append("")
    lines.append(
        f"- 원고 형식: `{ms.fmt}` (줄번호는 **{ms.line_label} 번호**)"
    )
    lines.append(f"- 인용 스타일: **{result.style}** ({result.style_source})")
    lines.append(
        f"- 본문 {counts.get('body_words', 0):,}단어 · 초록 {counts.get('abstract_words', 0):,}단어 · "
        f"참고문헌 {counts.get('ref_entries', 0)}개 · "
        f"그림 {counts.get('n_figures', 0)}·표 {counts.get('n_tables', 0)}"
    )
    lines.append(f"- 결과: **{summary_line(result)}**")
    if generated:
        lines.append(f"- 생성: {generated}")
    lines.append("")
    lines.append(
        "> 이 리포트는 **문서가 자기 안에서 앞뒤가 맞는지**만 기계적으로 대조한 결과입니다. "
        "내용의 과학적 타당성·영문 표현·문헌의 실존 여부는 점검하지 않습니다."
    )
    lines.append("")

    if result.blockers:
        lines.append("## ★ 점검 불가 (이상 없음이 아닙니다)")
        lines.append("")
        for reason in result.blockers:
            lines.append(f"- {reason}")
        lines.append("")
        lines.append("이 항목들은 **눈으로 확인해야 합니다.**")
        lines.append("")

    for severity in _SEVERITY_ORDER:
        items = [f for f in result.findings if f.severity == severity]
        if not items:
            continue
        lines.append(f"## {severity} {len(items)}건")
        lines.append("")
        lines.append(f"| {ms.line_label} | 유형 | 대상 | 내용 | 권고 |")
        lines.append("|---|---|---|---|---|")
        for f in items:
            lines.append(
                "| {} | {} | {} | {} | {} |".format(
                    _line_ref(result, f),
                    f.kind,
                    _md_cell(f.target),
                    _md_cell(f.message),
                    _md_cell(f.advice),
                )
            )
        lines.append("")

    if result.limit_rows:
        lines.append("## 분량" + (f" ({result.limits_name} 기준)" if result.limits_name else ""))
        lines.append("")
        lines.append("| 항목 | 실제 | 한도 | 판정 |")
        lines.append("|---|---:|---:|---|")
        for label, actual, limit, ok in result.limit_rows:
            limit_text = f"{limit:,}" if limit is not None else "—"
            verdict = "—" if limit is None else ("✓" if ok else f"✗ {actual - limit:,} 초과")
            lines.append(f"| {label} | {actual:,} | {limit_text} | {verdict} |")
        lines.append("")

    if result.coverage or ms.notes:
        lines.append("## 이 점검이 실제로 본 것")
        lines.append("")
        for note in list(result.coverage) + list(ms.notes):
            lines.append(f"- {note}")
        lines.append("")

    lines.append("## 다음 단계")
    lines.append("")
    lines.append(
        "- 이 폴더의 `references.csv` 는 **citecheck-인용DOI검증** 의 입력 형식과 같습니다. "
        "문헌이 실제로 존재하고 철회되지 않았는지까지 확인하려면:"
    )
    lines.append("")
    lines.append("  ```bash")
    lines.append("  citecheck references.csv        # 네트워크 필요 (Crossref/PubMed)")
    lines.append("  ```")
    lines.append("")
    return "\n".join(lines)


def _md_cell(text: str) -> str:
    """표 셀 안의 ``|`` 는 반드시 escape 해야 표가 깨지지 않는다."""
    return sanitize(text).replace("|", "\\|").replace("\n", " ")


# ── CSV ──────────────────────────────────────────────────────────────────────

ISSUE_HEADER = ["줄번호", "심각도", "유형", "대상", "설명", "권고"]
REF_HEADER = [
    "Study ID", "Authors", "Year", "Title", "Journal", "Article DOI", "PMID", "parse_ok",
]


def issue_rows(result: Result) -> List[List[str]]:
    rows = [ISSUE_HEADER]
    for f in result.findings:
        rows.append(
            [
                csv_safe(f.line if f.line is not None else ""),
                csv_safe(f.severity),
                csv_safe(f.kind),
                csv_safe(f.target),
                csv_safe(f.message),
                csv_safe(f.advice),
            ]
        )
    return rows


def reference_rows(result: Result) -> List[List[str]]:
    """citecheck 입력 스키마와 **동일한 열 이름**으로 참고문헌을 내보낸다."""
    rows = [REF_HEADER]
    for i, ref in enumerate(result.refs, start=1):
        study_id = str(ref.number) if ref.number else (ref.key or f"R{i}")
        rows.append(
            [
                csv_safe(study_id),
                csv_safe(ref.authors),
                csv_safe(ref.year),
                csv_safe(ref.title),
                csv_safe(ref.journal),
                csv_safe(ref.doi),
                csv_safe(ref.pmid),
                "yes" if ref.parse_ok else "no",
            ]
        )
    return rows


def _write_csv(path: Path, rows: Sequence[Sequence[str]]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(rows)


def write_outputs(result: Result, out_dir, generated: str = "") -> List[Path]:
    """세 개의 산출물을 ``out_dir`` **안에만** 쓴다. 원본 원고는 건드리지 않는다.

    파일 이름은 상수 3개뿐이라 원고 내용이 경로에 관여할 수 없다. 남는 구멍은 둘뿐이고,
    둘 다 여기서 막는다.

    1. 원고 파일 이름이 하필 ``점검결과.md`` 라면 산출물이 원고를 덮어쓴다(자료 손실).
    2. 출력 위치에 심볼릭 링크가 미리 놓여 있으면 ``open(...,"w")`` 가 링크를 따라가
       ``out_dir`` 바깥에 쓴다.
    """
    directory = Path(out_dir).expanduser()
    if directory.exists() and not directory.is_dir():
        raise NotADirectoryError(f"출력 경로가 폴더가 아닙니다: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    resolved = directory.resolve()

    md_path = resolved / REPORT_MD
    issues_path = resolved / ISSUES_CSV
    refs_path = resolved / REFERENCES_CSV
    try:
        source = Path(result.ms.path).resolve()
    except OSError:  # pragma: no cover - 원고는 이미 읽은 뒤다
        source = None
    for path in (md_path, issues_path, refs_path):
        if path.is_symlink():
            raise ManuscriptError(
                f"출력 위치에 심볼릭 링크가 있습니다: {path} — 링크를 지우거나 "
                "--out-dir 를 다른 폴더로 지정하세요."
            )
        if source is not None and path == source:
            raise ManuscriptError(
                f"출력 파일이 원고 파일과 같습니다({path.name}) — "
                "원고를 덮어쓰지 않도록 --out-dir 를 다른 폴더로 지정하세요."
            )

    written: List[Path] = []
    md_path.write_text(markdown_report(result, generated), encoding="utf-8")
    written.append(md_path)
    _write_csv(issues_path, issue_rows(result))
    written.append(issues_path)
    _write_csv(refs_path, reference_rows(result))
    written.append(refs_path)
    return written

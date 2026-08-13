"""콘솔 리포트와 CSV 출력.

**커버리지 자백이 리포트의 첫 문단**이다. 몇 개를 봤고 몇 개를 못 봤으며 왜
못 봤는지를 먼저 말하지 않는 체커는, "이상 없음"이라는 말로 사용자를 속이게
된다. 그래서 이 순서는 협상 대상이 아니다.

CSV 는 Excel 수식 인젝션을 막는다. `=`, `+`, `-`, `@` 로 시작하는 셀 앞에 `'`
를 붙이되, **숫자로 읽히는 값(-7.4 등)은 그대로 둔다** — 그건 수식이 아니고,
전부 따옴표를 붙이면 표를 쓸 수 없게 된다.
"""

from __future__ import annotations

import csv
import io
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .model import LEVELS, Finding, Report

__all__ = ["render_console", "write_csvs", "one_line", "csv_safe",
           "check_targets", "OutputRefused", "OUTPUT_FILES"]

OUTPUT_FILES = ("문제목록.csv", "재계산표.csv", "요약.txt")

MAX_SHOWN_PER_LEVEL = 25

_LEVEL_EN = {"치명": "CRITICAL", "경고": "WARNING", "정보": "INFO"}

_ITEM_EN = {
    "비율": "proportion",
    "비율 재계산": "proportion recomputation",
    "비율 재계산(추정 분모)": "proportion (inferred denominator)",
    "p 재계산": "p-value recomputation",
    "N 합계": "N subtotal",
    "표 열 N 합계": "table column N subtotal",
    "변화량 일치": "change score",
    "신뢰구간 정합": "confidence interval",
    "CI–p 정합": "CI vs p consistency",
    "유의성 문구": "significance wording",
}

_SKIP_EN = {
    "분모 없음": "no denominator",
    "척도 미상": "scale unknown",
    "표기 불명확": "ambiguous notation",
    "참고문헌 인용": "reference list",
    "검정통계량 없음": "no test statistic",
    "p 미보고": "no p reported",
    "자유도 없음": "no degrees of freedom",
    "표본수 없음": "no sample size",
    "판별력 없음": "no discriminating power",
    "값이 범위 밖": "value out of range",
    "기타": "other",
}

_HEADERS_KO = {
    "issues": ["줄번호", "절", "등급", "항목", "원문", "보고값", "재계산값", "판정", "설명"],
    "audit": ["줄번호", "절", "항목", "원문", "처리", "사유", "보고값", "재계산값",
              "판정", "비고"],
}
_HEADERS_EN = {
    "issues": ["line", "section", "level", "item", "quote", "reported", "recomputed",
               "verdict", "message"],
    "audit": ["line", "section", "item", "quote", "handling", "reason", "reported",
              "recomputed", "verdict", "note"],
}


def _item(name: str, lang: str) -> str:
    if lang != "en":
        return name
    if name in _ITEM_EN:
        return _ITEM_EN[name]
    if name.startswith("GRIMMER"):
        return "GRIMMER" + name[len("GRIMMER"):]
    if name.startswith("GRIM"):
        return "GRIM" + name[len("GRIM"):]
    return name


def _skip(name: str, lang: str) -> str:
    return _SKIP_EN.get(name, name) if lang == "en" else name


def _msg(finding: Finding, lang: str) -> str:
    if lang == "en" and finding.message_en:
        return finding.message_en
    return finding.message


# 강등 사유는 정형화된 짧은 문구다. 영문 리포트에서 한국어가 그대로 새어 나오면
# "강등 사유를 반드시 함께 출력한다"는 약속이 반쪽이 된다.
_DOWNGRADE_EN = (
    ("단측검정 가능성(단측)", "possible one-tailed test (keyword found)"),
    ("단측검정 가능성", "possible one-tailed test"),
    ("단측으로 계산하면 값이 맞음", "matches when computed one-tailed"),
    ("보정 단서", "correction keyword"),
    ("가 같은 문장에 있음", "present in the same sentence"),
    ("' present", "' is present"),
    ("차이가 근소하고 유의성 판정이 바뀌지 않음",
     "difference is small and the significance decision does not flip"),
    ("분모를 문맥에서 추정했으므로 경고",
     "denominator was inferred from context, so this is a warning"),
    ("검정·구간추정 방법 차이로 정당할 수 있어 경고",
     "test and interval methods can legitimately differ, so this is a warning"),
    ("SD 정의·중도절단 등 예외가 있을 수 있어 경고",
     "SD definition/censoring exceptions are possible, so this is a warning"),
)


def _downgrade(text: str, lang: str) -> str:
    if lang != "en" or not text:
        return text
    for ko, en in _DOWNGRADE_EN:
        text = text.replace(ko, en)
    return " ".join(text.split())


# 원고 읽기 메모는 정형화된 몇 가지뿐이므로 통째로 번역한다.
_NOTE_EN = (
    ("문장 도중에 줄바꿈된", "re-joined "),
    ("줄을 앞줄에 이어 붙여 읽었습니다 (줄번호는 그 문장이 시작하는 줄).",
     " hard-wrapped line(s) into the sentence that starts them "
     "(line numbers point at that sentence)."),
    ("추적 변경의 삭제 표시", "excluded "),
    ("곳을 제외했습니다(최종본 기준).", " tracked-change deletion(s) (final version only)."),
    ("표 ", "read "),
    ("행을 셀까지 읽어 한 줄로 이어 붙였습니다.", " table row(s), joining cells with '|'."),
    ("자를 넘는 줄", " chars: over-long line(s) truncated,"),
    ("개의 뒷부분을 잘랐습니다 — 그 뒤의 숫자는 **검사되지 않았고 건너뜀 집계에도 없습니다.**",
     " of them — numbers after the cut were NOT checked and are NOT in the skip counts."),
    ("개의 뒷부분을 잘랐습니다 — 그 뒤의 숫자는 **검사되지 않았습니다.**",
     " of them — numbers after the cut were NOT checked."),
    ("인코딩으로 읽었습니다.", "encoding was used."),
    ("각주 ", "footnote: "),
    ("미주 ", "endnote: "),
    ("개 문단도 함께 읽었습니다.", " paragraph(s) were also read."),
)


def _note(text: str, lang: str) -> str:
    if lang != "en":
        return text
    for ko, en in _NOTE_EN:
        text = text.replace(ko, en)
    return " ".join(text.split())


# ── 콘솔 ─────────────────────────────────────────────────────────────────────


def render_console(report: Report, lang: str = "ko",
                   out_dir: Optional[Path] = None,
                   min_checked: int = 5) -> str:
    ko = lang != "en"
    out: List[str] = []
    add = out.append
    name = Path(report.path).name

    add("numcheck — 원고 수치 재계산 검증" if ko
        else "numcheck — manuscript arithmetic verification")
    unit = ("문단" if report.line_label == "문단" else "줄") if ko else \
           ("paragraphs" if report.line_label == "문단" else "lines")
    if ko:
        add(f"입력: {name} ({report.fmt}, 본문 {report.word_count:,} 단어, "
            f"표 {report.table_rows}행) — 줄번호는 {unit} 번호입니다")
    else:
        add(f"input: {name} ({report.fmt}, {report.word_count:,} body words, "
            f"{report.table_rows} table rows) — line numbers are {unit}")
    add("")

    # -- 커버리지 자백 (항상 맨 위) ----------------------------------------
    if ko:
        add(f"검사 후보 {report.n_candidates}개 · 재계산 {report.n_checked}개 · "
            f"건너뜀 {report.n_skipped}개")
    else:
        add(f"candidates {report.n_candidates} · recomputed {report.n_checked} · "
            f"skipped {report.n_skipped}")
    breakdown = report.skip_breakdown()
    if breakdown:
        body = " · ".join(f"{_skip(reason, lang)} {count}" for reason, count in breakdown)
        add(("  건너뜀 사유: " if ko else "  skipped because: ") + body)
    add("  ※ 한 숫자가 두 가지 방식으로 검사될 수 있어 후보 수는 숫자 개수와 다릅니다."
        if ko else
        "  note: one number can be checked in more than one way, so candidates ≠ numbers.")

    if report.notes:
        add("")
        add("[원고 읽기 메모]" if ko else "[reading notes]")
        for note in report.notes:
            add(f"  · {_note(note, lang)}")

    if report.truncated and report.n_checked >= min_checked:
        add("")
        add("!! " + (
            "원고의 일부를 잘라 냈습니다(위 메모 참조). 잘린 뒷부분에 오류가 있었는지는 "
            "알 수 없으므로 이 실행은 '이상 없음'을 주장하지 않습니다 — 종료코드 3."
            if ko else
            "part of the manuscript was truncated (see the notes above). This run cannot "
            "claim 'all clear' for the part it did not read — exit code 3."))

    if report.n_checked < min_checked:
        add("")
        add("!! " + (
            f"재계산할 수 있는 claim 이 {report.n_checked}개뿐입니다. 이 원고를 제대로 "
            "파싱하지 못했을 가능성이 큽니다 — '이상 없음'으로 읽지 마세요."
            if ko else
            f"only {report.n_checked} claims could be recomputed. The manuscript was "
            "probably not parsed properly — do NOT read this as 'all clear'."))

    counts = report.counts()
    findings = report.sorted_findings()
    for level in LEVELS:
        group = [f for f in findings if f.level == level]
        if not group:
            continue
        title = level if ko else _LEVEL_EN[level]
        add("")
        add(f"■ {title} {counts[level]}건" if ko else f"■ {title}: {counts[level]}")
        for finding in group[:MAX_SHOWN_PER_LEVEL]:
            add(_render_finding(finding, lang))
        hidden = len(group) - MAX_SHOWN_PER_LEVEL
        if hidden > 0:
            add(f"... ({hidden}건 생략, 문제목록.csv 참조)" if ko
                else f"... ({hidden} more, see 문제목록.csv)")

    if not findings:
        add("")
        add("문제를 찾지 못했습니다." if ko else "No issues found.")

    add("")
    if out_dir is not None:
        files = ", ".join(str(Path(out_dir) / f) for f in OUTPUT_FILES)
        add(("출력: " if ko else "written: ") + files)
    code = report.exit_code(min_checked)
    label = {
        0: "이상 없음" if ko else "clean",
        1: "치명 있음" if ko else "critical findings",
        2: "경고만 있음" if ko else "warnings only",
        3: _exit3_label(report, min_checked, ko),
    }[code]
    add((f"종료코드 {code} ({label})" if ko else f"exit code {code} ({label})"))
    return "\n".join(out)


def _exit3_label(report, min_checked: int, ko: bool) -> str:
    """종료코드 3 의 사유를 정확히 말한다 — 원인이 둘이므로 뭉뚱그리지 않는다."""
    if report.n_checked < min_checked:
        return ("입력 처리 불가(재계산 가능 claim 부족)" if ko
                else "input not usable (too few recomputable claims)")
    return ("원고 일부를 읽지 못함(잘림)" if ko
            else "part of the manuscript was truncated")


def _render_finding(f: Finding, lang: str) -> str:
    head = f"[L{f.line_no:<5d}{f.section}]"
    quote = f.quote or ("(원문 생략)" if lang != "en" else "(quote suppressed)")
    lines = [f"{head:<24s}{quote}"]
    lines.append(f"      → {_msg(f, lang)}")
    if f.downgraded:
        lines.append(f"        ↓ 등급 강등: {f.downgraded}" if lang != "en"
                     else f"        ↓ downgraded: {_downgrade(f.downgraded, lang)}")
    return "\n".join(lines)


# ── CSV ──────────────────────────────────────────────────────────────────────

_DANGEROUS = ("=", "+", "-", "@")


def _strip_invisible(text: str) -> str:
    """앞쪽의 공백류·제어문자·서식문자를 전부 벗긴다.

    Excel/Sheets 는 CSV 를 읽을 때 이것들을 버리므로 `" =1+1"` 은 여전히 수식이
    된다. **손으로 고른 목록을 쓰면 안 된다** — U+2009 는 막고 U+2000 은 통과하는
    식의 구멍이 생긴다. 유니코드 카테고리로 판정한다(Zs 공백, Cc 제어, Cf 서식).
    """
    index = 0
    while index < len(text) and (
        text[index].isspace() or unicodedata.category(text[index]) in ("Zs", "Cc", "Cf")
    ):
        index += 1
    return text[index:]


def csv_safe(value) -> str:
    """Excel/Sheets 수식 인젝션 방어. 숫자로 읽히는 값은 그대로 둔다.

    `-7.4` 는 수식이 아니라 숫자다. 전부 따옴표를 붙이면 표를 쓸 수 없게 되므로
    float 로 읽히는 값은 건드리지 않는다.
    """
    text = "" if value is None else str(value)
    for ch in "\r\n\v\f\u0085\u2028\u2029":
        text = text.replace(ch, " ")
    head = _strip_invisible(text)
    if head[:1] in _DANGEROUS:
        try:
            float(head)
        except ValueError:
            return "'" + text
    return text


class OutputRefused(Exception):
    """남의 파일을 덮어쓸 위험이 있어 쓰기를 거부했을 때."""


# 우리가 만든 파일인지 알아보기 위한 표식 — **첫 줄 전체와 정확히 일치**해야 한다.
# 접두사만 보면 안 된다: `문제목록.csv` 는 draftcheck 의 지적 목록과 같은 이름·비슷한
# 머리글을 쓰도록 일부러 맞춰 놓았으므로, 접두사 매칭이면 draftcheck 의 결과를
# "우리 것"으로 오인해 지워 버린다. 요약.txt 는 첫 줄이 배너와 정확히 같아야 한다.
_SIGNATURES = {
    OUTPUT_FILES[0]: (
        ",".join(_HEADERS_KO["issues"]),
        ",".join(_HEADERS_EN["issues"]),
    ),
    OUTPUT_FILES[1]: (
        ",".join(_HEADERS_KO["audit"]),
        ",".join(_HEADERS_EN["audit"]),
    ),
    OUTPUT_FILES[2]: (
        "numcheck — 원고 수치 재계산 검증",
        "numcheck — manuscript arithmetic verification",
    ),
}


def _looks_like_ours(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8-sig", errors="replace")[:400]
    except (OSError, UnicodeError):
        return False
    first = head.splitlines()[0].strip() if head.strip() else ""
    return first in _SIGNATURES.get(path.name, ())


def check_targets(out_dir, force: bool = False, manuscript=None) -> None:
    """세 출력 파일을 안전하게 쓸 수 있는지 **쓰기 전에** 확인한다.

    두 가지를 막는다.
      · 남의 파일 덮어쓰기 — `--out-dir .` 처럼 원고 폴더를 그대로 주면, 거기
        있던 `요약.txt` 가 소리 없이 사라진다. 이전 numcheck 산출물이면 그냥
        덮어쓰고, 아니면 `--force` 를 요구한다.
      · 심볼릭 링크 — `write_text` 는 링크를 따라가므로, out-dir 안의
        `요약.txt → /어딘가/중요파일` 하나로 **--out-dir 바깥**을 쓰게 된다.
      · **원고 자체를 덮어쓰기** — 이건 이름이 아니라 파일 동일성으로 막는다.
        macOS 는 한글 파일명을 NFD 로 저장하므로 `요약.txt` 를 이름으로 비교하면
        `\uc694\uc57d.txt` ≠ `\u110b\u1170\u110b\u1163\u11a8.txt` 로 갈라져 가드를 그냥 통과한다.
        `--force` 로도 원고를 지울 수 없어야 하므로 여기에는 예외를 두지 않는다.
    """
    directory = Path(out_dir)
    blocked = []
    for name in OUTPUT_FILES:
        target = directory / name
        if manuscript is not None and target.exists():
            try:
                same = target.samefile(Path(manuscript))
            except OSError:  # pragma: no cover - 경합 상황 방어
                same = False
            if same:
                raise OutputRefused(
                    f"출력 파일 {name} 이 원고 파일과 같은 파일입니다. 원고를 덮어쓸 수 "
                    "없으므로 거부했습니다(--force 로도 허용하지 않습니다). "
                    "다른 --out-dir 를 쓰세요."
                )
        if target.is_symlink():
            raise OutputRefused(
                f"{name} 이 심볼릭 링크입니다. 링크를 따라가면 --out-dir 밖을 쓰게 되므로 "
                "거부했습니다. 링크를 지우거나 다른 --out-dir 를 쓰세요."
            )
        if target.is_dir():
            raise OutputRefused(
                f"{name} 이라는 이름의 **폴더**가 이미 있어 파일을 쓸 수 없습니다. "
                "다른 --out-dir 를 쓰세요."
            )
        if target.exists() and not _looks_like_ours(target) and not force:
            blocked.append(name)
    if blocked:
        raise OutputRefused(
            "이 폴더에 numcheck 가 만들지 않은 같은 이름의 파일이 있습니다: "
            + ", ".join(blocked)
            + "\n  덮어쓰지 않았습니다. 다른 --out-dir 를 쓰거나 --force 를 붙이세요."
        )


def _write_csv(path: Path, header: List[str], rows: Iterable[List]) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([csv_safe(cell) for cell in row])
    # utf-8-sig: 한국 사용자의 Excel 이 그냥 열 수 있도록
    path.write_text(buffer.getvalue(), encoding="utf-8-sig")


def write_csvs(report: Report, out_dir, lang: str = "ko",
               summary_text: str = "", force: bool = False,
               manuscript=None) -> List[Path]:
    """``out_dir`` 에 문제목록.csv · 재계산표.csv · 요약.txt 를 쓴다."""
    directory = Path(out_dir)
    # 거절할 실행이면 폴더를 만들지 않는다. 먼저 mkdir 하면 쓰기를 거부한 뒤에도
    # 빈 폴더가 남아, 사용자가 "결과가 나왔나?" 하고 열어 보게 된다.
    existed = directory.is_dir()
    if existed:
        check_targets(directory, force, manuscript)
    else:
        directory.mkdir(parents=True, exist_ok=True)
    headers = _HEADERS_EN if lang == "en" else _HEADERS_KO
    written: List[Path] = []

    issues = directory / OUTPUT_FILES[0]
    _write_csv(issues, headers["issues"], [
        [f.line_no, f.section, f.level if lang != "en" else _LEVEL_EN[f.level],
         _item(f.item, lang), f.quote, f.reported, f.recomputed, f.verdict,
         _msg(f, lang) + (
             (f" [강등: {f.downgraded}]" if lang != "en"
              else f" [downgraded: {_downgrade(f.downgraded, lang)}]")
             if f.downgraded else "")]
        for f in report.sorted_findings()
    ])
    written.append(issues)

    # 재계산표: 커버리지 요약을 맨 앞 몇 행에 넣어 사람이 검산할 수 있게 한다
    audit = directory / OUTPUT_FILES[1]
    summary_rows: List[List] = [
        [0, "(요약)" if lang != "en" else "(summary)",
         "커버리지" if lang != "en" else "coverage", "", "",
         "", f"후보 {report.n_candidates}" if lang != "en"
         else f"candidates {report.n_candidates}",
         f"재계산 {report.n_checked} / 건너뜀 {report.n_skipped}" if lang != "en"
         else f"recomputed {report.n_checked} / skipped {report.n_skipped}",
         "", Path(report.path).name],
    ]
    for reason, count in report.skip_breakdown():
        summary_rows.append([0, "(요약)" if lang != "en" else "(summary)",
                             "건너뜀 사유" if lang != "en" else "skip reason",
                             "", "", _skip(reason, lang), "", str(count), "", ""])
    _write_csv(audit, headers["audit"], summary_rows + [
        [c.line_no, c.section, _item(c.item, lang), c.quote,
         ("재계산" if c.checked else "건너뜀") if lang != "en"
         else ("recomputed" if c.checked else "skipped"),
         _skip(c.skip_reason, lang) if c.skip_reason else "",
         c.reported, c.recomputed, c.verdict, c.note]
        for c in report.claims
    ])
    written.append(audit)

    summary = directory / OUTPUT_FILES[2]
    summary.write_text(summary_text.rstrip() + "\n", encoding="utf-8")
    written.append(summary)
    return written


def one_line(report: Report, min_checked: int = 5, lang: str = "ko") -> str:
    """``--quiet`` 용 한 줄 요약."""
    counts = report.counts()
    name = Path(report.path).name
    if lang == "en":
        return (f"{name}: candidates {report.n_candidates} · recomputed "
                f"{report.n_checked} · critical {counts['치명']} · warnings "
                f"{counts['경고']} · info {counts['정보']} → exit "
                f"{report.exit_code(min_checked)}")
    return (f"{name}: 후보 {report.n_candidates} · 재계산 {report.n_checked} · "
            f"치명 {counts['치명']} · 경고 {counts['경고']} · 정보 {counts['정보']} "
            f"→ 종료코드 {report.exit_code(min_checked)}")

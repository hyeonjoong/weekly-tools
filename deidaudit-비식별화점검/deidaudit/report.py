"""리포트 렌더링 — 콘솔, 점검결과.md, 문제목록.csv, 재식별위험.csv, 비식별화_요약.md.

**커버리지 자백 블록을 만들 수 없으면 아무 리포트도 만들지 않습니다.**
(`Coverage.validate()` 가 `CoverageError` 를 던지고, CLI 는 종료코드 3 으로
멈춥니다.)
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import List, Optional, Sequence

from .audit import AuditResult
from .findings import CRITICAL, WARNING

def _group_line(group: dict) -> str:
    where = group["file"]
    if group["sheet"]:
        where += f"!{group['sheet']}"
    if group["column"]:
        where += f"  {group['column']} 열"
    evidence = group["first_evidence"] or "-"
    count = group["count"]
    tail = f"  ({count:,}건)" if count > 1 else ""
    return f"  {group['kind']:<26} {where}  {evidence}{tail}"


def console_report(result: AuditResult, target_k: int) -> str:
    """콘솔에 그대로 출력할 한국어 리포트를 만듭니다."""
    result.coverage.validate()
    lines: List[str] = []
    lines.append("deidaudit — 공유 전 비식별화 점검")
    lines.append(result.coverage.headline())
    lines.append("")

    for severity in (CRITICAL, WARNING):
        groups = [g for g in result.findings.grouped() if g["severity"] == severity]
        total = sum(g["count"] for g in groups)
        kinds = len({g["kind"] for g in groups})
        if not groups:
            lines.append(f"[{severity}] 없음")
            lines.append("")
            continue
        lines.append(f"[{severity}] {kinds}종 · {total:,}건")
        for group in groups[:40]:
            lines.append(_group_line(group))
        if len(groups) > 40:
            lines.append(f"  … 외 {len(groups) - 40}개 묶음 (전체는 문제목록.csv)")
        lines.append("")

    lines.extend(_k_section(result, target_k))

    lines.append("[커버리지 자백]")
    lines.extend(result.coverage.block())
    lines.append("")

    return "\n".join(lines)


def _k_section(result: AuditResult, target_k: int) -> List[str]:
    lines: List[str] = []
    if not result.quasi_requested:
        lines.append("[재식별 위험] 계산하지 않음 — `--quasi 열1,열2` 를 주면 계산합니다")
        if result.quasi_suggested:
            lines.append(f"  후보 열: {', '.join(result.quasi_suggested)}")
        lines.append("")
        return lines
    if not result.k_results:
        lines.append("[재식별 위험] 지정한 준식별자 열이 어느 파일에도 없어 계산하지 못했습니다")
        lines.append("")
        return lines
    lines.append(f"[재식별 위험] 준식별자 = {', '.join(result.quasi_requested)}")
    for k in result.k_results:
        label = f"{k.file}!{k.sheet}" if k.sheet else k.file
        lines.append(f"  {label}  (사용한 열: {', '.join(k.quasi_used)}; 단위: {k.unit})")
        counter = "명" if k.unit == "사람" else "개"
        lines.append(
            f"    최소 k = {k.min_k} · k=1 {k.unit} {k.n_units_k1:,}{counter}(전체 {k.n_units:,}{counter} 중 "
            f"{(k.n_units_k1 / k.n_units * 100 if k.n_units else 0):.0f}%) · k<{target_k} {k.unit} {k.n_units_lt_target:,}{counter}"
        )
        if k.quasi_missing:
            lines.append(f"    이 표에 없는 준식별자: {', '.join(k.quasi_missing)}")
        if k.min_k >= target_k:
            lines.append(f"    → 이미 k ≥ {target_k} 입니다")
        else:
            for scenario in k.scenarios[:3]:
                lines.append(f"    → {scenario.label} 를 빼면 min k = {scenario.min_k}")
            if k.best_removal is not None and len(k.best_removal.removed) < len(k.quasi_used):
                lines.append(
                    f"    → k ≥ {target_k} 를 만드는 가장 작은 제거 조합: {k.best_removal.label} (min k = {k.best_removal.min_k})"
                )
            elif k.best_removal is not None:
                lines.append(
                    f"    → 지정한 준식별자를 **전부** 빼야 k ≥ {target_k} 가 됩니다 — 열 제거만으로는 답이 안 나옵니다."
                )
                lines.append(
                    "      (연령 구간화·날짜 이동 같은 변형이 필요합니다. 이 툴은 그 변형을 자동으로 하지 않습니다.)"
                )
            elif not k.searched_all_subsets:
                lines.append(
                    f"    → 1~2개를 빼는 것만으로는 k ≥ {target_k} 에 도달하지 못했습니다 "
                    "(준식별자가 많아 3개 이상 조합은 탐색하지 않았습니다)"
                )
            else:
                lines.append(f"    → 열을 빼는 것만으로는 k ≥ {target_k} 에 도달하지 못했습니다")
        for note in k.notes:
            lines.append(f"    · {note}")
    lines.append("")
    return lines


def verdict_lines(result: AuditResult, undetermined: bool, subject: str = "입력 파일") -> List[str]:
    """마지막 결론 문장.

    Args:
        subject: 무엇에 대한 판정인지("입력 파일" 또는 "내보낸 사본").
    """
    crit = result.findings.critical_count
    warn = result.findings.warning_count
    if undetermined:
        return [
            f"판정불가. {subject} 중 검사하지 못한 부분이 있어 '치명 0건'이라고 말할 수 없습니다.",
            "  → 위 [커버리지 자백] 의 '건너뜀' 을 먼저 해결한 뒤 다시 돌려 주세요.",
        ]
    if crit:
        return [f"{subject}: 치명 {crit:,}건 발견. 이 상태로 내보내면 안 됩니다."]
    if warn:
        return [
            f"{subject}: 치명 0건 · 경고 {warn:,}건.",
            "  경고는 자동으로 처리하지 않습니다 — 어디까지 지울지는 사람이 정해야 합니다.",
        ]
    return [f"{subject}: 치명 0건 · 경고 0건. 검사한 범위 안에서는 내보내도 되는 상태입니다."]


def problem_rows(result: AuditResult, limit: int) -> List[List[str]]:
    """문제목록.csv 의 데이터 행(증거는 항상 마스킹된 값)."""
    rows: List[List[str]] = []
    for finding in result.findings.sorted_items():
        rows.append(
            [
                finding.severity,
                finding.kind,
                finding.file,
                finding.sheet,
                finding.column,
                "" if finding.row is None else str(finding.row),
                finding.evidence,
                finding.note,
            ]
        )
        if len(rows) >= limit:
            break
    return rows


PROBLEM_HEADER = ["심각도", "유형", "파일", "시트", "열", "행", "증거(마스킹됨)", "설명"]

RISK_HEADER = [
    "파일", "시트", "구분", "준식별자", "단위", "동치류크기", "해당동치류수",
    "최소k", "k1단위수", "k미달단위수", "비고",
]


def risk_rows(result: AuditResult, target_k: int) -> List[List[str]]:
    """재식별위험.csv 의 데이터 행.

    **동치류의 실제 값(생년월일·성별 조합 등)은 절대 쓰지 않습니다** —
    쓰는 순간 이 파일 자체가 재식별 자료가 됩니다. 크기만 씁니다.
    """
    rows: List[List[str]] = []
    for k in result.k_results:
        quasi = " + ".join(k.quasi_used)
        rows.append(
            [k.file, k.sheet, "기준선", quasi, k.unit, "", str(k.n_classes),
             str(k.min_k), str(k.n_units_k1), str(k.n_units_lt_target),
             "; ".join(k.notes)]
        )
        for size in sorted(k.size_distribution):
            rows.append(
                [k.file, k.sheet, "크기분포", quasi, k.unit, str(size),
                 str(k.size_distribution[size]), "", "", "", ""]
            )
        for scenario in k.scenarios:
            rows.append(
                [k.file, k.sheet, "제거시나리오", f"{quasi} 에서 {scenario.label} 제거", k.unit, "", "",
                 str(scenario.min_k), str(scenario.n_units_k1), str(scenario.n_units_lt_target),
                 "목표 달성" if scenario.min_k >= target_k else ""]
            )
        if k.best_removal is not None and len(k.best_removal.removed) > 1:
            rows.append(
                [k.file, k.sheet, "최소제거조합", f"{quasi} 에서 {k.best_removal.label} 제거", k.unit, "", "",
                 str(k.best_removal.min_k), str(k.best_removal.n_units_k1),
                 str(k.best_removal.n_units_lt_target), "목표 달성"]
            )
    return rows


def markdown_report(
    result: AuditResult,
    target_k: int,
    undetermined: bool,
    command: str,
    generated: str,
) -> str:
    """점검결과.md 본문."""
    result.coverage.validate()
    body = [
        "# 비식별화 점검 결과",
        "",
        f"- 실행: `{command}`",
        f"- 생성 시각(로컬): {generated}",
        f"- 입력: {', '.join(p.name for p in result.inputs)}",
        "",
        "> 이 리포트의 증거는 **전부 마스킹**되어 있습니다. 그래도 어느 파일 어느 열에",
        "> 무엇이 있는지를 알려 주는 문서이므로, 원본과 같은 취급으로 다뤄 주세요.",
        "",
        "## 요약",
        "",
        "```",
        console_report(result, target_k).rstrip(),
        "",
        *verdict_lines(result, undetermined),
        "```",
        "",
        "## 열 판정 (자유텍스트 여부)",
        "",
        "| 표 | 열 | 자유텍스트 | 판정 근거 |",
        "|---|---|---|---|",
    ]
    for label, col, reason in result.coverage.free_text_columns:
        body.append(f"| {label} | {col} | 예 (전 행 스캔) | {reason} |")
    for label, col, reason in result.coverage.non_free_text_columns:
        body.append(f"| {label} | {col} | 아니오 | {reason} |")
    body.append("")
    body.append("## 무엇을 하지 않았는가")
    body.append("")
    body.extend(
        [
            "- 자유텍스트를 **자동으로 지우지 않습니다.** 위치만 찍고 결정은 사람이 합니다.",
            "- k-익명성을 위한 **자동 일반화·억제를 하지 않습니다.** 나이를 5세 구간으로 묶는 변형은 분석을 바꿉니다.",
            "- 파일을 **합치지 않습니다.** 그건 `joinaudit` 의 일입니다.",
            "- **어떤 통계도 계산하지 않습니다.** 이 리포트의 숫자는 전부 개수(건수·k·셀 수)입니다.",
            "- PDF·DOCX 는 읽지 않습니다(v1 은 표 형식만).",
            "- 연-월-일 순서가 아닌 날짜 표기(`03/14/2026`)는 읽지 않고 자백에 남깁니다.",
        ]
    )
    body.append("")
    return "\n".join(body)


def summary_report(
    result: AuditResult,
    transforms: Sequence,
    plan,
    verification: Sequence[str],
    key_out: Optional[Path],
    out_dir: Path,
    dropped: Sequence[str],
    generated: str,
) -> str:
    """비식별화_요약.md — 무엇을 바꿨는지 + KR/EN 문단 초안."""
    total_rows = sum(len(t.rows) for t in transforms)
    shifted_cells = sum(t.n_shifted_cells for t in transforms)
    shifted_cols = sorted({c for t in transforms for c in t.shifted_columns})
    dropped_cols = sorted({c for t in transforms for c in t.dropped_columns})
    n_subjects = len(plan.pseudonyms) if plan else 0

    lines = [
        "# 비식별화 요약",
        "",
        f"- 생성 시각(로컬): {generated}",
        f"- 내보낸 표: {len(transforms)}개 · {total_rows:,}행",
        f"- 가명 부여 피험자: {n_subjects}명",
        f"- 날짜 이동: {len(shifted_cols)}개 열 · {shifted_cells:,}개 셀",
        f"- 제외한 열: {', '.join(dropped_cols) if dropped_cols else '없음'}",
        f"- 내보내기 폴더: `{Path(str(out_dir)).name}/내보내기`",
        # 키 파일의 **전체 경로**는 이 파일에 적지 않습니다 — 이 파일은 내보내기와
        # 함께 움직일 수 있고, 경로에는 사용자 계정명과 폴더 이름이 들어 있습니다.
        (f"- 키 파일: `{Path(str(key_out)).name}` (내보내기 폴더 **밖**에 별도 보관)" if key_out else "- 키 파일: 없음"),
        "",
        "## 자체 검증 통과 항목",
        "",
    ]
    for item in verification:
        lines.append(f"- {item}")
    if not verification:
        lines.append("- (검증 항목 없음)")
    lines.extend(
        [
            "",
            "> 하나라도 실패했다면 이 파일은 만들어지지 않았습니다 — 내보내기를 통째로 취소합니다.",
            "",
        ]
    )
    warnings = list(getattr(plan, "warnings", []) or [])
    if warnings:
        lines.append("## 주의")
        lines.append("")
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")
    lines.extend(["## 남아 있는 위험", ""])
    remaining = [g for g in result.findings.grouped() if g["severity"] == CRITICAL and g["column"] not in dropped_cols]
    if remaining:
        for group in remaining[:20]:
            lines.append(f"- **{group['kind']}** — {group['file']} {group['column']} 열 ({group['count']:,}건) — 아직 제거되지 않았습니다")
    else:
        lines.append("- 내보낸 사본에서 치명 항목은 열 제외로 사라졌습니다(경고 항목은 별도로 확인하세요).")
    screened = [col for _, col, _ in result.coverage.free_text_columns]
    week_aligned = bool(getattr(plan, "week_aligned", False))
    lines.extend(
        [
            "",
            "## 논문 Methods 초안 (한국어)",
            "",
            _methods_ko(n_subjects, shifted_cols, dropped_cols, screened, week_aligned),
            "",
            "## Methods draft (English)",
            "",
            _methods_en(n_subjects, shifted_cols, dropped_cols, screened, week_aligned),
            "",
            "## Data Availability Statement 초안 (한국어)",
            "",
            _das_ko(dropped_cols, shifted_cols),
            "",
            "## Data Availability Statement draft (English)",
            "",
            _das_en(dropped_cols, shifted_cols),
            "",
            "> 초안입니다. **이 문장들은 이번 실행에서 실제로 한 일만** 적습니다 —",
            "> 날짜를 이동하지 않았으면 이동했다고 쓰지 않고, 자유텍스트 열이 없었으면",
            "> 검토했다고 쓰지 않습니다. 그래도 저널 규정과 IRB 승인 문구에 맞춰 손보세요.",
            "",
        ]
    )
    return "\n".join(lines)


def _methods_ko(
    n_subjects: int,
    shifted: Sequence[str],
    dropped: Sequence[str],
    screened: Sequence[str],
    week_aligned: bool,
) -> str:
    parts = [
        f"공유용 데이터셋은 분석 전 비식별화하였다. 피험자 식별자는 연구용 난수 가명으로 치환하였으며"
        f"({n_subjects}명), 원본 식별자와 가명의 매핑표는 연구 데이터와 물리적으로 분리된 접근 통제 저장소에 보관하였다."
    ]
    if dropped:
        parts.append(f"직접식별자 열({', '.join(dropped)})은 공유 사본에서 제거하였다.")
    if shifted:
        grain = "7일 단위의" if week_aligned else "일 단위의"
        keeps = "방문 간격, 추적 기간, 자정을 넘긴 야간 귀속"
        if week_aligned:
            keeps += ", 요일"
        parts.append(
            f"날짜 변수({', '.join(shifted)})는 피험자별로 고정된 {grain} 무작위 오프셋(±180일 이내)을 적용해 이동하였다. "
            f"오프셋은 피험자 내에서 동일하므로 {keeps}은 원자료와 동일하게 보존된다."
            + ("" if week_aligned else " 다만 요일과 계절(월)은 보존되지 않는다.")
        )
    if screened:
        parts.append(
            f"자유기술 열({', '.join(sorted(set(screened)))})은 전 행을 검토하여 인명·연락처가 포함된 항목을 확인하였다."
        )
    return " ".join(parts)


def _methods_en(
    n_subjects: int,
    shifted: Sequence[str],
    dropped: Sequence[str],
    screened: Sequence[str],
    week_aligned: bool,
) -> str:
    parts = [
        f"The shared dataset was de-identified prior to analysis. Participant identifiers were replaced with "
        f"study-specific pseudonyms (n = {n_subjects}); the mapping between original identifiers and pseudonyms "
        "is held in an access-controlled location physically separate from the shared data."
    ]
    if dropped:
        parts.append(f"Direct identifier columns ({', '.join(dropped)}) were removed from the shared copy.")
    if shifted:
        grain = "whole-week" if week_aligned else "whole-day"
        keeps = ("inter-visit intervals, follow-up duration, past-midnight night attribution, and day of week"
                 if week_aligned else
                 "inter-visit intervals, follow-up duration, and past-midnight night attribution")
        parts.append(
            f"Date variables ({', '.join(shifted)}) were shifted by a participant-specific random {grain} offset "
            f"(within ±180 days). Because the offset is constant within a participant, {keeps} are preserved "
            "exactly as in the source data."
            + ("" if week_aligned else " Day of week and calendar season are not preserved.")
        )
    if screened:
        parts.append(
            f"Free-text columns ({', '.join(sorted(set(screened)))}) were screened row by row for personal names "
            "and contact details."
        )
    return " ".join(parts)


def _das_ko(dropped: Sequence[str], shifted: Sequence[str]) -> str:
    steps = []
    if dropped:
        steps.append("직접식별자 열은 제거되었고")
    if shifted:
        steps.append("날짜는 피험자별 고정 오프셋으로 이동되었으며")
    steps.append("피험자 식별자는 연구용 가명으로 치환되었다")
    return (
        "본 연구의 비식별화된 데이터셋은 합리적 요청 시 교신저자를 통해 제공 가능하다. "
        + " ".join(steps)
        + ". 원자료 및 식별자 매핑표는 IRB 승인 범위 내에서만 접근이 허용된다."
    )


def _das_en(dropped: Sequence[str], shifted: Sequence[str]) -> str:
    steps = []
    if dropped:
        steps.append("direct identifier columns were removed")
    if shifted:
        steps.append("dates were shifted by a participant-specific constant offset")
    steps.append("participant identifiers were replaced with study-specific pseudonyms")
    joined = "; ".join(steps)
    return (
        "The de-identified dataset supporting the findings of this study is available from the corresponding "
        f"author upon reasonable request. In the shared copy, {joined}. Source data and the identifier mapping "
        "are accessible only within the scope of the approved IRB protocol."
    )


def now_string() -> str:
    """리포트에 박을 로컬 시각 문자열."""
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

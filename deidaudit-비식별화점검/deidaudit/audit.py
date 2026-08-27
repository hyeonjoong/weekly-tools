"""감사 본체 — 파일을 읽고, 열을 판정하고, 전 셀을 스캔합니다."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from .columns import ColumnProfile, looks_like_known_header, profile_table
from .coverage import Coverage
from .detect import COMPOUND_SURNAMES, scan_free_text_person, scan_name_cell, scan_structured
from .findings import CRITICAL, Finding, FindingSet, WARNING
from .kanon import KResult, compute_k
from .masking import mask_date, mask_generic
from .tabular import LoadResult, Table, load_csv, norm_key
from .xlsx import load_xlsx

# 이 이상 지적이 쌓이면 더 쌓지 않고 그 사실을 자백합니다(메모리·리포트 폭발 방지).
MAX_FINDINGS = 200_000
HIPAA_AGE_LIMIT = 89

_AGE_VALUE_RE = re.compile(r"^\s*(\d{1,3})(?:\s*세|\s*살)?\s*$")
_QUASI_HINT_HEADERS = (
    "성별", "sex", "gender", "site", "기관", "센터", "지역", "거주", "직업", "학력",
    "혼인", "군", "arm", "그룹", "group", "국적", "인종", "키", "신장", "몸무게", "체중",
    "bmi", "학교", "부서", "병원",
)


@dataclass
class TableInfo:
    """검사 대상 표 하나와 그 열 판정."""

    table: Table
    profiles: List[ColumnProfile]
    link_index: Optional[int] = None
    header_is_data: bool = False

    @property
    def label(self) -> str:
        """리포트에 쓸 안전한 표 이름."""
        return safe_table_label(self.table)

    def safe_column(self, index: int) -> str:
        """리포트에 쓸 안전한 열 이름."""
        return safe_column_label(self.table.columns[index], self.header_is_data)


def sheet_name_has_person(name: str) -> bool:
    """시트 이름 안에 사람 이름으로 보이는 토큰이 있는가.

    `명단`·`원본`·`최종`·`서울`·`강남` 처럼 **첫 음절이 성씨인 두 글자 낱말**이
    시트 이름으로 압도적으로 흔합니다. 그래서 공백/밑줄/하이픈으로 구분된
    토큰 중 **정확히 3음절**(복성이면 4음절)인 것만 이름 후보로 봅니다 —
    한국인 성명은 거의 언제나 세 글자이기 때문입니다.

    (`숨김_정하늘_명단` 의 `정하늘` 은 잡고, `원본명단`·`최종본` 은 잡지 않습니다.)
    """
    text = str(name or "")
    for token in text.replace("_", " ").replace("-", " ").split():
        if looks_like_known_header(token):
            continue
        if len(token) == 3 and scan_name_cell(token):
            return True
        if len(token) == 4 and token[:2] in COMPOUND_SURNAMES and scan_name_cell(token):
            return True
    return False


def safe_sheet_label(name: str) -> str:
    """리포트에 쓸 시트 이름.

    **고정 형식 식별자(전화·이메일·주민번호)가 들어 있을 때만 가립니다.**
    이름처럼 보인다는 이유로 `****` 로 바꿔 버리면 어느 시트가 문제인지 알 수
    없게 되어 리포트가 쓸모없어집니다 — 그런 경우는 가리는 대신 **경고로 알립니다**
    (`시트 이름에 인명`). 다만 **내보내는 사본의 파일 이름**은 보낼 폴더까지
    따라가므로, 그쪽은 `sheet_name_has_person()` 으로 걸러 서수를 씁니다.
    """
    text = str(name or "")
    if not text:
        return text
    if scan_structured(text):
        return mask_generic(text)
    return text


def safe_table_label(table: Table) -> str:
    """리포트에 쓸 `파일!시트` 표기(시트 이름 마스킹 포함)."""
    sheet = safe_sheet_label(table.sheet)
    return f"{table.file}!{sheet}" if sheet else table.file


def sanitize_finding(finding: Finding, header_is_data: bool = False) -> Finding:
    """구조적 지적(XLSX 로더가 만든 것)의 위치 필드를 마스킹합니다."""
    sheet = safe_sheet_label(finding.sheet)
    column = safe_column_label(finding.column, header_is_data)
    if sheet == finding.sheet and column == finding.column:
        return finding
    return Finding(
        severity=finding.severity, kind=finding.kind, file=finding.file,
        sheet=sheet, column=column, row=finding.row,
        evidence=finding.evidence, note=finding.note,
    )


def header_looks_like_data(columns: Sequence[str]) -> bool:
    """헤더 행이 사실은 데이터 행인지 판정합니다.

    헤더 없이 저장된 CSV 는 첫 데이터 행이 열 이름이 됩니다. 그 행은 검사되지
    않을 뿐 아니라, 열 이름이 되어 리포트에 그대로 실립니다.

    판정은 **보수적으로** 합니다. 전화·이메일·주민번호(고정 형식, 오탐이 거의
    없음)가 열 이름에 하나라도 있으면 데이터로 보고, 이름 형태만으로는
    과반이면서 2개 이상일 때만 데이터로 봅니다 — `주치의`·`담당자` 처럼 성씨로
    시작하는 3음절 직함이 열 이름으로 흔하기 때문입니다.
    """
    names = [str(c or "") for c in columns if str(c or "").strip()]
    if not names:
        return False
    if any(scan_structured(name) for name in names):
        return True
    # `이름`·`연락처`·`주소`·`연령대` 는 전부 첫 음절이 한국 성씨라서 이름처럼 보입니다.
    # 이 툴이 헤더로 알고 있는 단어는 세지 않습니다.
    candidates = [n for n in names if not looks_like_known_header(n)]
    name_like = sum(1 for name in candidates if scan_name_cell(name))
    return name_like >= 2 and name_like >= len(names) / 2


def safe_column_label(name: str, header_is_data: bool = False) -> str:
    """리포트에 실을 열 이름. **열 이름 자체가 식별자면 마스킹합니다.**

    고정 형식 식별자(전화·이메일·주민번호)는 언제나 가립니다.

    헤더 행 전체가 데이터로 판정됐으면(`header_is_data`) **그 행의 모든 칸을**
    가립니다. 한 칸씩 탐지기에 걸어 보면 생년월일·주소·직업처럼 어느 탐지기에도
    걸리지 않는 값이 원문 그대로 남는데, 그 행이 데이터라는 것은 이미 알고
    있으므로 개별 판정을 다시 할 이유가 없습니다.
    """
    text = str(name or "")
    if not text:
        return text
    if scan_structured(text):
        return mask_generic(text)
    if header_is_data:
        return mask_generic(text)
    return text


@dataclass
class AuditResult:
    """감사 전체 결과."""

    findings: FindingSet = field(default_factory=FindingSet)
    coverage: Coverage = field(default_factory=Coverage)
    k_results: List[KResult] = field(default_factory=list)
    tables: List[TableInfo] = field(default_factory=list)
    quasi_requested: List[str] = field(default_factory=list)
    quasi_suggested: List[str] = field(default_factory=list)
    inputs: List[Path] = field(default_factory=list)
    truncated_findings: bool = False


def _safe_target(target: str) -> str:
    """`파일!시트` 형태의 대상 문자열에서 시트 이름을 마스킹합니다."""
    if "!" not in target:
        return target
    head, _, tail = target.partition("!")
    return f"{head}!{safe_sheet_label(tail)}"


def load_any(path: Path, max_bytes: int, display_prefix: str = "") -> LoadResult:
    """확장자에 따라 CSV/TSV 또는 XLSX 로 읽습니다."""
    suffix = path.suffix.lower()
    display = display_prefix + path.name
    if suffix in (".xlsx", ".xlsm"):
        return load_xlsx(path, display=display, max_bytes=max_bytes)
    if suffix == ".xls":
        result = LoadResult(file=display)
        result.fatal = ".xls(구형식)는 읽지 않습니다 — 엑셀에서 .xlsx 로 저장한 뒤 다시 돌려 주세요"
        return result
    return load_csv(path, display=display, max_bytes=max_bytes)


def source_row(table: Table, row_index: int) -> int:
    """데이터 행 인덱스를 **원본 파일의 물리 행 번호**로 바꿉니다.

    엑셀이 내보낸 CSV 에는 빈 줄이 섞이는 일이 흔한데, 그걸 무시하고 세면
    리포트가 가리키는 행 번호로 파일을 열어도 그 자리에 아무것도 없습니다.
    """
    if table.source_rows and row_index < len(table.source_rows):
        return table.source_rows[row_index]
    return row_index + 2 if table.sheet else row_index + 1


def scan_table(info: TableInfo, findings: FindingSet, budget: List[int]) -> None:
    """표 하나의 모든 셀을 스캔해 지적을 쌓습니다."""
    table = info.table
    hidden_note = " (숨김 시트)" if table.hidden_sheet else ""
    matched_columns = set()
    header_is_data = header_looks_like_data(table.columns)

    for profile in info.profiles:
        if profile.n_non_empty == 0:
            continue
        col_name = safe_column_label(profile.name, header_is_data)
        hidden_col = " (숨김 열)" if profile.is_hidden else ""
        scan_names = profile.is_name_column
        scan_person = profile.is_free_text

        for row_index, row in enumerate(table.rows):
            if budget[0] <= 0:
                return
            value = row[profile.index] if profile.index < len(row) else ""
            if not str(value).strip():
                continue
            hits = scan_structured(str(value))
            if scan_names:
                hits = hits + scan_name_cell(str(value))
            if scan_person:
                hits = hits + scan_free_text_person(str(value))
            if hits:
                matched_columns.add(profile.index)
            for hit in hits:
                findings.add(
                    Finding(
                        severity=hit.severity,
                        kind=hit.kind,
                        file=table.file,
                        sheet=safe_sheet_label(table.sheet),
                        column=col_name,
                        row=source_row(table, row_index),
                        evidence=hit.evidence,
                        note=(hit.note + hidden_note + hidden_col).strip(),
                    )
                )
                budget[0] -= 1
                if budget[0] <= 0:
                    return

        _column_level_findings(
            table, profile, findings, budget, hidden_note + hidden_col,
            matched=profile.index in matched_columns,
            header_is_data=header_is_data,
        )

    if table.sheet and sheet_name_has_person(table.sheet) and budget[0] > 0:
        findings.add(
            Finding(
                severity=WARNING,
                kind="시트 이름에 인명",
                file=table.file,
                sheet=safe_sheet_label(table.sheet),
                evidence=f"{len(table.sheet)}자",
                note=(
                    "시트 이름에 사람 이름으로 보이는 표현이 있습니다. 시트 이름은 파일을 열면 "
                    "바로 보이고, 시트별로 내보내면 파일 이름이 됩니다. 원본에서 고치세요." + hidden_note
                ).strip(),
            )
        )
        budget[0] -= 1

    _scan_overflow_cells(table, findings, budget, hidden_note)
    _scan_preheader_cells(table, findings, budget, hidden_note)
    if header_is_data:
        _scan_header_row(table, findings, budget, hidden_note)


def _scan_preheader_cells(table: Table, findings: FindingSet, budget: List[int], suffix: str) -> None:
    """헤더 위의 제목·서문 행을 스캔합니다.

    `2026년 수면연구 참여자 명단 (담당 김철수 010-1234-5678)` 같은 줄이 실제로
    있습니다. 헤더를 찾느라 건너뛴 행을 그냥 버리면 그 안의 식별자가 영원히
    검사되지 않습니다.
    """
    for value in table.preheader_cells:
        if budget[0] <= 0:
            return
        hits = scan_structured(str(value)) + scan_name_cell(str(value)) + scan_free_text_person(str(value))
        for hit in hits:
            findings.add(
                Finding(
                    severity=hit.severity,
                    kind=hit.kind,
                    file=table.file,
                    sheet=safe_sheet_label(table.sheet),
                    column="(헤더 위 제목 행)",
                    evidence=hit.evidence,
                    note=("표 위의 제목·서문 줄에 있는 값입니다." + suffix).strip(),
                )
            )
            budget[0] -= 1


def _scan_overflow_cells(table: Table, findings: FindingSet, budget: List[int], suffix: str) -> None:
    """헤더보다 열이 많아 밀려난 셀을 스캔합니다.

    잘라 버리면 그 안의 전화번호·이름이 영원히 검사되지 않습니다 — 자유기술 칸에
    이스케이프되지 않은 쉼표가 하나 들어가면 정확히 이 모양이 됩니다.
    """
    for value in table.overflow_cells:
        if budget[0] <= 0:
            return
        hits = scan_structured(str(value)) + scan_name_cell(str(value)) + scan_free_text_person(str(value))
        for hit in hits:
            findings.add(
                Finding(
                    severity=hit.severity,
                    kind=hit.kind,
                    file=table.file,
                    sheet=safe_sheet_label(table.sheet),
                    column="(열 밖으로 밀린 값)",
                    evidence=hit.evidence,
                    note=("헤더보다 열이 많은 행에서 밀려난 값입니다 — 원본에서 쉼표 이스케이프를 확인하세요." + suffix).strip(),
                )
            )
            budget[0] -= 1


def _scan_header_row(table: Table, findings: FindingSet, budget: List[int], suffix: str) -> None:
    """헤더 행 자체가 데이터(=식별자)인 경우를 잡습니다.

    헤더 없이 저장된 CSV 는 첫 행이 헤더로 소비됩니다. 그러면 그 행의 이름·전화번호는
    검사되지 않을 뿐 아니라 **열 이름이 되어 리포트에 그대로 실립니다.**
    """
    for name in table.columns:
        if budget[0] <= 0:
            return
        # **고정 형식 식별자를 먼저 봅니다.** 헤더 사전 검사를 앞에 두면
        # `davidkim@hosp.kr` 처럼 `id`/`mail` 을 포함한 이메일이 통째로 빠집니다.
        hits = scan_structured(str(name))
        if not hits and not looks_like_known_header(str(name)):
            hits = scan_name_cell(str(name))
        for hit in hits:
            findings.add(
                Finding(
                    severity=CRITICAL,
                    kind="헤더 행이 데이터",
                    file=table.file,
                    sheet=safe_sheet_label(table.sheet),
                    column=mask_generic(str(name)),
                    row=1,
                    evidence=hit.evidence,
                    note=(
                        "첫 행이 헤더가 아니라 데이터로 보입니다. 헤더로 소비된 이 행은 검사되지 않았고, "
                        "열 이름이 식별자가 되어 리포트에도 남습니다. 원본에 헤더 행을 추가한 뒤 다시 돌리세요." + suffix
                    ).strip(),
                )
            )
            budget[0] -= 1
            break


def _column_level_findings(
    table: Table,
    profile: ColumnProfile,
    findings: FindingSet,
    budget: List[int],
    suffix: str,
    matched: bool = False,
    header_is_data: bool = False,
) -> None:
    """열 단위로 판정하는 지적(생년월일·연령·정확 날짜·주소·헤더 계열)."""
    values = table.column_values(profile.index)

    # 헤더가 연락처/주민번호/이메일/이름 계열인데 값에서 아무 형식도 못 찾은 경우.
    # 형식을 못 읽었을 뿐 내용은 식별자일 수 있으므로, 조용히 통과시키지 않고 알립니다.
    header_kind = None
    if profile.is_rrn_header:
        header_kind = "주민등록번호"
    elif profile.is_phone_header:
        header_kind = "연락처"
    elif profile.is_email_header:
        header_kind = "이메일"
    elif profile.is_name_column:
        header_kind = "성명"
    if header_kind and not matched and profile.n_non_empty and budget[0] > 0:
        findings.add(
            Finding(
                severity=WARNING,
                kind=f"헤더가 {header_kind} 계열(값 미인식)",
                file=table.file,
                sheet=safe_sheet_label(table.sheet),
                column=safe_column_label(profile.name, header_is_data),
                evidence=f"{profile.n_non_empty:,}개 값",
                note=(
                    f"열 이름이 {header_kind} 계열인데 값에서 그 형식을 찾지 못했습니다. "
                    "형식이 달라서 못 읽었을 뿐 내용은 식별자일 수 있으니 사람이 한 번 보세요." + suffix
                ).strip(),
            )
        )
        budget[0] -= 1

    if profile.is_birth_column:
        for row_index, value in enumerate(values):
            if not str(value).strip():
                continue
            if budget[0] <= 0:
                return
            findings.add(
                Finding(
                    severity=CRITICAL,
                    kind="생년월일",
                    file=table.file,
                    sheet=safe_sheet_label(table.sheet),
                    column=safe_column_label(profile.name, header_is_data),
                    row=source_row(table, row_index),
                    evidence=mask_date(str(value)),
                    note=("생년월일은 그 자체로 직접식별자입니다. 연령(또는 연령대)으로 바꾸는 것을 권합니다." + suffix).strip(),
                )
            )
            budget[0] -= 1
        return

    if profile.is_age_column:
        for row_index, value in enumerate(values):
            m = _AGE_VALUE_RE.match(str(value))
            if not m:
                continue
            if int(m.group(1)) <= HIPAA_AGE_LIMIT:
                continue
            if budget[0] <= 0:
                return
            findings.add(
                Finding(
                    severity=WARNING,
                    kind="89세 초과 연령",
                    file=table.file,
                    sheet=safe_sheet_label(table.sheet),
                    column=safe_column_label(profile.name, header_is_data),
                    row=source_row(table, row_index),
                    evidence="90세 이상",
                    note=("90세 이상은 인원이 적어 그 자체로 좁혀집니다. `90+` 로 묶는 것을 권합니다." + suffix).strip(),
                )
            )
            budget[0] -= 1
        return

    if profile.is_partial_date_column:
        if budget[0] <= 0:
            return
        findings.add(
            Finding(
                severity=WARNING,
                kind="부분적으로만 날짜인 열",
                file=table.file,
                sheet=safe_sheet_label(table.sheet),
                column=safe_column_label(profile.name, header_is_data),
                evidence=f"{profile.date_ratio:.0%}만 날짜로 읽힘",
                note=(
                    "`미상`·`N/A` 같은 값이 섞여 날짜 열로 판정하지 못했습니다. "
                    "`--shift-dates` 를 써도 **이 열은 이동하지 않습니다** — 원본 날짜가 그대로 나갑니다. "
                    "원본에서 값을 정리한 뒤 다시 돌리세요." + suffix
                ).strip(),
            )
        )
        budget[0] -= 1

    if profile.is_date_column:
        if budget[0] <= 0:
            return
        findings.add(
            Finding(
                severity=WARNING,
                kind="정확 날짜 열",
                file=table.file,
                sheet=safe_sheet_label(table.sheet),
                column=safe_column_label(profile.name, header_is_data),
                evidence=f"{profile.n_non_empty:,}개 값이 정확한 날짜",
                note=(
                    "가명화해도 남는 재식별 벡터입니다 — 병원 방문 기록·근무표와 대조하면 사람이 특정됩니다. "
                    "`--shift-dates` 로 피험자별 고정 오프셋 이동을 권합니다." + suffix
                ).strip(),
            )
        )
        budget[0] -= 1

    if profile.is_address_header and profile.n_non_empty:
        if budget[0] <= 0:
            return
        findings.add(
            Finding(
                severity=WARNING,
                kind="주소 열(헤더 기준)",
                file=table.file,
                sheet=safe_sheet_label(table.sheet),
                column=safe_column_label(profile.name, header_is_data),
                evidence=f"{profile.n_non_empty:,}개 값",
                note=("헤더가 주소 계열입니다. 값의 정확도(동/읍/면 단위)는 판정하지 않으니 사람이 확인하세요." + suffix).strip(),
            )
        )
        budget[0] -= 1


def suggest_quasi(infos: Sequence[TableInfo]) -> List[str]:
    """`--quasi` 가 없을 때 후보 열을 제안합니다(계산은 하지 않습니다)."""
    out: List[str] = []
    seen = set()
    for info in infos:
        for profile in info.profiles:
            key = norm_key(profile.name)
            if key in seen or profile.n_non_empty == 0:
                continue
            hit = (
                profile.is_birth_column
                or profile.is_age_column
                or profile.is_date_column
                or any(h in key for h in _QUASI_HINT_HEADERS)
            )
            if hit:
                seen.add(key)
                out.append(info.safe_column(profile.index))
    return out


def run_audit(
    paths: Sequence[Path],
    quasi: Sequence[str],
    link_ids: Sequence[str],
    max_bytes: int,
    target_k: int,
    display_prefix: str = "",
) -> AuditResult:
    """입력 파일들을 읽고 감사합니다(파일을 만들지 않습니다)."""
    result = AuditResult(quasi_requested=list(quasi), inputs=list(paths))
    coverage = result.coverage
    coverage.files_given = len(paths)
    budget = [MAX_FINDINGS]

    for path in paths:
        load = load_any(path, max_bytes, display_prefix)
        if load.fatal:
            coverage.skipped.append((load.file, load.fatal))
            continue
        if not load.tables:
            coverage.skipped.append((load.file, "읽을 수 있는 시트/행이 없음"))
            for target, reason in load.skipped:
                coverage.skipped.append((_safe_target(target), reason))
            continue
        coverage.files_read += 1
        coverage.unreadable_sheets += load.unreadable_sheets
        result.findings.extend(sanitize_finding(f) for f in load.structural)
        for target, reason in load.skipped:
            coverage.skipped.append((_safe_target(target), reason))

        for table in load.tables:
            profiles = profile_table(table)
            link_index = None
            for cand in link_ids:
                idx = table.column_index(cand)
                if idx is not None:
                    link_index = idx
                    break
            info = TableInfo(
                table=table, profiles=profiles, link_index=link_index,
                header_is_data=header_looks_like_data(table.columns),
            )
            result.tables.append(info)

            coverage.sheets += 1
            coverage.columns += table.n_cols
            coverage.cells += table.n_cells
            for profile in profiles:
                entry = (info.label, info.safe_column(profile.index), profile.free_text_reason)
                if profile.is_free_text:
                    coverage.free_text_columns.append(entry)
                else:
                    coverage.non_free_text_columns.append(entry)
                if profile.name_by_content:
                    coverage.notes.append(
                        f"{info.label} · {info.safe_column(profile.index)} 열: "
                        "헤더 단서 없이 값 형태로 이름 열이라 판정했습니다"
                    )
                if profile.is_partial_date_column:
                    coverage.skipped.append(
                        (
                            f"{info.label} · {info.safe_column(profile.index)} 열",
                            f"값의 {profile.date_ratio:.0%}만 날짜로 읽혀 날짜 이동 대상에서 뺐습니다",
                        )
                    )
                if profile.ambiguous_date_ratio >= 0.5:
                    coverage.skipped.append(
                        (
                            f"{info.label} · {info.safe_column(profile.index)} 열",
                            "일/월 순서를 알 수 없는 날짜 표기(예: 03/14/2026) — 날짜로 읽지 않았습니다",
                        )
                    )
            if table.truncated_cells:
                # 앞부분만 본 셀은 "다 보지 못한 셀"로 셉니다 — 검사율에 반영됩니다.
                coverage.cells_skipped += table.truncated_cells
                coverage.skipped.append(
                    (info.label, f"{table.truncated_cells}개 셀의 뒷부분(20,000자 초과)")
                )
            for note in table.notes:
                coverage.notes.append(f"{info.label}: {note}")

            before = budget[0]
            scan_table(info, result.findings, budget)
            if budget[0] <= 0 and before > 0:
                # 예산이 바닥나 이후 셀을 못 봤습니다. 본 것처럼 세면 안 됩니다.
                coverage.cells_skipped += table.n_cells
                coverage.cells -= table.n_cells

    total_rows = sum(info.table.n_rows for info in result.tables)
    if result.tables and total_rows == 0:
        coverage.skipped.append(
            ("(전체)", "읽기는 했지만 데이터 행이 하나도 없습니다 — 파싱이 잘못됐을 수 있습니다")
        )
        coverage.cells_skipped = max(coverage.cells_skipped, 1)
        coverage.cells = 0

    if budget[0] <= 0:
        result.truncated_findings = True
        coverage.not_computed.append(
            f"지적이 {MAX_FINDINGS:,}건을 넘어 더 세지 않았습니다 — 이 리포트의 건수는 하한입니다"
        )

    # 재식별 위험
    if quasi:
        for info in result.tables:
            id_col = info.table.columns[info.link_index] if info.link_index is not None else None
            k_result = compute_k(info.table, quasi, id_column=id_col, target=target_k)
            if k_result is None:
                coverage.not_computed.append(
                    f"{info.label}: 지정한 준식별자 열이 하나도 없어 k 를 계산하지 않았습니다"
                )
            else:
                result.k_results.append(k_result)
    else:
        result.quasi_suggested = suggest_quasi(result.tables)
        coverage.not_computed.append(
            "재식별 위험(k) — `--quasi` 를 주지 않아 계산하지 않았습니다"
            + (f" (후보: {', '.join(result.quasi_suggested)})" if result.quasi_suggested else "")
        )
    return result

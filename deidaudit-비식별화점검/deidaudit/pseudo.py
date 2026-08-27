"""가명화 내보내기 — 결정론적 가명 ID + 피험자별 고정 날짜 오프셋.

**자체 검증이 이 모듈의 본체입니다.** 오프셋이 잘못되면 방문창 계산과
자정 넘김 야간 귀속이 어긋나는데, 눈으로는 절대 안 보입니다. 그래서
내보내기 전에 다음을 독립적으로 다시 계산해 확인하고, 하나라도 깨지면
**내보내기를 통째로 취소**합니다.

1. 모든 날짜값이 정확히 `원본 + 피험자 오프셋` 인가
2. 피험자 내 날짜 **간격**이 이동 전후 완전히 동일한가 (독립 재계산)
3. 야간 귀속(정오 기준, 자정 넘김) 라벨이 오프셋만큼만 이동했는가
4. 행 수가 보존되고, 사라진 열이 `--drop-columns` 로 지정한 것뿐인가
5. 가명 매핑이 단사(injective)이고 파일 간 일관되는가
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import os
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .columns import ColumnProfile
from .dates import night_date, parse_date, render_date
from .tabular import Table, norm_key

MAX_SHIFT_DAYS = 180
# 요일 보존 모드에서 쓰는 주 단위 최대 이동폭(±25주 = ±175일).
MAX_SHIFT_WEEKS = 25
EMPTY_SUBJECT_KEY = "(빈ID)"


class VerificationError(Exception):
    """날짜 이동 자체검증 실패 — 호출자는 내보내기를 취소해야 합니다."""


@dataclass
class PseudoPlan:
    """가명 ID 와 피험자별 날짜 오프셋."""

    salt: str
    pseudonyms: Dict[str, str] = field(default_factory=dict)
    offsets: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    week_aligned: bool = False

    def pseudonym(self, subject: str) -> str:
        return self.pseudonyms.get(subject, subject)

    def offset(self, subject: str) -> int:
        return self.offsets.get(subject, 0)


def normalize_subject(value: str) -> str:
    """피험자 ID 비교용 정규화 — NFC + 앞뒤 공백 제거."""
    text = unicodedata.normalize("NFC", str(value or "")).strip()
    return text if text else EMPTY_SUBJECT_KEY


def _digest(salt: str, subject: str) -> bytes:
    return hmac.new(salt.encode("utf-8"), subject.encode("utf-8"), hashlib.sha256).digest()


def make_salt() -> str:
    """새 난수 솔트(16바이트 hex). 키 파일에 기록되어 재현에 쓰입니다."""
    return os.urandom(16).hex()


def build_plan(
    subjects: Sequence[str],
    salt: Optional[str] = None,
    prefix: str = "P",
    shift_dates: bool = True,
    week_aligned: bool = False,
) -> PseudoPlan:
    """피험자 목록에서 가명 ID 와 날짜 오프셋을 만듭니다.

    가명 번호는 솔트에 따라 결정되는 순서로 부여합니다(원본 ID 의
    사전순이 그대로 드러나지 않도록). 같은 솔트 + 같은 피험자 집합이면
    항상 같은 결과가 나옵니다.

    Args:
        week_aligned: True 면 오프셋을 7의 배수(±25주)로 만들어 **요일을
            보존**합니다. 수면 연구에서 주중/주말은 TST·SE 의 공변량이라
            요일이 바뀌면 분석이 달라집니다. 대신 오프셋 후보가 360개에서
            50개로 줄어 보호 강도는 약간 낮아집니다 — 그 맞바꿈을 사용자가
            고르도록 기본값은 일 단위입니다.
    """
    salt = salt or make_salt()
    unique = sorted({normalize_subject(s) for s in subjects})
    ordered = sorted(unique, key=lambda s: (_digest(salt, s), s))
    width = max(3, len(str(len(ordered))))
    plan = PseudoPlan(salt=salt, week_aligned=bool(week_aligned and shift_dates))
    for i, subject in enumerate(ordered, start=1):
        plan.pseudonyms[subject] = f"{prefix}{i:0{width}d}"
        if shift_dates:
            raw = int.from_bytes(_digest(salt, "offset:" + subject)[:8], "big")
            if week_aligned:
                span = raw % (2 * MAX_SHIFT_WEEKS)  # 0 .. 49
                weeks = span - MAX_SHIFT_WEEKS if span < MAX_SHIFT_WEEKS else span - MAX_SHIFT_WEEKS + 1
                plan.offsets[subject] = weeks * 7
            else:
                span = raw % (2 * MAX_SHIFT_DAYS)  # 0 .. 359
                plan.offsets[subject] = (
                    span - MAX_SHIFT_DAYS if span < MAX_SHIFT_DAYS else span - MAX_SHIFT_DAYS + 1
                )
        else:
            plan.offsets[subject] = 0
    if EMPTY_SUBJECT_KEY in plan.pseudonyms:
        plan.warnings.append(
            "피험자 ID 가 비어 있는 행이 있어 하나의 가짜 피험자로 묶어 같은 오프셋을 적용했습니다. "
            "서로 다른 사람이 섞여 있을 수 있으니 원본에서 ID 를 채운 뒤 다시 돌리는 것이 좋습니다."
        )
    return plan


@dataclass
class TransformResult:
    """표 하나를 변환한 결과."""

    table: Table
    columns: List[str]
    rows: List[List[str]]
    dropped_columns: List[str]
    shifted_columns: List[str]
    pseudonymized_column: Optional[str]
    n_shifted_cells: int
    unparsed_dates: int
    notes: List[str] = field(default_factory=list)


def transform_table(
    table: Table,
    profiles: Sequence[ColumnProfile],
    plan: PseudoPlan,
    link_index: Optional[int],
    drop_columns: Sequence[str],
    shift_dates: bool,
    fallback_subject: str,
    replace_id: bool = True,
) -> TransformResult:
    """표 하나에 가명화·날짜이동·열제외를 적용합니다(원본은 건드리지 않습니다).

    Args:
        link_index: 피험자 ID 열 인덱스. **오프셋을 고르는 데 항상 쓰입니다** —
            `--pseudonymize` 없이 `--shift-dates` 만 써도 피험자별 오프셋이
            적용되어야 하기 때문입니다.
        replace_id: True 면 ID 열 값을 가명으로 치환합니다.
    """
    drop_keys = {norm_key(c) for c in drop_columns}

    def _is_dropped(index: int) -> bool:
        """중복 헤더 때문에 `이름#2` 로 이름이 바뀐 열도 원래 이름으로 판정합니다.

        (이 처리가 없으면 `--drop-columns 이름` 이 두 번째 `이름` 열을 놓치고,
        그 열의 값이 그대로 사본에 실립니다.)
        """
        if norm_key(table.columns[index]) in drop_keys:
            return True
        if index < len(table.original_columns):
            return norm_key(table.original_columns[index]) in drop_keys
        return False

    keep_idx = [i for i in range(len(table.columns)) if not _is_dropped(i)]
    dropped = [table.columns[i] for i in range(len(table.columns)) if _is_dropped(i)]

    date_idx = [p.index for p in profiles if p.is_date_column and p.index in keep_idx] if shift_dates else []
    shifted_names = [table.columns[i] for i in date_idx]

    out_columns = [table.columns[i] for i in keep_idx]
    out_rows: List[List[str]] = []
    n_shifted = 0
    unparsed = 0

    for row in table.rows:
        subject = (
            normalize_subject(row[link_index]) if link_index is not None and link_index < len(row) else fallback_subject
        )
        offset = plan.offset(subject) if shift_dates else 0
        new_row: List[str] = []
        for i in keep_idx:
            value = row[i] if i < len(row) else ""
            if replace_id and link_index is not None and i == link_index:
                new_row.append(plan.pseudonym(subject))
                continue
            if i in date_idx and str(value).strip():
                parsed = parse_date(value)
                if parsed is None:
                    unparsed += 1
                    new_row.append(value)
                else:
                    shifted = parsed.value + _dt.timedelta(days=offset)
                    new_row.append(render_date(parsed, shifted))
                    n_shifted += 1
                continue
            new_row.append(value)
        out_rows.append(new_row)

    return TransformResult(
        table=table,
        columns=out_columns,
        rows=out_rows,
        dropped_columns=dropped,
        shifted_columns=shifted_names,
        pseudonymized_column=table.columns[link_index] if (replace_id and link_index is not None) else None,
        n_shifted_cells=n_shifted,
        unparsed_dates=unparsed,
    )


def verify_transform(
    table: Table,
    result: TransformResult,
    plan: PseudoPlan,
    link_index: Optional[int],
    shift_dates: bool,
    fallback_subject: str,
    profiles: Optional[Sequence[ColumnProfile]] = None,
    replace_id: bool = True,
    drop_columns: Optional[Sequence[str]] = None,
) -> List[str]:
    """변환 결과를 원본과 대조해 **독립적으로** 재검증합니다.

    검증 범위는 변환 결과가 스스로 신고한 `shifted_columns` 가 아니라 **원본
    프로파일**에서 다시 계산합니다. 자기가 한 일을 자기가 신고한 목록으로
    검사하면, "아무것도 안 하고 아무것도 안 했다고 신고"하는 변환이 통과합니다.

    검사 항목:
        1. 행 수 · 열 구성이 보존되었는가
        2. 이동해야 할 날짜 열이 **하나도 빠짐없이** 이동했는가
        3. 모든 날짜값이 정확히 `원본 + 피험자 오프셋` 인가
        4. 피험자 내 날짜 간격이 이동 전후 동일한가(출력 문자열에서 독립 재계산)
        5. 야간 귀속(정오 기준) 라벨이 오프셋만큼만 이동했는가
        6. 가명화 대상 ID 열이 실제로 가명으로 바뀌었는가
        7. **그 밖의 모든 셀이 한 글자도 바뀌지 않았는가**

    Raises:
        VerificationError: 하나라도 어긋나면.
    """
    checks: List[str] = []

    if len(result.rows) != len(table.rows):
        raise VerificationError(
            f"{table.label}: 행 수가 달라졌습니다 ({len(table.rows)} → {len(result.rows)})"
        )
    checks.append(f"행 수 보존 {len(table.rows):,}행")

    # 사라진 열이 **사용자가 지정한 것뿐인지**를 원본 지시에서 다시 계산합니다.
    # 변환 결과가 신고한 `dropped_columns` 를 믿으면, 지정하지 않은 열을 몰래
    # 빼고 "뺐다고 신고"하는 변환이 통과합니다.
    if drop_columns is not None:
        drop_keys = {norm_key(c) for c in drop_columns}
        expected_cols = [
            table.columns[i]
            for i in range(len(table.columns))
            if norm_key(table.columns[i]) not in drop_keys
            and not (i < len(table.original_columns) and norm_key(table.original_columns[i]) in drop_keys)
        ]
    else:
        expected_cols = [c for c in table.columns if c not in set(result.dropped_columns)]
    drop_set = set(table.columns) - set(expected_cols)
    if result.columns != expected_cols:
        missing = [c for c in expected_cols if c not in result.columns]
        raise VerificationError(
            f"{table.label}: 열 구성이 예상과 다릅니다"
            + (f" (요청하지 않았는데 사라진 열: {', '.join(missing)})" if missing else "")
        )
    checks.append(f"열 보존 {len(result.columns)}열 (제외 {len(drop_set)}열)")

    # 검증 범위를 **원본 프로파일**에서 다시 계산합니다.
    if profiles is not None and shift_dates:
        expected_shifted = {
            table.columns[p.index] for p in profiles if p.is_date_column and table.columns[p.index] not in drop_set
        }
    else:
        expected_shifted = set(result.shifted_columns)
    if shift_dates and expected_shifted != set(result.shifted_columns):
        missing = sorted(expected_shifted - set(result.shifted_columns))
        raise VerificationError(
            f"{table.label}: 이동했어야 할 날짜 열이 이동되지 않았습니다: {', '.join(missing)}"
        )

    col_pos = {name: i for i, name in enumerate(result.columns)}
    date_pairs = [
        (table.column_index(name), col_pos[name]) for name in sorted(expected_shifted) if name in col_pos
    ]
    date_src_indices = {src for src, _ in date_pairs if src is not None}
    id_src = link_index if (replace_id and link_index is not None) else None

    n_dates = 0
    n_ids = 0
    n_untouched = 0
    src_by_subject: Dict[str, List[_dt.datetime]] = defaultdict(list)
    out_by_subject: Dict[str, List[_dt.datetime]] = defaultdict(list)

    keep_src_indices = [table.column_index(name) for name in result.columns]

    for r, (src_row, out_row) in enumerate(zip(table.rows, result.rows)):
        raw_subject = src_row[link_index] if link_index is not None and link_index < len(src_row) else ""
        subject = normalize_subject(raw_subject) if link_index is not None else fallback_subject
        offset = plan.offset(subject) if shift_dates else 0

        for out_i, src_i in enumerate(keep_src_indices):
            if src_i is None:
                raise VerificationError(f"{table.label}: 출력 열 {result.columns[out_i]} 의 원본을 찾을 수 없습니다")
            src_val = src_row[src_i] if src_i < len(src_row) else ""
            out_val = out_row[out_i] if out_i < len(out_row) else ""

            if src_i == id_src:
                expected = plan.pseudonym(subject)
                if out_val != expected:
                    raise VerificationError(
                        f"{table.label}: {r + 1}행의 ID 가 가명으로 바뀌지 않았습니다"
                    )
                n_ids += 1
                continue

            if src_i in date_src_indices and shift_dates:
                if not str(src_val).strip():
                    if str(out_val).strip():
                        raise VerificationError(f"{table.label}: 빈 날짜 칸이 값으로 채워졌습니다 ({r + 1}행)")
                    continue
                src_parsed = parse_date(src_val)
                if src_parsed is None:
                    if src_val != out_val:
                        raise VerificationError(
                            f"{table.label}: 날짜로 읽히지 않는 값이 바뀌었습니다 ({r + 1}행)"
                        )
                    continue
                out_parsed = parse_date(out_val)
                if out_parsed is None:
                    raise VerificationError(
                        f"{table.label}: {r + 1}행의 이동 후 날짜를 다시 읽을 수 없습니다 "
                        f"(원본 연도 {src_parsed.value.year}, 오프셋 {offset}일 — "
                        "이동 결과가 1900~2100년 밖으로 나갔을 수 있습니다)"
                    )
                delta = (out_parsed.value - src_parsed.value).days
                if delta != offset or out_parsed.value.time() != src_parsed.value.time():
                    raise VerificationError(
                        f"{table.label}: {r + 1}행 날짜 이동량이 어긋납니다 (기대 {offset}일, 실제 {delta}일)"
                    )
                src_by_subject[subject].append(src_parsed.value)
                out_by_subject[subject].append(out_parsed.value)
                n_dates += 1
                continue

            # 그 밖의 모든 셀은 한 글자도 바뀌면 안 됩니다.
            if src_val != out_val:
                raise VerificationError(
                    f"{table.label}: {r + 1}행 '{result.columns[out_i]}' 열의 값이 변경되었습니다 "
                    "(이 툴은 지정한 열 외에는 아무것도 바꾸지 않습니다)"
                )
            n_untouched += 1

    if id_src is not None:
        checks.append(f"ID {n_ids:,}칸이 전부 가명으로 치환됨")
    checks.append(f"가명·날짜 외 {n_untouched:,}칸이 원본과 완전히 동일")

    if not shift_dates:
        checks.append("날짜 이동 없음 — 간격 검증 생략")
        return checks

    checks.append(f"날짜 셀 {n_dates:,}개가 정확히 피험자 오프셋만큼 이동 ({len(expected_shifted)}개 열 전부)")

    # 피험자 내 날짜 간격 — 출력 문자열에서 다시 읽어 독립적으로 비교합니다.
    for subject, before in src_by_subject.items():
        after = out_by_subject[subject]
        b, a = sorted(before), sorted(after)
        gaps_before = [(b[i + 1] - b[i]).total_seconds() for i in range(len(b) - 1)]
        gaps_after = [(a[i + 1] - a[i]).total_seconds() for i in range(len(a) - 1)]
        if gaps_before != gaps_after:
            raise VerificationError(f"{table.label}: 피험자 내 날짜 간격이 달라졌습니다")
    checks.append(f"피험자 {len(src_by_subject)}명의 날짜 간격 분포 동일")

    # 야간 귀속(정오 기준) 라벨 보존
    n_night = 0
    for r, (src_row, out_row) in enumerate(zip(table.rows, result.rows)):
        raw_subject = src_row[link_index] if link_index is not None and link_index < len(src_row) else ""
        subject = normalize_subject(raw_subject) if link_index is not None else fallback_subject
        offset = plan.offset(subject)
        for src_i, out_i in date_pairs:
            if src_i is None:
                continue
            src_parsed = parse_date(src_row[src_i] if src_i < len(src_row) else "")
            out_parsed = parse_date(out_row[out_i] if out_i < len(out_row) else "")
            if src_parsed is None or out_parsed is None or not src_parsed.has_time:
                continue
            expected = night_date(src_parsed.value) + _dt.timedelta(days=offset)
            if night_date(out_parsed.value) != expected:
                raise VerificationError(
                    f"{table.label}: {r + 1}행의 야간 귀속(자정 넘김) 라벨이 보존되지 않았습니다"
                )
            n_night += 1
    checks.append(
        f"야간 귀속 라벨 보존 {n_night:,}개" if n_night else "야간 귀속 검사 대상(시각이 있는 날짜) 없음"
    )
    return checks


def verify_plan(plan: PseudoPlan) -> List[str]:
    """가명 매핑이 단사인지 확인합니다."""
    values = list(plan.pseudonyms.values())
    if len(values) != len(set(values)):
        raise VerificationError("가명 ID 가 서로 겹칩니다 — 내보내기를 취소합니다")
    for subject, offset in plan.offsets.items():
        if offset != 0 and not (-MAX_SHIFT_DAYS <= offset <= MAX_SHIFT_DAYS):
            raise VerificationError(f"오프셋이 허용 범위를 벗어났습니다: {offset}일")
    checks = [f"가명 {len(values)}개가 서로 겹치지 않음", f"오프셋 전부 ±{MAX_SHIFT_DAYS}일 이내"]
    if plan.week_aligned:
        offsets = [o for o in plan.offsets.values() if o]
        if any(o % 7 for o in offsets):
            raise VerificationError("요일 보존 모드인데 7의 배수가 아닌 오프셋이 있습니다")
        checks.append(f"오프셋 전부 7의 배수 — 요일 보존({len(offsets)}명)")
    return checks


def key_rows(plan: PseudoPlan) -> List[Tuple[str, str, int]]:
    """키 파일에 쓸 (원본ID, 가명ID, 오프셋일) 목록."""
    return [
        (subject, plan.pseudonyms[subject], plan.offsets.get(subject, 0))
        for subject in sorted(plan.pseudonyms, key=lambda s: plan.pseudonyms[s])
    ]

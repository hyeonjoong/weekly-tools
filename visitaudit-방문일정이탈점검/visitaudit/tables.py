"""입력 표 읽기 — 방문기록 CSV(long/wide)와 피험자 CSV.

인코딩은 utf-8(-sig) → cp949 순서로 시도(BOM 이 있으면 utf-16 우선). 열 이름은 한국어 기본 + 흔한 별칭
(joinaudit merged.csv 의 subject_id/timepoint 포함)을 인식하고,
--id-col 등으로 명시 지정할 수 있다. 후보가 둘 이상 잡히면 고르지 않고 멈춘다.
"""

from __future__ import annotations

import csv
import io
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .dates import Parsed, parse_date


class InputError(Exception):
    """입력 파일을 신뢰할 수 없다 — exit 2 감."""


# CSV 셀 하나를 '평범한 숫자'로 볼지 판단하는 단 하나의 규칙.
# float() 은 'nan'/'inf'/'1_0' 까지 받아 주는데, 그것들이 숫자로 통과하면
# 기준 재점검에서는 없는 위반이 만들어지고(criteria) CSV 수식 가드에는 구멍이
# 난다(report). 두 곳이 따로 판단하다 어긋나지 않도록 여기 한 곳에 둔다.
PLAIN_NUM = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


class Row(list):
    """셀 목록 + 원본 파일에서의 줄 번호(`lineno`).

    list 를 그대로 상속해 기존 인덱싱(`row[i]`)이 전부 그대로 동작한다.
    """

    __slots__ = ("lineno",)

    def __init__(self, lineno: int, cells: List[str]):
        super().__init__(cells)
        self.lineno = lineno


MAX_INPUT_BYTES = 256 * 1024 * 1024  # 폭주 방지 상한

ID_ALIASES = ["피험자ID", "피험자번호", "피험자", "대상자ID", "subject_id", "subject", "id", "ID"]
VISIT_ALIASES = ["방문명", "방문", "시점", "visit", "visit_name", "timepoint"]
DATE_ALIASES = ["방문일", "방문일자", "방문날짜", "날짜", "visit_date", "date"]
STATUS_ALIASES = ["상태", "방문상태", "status", "visit_status"]

ARM_ALIASES = ["군", "배정군", "치료군", "group", "arm"]
ENROLL_ALIASES = ["등록일", "무작위배정일", "배정일", "enroll_date", "enrollment_date", "randomization_date"]
DROPOUT_ALIASES = ["탈락일", "중도탈락일", "dropout_date", "withdrawal_date", "discontinuation_date"]
DROPOUT_REASON_ALIASES = ["탈락사유", "중도탈락사유", "dropout_reason", "withdrawal_reason"]
SCREENFAIL_ALIASES = ["제외사유", "스크린실패사유", "스크리닝제외사유", "screen_fail_reason", "exclusion_reason"]

# '기록 아님' 으로 취급하는 상태값 (소문자 비교)
STATUS_PLANNED = {"예정", "계획", "예약", "scheduled", "planned"}
STATUS_NOTDONE = {"취소", "미실시", "누락", "노쇼", "no-show", "noshow", "missed", "cancelled", "canceled", "not done"}


@dataclass
class VisitRecord:
    subject: str
    visit: str
    raw_date: str
    date: Optional[object]          # datetime.date | None
    had_time: bool
    parse_error: Optional[str]
    status_raw: str
    status_kind: str                # "record" | "planned" | "notdone"
    row_no: int                     # 원본 파일에서 이 레코드가 시작한 물리적 줄 번호


@dataclass
class Subject:
    sid: str
    row_no: int
    arm: str = ""
    enroll_raw: str = ""
    enroll: Optional[object] = None
    enroll_error: Optional[str] = None
    dropout_raw: str = ""
    dropout: Optional[object] = None
    dropout_error: Optional[str] = None
    dropout_reason: str = ""
    screenfail_reason: str = ""
    extras: Dict[str, str] = field(default_factory=dict)  # 선정/제외기준 항목 열
    duplicated: bool = False

    @property
    def randomized(self) -> bool:
        return bool(self.arm.strip())


def _read_text(path: str) -> Tuple[str, str]:
    """(본문, 인코딩). 정규 파일이 아니거나 너무 크면 거부."""
    if not os.path.exists(path):
        raise InputError(f"파일이 없습니다: {path}")
    if not os.path.isfile(path):
        raise InputError(f"정규 파일이 아닙니다: {path}")
    size = os.path.getsize(path)
    if size > MAX_INPUT_BYTES:
        raise InputError(f"파일이 너무 큽니다({size} bytes > {MAX_INPUT_BYTES}): {path}")
    with open(path, "rb") as fh:
        blob = fh.read()
    # utf-16 은 아무 짝수 길이 바이트열이나 받아 주므로 BOM 이 있을 때만 시도한다
    # (순서대로 먼저 넣으면 cp949 파일을 삼켜 깨진 글자를 내놓는다).
    # 엑셀 '유니코드 텍스트'로 저장한 CSV 가 여기로 온다 — 프로토콜 쪽과 규칙을 맞춘다.
    encodings = (("utf-16", "utf-8-sig", "cp949")
                 if blob[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp949"))
    for enc in encodings:
        try:
            return blob.decode(enc), enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise InputError(f"인코딩을 해석할 수 없습니다(utf-8/utf-16/cp949 아님): {path}")


def _read_rows(path: str) -> Tuple[List[str], List[List[str]], str]:
    text, enc = _read_text(path)
    reader = csv.reader(io.StringIO(text, newline=""))
    # 원본에서 몇 번째 **줄**이었는지를 같이 들고 간다. 레코드 순번으로 세면,
    # 따옴표 안에 줄바꿈이 든 칸(엑셀에서 Alt+Enter 로 적는 비고 열 — 실제
    # 트래커에 흔하다) 하나에 그 뒤 번호가 통째로 밀린다. '46행을 확인하세요'
    # 가 엉뚱한 줄을 가리키면 [데이터 오류] 블록은 존재 이유를 잃는다.
    # csv.reader.line_num 은 지금까지 읽은 물리적 줄 수라, 레코드가 끝난 줄을
    # 가리킨다. 시작 줄은 '직전 레코드가 끝난 줄 + 1'.
    numbered = []
    try:
        prev_end = 0
        for row in reader:
            start_line = prev_end + 1
            prev_end = reader.line_num
            if any(cell.strip() for cell in row):
                numbered.append((start_line, row))
    except csv.Error as e:
        # 예: 한 필드가 131,072자 초과, (구버전 파이썬의) NUL 문자 행 등
        raise InputError(f"CSV 해석 실패: {path} ({e}) — 파일이 표준 CSV 인지 확인하세요")
    if not numbered:
        raise InputError(f"빈 파일입니다: {path}")
    rows = [row for _n, row in numbered]
    header = [h.strip() for h in rows[0]]
    if len([h for h in header if h]) < 2:
        raise InputError(f"헤더를 인식할 수 없습니다(열 2개 미만): {path}")
    dup = {h for h in header if h and header.count(h) > 1}
    if dup:
        raise InputError(f"열 이름이 중복됩니다({', '.join(sorted(dup))}): {path}")
    body = []
    for lineno, row in numbered[1:]:
        row = list(row) + [""] * (len(header) - len(row))
        body.append(Row(lineno, [cell.strip() for cell in row[: len(header)]]))
    return header, body, enc


def _resolve(header: List[str], aliases: List[str], override: Optional[str],
             what: str, path: str, required: bool = True) -> Optional[int]:
    if override:
        if override not in header:
            raise InputError(f"{path}: 지정한 {what} 열 {override!r} 이 없습니다. 실제 열: {', '.join(header)}")
        return header.index(override)
    lower = [h.lower() for h in header]
    hits = []
    for alias in aliases:
        a = alias.lower()
        if a in lower:
            idx = lower.index(a)
            if idx not in hits:
                hits.append(idx)
    if not hits:
        if required:
            raise InputError(
                f"{path}: {what} 열을 찾지 못했습니다. 인식하는 이름: {', '.join(aliases)} "
                f"(직접 지정: --{ {'피험자ID':'id-col','방문명':'visit-col','방문일':'date-col','상태':'status-col'}.get(what,'?') } 열이름)"
            )
        return None
    if len(hits) > 1:
        raise InputError(
            f"{path}: {what} 열 후보가 여러 개입니다({', '.join(header[i] for i in hits)}) — 고르지 않고 멈춥니다. 열 이름을 직접 지정하세요."
        )
    return hits[0]


def load_visits_long(path: str, id_col: Optional[str] = None, visit_col: Optional[str] = None,
                     date_col: Optional[str] = None, status_col: Optional[str] = None
                     ) -> Tuple[List[VisitRecord], str, int, int]:
    """→ (기록 목록, 인코딩, 빈 ID/방문명 행 수, 방문일이 빈 행 수)."""
    header, body, enc = _read_rows(path)
    i_id = _resolve(header, ID_ALIASES, id_col, "피험자ID", path)
    i_visit = _resolve(header, VISIT_ALIASES, visit_col, "방문명", path)
    i_date = _resolve(header, DATE_ALIASES, date_col, "방문일", path)
    i_status = _resolve(header, STATUS_ALIASES, status_col, "상태", path, required=False)
    records = []
    n_blank = 0
    n_blank_date = 0
    for row in body:
        n = row.lineno
        sid = row[i_id]
        visit = row[i_visit]
        if not sid and not visit:
            n_blank += 1        # 다른 칸에 내용은 있는데 ID·방문명이 빈 행 — 자백 대상
            continue
        if not sid:
            raise InputError(f"{path} {n}행: 피험자ID 가 비어 있습니다")
        if not visit:
            raise InputError(f"{path} {n}행: 방문명이 비어 있습니다")
        raw_date = row[i_date]
        status_raw = row[i_status] if i_status is not None else ""
        # 날짜 칸이 빈 행은 '아직 안 온 방문'을 미리 깔아 둔 트래커의 정상적인
        # 모습이다. 이걸 파싱 실패(데이터 오류)로 세면 첫 실행부터 판정률이
        # 무너져 exit 3 이 뜬다 — 이 툴이 피하려던 바로 그 크라잉울프다.
        # 기록이 없는 것으로 두면 judge 가 미도래/결측/해당없음으로 알아서 가른다.
        # (wide 로더와 피험자 CSV 도 빈 칸을 같은 뜻으로 읽는다.)
        s_norm = status_raw.strip().lower()
        if not raw_date.strip() and s_norm not in STATUS_NOTDONE and s_norm not in STATUS_PLANNED:
            n_blank_date += 1
            continue
        records.append(_make_record(sid, visit, raw_date, status_raw, n))
    if not records:
        # 빈 날짜를 '기록 없음'으로 넘기는 것은 *일부* 방문이 아직인 트래커를 위한
        # 것이다. 쓸 수 있는 날짜가 한 줄도 없으면 판정의 근거가 통째로 없는
        # 것이므로, 0건을 '이탈 없음'으로 흘려보내지 않고 입력 오류로 멈춘다.
        if n_blank_date:
            raise InputError(
                f"{path}: 방문일이 채워진 행이 0건입니다 — {n_blank_date}건이 전부 빈 칸입니다. "
                "판정할 근거가 없어 멈춥니다(빈 칸은 '아직 기록 없음'으로만 다룹니다)")
        extra = f" (빈 ID/방문명으로 건너뛴 행 {n_blank}건)" if n_blank else ""
        raise InputError(f"{path}: 판정할 데이터 행이 0건입니다 — 헤더만 있는 파일{extra}")
    return records, enc, n_blank, n_blank_date


def load_visits_wide(path: str, visit_names: List[str], id_col: Optional[str] = None
                     ) -> Tuple[List[VisitRecord], str, List[str]]:
    """1행 = 1피험자, 방문명이 열 이름. 프로토콜 방문명과 정확히 일치하는 열만 쓴다."""
    header, body, enc = _read_rows(path)
    i_id = _resolve(header, ID_ALIASES, id_col, "피험자ID", path)
    visit_cols = [(i, h) for i, h in enumerate(header) if h in visit_names]
    if not visit_cols:
        raise InputError(
            f"{path}: --wide 인데 프로토콜 방문명({', '.join(visit_names)})과 일치하는 열이 하나도 없습니다"
        )
    ignored = [h for i, h in enumerate(header) if i != i_id and h not in visit_names]
    records = []
    for row in body:
        n = row.lineno
        sid = row[i_id]
        if not sid:
            raise InputError(f"{path} {n}행: 피험자ID 가 비어 있습니다")
        for i, name in visit_cols:
            raw = row[i]
            if not raw:
                continue  # wide 에서 빈 칸 = 기록 없음
            records.append(_make_record(sid, name, raw, "", n))
    if not records:
        raise InputError(f"{path}: 판정할 데이터 행이 0건입니다 — 헤더만 있거나 방문 날짜 칸이 전부 비었습니다")
    return records, enc, ignored


def _make_record(sid: str, visit: str, raw_date: str, status_raw: str, row_no: int) -> VisitRecord:
    s = status_raw.strip().lower()
    if s in STATUS_PLANNED:
        kind = "planned"
    elif s in STATUS_NOTDONE:
        kind = "notdone"
    else:
        kind = "record"
    parsed: Parsed = parse_date(raw_date)
    return VisitRecord(
        subject=sid, visit=visit, raw_date=raw_date, date=parsed.date,
        had_time=parsed.had_time, parse_error=parsed.error,
        status_raw=status_raw, status_kind=kind, row_no=row_no,
    )


def load_subjects(path: str, id_col: Optional[str] = None
                  ) -> Tuple[List[Subject], List[str], str]:
    """피험자 CSV → (피험자 목록, 경고 목록, 인코딩). 중복 ID 는 duplicated 표시."""
    header, body, enc = _read_rows(path)
    i_id = _resolve(header, ID_ALIASES, id_col, "피험자ID", path)
    i_arm = _resolve(header, ARM_ALIASES, None, "군", path, required=False)
    i_enroll = _resolve(header, ENROLL_ALIASES, None, "등록일", path, required=False)
    i_drop = _resolve(header, DROPOUT_ALIASES, None, "탈락일", path, required=False)
    i_dropr = _resolve(header, DROPOUT_REASON_ALIASES, None, "탈락사유", path, required=False)
    i_scrf = _resolve(header, SCREENFAIL_ALIASES, None, "제외사유", path, required=False)
    known = {i for i in (i_id, i_arm, i_enroll, i_drop, i_dropr, i_scrf) if i is not None}

    subjects: List[Subject] = []
    warnings: List[str] = []
    seen: Dict[str, Subject] = {}
    for row in body:
        n = row.lineno
        sid = row[i_id]
        if not sid:
            raise InputError(f"{path} {n}행: 피험자ID 가 비어 있습니다")
        subj = Subject(sid=sid, row_no=n)
        if i_arm is not None:
            subj.arm = row[i_arm]
        if i_enroll is not None and row[i_enroll]:
            subj.enroll_raw = row[i_enroll]
            p = parse_date(subj.enroll_raw)
            subj.enroll, subj.enroll_error = p.date, p.error
        if i_drop is not None and row[i_drop]:
            subj.dropout_raw = row[i_drop]
            p = parse_date(subj.dropout_raw)
            subj.dropout, subj.dropout_error = p.date, p.error
        if i_dropr is not None:
            subj.dropout_reason = row[i_dropr]
        if i_scrf is not None:
            subj.screenfail_reason = row[i_scrf]
        subj.extras = {header[i]: row[i] for i in range(len(header)) if i not in known and header[i]}
        if sid in seen:
            seen[sid].duplicated = True
            subj.duplicated = True
            warnings.append(f"피험자.csv 에 {sid} 가 중복 기재({seen[sid].row_no}행, {n}행) — 이 피험자는 판정불가로 강등")
        else:
            seen[sid] = subj
        subjects.append(subj)
    return subjects, warnings, enc

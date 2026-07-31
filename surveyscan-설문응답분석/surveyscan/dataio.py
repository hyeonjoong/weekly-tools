"""CSV 입출력 및 결측 처리.

설문 응답 CSV는 보통 '행=응답자, 열=문항'이다. 첫 행을 헤더(문항 이름)로 본다.
빈 칸, 흔한 결측 표기(NA, N/A, NaN, ., -, 999 등은 옵션)를 결측으로 처리한다.
"""
from __future__ import annotations

import csv
import math
from typing import Dict, List, Optional, Sequence

# 결측으로 간주할 문자열(소문자 비교, 공백 제거 후).
DEFAULT_NA = {"", "na", "n/a", "nan", "null", "none", ".", "-", "missing"}


class DataError(ValueError):
    """CSV 구조가 분석에 부적합할 때 발생."""


# 눈에 보이지 않는 문자들. 그룹 라벨에 섞이면 '치료군' 과 '치료군​' 이 픽셀 단위로
# 똑같은 별개 집단이 되어(엑셀/웹 복사에서 흔함) 비교표가 조용히 쪼개진다.
_INVISIBLE = dict.fromkeys(
    map(ord, "​‌‍﻿‎‏⁠"), None
)
_NBSP_MAP = {ord(" "): " ", ord("　"): " "}


def normalize_label(raw: str) -> str:
    """집단 라벨 정규화: 보이지 않는 문자 제거 · 비분리공백→공백 · 양끝 공백 제거.

    라벨을 '보이는 대로' 다루기 위한 최소 정규화다. 대소문자·내부 공백은 건드리지 않는다
    (실제로 다른 군일 수 있으므로 임의로 합치지 않는다)."""
    return str(raw).translate(_NBSP_MAP).translate(_INVISIBLE).strip()


class SurveyData:
    """설문 응답 표.

    columns: 문항 이름 순서 (헤더)
    rows:    각 응답자의 {문항이름: float|None}
    """

    def __init__(
        self,
        columns: List[str],
        rows: List[Dict[str, Optional[float]]],
        unknown_id_columns: Optional[List[str]] = None,
        id_columns: Optional[List[str]] = None,
        id_values: Optional[List[Dict[str, str]]] = None,
        source_lines: Optional[List[int]] = None,
        skipped_blank_lines: Optional[List[int]] = None,
        unreadable: Optional[Dict[str, Dict[str, object]]] = None,
        group_column: Optional[str] = None,
        group_values: Optional[List[str]] = None,
        source_columns: Optional[List[str]] = None,
    ):
        self.columns = columns
        self.rows = rows
        # --id-col 로 지정했으나 헤더에 없던 이름들(오타 감지용).
        self.unknown_id_columns = unknown_id_columns or []
        # 분석에서 제외했지만 점수 내보내기에 다시 붙일 ID 컬럼 이름(헤더에 존재한 것만).
        self.id_columns = id_columns or []
        # 응답자 순서와 1:1 대응하는 ID 값(원문 문자열). rows 와 길이 동일.
        self.id_values = id_values or []
        # 각 응답자가 원본 CSV의 몇 번째 줄에서 왔는지(헤더가 1번 줄). rows 와 길이 동일.
        # 빈 줄을 건너뛰면 '몇 번째 응답자'와 '파일의 몇 번째 줄'이 어긋나므로,
        # 점수 CSV를 원자료에 다시 붙일 때 반드시 이 값을 써야 응답자가 밀리지 않는다.
        self.source_lines = source_lines or list(range(2, len(rows) + 2))
        # 건너뛴 완전 빈 줄들의 원본 줄 번호(엑셀이 중간에 남기는 빈 행 탐지용).
        self.skipped_blank_lines = skipped_blank_lines or []
        # 값은 있는데 숫자로 읽지 못한 셀: {컬럼: {"count": n, "examples": [원문...]}}
        self.unreadable = unreadable or {}
        # 집단 비교(--group-col)용 컬럼 이름과 응답자별 라벨(원문 문자열).
        # ID 컬럼과 달리 중복 판정에는 쓰지 않는다(같은 군에 여러 명이 있는 게 정상).
        self.group_column = group_column
        self.group_values = group_values or []
        # 원본 CSV 헤더 전체(ID·집단 컬럼 포함). config 문항이 '없다'고 할 때
        # "CSV에는 있는데 --id-col/--group-col 로 뺀 것"인지 구분해 안내하기 위해 보관.
        self.source_columns = source_columns or list(columns)

    @property
    def n_respondents(self) -> int:
        return len(self.rows)

    def numeric_columns(self) -> List[str]:
        """값이 하나라도 숫자로 파싱된 컬럼만 추린다(완전 빈/텍스트 컬럼 제외).

        주의: 0.0 은 falsy 이므로 `any(values)` 로 판단하면 전부 0인 문항이
        잘못 제외된다(예: ISI/PHQ에서 모두 '0=문제없음'). 반드시 None 여부로 판단.
        """
        return [
            col
            for col in self.columns
            if any(v is not None for v in self.rows_value(col))
        ]

    def nonnumeric_columns(self) -> List[str]:
        """숫자 값이 하나도 없는(전부 결측/텍스트) 컬럼 — 자동설정에서 제외되는 컬럼."""
        numeric = set(self.numeric_columns())
        return [c for c in self.columns if c not in numeric]

    def rows_value(self, col: str) -> List[Optional[float]]:
        return [r.get(col) for r in self.rows]

    def present_values(self, col: str) -> List[float]:
        """결측을 뺀 실제 응답 값들."""
        return [v for v in self.rows_value(col) if v is not None]


def _parse_cell(raw: str, na_values: set, na_numbers: Sequence[float]) -> Optional[float]:
    """셀 하나를 숫자로. 결측이면 None (읽지 못한 값과 구분하려면 _classify_cell 사용)."""
    return _classify_cell(raw, na_values, na_numbers)[0]


def _classify_cell(
    raw: str, na_values: set, na_numbers: Sequence[float]
) -> "tuple[Optional[float], str]":
    """셀을 (값, 종류) 로 분류한다.

    종류: "ok"(숫자), "blank"(빈칸), "na"(NA/999 등 선언된 결측 표기),
          "unreadable"(값이 있는데 숫자로 못 읽음 — 텍스트 라벨, '3,5', '3점',
          제로폭공백, 엑셀 아포스트로피 등).

    'blank' 와 'unreadable' 을 구분하는 것이 핵심이다. 둘 다 결측으로 처리되지만
    전자는 '응답 안 함', 후자는 **값이 있는데 도구가 못 읽은 것**이라 원인이
    전혀 다르고, 후자를 조용히 결측으로 묻으면 N·결측률·α가 모두 틀어진다.
    """
    s = raw.strip()
    if s == "":
        return None, "blank"
    if s.lower() in na_values:
        return None, "na"
    try:
        val = float(s)
    except (ValueError, OverflowError):
        return None, "unreadable"
    # inf/-inf/nan(예: 'inf', '1e400', '+nan') 는 통계를 오염시키고 JSON을 깨뜨리므로
    # 결측으로 처리한다('nan'/'NaN' 문자열은 이미 DEFAULT_NA에서 걸러짐).
    if not math.isfinite(val):
        return None, "na"
    if val in na_numbers:
        return None, "na"
    return val, "ok"


def load_csv(
    path: str,
    id_columns: Optional[Sequence[str]] = None,
    na_numbers: Optional[Sequence[float]] = None,
    delimiter: str = ",",
    group_column: Optional[str] = None,
) -> SurveyData:
    """설문 CSV를 읽어 SurveyData로 변환.

    id_columns: 응답자 ID 등 분석에서 제외할 컬럼 이름들.
    na_numbers: 결측 코드로 쓰인 숫자들(예: 999, -9).
    group_column: 집단 비교 기준 컬럼(치료군·성별 등). 문항 분석에서는 제외된다.
    """
    id_set = set(id_columns or [])
    na_nums = list(na_numbers or [])
    group_column = group_column.strip() if isinstance(group_column, str) else None
    if group_column == "":
        group_column = None
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        try:
            header = next(reader)
        except StopIteration:
            raise DataError("빈 파일입니다.")
        header = [h.strip() for h in header]
        if not header or all(h == "" for h in header):
            raise DataError("헤더(첫 행)가 비어 있습니다.")
        if len(set(header)) != len(header):
            dupes = sorted({h for h in header if header.count(h) > 1})
            raise DataError("헤더에 중복된 컬럼 이름이 있습니다: " + ", ".join(dupes))

        if group_column is not None and group_column not in header:
            raise DataError(
                f"--group-col 로 지정한 '{group_column}' 컬럼이 헤더에 없습니다. "
                "헤더의 컬럼: " + ", ".join(header)
            )
        # 집단 컬럼은 문항이 아니므로 분석에서 빼되, ID 처럼 중복 판정에는 쓰지 않는다.
        keep_cols = [h for h in header if h not in id_set and h != group_column]
        id_cols_present = [h for h in header if h in id_set]
        rows: List[Dict[str, Optional[float]]] = []
        id_values: List[Dict[str, str]] = []
        group_values: List[str] = []
        source_lines: List[int] = []
        skipped_blank: List[int] = []
        unreadable: Dict[str, Dict[str, object]] = {}
        for record in reader:
            # reader.line_num 은 따옴표 안 줄바꿈까지 반영한 '파일의 실제 줄 번호'다.
            # enumerate 로 세면 셀 안에 개행이 있을 때 줄 번호가 밀려 오류 메시지가 엉뚱한
            # 줄을 가리킨다.
            lineno = reader.line_num
            if not record or all(c.strip() == "" for c in record):
                skipped_blank.append(lineno)
                continue  # 완전 빈 줄은 건너뜀
            if len(record) != len(header):
                raise DataError(
                    f"{lineno}행의 열 개수({len(record)})가 헤더({len(header)})와 다릅니다."
                )
            row: Dict[str, Optional[float]] = {}
            ids: Dict[str, str] = {}
            gval = ""
            for name, cell in zip(header, record):
                # 집단 컬럼은 ID 컬럼으로도 동시에 지정될 수 있으므로 먼저 담는다.
                if name == group_column:
                    gval = normalize_label(cell)
                    # 'NA', '.', '-', 999 같은 결측 표기가 하나의 '군'으로 잡히면
                    # 결측 코드끼리 검정하는 유령 집단이 생긴다 → 라벨 없음으로 본다.
                    if _classify_cell(gval, DEFAULT_NA, na_nums)[1] in ("blank", "na"):
                        gval = ""
                if name in id_set:
                    ids[name] = cell.strip()
                    continue
                if name == group_column:
                    continue
                val, kind = _classify_cell(cell, DEFAULT_NA, na_nums)
                row[name] = val
                if kind == "unreadable":
                    rec = unreadable.setdefault(name, {"count": 0, "examples": []})
                    rec["count"] += 1
                    ex = cell.strip()
                    if len(rec["examples"]) < 5 and ex not in rec["examples"]:
                        rec["examples"].append(ex)
            rows.append(row)
            id_values.append(ids)
            group_values.append(gval)
            source_lines.append(lineno)

    if not rows:
        raise DataError("데이터 행이 없습니다(헤더만 존재).")
    unknown_ids = [c for c in id_set if c not in header]
    return SurveyData(
        columns=keep_cols,
        rows=rows,
        unknown_id_columns=unknown_ids,
        id_columns=id_cols_present,
        id_values=id_values,
        source_lines=source_lines,
        skipped_blank_lines=skipped_blank,
        unreadable=unreadable,
        group_column=group_column,
        group_values=group_values,
        source_columns=list(header),
    )

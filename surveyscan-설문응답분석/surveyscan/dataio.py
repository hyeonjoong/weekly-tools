"""CSV 입출력 및 결측 처리.

설문 응답 CSV는 보통 '행=응답자, 열=문항'이다. 첫 행을 헤더(문항 이름)로 본다.
빈 칸, 흔한 결측 표기(NA, N/A, NaN, ., -, 999 등은 옵션)를 결측으로 처리한다.
"""
from __future__ import annotations

import csv
from typing import Dict, List, Optional, Sequence

# 결측으로 간주할 문자열(소문자 비교, 공백 제거 후).
DEFAULT_NA = {"", "na", "n/a", "nan", "null", "none", ".", "-", "missing"}


class DataError(ValueError):
    """CSV 구조가 분석에 부적합할 때 발생."""


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
    ):
        self.columns = columns
        self.rows = rows
        # --id-col 로 지정했으나 헤더에 없던 이름들(오타 감지용).
        self.unknown_id_columns = unknown_id_columns or []

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
    s = raw.strip()
    if s.lower() in na_values:
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    if val in na_numbers:
        return None
    return val


def load_csv(
    path: str,
    id_columns: Optional[Sequence[str]] = None,
    na_numbers: Optional[Sequence[float]] = None,
    delimiter: str = ",",
) -> SurveyData:
    """설문 CSV를 읽어 SurveyData로 변환.

    id_columns: 응답자 ID 등 분석에서 제외할 컬럼 이름들.
    na_numbers: 결측 코드로 쓰인 숫자들(예: 999, -9).
    """
    id_set = set(id_columns or [])
    na_nums = list(na_numbers or [])
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

        keep_cols = [h for h in header if h not in id_set]
        rows: List[Dict[str, Optional[float]]] = []
        for lineno, record in enumerate(reader, start=2):
            if not record or all(c.strip() == "" for c in record):
                continue  # 완전 빈 줄은 건너뜀
            if len(record) != len(header):
                raise DataError(
                    f"{lineno}행의 열 개수({len(record)})가 헤더({len(header)})와 다릅니다."
                )
            row: Dict[str, Optional[float]] = {}
            for name, cell in zip(header, record):
                if name in id_set:
                    continue
                row[name] = _parse_cell(cell, DEFAULT_NA, na_nums)
            rows.append(row)

    if not rows:
        raise DataError("데이터 행이 없습니다(헤더만 존재).")
    unknown_ids = [c for c in id_set if c not in header]
    return SurveyData(columns=keep_cols, rows=rows, unknown_id_columns=unknown_ids)

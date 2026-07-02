"""CSV 로딩 · 문항 선택 · 역문항 처리 · 결측(NaN) 처리."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np


class DataError(Exception):
    """데이터 로딩/전처리 관련 오류."""


# 결측으로 간주할 문자열(대소문자 무시)
_NA_STRINGS = {"", "na", "n/a", "nan", "null", "none", ".", "-", "missing"}


@dataclass
class Dataset:
    names: List[str]        # 문항(열) 이름
    data: np.ndarray        # (n_raw, p) 실수 행렬, 결측은 NaN


def _to_float(token: str, extra_na: Sequence[str]) -> float:
    t = token.strip()
    if t.lower() in _NA_STRINGS or t in extra_na:
        return np.nan
    try:
        return float(t)
    except ValueError:
        return np.nan


def load_csv(path: str, na_values: Optional[Sequence[str]] = None) -> Dict[str, np.ndarray]:
    """CSV를 열 이름 -> 열 벡터(object) 로 읽는다. 숫자 변환은 뒤에서 수행."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            raise DataError("빈 파일입니다.")
        header = [h.strip() for h in header]
        if len(set(header)) != len(header):
            raise DataError("중복된 열 이름이 있습니다: 열 이름을 고유하게 만들어 주세요.")
        cols: List[List[str]] = [[] for _ in header]
        n_rows = 0
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue  # 완전 빈 줄 건너뜀
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))
            for i in range(len(header)):
                cols[i].append(row[i])
            n_rows += 1
    if n_rows == 0:
        raise DataError("데이터 행이 없습니다(헤더만 존재).")
    return {name: np.array(col, dtype=object) for name, col in zip(header, cols)}


def select_items(columns: Dict[str, np.ndarray],
                 items: Optional[Sequence[str]] = None,
                 id_cols: Sequence[str] = (),
                 na_values: Optional[Sequence[str]] = None,
                 min_unique: int = 2) -> Dataset:
    """분석할 문항 열을 골라 실수 행렬로 변환.

    items가 주어지면 그 열만, 아니면 id_cols를 제외한 '숫자형' 열 전체를 사용한다.
    분산이 0(모두 동일값)이거나 유효값이 없는 열은 제외한다.
    """
    extra_na = list(na_values or [])
    all_names = list(columns.keys())

    if items:
        missing = [c for c in items if c not in columns]
        if missing:
            raise DataError(f"CSV에 없는 문항 열: {', '.join(missing)}")
        candidates = list(items)
    else:
        candidates = [c for c in all_names if c not in set(id_cols)]

    names: List[str] = []
    vectors: List[np.ndarray] = []
    for name in candidates:
        vec = np.array([_to_float(str(v), extra_na) for v in columns[name]], dtype=float)
        finite = vec[np.isfinite(vec)]
        if items is None:
            # 자동 선택: 유효 숫자값이 거의 없으면(문자열/ID 열) 건너뜀
            if finite.size < max(2, int(0.5 * vec.size)):
                continue
            if finite.size and np.unique(finite).size < min_unique:
                # 상수 열은 상관계산 불가 → 자동선택에서는 조용히 제외
                continue
        else:
            if finite.size == 0:
                raise DataError(f"문항 '{name}'에 유효한 숫자값이 없습니다.")
            if np.unique(finite).size < min_unique:
                # 명시적으로 지정한 문항이 상수면 조용히 빠뜨리지 않고 오류로 알린다
                raise DataError(
                    f"문항 '{name}'은 값이 모두 동일(분산 0)하여 요인분석에 쓸 수 없습니다. "
                    f"입력 오류가 아닌지 확인하거나 --items 에서 제외하세요.")
        names.append(name)
        vectors.append(vec)

    if not names:
        raise DataError("분석할 숫자형 문항 열을 찾지 못했습니다. --items 로 열을 지정해 보세요.")

    return Dataset(names=names, data=np.column_stack(vectors))


def reverse_range_violations(ds: Dataset, reverse: Sequence[str],
                             scale_min: float, scale_max: float) -> Dict[str, int]:
    """역문항 재점수화 전에 선언된 [min, max] 범위를 벗어나는 값의 개수를 문항별로 센다."""
    idx = {name: i for i, name in enumerate(ds.names)}
    out: Dict[str, int] = {}
    for r in reverse:
        if r not in idx:
            continue
        col = ds.data[:, idx[r]]
        finite = col[np.isfinite(col)]
        bad = int(np.sum((finite < scale_min) | (finite > scale_max)))
        if bad:
            out[r] = bad
    return out


def apply_reverse(ds: Dataset, reverse: Sequence[str], scale_min: float, scale_max: float) -> Dataset:
    """역문항 재점수화: x -> (scale_min + scale_max) - x."""
    if not reverse:
        return ds
    idx = {name: i for i, name in enumerate(ds.names)}
    unknown = [r for r in reverse if r not in idx]
    if unknown:
        raise DataError(f"역문항 목록에 없는 문항: {', '.join(unknown)}")
    data = ds.data.copy()
    const = scale_min + scale_max
    for r in reverse:
        col = data[:, idx[r]]
        data[:, idx[r]] = const - col
    return Dataset(names=list(ds.names), data=data)


@dataclass
class Prepared:
    names: List[str]
    matrix: np.ndarray   # (n, p) 결측제거 완료
    n_total: int
    n_used: int
    n_dropped: int


def listwise(ds: Dataset) -> Prepared:
    """행 단위 결측 제거(listwise deletion)."""
    mask = np.all(np.isfinite(ds.data), axis=1)
    used = ds.data[mask]
    return Prepared(
        names=list(ds.names),
        matrix=used,
        n_total=ds.data.shape[0],
        n_used=int(used.shape[0]),
        n_dropped=int(ds.data.shape[0] - used.shape[0]),
    )

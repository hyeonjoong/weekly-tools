"""CSV 로딩 — 순수 표준 라이브러리(csv). RR/IBI 또는 순간 HR 시계열을 읽습니다.

지원하는 형태:
  1) 단일 열                : 값만 한 줄에 하나씩 (헤더 유무 무관)
        rr_ms
        812
        798
        ...
  2) 시간+값(time+value) 형 : 두 열 이상 — 값 열을 골라 사용
        time_s,rr_ms
        0.000,812
        0.812,798
        ...

단위 자동 감지(값의 중앙값 기준):
  - 중앙값 < 10      → 초(s)   → ×1000 하여 ms
  - 중앙값 < 300     → 분당 심박수(bpm) → 60000/x 하여 ms
  - 그 외            → 밀리초(ms)
`--unit ms|s|bpm` 로 수동 지정할 수 있습니다.
"""

from __future__ import annotations

import csv
import statistics
from typing import List, Optional, Sequence, Tuple

__all__ = ["parse_float", "load_series", "to_rr_ms", "detect_unit"]

_NA_LABELS = {"NA", "N/A", "NAN", "NULL", "."}

# 값 열/시간 열 추정용 이름 키워드(소문자 부분일치)
_VALUE_KEYS = ("rr", "ibi", "nn", "rri", "interval", "hr", "bpm", "beat",
               "value", "val", "heart")
_TIME_KEYS = ("time", "timestamp", "sec", "seconds", "elapsed", "clock", "t_")


def parse_float(token: str) -> Optional[float]:
    """셀을 float로 변환. 빈칸/NA/비수치는 None."""
    t = token.strip()
    if t == "" or t.upper() in _NA_LABELS:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _read_table(path: str) -> Tuple[Optional[List[str]], List[List[str]]]:
    """(header 또는 None, 데이터 행들)을 반환. 빈 줄 제거, BOM 처리."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = [r for r in csv.reader(fh) if any(c.strip() for c in r)]
    if not rows:
        raise ValueError(f"'{path}' 가 비어 있습니다.")
    first = rows[0]
    # 첫 행에 수치로 해석되지 않는 셀이 하나라도 있으면 헤더로 간주.
    has_header = any(parse_float(c) is None for c in first)
    if has_header:
        return [c.strip() for c in first], rows[1:]
    return None, rows


def _is_increasing(vals: Sequence[float]) -> bool:
    return all(vals[i + 1] >= vals[i] for i in range(len(vals) - 1))


def _pick_column(header: Optional[List[str]], data: List[List[str]],
                 col: Optional[str]) -> Tuple[int, str]:
    """사용할 값 열의 (인덱스, 이름)을 결정."""
    n_cols = max(len(r) for r in data)

    # 명시적 지정
    if col is not None:
        if header is not None and col in header:
            return header.index(col), col
        # 정수 인덱스로도 허용
        try:
            idx = int(col)
        except ValueError:
            raise ValueError(
                f"열 '{col}' 을 찾을 수 없습니다. header={header}")
        if not (0 <= idx < n_cols):
            raise ValueError(f"열 인덱스 {idx} 가 범위를 벗어났습니다(0..{n_cols-1}).")
        name = header[idx] if header and idx < len(header) else f"col{idx}"
        return idx, name

    # 단일 열
    if n_cols == 1:
        name = header[0] if header else "value"
        return 0, name

    # 헤더가 있으면 이름으로 값 열 추정
    if header is not None:
        for i, h in enumerate(header):
            hl = h.lower()
            if any(k in hl for k in _VALUE_KEYS):
                return i, h
        # 시간 열로 보이는 것을 제외하고 마지막 열
        for i in range(len(header) - 1, -1, -1):
            hl = header[i].lower()
            if not any(k in hl for k in _TIME_KEYS):
                return i, header[i]
        return len(header) - 1, header[-1]

    # 헤더 없음, 다열: 첫 열이 단조 증가면 시간으로 보고 마지막 열을 값으로.
    def column(i):
        out = []
        for r in data:
            if i < len(r):
                v = parse_float(r[i])
                if v is not None:
                    out.append(v)
        return out

    if n_cols == 2:
        c0 = column(0)
        if c0 and _is_increasing(c0):
            return 1, "col1"
        return n_cols - 1, f"col{n_cols - 1}"
    return n_cols - 1, f"col{n_cols - 1}"


def detect_unit(values: Sequence[float]) -> str:
    """값의 중앙값으로 단위를 추정: 's' | 'bpm' | 'ms'."""
    if not values:
        raise ValueError("단위를 추정할 값이 없습니다.")
    med = statistics.median(values)
    if med < 10:
        return "s"
    if med < 300:
        return "bpm"
    return "ms"


def to_rr_ms(values: Sequence[float], unit: str) -> List[float]:
    """주어진 단위의 값들을 RR(ms) 리스트로 변환."""
    if unit == "ms":
        return [float(v) for v in values]
    if unit == "s":
        return [float(v) * 1000.0 for v in values]
    if unit == "bpm":
        out = []
        for v in values:
            if v <= 0:
                continue
            out.append(60000.0 / float(v))
        return out
    raise ValueError(f"알 수 없는 단위: {unit!r} (ms/s/bpm/auto 중 하나)")


def load_series(path: str, col: Optional[str] = None, unit: str = "auto"
                ) -> Tuple[List[float], dict]:
    """CSV에서 RR(ms) 시계열과 메타데이터를 로드.

    반환: (rr_ms 리스트, {'unit': 감지/지정 단위, 'column': 값 열 이름,
                          'n_raw': 원시 값 수, 'n_dropped': 버려진 셀 수})
    """
    header, data = _read_table(path)
    idx, name = _pick_column(header, data, col)

    raw: List[float] = []
    dropped = 0
    for r in data:
        if idx < len(r):
            v = parse_float(r[idx])
            if v is None:
                dropped += 1
            else:
                raw.append(v)
        else:
            dropped += 1

    if not raw:
        raise ValueError(
            f"'{name}' 열에서 사용할 수치를 찾지 못했습니다 (열/형식을 확인하세요).")

    resolved = detect_unit(raw) if unit == "auto" else unit
    rr_ms = to_rr_ms(raw, resolved)
    if not rr_ms:
        raise ValueError("단위 변환 후 남은 값이 없습니다 (bpm에 0/음수만 있었을 수 있음).")

    meta = {
        "unit": resolved,
        "unit_source": "auto-detected" if unit == "auto" else "user-specified",
        "column": name,
        "n_raw": len(raw),
        "n_dropped": dropped,
    }
    return rr_ms, meta

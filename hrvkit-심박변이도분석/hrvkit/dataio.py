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

지저분한 실측 CSV에 대한 강건성(robustness):
  - 인코딩: UTF-8(BOM) 우선, 실패하면 cp949/euc-kr/latin-1 순으로 폴백
    (한국·윈도우 환경 임상 CSV 대응).
  - 구분자 자동 감지: 쉼표(,)·세미콜론(;)·탭·파이프(|).
  - 소수점 쉼표: 구분자가 쉼표가 아니면 "0,82" 같은 유럽식 표기를 0.82로 해석.
  - 박동 발생시각 입력: --timestamps(beat_times=True) 로 누적 발생시각 열을
    차분하여 RR을 계산.
"""

from __future__ import annotations

import csv
import os
import statistics
from typing import List, Optional, Sequence, Tuple

__all__ = ["parse_float", "load_series", "to_rr_ms", "detect_unit",
           "load_manifest"]

_NA_LABELS = {"NA", "N/A", "NAN", "NULL", ".", "-", "--", "?", "NONE"}

# 값 열/시간 열 추정용 이름 키워드(소문자 부분일치)
_VALUE_KEYS = ("rr", "ibi", "nn", "rri", "interval", "hr", "bpm", "beat",
               "value", "val", "heart")
_TIME_KEYS = ("time", "timestamp", "sec", "seconds", "elapsed", "clock", "t_")

_ENCODINGS = ("utf-8-sig", "cp949", "euc-kr", "latin-1")
_DELIMS = (",", ";", "\t", "|")


def parse_float(token: str, decimal_comma: bool = False) -> Optional[float]:
    """셀을 float로 변환. 빈칸/NA/비수치는 None.

    decimal_comma=True 이면 유럽식 소수점 쉼표("0,82"→0.82)를 처리합니다.
    """
    t = token.strip()
    if t == "" or t.upper() in _NA_LABELS:
        return None
    if decimal_comma and "," in t:
        # 천단위 구분(1.234,5)과 소수점 쉼표(0,82)를 모두 처리:
        # 점은 제거, 마지막 쉼표를 소수점으로.
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _read_text(path: str) -> str:
    """인코딩 폴백으로 파일 텍스트를 읽습니다."""
    raw = open(path, "rb").read()
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    # 최후: 손실 허용 디코드
    return raw.decode("latin-1", errors="replace")


def _sniff_delimiter(lines: Sequence[str]) -> str:
    """샘플 라인들에서 가장 일관되게 나타나는 구분자를 고릅니다.

    쉼표(기본)가 아닌 구분자는 **샘플의 과반 라인**에 나타나야 채택합니다.
    한 줄에만 우연히 섞인 세미콜론 등이 단일 열 파일을 잘못 쪼개는 것을 방지.
    """
    sample = [ln for ln in lines[:20] if ln.strip()]
    if not sample:
        return ","
    majority = (len(sample) + 1) // 2
    best, best_score = ",", -1
    for d in _DELIMS:
        counts = [ln.count(d) for ln in sample]
        nonzero = [c for c in counts if c > 0]
        # 비쉼표 구분자는 과반 라인에서 관측돼야 후보로 인정.
        if d != "," and len(nonzero) < majority:
            continue
        if not nonzero:
            continue
        # 가장 흔한 필드 수와 그 일관성(같은 값을 갖는 라인 수)으로 점수화.
        common = max(set(nonzero), key=nonzero.count)
        consistency = nonzero.count(common)
        score = consistency * 100 + common
        if score > best_score:
            best_score, best = score, d
    return best


def _read_table(path: str) -> Tuple[Optional[List[str]], List[List[str]], dict]:
    """(header 또는 None, 데이터 행들, 파싱 메타)을 반환.

    빈 줄 제거, BOM/인코딩 폴백, 구분자 자동 감지, 소수점 쉼표 감지.
    """
    text = _read_text(path)
    lines = text.splitlines()
    delimiter = _sniff_delimiter(lines)
    decimal_comma = delimiter != ","

    reader = csv.reader(lines, delimiter=delimiter)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise ValueError(f"'{path}' 가 비어 있습니다.")
    first = rows[0]
    # 첫 행에 수치로 해석되지 않는 셀이 하나라도 있으면 헤더로 간주.
    has_header = any(parse_float(c, decimal_comma) is None for c in first)
    meta = {"delimiter": delimiter, "decimal_comma": decimal_comma}
    if has_header:
        return [c.strip() for c in first], rows[1:], meta
    return None, rows, meta


def _is_increasing(vals: Sequence[float]) -> bool:
    return all(vals[i + 1] >= vals[i] for i in range(len(vals) - 1))


def _pick_column(header: Optional[List[str]], data: List[List[str]],
                 col: Optional[str], decimal_comma: bool = False
                 ) -> Tuple[int, str]:
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
                v = parse_float(r[i], decimal_comma)
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


def _looks_like_timestamps(raw: Sequence[float]) -> bool:
    """값들이 (RR이 아니라) 누적 박동 발생시각처럼 보이는지 추정.

    엄격 증가하고, 인접 차분이 모두 양수이며 차분 중앙값이 생리적 RR 범위
    (0.3–2 s 또는 300–2000 ms)에 들면 True. --timestamps 힌트용.
    """
    if len(raw) < 5:
        return False
    diffs = [raw[i + 1] - raw[i] for i in range(len(raw) - 1)]
    if any(d <= 0 for d in diffs):
        return False
    med = statistics.median(diffs)
    span = raw[-1] - raw[0]
    # 값 자체가 차분보다 훨씬 크면(누적) 타임스탬프 신호
    if span < 3 * med:
        return False
    return (0.3 <= med <= 2.0) or (300.0 <= med <= 2000.0)


def load_series(path: str, col: Optional[str] = None, unit: str = "auto",
                beat_times: bool = False) -> Tuple[List[float], dict]:
    """CSV에서 RR(ms) 시계열과 메타데이터를 로드.

    beat_times=True 이면 값 열을 '누적 박동 발생시각'으로 보고 인접 차분하여
    RR을 만든 뒤 단위를 감지/변환합니다.

    반환: (rr_ms 리스트, meta) — meta 키:
      unit, unit_source, column, n_raw, n_dropped, delimiter, decimal_comma,
      beat_times, looks_like_timestamps
    """
    header, data, pmeta = _read_table(path)
    if not data:
        raise ValueError(f"'{path}' 에 데이터 행이 없습니다 (헤더만 있는 파일).")
    decimal_comma = pmeta["decimal_comma"]
    ragged = len({len(r) for r in data}) > 1
    idx, name = _pick_column(header, data, col, decimal_comma)

    raw: List[float] = []
    dropped = 0
    for r in data:
        if idx < len(r):
            v = parse_float(r[idx], decimal_comma)
            if v is None:
                dropped += 1
            else:
                raw.append(v)
        else:
            dropped += 1

    if not raw:
        raise ValueError(
            f"'{name}' 열에서 사용할 수치를 찾지 못했습니다 (열/형식을 확인하세요).")

    looks_ts = _looks_like_timestamps(raw)

    if beat_times:
        if len(raw) < 2:
            raise ValueError("박동 발생시각 입력에는 최소 2개의 시각이 필요합니다.")
        diffs = [raw[i + 1] - raw[i] for i in range(len(raw) - 1)]
        # 시각이 정렬돼 있지 않으면 정렬 후 차분(순서 뒤섞임 방어).
        if any(d <= 0 for d in diffs):
            srt = sorted(raw)
            diffs = [srt[i + 1] - srt[i] for i in range(len(srt) - 1)]
        diffs = [d for d in diffs if d > 0]
        if not diffs:
            raise ValueError("박동 발생시각 차분에서 양의 간격을 찾지 못했습니다.")
        series = diffs
    else:
        series = raw

    resolved = detect_unit(series) if unit == "auto" else unit
    rr_ms = to_rr_ms(series, resolved)
    if not rr_ms:
        raise ValueError("단위 변환 후 남은 값이 없습니다 (bpm에 0/음수만 있었을 수 있음).")

    meta = {
        "unit": resolved,
        "unit_source": "auto-detected" if unit == "auto" else "user-specified",
        "column": name,
        "n_raw": len(raw),
        "n_dropped": dropped,
        "delimiter": pmeta["delimiter"],
        "decimal_comma": decimal_comma,
        "beat_times": beat_times,
        "looks_like_timestamps": looks_ts and not beat_times,
        "ragged": ragged,
    }
    return rr_ms, meta


def load_manifest(path: str) -> List[Tuple[str, str, str]]:
    """짝지은 코호트 매니페스트 CSV를 (기저경로, 개입경로, 라벨) 목록으로 로드.

    형식: 최소 2개 열. 헤더가 있으면 이름으로 기저/개입 열을 추정
    (base/pre/rest/기저 vs interv/post/slow/treat/개입), 없으면 1열=기저,
    2열=개입. 선택적 3번째 열은 피험자 라벨. 상대경로는 매니페스트 파일
    디렉터리 기준으로 해석합니다.
    """
    header, data, _ = _read_table(path)
    base_dir = os.path.dirname(os.path.abspath(path))

    def _resolve(p: str) -> str:
        p = p.strip()
        return p if os.path.isabs(p) else os.path.join(base_dir, p)

    # _read_table 은 첫 행에 비수치 셀이 있으면 헤더로 보지만, 매니페스트의 첫
    # 행은 항상 파일 경로(비수치)라 헤더가 없는데도 헤더로 오인됩니다. 첫 행 셀이
    # 실제 파일로 해석되면(=데이터) 헤더 오인을 되돌립니다.
    if header is not None and any(
            c.strip() and os.path.isfile(_resolve(c)) for c in header):
        data = [header] + data
        header = None

    if not data:
        raise ValueError(f"매니페스트 '{path}' 에 데이터 행이 없습니다.")

    bcol, icol, lcol = 0, 1, None
    if header:
        low = [h.lower() for h in header]
        for i, h in enumerate(low):
            if any(k in h for k in ("base", "pre", "rest", "기저", "baseline")):
                bcol = i
            if any(k in h for k in ("interv", "post", "slow", "treat", "개입")):
                icol = i
            if any(k in h for k in ("id", "subject", "label", "피험자", "라벨")):
                lcol = i

    pairs: List[Tuple[str, str, str]] = []
    for r in data:
        if max(bcol, icol) >= len(r):
            continue
        b, iv = r[bcol].strip(), r[icol].strip()
        if not b or not iv:
            continue
        label = r[lcol].strip() if (lcol is not None and lcol < len(r)) else ""
        pairs.append((_resolve(b), _resolve(iv), label))
    if not pairs:
        raise ValueError(f"매니페스트 '{path}' 에서 유효한 (기저,개입) 짝을 찾지 못했습니다.")
    return pairs

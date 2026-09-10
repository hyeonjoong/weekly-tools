"""rsalink.parse — 입력 CSV 읽기 (호흡 파형 / 호흡 이벤트 / RR 간격).

규칙
- 인코딩: utf-8 → utf-8-sig → cp949 순으로 시도.
- 타임스탬프: ISO 8601(`2026-01-01T09:00:00.250`, 공백 구분도 허용) /
  `HH:MM:SS(.fff)` / epoch 초(≥ 1e9) / 시작 기준 초(plain float).
  모두 "첫 샘플 기준 경과 초"(float)로 정규화한다. tz가 파일 안에서 섞이면 거부.
- 호흡 파형: `timestamp,value` 두 열. 샘플링레이트는 Δt 중앙값으로 추정, Δt가
  중앙값의 ±20% 밖인 비율을 "불균등 샘플링"으로 자백.
- 호흡 이벤트: `timestamp` 한 열(흡기 시작 시각).
- RR: 값 열 하나(hrvkit 호환) — 단위 자동(중앙값 <10 → 초, <300 → bpm, else ms),
  `--rr-col NAME|IDX` 로 열 지정. 300–2000 ms 밖은 제외하고 개수를 자백.
  빈칸/NA/문자/nan/inf 셀도 "제외"로 세고(n_unparsed 별도 자백) 시간축은 중앙값 RR 로 진행.
  보정은 하지 않는다(제외만).
- 역행 타임스탬프는 거부(추측 정렬 금지), 중복은 개수 자백 후 첫 값만 사용.
"""
from __future__ import annotations

import csv
import io
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

from . import RsalinkError

ENCODINGS = ("utf-8", "utf-8-sig", "cp949")
RR_MIN_MS = 300.0
RR_MAX_MS = 2000.0

_HMS_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2}(?:\.\d+)?)$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?")
_NUM_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


# ---------------------------------------------------------------- 파일 읽기


def read_text(path: str) -> str:
    """읽기 전용으로 텍스트를 읽는다(utf-8 / utf-8-sig / cp949)."""
    if not os.path.exists(path):
        raise RsalinkError(f"입력 파일이 없습니다: {os.path.basename(path)}")
    if os.path.isdir(path):
        raise RsalinkError(f"입력 경로가 폴더입니다: {os.path.basename(path)}")
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except PermissionError:
        raise RsalinkError(f"입력 파일 읽기 권한이 없습니다: {os.path.basename(path)}")
    last_err: Optional[Exception] = None
    for enc in ENCODINGS:
        try:
            text = raw.decode(enc)
            if text.startswith("﻿"):
                text = text[1:]
            return text
        except UnicodeDecodeError as e:
            last_err = e
    raise RsalinkError(
        f"인코딩을 인식하지 못했습니다(utf-8/utf-8-sig/cp949 시도): "
        f"{os.path.basename(path)} ({last_err})")


def _sniff_delim(sample: str) -> str:
    head = sample[:4096]
    counts = {d: head.count(d) for d in (",", "\t", ";")}
    best = max(counts, key=lambda k: counts[k])
    return best if counts[best] > 0 else ","


def read_rows(path: str) -> Tuple[List[str], List[List[str]]]:
    """(header, rows). 첫 행이 전부 값 모양이면 헤더 없음(빈 이름)."""
    text = read_text(path)
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise RsalinkError(f"빈 파일입니다: {os.path.basename(path)}")
    delim = _sniff_delim("\n".join(lines[:50]))
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delim)
    rows = [[c.strip() for c in r] for r in reader]
    rows = [r for r in rows if any(c for c in r)]
    first = rows[0]
    if all(_looks_value(c) for c in first if c):
        header = [""] * len(first)
        body = rows
    else:
        header = first
        body = rows[1:]
    if not body:
        raise RsalinkError(f"데이터 행이 없습니다: {os.path.basename(path)}")
    return header, body


def _looks_value(cell: str) -> bool:
    return bool(_NUM_RE.match(cell)) or bool(_HMS_RE.match(cell)) or bool(_ISO_RE.match(cell))


# ---------------------------------------------------------------- 타임스탬프


@dataclass
class TimeInfo:
    kind: str                 # "iso" | "hms" | "epoch" | "seconds"
    t0_abs: Optional[float]   # epoch 초 (iso/epoch 일 때), 아니면 None
    tz_aware: Optional[bool] = None   # iso 일 때 타임존 유무(epoch 은 True 로 본다), 아니면 None


def _parse_iso(s: str) -> Tuple[float, bool]:
    """(epoch seconds, has_tz)."""
    s2 = s.replace(" ", "T", 1) if " " in s and "T" not in s else s
    if s2.endswith("Z"):
        s2 = s2[:-1] + "+00:00"
    dt = datetime.fromisoformat(s2)
    has_tz = dt.tzinfo is not None
    if not has_tz:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp(), has_tz


def parse_timestamps(cells: Sequence[str], label: str) -> Tuple[List[float], TimeInfo]:
    """문자열 열 → 첫 샘플 기준 경과 초. 형식은 첫 셀로 정하고 전체가 같아야 한다."""
    if not cells:
        raise RsalinkError(f"{label}: 타임스탬프가 없습니다")
    first = cells[0]
    if _ISO_RE.match(first):
        vals: List[float] = []
        tz_flags = set()
        for c in cells:
            try:
                v, tz = _parse_iso(c)
            except ValueError:
                raise RsalinkError(f"{label}: ISO 타임스탬프를 읽지 못했습니다: {c!r}")
            vals.append(v)
            tz_flags.add(tz)
        if len(tz_flags) > 1:
            raise RsalinkError(
                f"{label}: 타임존이 있는 값과 없는 값이 섞여 있습니다 — 추측하지 않고 거부합니다")
        t0 = vals[0]
        out = [v - t0 for v in vals]
        _check_monotonic(out, label)
        return out, TimeInfo("iso", t0, tz_aware=(True in tz_flags))
    if _HMS_RE.match(first):
        vals = []
        prev = None
        day = 0.0
        for c in cells:
            m = _HMS_RE.match(c)
            if not m:
                raise RsalinkError(f"{label}: HH:MM:SS 형식이 섞여 있습니다: {c!r}")
            v = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            # 자정 통과(23:59 → 00:00)만 허용
            if prev is not None and v + day < prev - 12 * 3600:
                day += 86400.0
            v += day
            vals.append(v)
            prev = v
        t0 = vals[0]
        out = [v - t0 for v in vals]
        _check_monotonic(out, label)
        return out, TimeInfo("hms", None)
    if _NUM_RE.match(first):
        try:
            nums = [float(c) for c in cells]
        except ValueError as e:
            raise RsalinkError(f"{label}: 숫자 타임스탬프를 읽지 못했습니다 ({e})")
        if any(math.isnan(x) or math.isinf(x) for x in nums):
            raise RsalinkError(f"{label}: NaN/inf 타임스탬프가 있습니다")
        if nums[0] >= 1e9:  # epoch 초 (ms 단위면 1e12 이상 → 초로 환산)
            scale = 1000.0 if nums[0] >= 1e12 else 1.0
            nums = [x / scale for x in nums]
            t0 = nums[0]
            out = [x - t0 for x in nums]
            _check_monotonic(out, label)
            return out, TimeInfo("epoch", t0, tz_aware=True)
        t0 = nums[0]
        out = [x - t0 for x in nums]
        _check_monotonic(out, label)
        return out, TimeInfo("seconds", None)
    raise RsalinkError(f"{label}: 타임스탬프 형식을 인식하지 못했습니다: {first!r}")


def _check_monotonic(t: Sequence[float], label: str) -> int:
    """중복 개수를 돌려주고, 역행이 있으면 거부."""
    dup = 0
    back = 0
    for i in range(1, len(t)):
        d = t[i] - t[i - 1]
        if d == 0:
            dup += 1
        elif d < 0:
            back += 1
    if back:
        raise RsalinkError(
            f"{label}: 타임스탬프 역행 {back}건 — 파일 순서를 추측해서 정렬하지 않습니다. "
            "원본을 확인하세요")
    return dup


# ---------------------------------------------------------------- 호흡


@dataclass
class RespWaveform:
    t: List[float]                 # 경과 초
    v: List[float]
    fs_est: float                  # 추정 샘플링레이트(Hz)
    irregular_frac: float          # Δt 가 중앙값 ±20% 밖인 비율
    n_gaps: int                    # Δt > 3×중앙값 인 구간 수
    gap_total_s: float
    dup_ts: int
    time: TimeInfo
    source: str = "waveform"
    n_bad_values: int = 0          # nan/inf/비숫자 값 셀 — 제외하고 자백(라운드 1 D11)

    @property
    def duration_s(self) -> float:
        return self.t[-1] - self.t[0] if len(self.t) > 1 else 0.0


@dataclass
class RespEvents:
    t: List[float]                 # 흡기 시작 시각(경과 초)
    dup_ts: int
    time: TimeInfo
    source: str = "events"

    @property
    def duration_s(self) -> float:
        return self.t[-1] - self.t[0] if len(self.t) > 1 else 0.0


def _find_col(header: List[str], names: Sequence[str]) -> Optional[int]:
    low = [h.lower() for h in header]
    for n in names:
        if n in low:
            return low.index(n)
    return None


def parse_resp(path: str):
    """호흡 파일 → RespWaveform(2열 이상) 또는 RespEvents(1열)."""
    header, rows = read_rows(path)
    ncol = max(len(r) for r in rows)
    label = f"호흡({os.path.basename(path)})"
    t_idx = _find_col(header, ("timestamp", "time", "t", "시각", "시간"))
    if t_idx is None:
        t_idx = 0
    if ncol == 1:
        cells = [r[0] for r in rows if r and r[0]]
        t, info = parse_timestamps(cells, label)
        dup = _check_monotonic(t, label)
        if dup:
            t = sorted(set(t))
        if len(t) < 3:
            raise RsalinkError(f"{label}: 흡기 시작 이벤트가 3개 미만입니다")
        return RespEvents(t=t, dup_ts=dup, time=info)
    v_idx = _find_col(header, ("value", "resp", "respiration", "breath", "signal", "호흡", "값"))
    if v_idx is None:
        v_idx = 1 if t_idx == 0 else 0
    ts_cells: List[str] = []
    vals: List[float] = []
    n_bad = 0
    for r in rows:
        if len(r) <= max(t_idx, v_idx) or not r[t_idx]:
            continue
        try:
            x = float(r[v_idx])
            if not math.isfinite(x):
                raise ValueError
        except ValueError:
            n_bad += 1          # 빈칸·NA·nan/inf — 샘플에서 제외하고 개수를 자백
            continue
        vals.append(x)
        ts_cells.append(r[t_idx])
    if len(vals) < 10:
        raise RsalinkError(f"{label}: 유효 샘플이 10개 미만입니다 (값 셀 제외 {n_bad}건)")
    t, info = parse_timestamps(ts_cells, label)
    dup = _check_monotonic(t, label)
    if dup:
        keep_t, keep_v, last = [], [], None
        for a, b in zip(t, vals):
            if a != last:
                keep_t.append(a)
                keep_v.append(b)
            last = a
        t, vals = keep_t, keep_v
    if len(t) < 10:
        raise RsalinkError(f"{label}: 서로 다른 타임스탬프가 10개 미만입니다(전부 같은 값? 중복 {dup}건)")
    dts = [t[i] - t[i - 1] for i in range(1, len(t))]
    med = _median(dts)
    if med <= 0:
        raise RsalinkError(f"{label}: 샘플 간격 중앙값이 0 입니다")
    irregular = sum(1 for d in dts if abs(d - med) > 0.2 * med) / len(dts)
    gaps = [d for d in dts if d > 3 * med]
    return RespWaveform(
        t=t, v=vals, fs_est=1.0 / med, irregular_frac=irregular,
        n_gaps=len(gaps), gap_total_s=sum(gaps), dup_ts=dup, time=info, n_bad_values=n_bad)


# ---------------------------------------------------------------- RR


@dataclass
class RRSeries:
    rr_ms: List[float]             # 유효(300–2000 ms) RR
    t_beat: List[float]            # 각 유효 RR 의 박동 시각(경과 초, RR 누적 끝점)
    unit_detected: str             # "ms" | "s" | "bpm"
    n_total: int
    n_excluded: int
    excluded_frac: float
    col_used: str
    time: TimeInfo
    n_unparsed: int = 0            # 빈칸/NA/문자/nan/inf 셀 — n_excluded 에 포함됨

    @property
    def duration_s(self) -> float:
        return self.t_beat[-1] if self.t_beat else 0.0

    @property
    def t_mid(self) -> List[float]:
        """각 RR 간격의 중점 시각(초) = 박동 시각 − RR/2. RSA peak-valley 창 배정에 쓴다
        (끝 박동 시각에 배정하면 RR/2 ≈ 0.45 s 의 기계적 지연이 생긴다). 단조 증가."""
        return [tb - x / 2000.0 for tb, x in zip(self.t_beat, self.rr_ms)]


def _median(xs: Sequence[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return float("nan")
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _pick_rr_col(header: List[str], rows: List[List[str]], rr_col: Optional[str]) -> Tuple[int, str]:
    ncol = max(len(r) for r in rows)
    if rr_col is not None:
        if rr_col.isdigit():
            idx = int(rr_col)
            if idx >= ncol:
                raise RsalinkError(f"--rr-col {idx}: 열 수({ncol})를 넘습니다")
            return idx, header[idx] if idx < len(header) and header[idx] else str(idx)
        low = [h.lower() for h in header]
        if rr_col.lower() not in low:
            raise RsalinkError(f"--rr-col {rr_col!r}: 헤더에 없습니다 (헤더: {header})")
        return low.index(rr_col.lower()), rr_col
    idx = _find_col(header, ("rr", "rr_ms", "ibi", "rri", "rr_interval", "interval", "nn", "ibi_ms", "value"))
    if idx is not None:
        return idx, header[idx]
    numeric = []
    for j in range(ncol):
        col = [r[j] for r in rows[:50] if len(r) > j and r[j]]
        # NA/빈칸이 섞여도 열은 숫자 열 — 비어 있지 않은 셀의 80% 이상이 숫자면 채택(라운드 1 A4)
        if col and sum(1 for c in col if _NUM_RE.match(c)) >= 0.8 * len(col):
            numeric.append(j)
    if not numeric:
        raise RsalinkError("RR: 숫자 열을 찾지 못했습니다 — --rr-col 로 지정하세요")
    j = numeric[-1] if len(numeric) > 1 else numeric[0]
    return j, header[j] if j < len(header) and header[j] else str(j)


def parse_rr(path: str, rr_col: Optional[str] = None) -> RRSeries:
    header, rows = read_rows(path)
    label = f"RR({os.path.basename(path)})"
    j, name = _pick_rr_col(header, rows, rr_col)
    # 파싱 실패 셀(빈칸·NA·문자·nan/inf)은 None 으로 남겨 "제외"로 세고 시간축은 중앙값으로 진행(라운드 1 A4).
    raw: List[Optional[float]] = []
    n_unparsed = 0
    for r in rows:
        cell = r[j] if len(r) > j else ""
        try:
            x = float(cell)
            if not math.isfinite(x):
                raise ValueError
            raw.append(x)
        except ValueError:
            raw.append(None)
            n_unparsed += 1
    parsed = [x for x in raw if x is not None]
    if len(parsed) < 3:
        raise RsalinkError(f"{label}: RR 값이 3개 미만입니다 (파싱 실패 셀 {n_unparsed}건)")
    med = _median(parsed)
    if med < 10:
        unit, ms = "s", [x * 1000.0 if x is not None else None for x in raw]
    elif med < 300:
        unit, ms = "bpm", [(60000.0 / x if x > 0 else 0.0) if x is not None else None for x in raw]
    else:
        unit, ms = "ms", list(raw)
    t_idx = _find_col(header, ("timestamp", "time", "t", "시각", "시간"))
    info = TimeInfo("seconds", None)
    if t_idx is not None and t_idx != j:
        cells = [r[t_idx] for r in rows if len(r) > t_idx and r[t_idx]]
        try:
            _, info = parse_timestamps(cells, label)
        except RsalinkError:
            info = TimeInfo("seconds", None)
    fill_ms = _median([x for x in ms if x is not None and RR_MIN_MS <= x <= RR_MAX_MS] or [1000.0])
    rr_ok: List[float] = []
    t_beat: List[float] = []
    t = 0.0
    excluded = 0
    for x in ms:
        if x is not None and RR_MIN_MS <= x <= RR_MAX_MS:
            t += x / 1000.0
            rr_ok.append(x)
            t_beat.append(t)
        else:
            excluded += 1
            # 제외된 박동(범위 밖·파싱 실패)만큼도 시간은 흘러야 한다 — 값은 버리고 시간축만 중앙값으로 진행
            t += fill_ms / 1000.0
    return RRSeries(
        rr_ms=rr_ok, t_beat=t_beat, unit_detected=unit, n_total=len(ms),
        n_excluded=excluded, excluded_frac=excluded / len(ms), col_used=name, time=info,
        n_unparsed=n_unparsed)


# ---------------------------------------------------------------- 시간 인자


def parse_mmss(s: str) -> float:
    """'M:SS' / 'H:MM:SS' / '초' → 초. 오형식·nan/inf·음수는 RsalinkError(exit 2)."""
    s = s.strip()
    try:
        if _NUM_RE.match(s):
            v = float(s)
        else:
            parts = s.split(":")
            if any(p.strip().startswith(("-", "+")) for p in parts):
                raise ValueError
            if len(parts) == 2:
                v = int(parts[0]) * 60 + float(parts[1])
            elif len(parts) == 3:
                v = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            else:
                raise ValueError
    except ValueError:
        raise RsalinkError(f"시간 형식을 인식하지 못했습니다: {s!r} (M:SS, H:MM:SS 또는 초)")
    if not math.isfinite(v) or v < 0:
        raise RsalinkError(f"시간은 0 이상 유한해야 합니다: {s!r}")
    return v


def parse_range(s: str) -> Tuple[float, float]:
    """'0:00-5:00' → (0.0, 300.0). 부호로 시작('-1:00-5:00')하거나 한쪽이 비면('5:00-') 빈 문자열 ''
    을 노출하지 않는 한국어 오류(exit 2, 라운드 2 #9)."""
    s = s.strip()
    if s.startswith(("-", "+")):
        raise RsalinkError(f"구간이 부호로 시작합니다: {s!r} — 음수 시각은 쓸 수 없습니다. 형식은 M:SS-M:SS (예: 0:00-5:00)")
    if "-" not in s:
        raise RsalinkError(f"구간 형식은 M:SS-M:SS 입니다: {s!r}")
    a, b = s.split("-", 1)
    if not a.strip() or not b.strip():
        raise RsalinkError(f"구간 형식은 M:SS-M:SS 입니다(시작 또는 끝이 비어 있음): {s!r}")
    st, en = parse_mmss(a), parse_mmss(b)
    if en <= st:
        raise RsalinkError(f"구간 끝이 시작보다 앞입니다: {s!r}")
    return st, en

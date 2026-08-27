"""CSV 읽기 — 인코딩·구분자·열 이름 자동 인식과 타임스탬프 규율.

설계 원칙 (이 저장소 공통):
- 추측하지 않는다. 열 후보가 둘이면 에러(어느 쪽인지 말해 달라고 요청),
  타임스탬프가 역행·중복·미래·시간대 혼재면 에러 — 조용히 정렬하거나
  버리지 않는다.
- 제외한 행은 개수와 사유를 남긴다(리포트의 커버리지 자백에 들어간다).
- 시간대(tz)는 변환하지 않는다. 파일 전체가 동일한 고정 오프셋이면
  "그 로컬 시각"으로 해석하고 오프셋을 기록만 한다. 오프셋이 섞이면
  (여행·DST 걸친 export) 일주기 분석의 기준 시계가 무너지므로 거부한다.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import math
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


class CircadiaError(Exception):
    """사용자에게 보여줄 수 있는 오류(입력·인자 문제, exit 2)."""


# ---------------------------------------------------------------------------
# 열 이름 별칭 사전
#
# "대표 열 이름"입니다 — Apple 건강(서드파티 CSV 변환 포함)·삼성헬스·Fitbit
# export에서 흔한 이름을 모았지만, 실제 export는 앱 버전에 따라 다릅니다.
# 안 맞으면 --hr-col 등 오버라이드 + --inspect 로 확인하세요.
# 매칭은 정규화(소문자, 공백/밑줄/하이픈/점/괄호 제거) 후 정확 일치입니다.
# ---------------------------------------------------------------------------

TIME_ALIASES = {
    # 공통/Apple/삼성/Fitbit
    "timestamp", "time", "datetime", "date", "recordedat",
    "startdate", "starttime", "start",
    # 한국어
    "시각", "시간", "날짜", "측정시각",
}

HR_VALUE_ALIASES = {
    "hr", "heartrate", "bpm", "value", "avg", "avghr", "heartratebpm",
    "beatsperminute", "심박수", "심박", "맥박",
}

STEPS_VALUE_ALIASES = {
    "steps", "stepcount", "count", "value", "걸음", "걸음수",
}

SLEEP_START_ALIASES = {
    "start", "starttime", "startdate", "sleepstart", "bedtime",
    "수면시작", "취침", "취침시각", "시작",
}

SLEEP_END_ALIASES = {
    "end", "endtime", "enddate", "sleepend", "waketime", "wakeuptime",
    "수면종료", "기상", "기상시각", "종료",
}

# 수면 파일의 단계(stage) 열 — Apple 건강 export가 대표적
SLEEP_STAGE_ALIASES = {"value", "stage", "sleepstage", "state", "수면단계"}

# 단계 라벨 분류(정규화 후 부분 문자열 매칭)
_STAGE_WAKE_TOKENS = ("awake", "inbed", "wake", "각성")
_STAGE_ASLEEP_TOKENS = ("asleep", "sleep", "core", "deep", "rem", "light",
                        "restless", "수면", "얕은", "깊은", "렘")


def _norm(name: str) -> str:
    """열 이름 정규화 — 소문자화 후 공백·구두점 제거."""
    return re.sub(r"[\s_\-./()\[\]'\"]+", "", name.strip().lower().lstrip("﻿"))


# ---------------------------------------------------------------------------
# 타임스탬프
# ---------------------------------------------------------------------------

# 끝에 붙는 고정 오프셋: +09:00 / +0900 / Z
_OFFSET_RE = re.compile(r"\s*(Z|[+-]\d{2}:?\d{2})$")

_TS_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",   # 삼성헬스: 2026-08-03 23:30:00.000
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%m/%d/%Y %I:%M:%S %p",   # Fitbit: 8/3/2026 11:30:00 PM
    "%m/%d/%Y %I:%M %p",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%y %I:%M:%S %p",
    "%m/%d/%y %H:%M",
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
)


def parse_timestamp(text: str) -> Tuple[dt.datetime, Optional[int]]:
    """타임스탬프 문자열 → (naive datetime, 오프셋 분 또는 None).

    오프셋이 있으면 떼어 분 단위로 반환하고, datetime 은 '그 오프셋의 로컬
    시각' 그대로(naive)입니다 — 변환하지 않습니다. 일주기 분석은 그 사람의
    현지 시계가 기준이기 때문입니다.
    """
    s = text.strip().strip('"').strip("'")
    if not s:
        raise CircadiaError("빈 타임스탬프 셀이 있습니다")
    if re.fullmatch(r"\d{10}(\d{3})?", s):
        raise CircadiaError(
            f"epoch 숫자 타임스탬프({s!r})는 지원하지 않습니다 — epoch은 UTC라 "
            "시간대 가정 없이는 현지 시각을 알 수 없고, 이 도구는 시간대를 "
            "추측하지 않습니다. 내보내기 앱에서 로컬 시각 문자열로 변환해 주세요")
    offset_min: Optional[int] = None
    m = _OFFSET_RE.search(s)
    if m:
        token = m.group(1)
        if token == "Z":
            offset_min = 0
        else:
            sign = 1 if token[0] == "+" else -1
            digits = token[1:].replace(":", "")
            offset_min = sign * (int(digits[:2]) * 60 + int(digits[2:]))
        s = s[: m.start()].strip()
    for fmt in _TS_FORMATS:
        try:
            return dt.datetime.strptime(s, fmt), offset_min
        except ValueError:
            continue
    raise CircadiaError(
        f"타임스탬프 형식을 인식하지 못했습니다: {text!r} — 지원 형식은 "
        "ISO(2026-08-03 23:30[:00[.000]][+09:00]), 미국식(8/3/2026 11:30:00 PM), "
        "점/슬래시 날짜(2026.08.03 23:30)입니다")


# ---------------------------------------------------------------------------
# 파일 읽기 공통
# ---------------------------------------------------------------------------

@dataclass
class ColumnPick:
    raw_name: str          # 파일에 적힌 원래 이름
    how: str               # "별칭" | "지정(--*-col)"


@dataclass
class ParseMeta:
    path: str
    kind: str                       # "심박" | "걸음" | "수면"
    encoding: str = ""
    delimiter: str = ","
    columns: dict = field(default_factory=dict)   # 역할 -> ColumnPick
    n_rows: int = 0                  # 데이터 행 수(헤더 제외)
    n_used: int = 0
    excluded: dict = field(default_factory=dict)  # 사유 -> 개수
    tz_note: str = ""                # 고정 오프셋 등 기록
    notes: List[str] = field(default_factory=list)
    first_ts: Optional[dt.datetime] = None
    last_ts: Optional[dt.datetime] = None
    offset_min: Optional[int] = None


def _read_text(path: str) -> Tuple[str, str]:
    """파일 내용과 사용 인코딩. utf-8(-sig) → cp949 순서로 시도."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise CircadiaError(f"파일을 열 수 없습니다: {path} ({exc})") from exc
    if not raw.strip():
        raise CircadiaError(f"빈 파일입니다: {path}")
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise CircadiaError(
        f"인코딩을 인식하지 못했습니다: {path} — utf-8, utf-8-sig, cp949 를 지원합니다")


def _sniff_delimiter(first_line: str) -> str:
    counts = {d: first_line.count(d) for d in (",", ";", "\t")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] > 0 else ","


def _read_rows(path: str) -> Tuple[List[str], List[List[str]], str, str]:
    text, enc = _read_text(path)
    # CR-only 줄바꿈(옛 엑셀) 정규화
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if len(lines) < 2:
        raise CircadiaError(f"헤더 외 데이터 행이 없습니다: {path}")
    delim = _sniff_delimiter(lines[0])
    try:
        reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delim)
        rows = list(reader)
    except csv.Error as exc:
        raise CircadiaError(f"CSV 구문 오류: {path} ({exc})") from exc
    header = [h.strip() for h in rows[0]]
    return header, rows[1:], enc, delim


def _pick_column(header: List[str], aliases, role: str, override: Optional[str],
                 path: str, exclude_idx: Tuple[int, ...] = ()) -> Tuple[int, ColumnPick]:
    """역할(role)에 맞는 열 하나를 고른다 — 후보가 둘이면 에러, 추측 금지."""
    if override is not None:
        for i, h in enumerate(header):
            if h.strip().lower() == override.strip().lower():
                return i, ColumnPick(h, "지정")
        raise CircadiaError(
            f"{path}: 지정한 열 {override!r} 이(가) 없습니다. 실제 열: {header}")
    hits = [(i, h) for i, h in enumerate(header)
            if _norm(h) in aliases and i not in exclude_idx]
    if not hits:
        raise CircadiaError(
            f"{path}: {role} 열을 찾지 못했습니다. 실제 열: {header} — "
            f"--inspect 로 인식 결과를 보고, 오버라이드 옵션으로 열 이름을 지정하세요")
    if len(hits) > 1:
        names = [h for _, h in hits]
        raise CircadiaError(
            f"{path}: {role} 열 후보가 여러 개입니다: {names} — 추측하지 않습니다. "
            f"오버라이드 옵션으로 하나를 지정하세요")
    i, h = hits[0]
    return i, ColumnPick(h, "별칭")


def _check_offsets(offsets: List[Optional[int]], path: str, meta: ParseMeta) -> None:
    seen = {o for o in offsets if o is not None}
    if len(seen) > 1:
        pretty = sorted(f"{'+' if o >= 0 else '-'}{abs(o)//60:02d}:{abs(o)%60:02d}"
                        for o in seen)
        raise CircadiaError(
            f"{path}: 시간대 오프셋이 섞여 있습니다({', '.join(pretty)}) — 여행·DST가 "
            "걸친 기록으로 보입니다. 일주기 분석의 기준 시계를 정할 수 없어 "
            "거부합니다. 단일 시간대 구간으로 잘라서 다시 넣어 주세요")
    if seen:
        o = seen.pop()
        if None in offsets:
            raise CircadiaError(
                f"{path}: 일부 행에만 시간대 오프셋이 있습니다 — 혼재 기록은 "
                "거부합니다")
        meta.offset_min = o
        meta.tz_note = (f"고정 오프셋 UTC{'+' if o >= 0 else '-'}"
                        f"{abs(o)//60:02d}:{abs(o)%60:02d} — 해당 로컬 시각으로 해석"
                        "(변환하지 않음)")


def _validate_monotonic(stamps: List[dt.datetime], path: str) -> None:
    for i in range(1, len(stamps)):
        if stamps[i] == stamps[i - 1]:
            raise CircadiaError(
                f"{path}: 중복 타임스탬프({stamps[i]}, 행 {i + 1}/{i + 2}번째 데이터) — "
                "같은 시각이 두 번 기록된 파일은 export 오류일 수 있어 거부합니다")
        if stamps[i] < stamps[i - 1]:
            raise CircadiaError(
                f"{path}: 타임스탬프 역행({stamps[i - 1]} → {stamps[i]}) — 정렬되지 "
                "않았거나 시간대가 섞인 파일입니다. 조용히 재정렬하면 원인이 "
                "묻히므로 거부합니다. export를 시간순으로 다시 만들어 주세요")


def _validate_not_future(stamps: List[dt.datetime], path: str,
                         now: Optional[dt.datetime] = None) -> None:
    now = now or dt.datetime.now()
    horizon = now + dt.timedelta(hours=24)
    for s in (stamps[0], stamps[-1]):
        if s > horizon:
            raise CircadiaError(
                f"{path}: 미래 날짜 타임스탬프({s})가 있습니다 — 기기 시계 오류나 "
                "연도 오타일 수 있어 거부합니다")


# ---------------------------------------------------------------------------
# 시계열(심박/걸음) 읽기
# ---------------------------------------------------------------------------

# 생리적으로 그럴듯한 심박 범위(bpm). 밖이면 센서 이상값으로 보고 제외+자백.
HR_MIN, HR_MAX = 20.0, 260.0
# 걸음 상한(한 구간 값) — 하루 종일 집계 행이라도 20만 걸음은 비현실적.
# 극단값(1e154 등)이 분산 계산을 overflow 시키는 것도 여기서 막는다(라운드 1 M4).
STEPS_MAX = 200000.0

# 천단위 구분 콤마만 허용(예: 1,234 / 12,345.6). 그 외 콤마(예: "3,5")는
# 유럽식 소수점일 수 있어 10배 오염 위험 — 추측하지 않고 제외+자백(라운드 1 M5).
_THOUSANDS_RE = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")


def _parse_number(raw: str) -> Optional[float]:
    """숫자 파싱 — 콤마는 천단위 패턴일 때만 제거. 실패하면 None."""
    if "," in raw:
        if not _THOUSANDS_RE.match(raw):
            return None
        raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


@dataclass
class Series:
    samples: List[Tuple[dt.datetime, float]]
    meta: ParseMeta


def read_series(path: str, kind: str, time_col: Optional[str] = None,
                value_col: Optional[str] = None,
                now: Optional[dt.datetime] = None) -> Series:
    """심박(kind='심박') 또는 걸음(kind='걸음') CSV → 시계열."""
    assert kind in ("심박", "걸음")
    header, rows, enc, delim = _read_rows(path)
    meta = ParseMeta(path=path, kind=kind, encoding=enc, delimiter=delim)

    ti, tpick = _pick_column(header, TIME_ALIASES, "시각", time_col, path)
    aliases = HR_VALUE_ALIASES if kind == "심박" else STEPS_VALUE_ALIASES
    vi, vpick = _pick_column(header, aliases, f"{kind} 값", value_col, path,
                             exclude_idx=(ti,))
    meta.columns = {"시각": tpick, "값": vpick}

    samples: List[Tuple[dt.datetime, float]] = []
    offsets: List[Optional[int]] = []
    excluded = meta.excluded
    for r, row in enumerate(rows, start=2):
        if len(row) <= max(ti, vi):
            excluded["열 수 부족"] = excluded.get("열 수 부족", 0) + 1
            continue
        meta.n_rows += 1
        raw_v = row[vi].strip().strip('"')
        if not raw_v:
            excluded["빈 값"] = excluded.get("빈 값", 0) + 1
            continue
        v = _parse_number(raw_v)
        if v is None:
            excluded["숫자 아님"] = excluded.get("숫자 아님", 0) + 1
            continue
        if not math.isfinite(v):
            excluded["nan/inf"] = excluded.get("nan/inf", 0) + 1
            continue
        if kind == "심박" and not (HR_MIN <= v <= HR_MAX):
            excluded[f"심박 범위 밖({HR_MIN:.0f}–{HR_MAX:.0f}bpm)"] = \
                excluded.get(f"심박 범위 밖({HR_MIN:.0f}–{HR_MAX:.0f}bpm)", 0) + 1
            continue
        if kind == "걸음" and v < 0:
            excluded["음수 걸음"] = excluded.get("음수 걸음", 0) + 1
            continue
        if kind == "걸음" and v > STEPS_MAX:
            excluded[f"걸음 범위 밖(>{STEPS_MAX:.0f})"] = \
                excluded.get(f"걸음 범위 밖(>{STEPS_MAX:.0f})", 0) + 1
            continue
        stamp, off = parse_timestamp(row[ti])
        samples.append((stamp, v))
        offsets.append(off)
    if not samples:
        raise CircadiaError(f"{path}: 사용할 수 있는 데이터 행이 없습니다 "
                            f"(제외 사유: {dict(excluded) or '없음'})")
    _check_offsets(offsets, path, meta)
    stamps = [s for s, _ in samples]
    _validate_monotonic(stamps, path)
    _validate_not_future(stamps, path, now=now)
    meta.n_used = len(samples)
    meta.first_ts, meta.last_ts = stamps[0], stamps[-1]
    return Series(samples, meta)


# ---------------------------------------------------------------------------
# 수면구간 읽기
# ---------------------------------------------------------------------------

@dataclass
class SleepData:
    intervals: List[Tuple[dt.datetime, dt.datetime]]   # 병합·정렬 완료
    meta: ParseMeta


def _classify_stage(label: str) -> str:
    """단계 라벨 → 'wake' | 'asleep' | 'unknown'."""
    n = _norm(label)
    if any(tok in n for tok in _STAGE_WAKE_TOKENS):
        # 'awake' 안에 'wake'가 있으므로 wake 토큰을 먼저 검사한다
        return "wake"
    if any(tok in n for tok in _STAGE_ASLEEP_TOKENS):
        return "asleep"
    return "unknown"


def read_sleep(path: str, start_col: Optional[str] = None,
               end_col: Optional[str] = None,
               now: Optional[dt.datetime] = None) -> SleepData:
    header, rows, enc, delim = _read_rows(path)
    meta = ParseMeta(path=path, kind="수면", encoding=enc, delimiter=delim)

    si, spick = _pick_column(header, SLEEP_START_ALIASES, "수면 시작", start_col, path)
    ei, epick = _pick_column(header, SLEEP_END_ALIASES, "수면 종료", end_col, path,
                             exclude_idx=(si,))
    meta.columns = {"시작": spick, "종료": epick}

    # 단계 열(선택) — Apple 건강 export처럼 행이 수면 '단계'인 경우
    stage_i: Optional[int] = None
    stage_hits = [(i, h) for i, h in enumerate(header)
                  if _norm(h) in SLEEP_STAGE_ALIASES and i not in (si, ei)]
    if len(stage_hits) == 1:
        stage_i = stage_hits[0][0]
        meta.columns["단계"] = ColumnPick(stage_hits[0][1], "별칭")
    elif len(stage_hits) > 1:
        names = [h for _, h in stage_hits]
        raise CircadiaError(f"{path}: 수면 단계 열 후보가 여러 개입니다: {names}")

    raw_intervals: List[Tuple[dt.datetime, dt.datetime]] = []
    offsets: List[Optional[int]] = []
    excluded = meta.excluded
    unknown_labels = set()
    for r, row in enumerate(rows, start=2):
        if len(row) <= max(si, ei, stage_i or 0):
            excluded["열 수 부족"] = excluded.get("열 수 부족", 0) + 1
            continue
        meta.n_rows += 1
        if stage_i is not None:
            label = row[stage_i].strip()
            if label:
                cls = _classify_stage(label)
                if cls == "wake":
                    excluded["각성/침대(InBed·Awake) 단계 행"] = \
                        excluded.get("각성/침대(InBed·Awake) 단계 행", 0) + 1
                    continue
                if cls == "unknown":
                    unknown_labels.add(label)
                    continue
        s, so = parse_timestamp(row[si])
        e, eo = parse_timestamp(row[ei])
        if e <= s:
            raise CircadiaError(
                f"{path} {r}행: 수면 종료({e})가 시작({s})보다 빠르거나 같습니다 — "
                "기록 오류로 보여 거부합니다")
        if (e - s) > dt.timedelta(hours=24):
            raise CircadiaError(
                f"{path} {r}행: 24시간을 넘는 수면구간({s} → {e}) — 단위나 날짜 "
                "오류로 보여 거부합니다")
        raw_intervals.append((s, e))
        offsets.extend([so, eo])
    if unknown_labels:
        raise CircadiaError(
            f"{path}: 알 수 없는 수면 단계 라벨 {sorted(unknown_labels)} — 수면인지 "
            "각성인지 추측하지 않습니다. 라벨 의미를 확인해 각성 행을 지우거나 "
            "start,end 두 열짜리 파일로 만들어 주세요")
    if not raw_intervals:
        raise CircadiaError(f"{path}: 사용할 수 있는 수면구간이 없습니다 "
                            f"(제외 사유: {dict(excluded) or '없음'})")
    _check_offsets(offsets, path, meta)

    # 정렬 후 겹침/중복 병합 — Apple 단계 행은 연속 구간으로 잘게 쪼개져
    # 있으므로 '맞닿은'(gap 0) 구간도 병합한다. 병합 횟수는 자백.
    raw_intervals.sort()
    merged: List[Tuple[dt.datetime, dt.datetime]] = []
    n_merged = 0
    for s, e in raw_intervals:
        if merged and s <= merged[-1][1]:
            n_merged += 1
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    if n_merged:
        meta.notes.append(f"겹치거나 맞닿은 수면구간 {n_merged}건 병합")

    stamps = [s for s, _ in merged] + [merged[-1][1]]
    _validate_not_future(stamps, path, now=now)
    meta.n_used = len(merged)
    meta.first_ts, meta.last_ts = merged[0][0], merged[-1][1]
    return SleepData(merged, meta)


# ---------------------------------------------------------------------------
# 파일 간 시간대 일관성
# ---------------------------------------------------------------------------

def check_cross_file_offsets(metas: List[ParseMeta]) -> None:
    """서로 다른 '명시적' 오프셋을 가진 파일들은 같은 기록일 수 없다."""
    explicit = {m.offset_min for m in metas if m.offset_min is not None}
    if len(explicit) > 1:
        raise CircadiaError(
            "입력 파일들의 시간대 오프셋이 서로 다릅니다 — 같은 기록의 export가 "
            "아니거나 시간대가 섞였습니다. 확인 후 다시 넣어 주세요")

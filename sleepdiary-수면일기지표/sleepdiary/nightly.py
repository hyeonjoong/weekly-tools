"""1박(하룻밤) 단위 수면지표 계산.

Consensus Sleep Diary(Carney et al., Sleep 2012)의 표준 정의를 따른다.

    TIB  (Time in Bed)          = 잠자리에 든 시각 → 잠자리에서 나온 시각
    SPT  (lights-off → 최종기상) = 불 끈 시각 → 최종 기상 시각
    TST  (Total Sleep Time)     = SPT − SOL − WASO
    TWAK (Terminal Wakefulness) = 최종 기상 → 잠자리에서 나옴
    SE   (Sleep Efficiency, %)  = TST / TIB × 100
    수면중앙시각(midsleep)       = 입면시각과 최종기상 시각의 중점

모든 시각은 `timeparse`가 자정 기준 분으로 정규화하고, 구간은 시계 방향
경과시간으로 계산하므로 자정을 넘어가는 밤도 그대로 처리된다.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Optional

from .timeparse import (
    TimeParseError,
    forward_minutes,
    parse_clock,
    parse_duration_minutes,
)

# 계산은 하지만 "이건 좀 이상하다"고 표시할 임계값 (경고: 집계에는 포함)
WARN_TIB_MIN = 14 * 60
WARN_TWAK_MIN = 180
WARN_SOL_MIN = 240
WARN_WASO_MIN = 300
WARN_SE_LOW = 40.0
WARN_AWAKENINGS = 20

# 물리적으로 말이 안 되는 값 (오류: 집계에서 제외)
MAX_TIB_MIN = 18 * 60

_ISO_DATE = re.compile(r"^\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*$")


class Night:
    """한 밤의 원자료 + 파생지표 + 품질 플래그."""

    __slots__ = (
        "row_no", "subject", "date_raw", "date", "period",
        "bedtime", "lights_off", "sol", "waso", "awakenings",
        "final_awake", "out_of_bed",
        "tib", "spt", "tst", "twak", "se", "onset", "midsleep",
        "errors", "warnings",
    )

    def __init__(self, row_no: int, subject: str):
        self.row_no = row_no
        self.subject = subject
        self.date_raw: Optional[str] = None
        self.date: Optional[_dt.date] = None
        self.period: Optional[str] = None
        for name in ("bedtime", "lights_off", "sol", "waso", "awakenings",
                     "final_awake", "out_of_bed", "tib", "spt", "tst",
                     "twak", "se", "onset", "midsleep"):
            setattr(self, name, None)
        self.errors: list[str] = []
        self.warnings: list[str] = []

    @property
    def valid(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "row": self.row_no,
            "subject": self.subject,
            "date": self.date_raw,
            "period": self.period,
            "bedtime_min": self.bedtime,
            "lights_off_min": self.lights_off,
            "sol_min": self.sol,
            "waso_min": self.waso,
            "awakenings": self.awakenings,
            "final_awake_min": self.final_awake,
            "out_of_bed_min": self.out_of_bed,
            "tib_min": self.tib,
            "spt_min": self.spt,
            "tst_min": self.tst,
            "twak_min": self.twak,
            "se_pct": self.se,
            "onset_min": self.onset,
            "midsleep_min": self.midsleep,
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def parse_date(text: str) -> Optional[_dt.date]:
    """ISO 계열(YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD)만 날짜로 인정.

    MM/DD/YYYY 와 DD/MM/YYYY 는 구분할 수 없어 일부러 해석하지 않는다
    (잘못 찍힌 요일로 주말/평일 분석이 뒤집히는 것보다 낫다).
    """
    if text is None:
        return None
    m = _ISO_DATE.match(str(text))
    if not m:
        return None
    try:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _get(row: dict, key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    value = row.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def build_night(row: dict, cols: dict, row_no: int, *, date_means: str = "morning") -> Night:
    """CSV 한 줄 → `Night`.

    `cols`는 논리적 필드명 → 실제 열이름 매핑
    (subject, date, period, bedtime, lights_off, sol, waso, awakenings,
     final_awake, out_of_bed).
    """
    subject = _get(row, cols.get("subject")) or "(전체)"
    night = Night(row_no, subject)
    night.period = _get(row, cols.get("period"))

    night.date_raw = _get(row, cols.get("date"))
    night.date = parse_date(night.date_raw) if night.date_raw else None
    if night.date is not None and date_means == "morning":
        # 일기의 날짜가 '기상한 아침'이면 그 밤이 시작된 날은 하루 전이다.
        night.date -= _dt.timedelta(days=1)

    def clock(field: str) -> Optional[float]:
        raw = _get(row, cols.get(field))
        if raw is None:
            return None
        try:
            return parse_clock(raw)
        except TimeParseError as exc:
            night.errors.append(f"{field}: {exc}")
            return None

    def duration(field: str, default: Optional[float] = None) -> Optional[float]:
        raw = _get(row, cols.get(field))
        if raw is None:
            return default
        try:
            value = parse_duration_minutes(raw)
        except TimeParseError as exc:
            night.errors.append(f"{field}: {exc}")
            return None
        if value < 0:
            night.errors.append(f"{field}: 음수({value:g}분)는 있을 수 없습니다")
            return None
        return value

    night.lights_off = clock("lights_off")
    night.bedtime = clock("bedtime")
    if night.lights_off is None:
        night.lights_off = night.bedtime
    if night.bedtime is None:
        night.bedtime = night.lights_off

    night.final_awake = clock("final_awake")
    night.out_of_bed = clock("out_of_bed")
    if night.out_of_bed is None:
        night.out_of_bed = night.final_awake
    if night.final_awake is None:
        night.final_awake = night.out_of_bed

    night.sol = duration("sol", 0.0)
    night.waso = duration("waso", 0.0)

    raw_awk = _get(row, cols.get("awakenings"))
    if raw_awk is not None:
        try:
            night.awakenings = float(raw_awk)
            if night.awakenings < 0:
                night.warnings.append("각성횟수가 음수 — 무시")
                night.awakenings = None
        except ValueError:
            night.warnings.append(f"각성횟수를 숫자로 읽을 수 없음: {raw_awk!r}")

    missing = [name for name in ("lights_off", "final_awake", "out_of_bed")
               if getattr(night, name) is None]
    if missing:
        night.errors.append("필수 시각 누락: " + ", ".join(missing))
    if night.errors:
        return night

    _derive(night)
    return night


def _derive(night: Night) -> None:
    """시각/소요시간이 모두 준비된 밤에 대해 파생지표와 플래그를 채운다."""
    night.tib = forward_minutes(night.bedtime, night.out_of_bed)
    night.spt = forward_minutes(night.lights_off, night.final_awake)
    night.twak = forward_minutes(night.final_awake, night.out_of_bed)
    night.tst = night.spt - night.sol - night.waso
    night.onset = (night.lights_off + night.sol) % 1440.0
    night.midsleep = (night.onset + forward_minutes(night.onset, night.final_awake) / 2.0) % 1440.0

    if night.tib <= 0:
        night.errors.append("잠자리에 든 시각과 나온 시각이 같아 TIB=0")
    elif night.tib > MAX_TIB_MIN:
        night.errors.append(
            f"TIB {night.tib / 60:.1f}시간 — 시각이 뒤바뀌었거나 오기입으로 보입니다")
    if night.tst <= 0:
        night.errors.append(
            f"TST ≤ 0 (SPT {night.spt:.0f}분 < SOL {night.sol:.0f} + WASO {night.waso:.0f})")
    if night.errors:
        return

    night.se = night.tst / night.tib * 100.0
    if night.se > 100.0:
        night.errors.append(f"수면효율 {night.se:.1f}% — TST가 TIB보다 깁니다 (시각 오기입)")
        return

    if night.tib > WARN_TIB_MIN:
        night.warnings.append(f"TIB {night.tib / 60:.1f}시간 (비정상적으로 김)")
    if night.twak > WARN_TWAK_MIN:
        night.warnings.append(f"최종기상 후 {night.twak:.0f}분간 침대에 머묾")
    if night.sol > WARN_SOL_MIN:
        night.warnings.append(f"입면잠복기 {night.sol:.0f}분")
    if night.waso > WARN_WASO_MIN:
        night.warnings.append(f"WASO {night.waso:.0f}분")
    if night.se < WARN_SE_LOW:
        night.warnings.append(f"수면효율 {night.se:.1f}%")
    if night.awakenings is not None and night.awakenings > WARN_AWAKENINGS:
        night.warnings.append(f"각성 {night.awakenings:.0f}회")

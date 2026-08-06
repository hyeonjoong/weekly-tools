"""1박(하룻밤) 단위 수면지표 계산.

문항 정의는 Consensus Sleep Diary(Carney et al., Sleep 2012)를 따르되,
아래 파생지표 산식은 이 모듈의 운용적 정의다 (CSD는 문항 표준이지 파생변수
계산법을 정해 주는 문서가 아니다).

    TIB  (Time in Bed)          = 잠자리에 든 시각 → 잠자리에서 나온 시각
    SPT  (수면기회시간)          = 불 끈 시각 → 최종 기상 시각
                                  ※ 관례적 SPT(입면→최종기상)와 달리 SOL을 포함한다
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
        "errors", "warnings", "imputed",
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
        # 원자료에 없어서 0(또는 다른 열)으로 채운 항목 이름들.
        # 어떤 밤이 채워졌는지 산출물에서 확인할 수 있어야 한다.
        self.imputed: list[str] = []

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
            "imputed": list(self.imputed),
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


# 엑셀 파일을 이어 붙이면 값 안에 제로폭 문자나 중간 BOM이 섞여 들어온다.
# 그대로 두면 "S01"과 "S01\u200b"이 서로 다른 대상자가 되어 n이 조용히 늘어난다.
_INVISIBLE = "\ufeff\u200b\u200c\u200d\u00a0\u2060"


def _get(row: dict, key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    value = row.get(key)
    if value is None:
        return None
    text = str(value).strip().strip(_INVISIBLE).strip()
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
            if default is not None:
                night.imputed.append(
                    field if cols.get(field) else field + "(열없음)")
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
    if night.lights_off is None and night.bedtime is not None:
        night.lights_off = night.bedtime
        night.imputed.append("lights_off←bedtime")
    if night.bedtime is None and night.lights_off is not None:
        # 취침시각을 소등시각으로 대신하면 TIB가 짧아져 SE가 높게 나온다.
        night.bedtime = night.lights_off
        night.imputed.append("bedtime←lights_off")

    night.final_awake = clock("final_awake")
    night.out_of_bed = clock("out_of_bed")
    if night.out_of_bed is None and night.final_awake is not None:
        # 침대에서 나온 시각을 최종기상으로 대신하면 TWAK=0, TIB가 짧아져
        # 역시 SE가 높게 나온다.
        night.out_of_bed = night.final_awake
        night.imputed.append("out_of_bed←final_awake")
        if cols.get("out_of_bed") and cols["out_of_bed"] != cols.get("final_awake"):
            # 열은 있는데 이 행만 비었다 = 이 밤만 누락. TIB가 짧아지므로 알린다.
            night.warnings.append(
                "침대에서 나온 시각이 비어 최종기상으로 대체 — TIB가 짧아져 SE가 높아집니다")
    if night.final_awake is None and night.out_of_bed is not None:
        night.final_awake = night.out_of_bed
        night.imputed.append("final_awake←out_of_bed")

    # 열 자체가 없으면 "측정하지 않음", 열은 있는데 칸이 비었으면 "미기입".
    # 어느 쪽이든 계산은 0으로 하되 구분해서 기록한다 — 0을 넣은 사실이
    # 산출물에 남지 않으면 SE가 왜 높은지 나중에 아무도 알 수 없다.
    night.sol = duration("sol", 0.0)
    night.waso = duration("waso", 0.0)

    raw_awk = _get(row, cols.get("awakenings"))
    if raw_awk is not None:
        try:
            night.awakenings = float(raw_awk)
            if not (night.awakenings == night.awakenings          # NaN 배제
                    and abs(night.awakenings) != float("inf")):
                night.warnings.append(f"각성횟수가 수가 아님: {raw_awk!r}")
                night.awakenings = None
            elif night.awakenings < 0:
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
    # 네 시각은 반드시 잠자리 → 소등 → 최종기상 → 침대에서 나옴 순서로 놓여야
    # 하며, 그렇다면 각 구간의 합이 TIB와 정확히 같다. 순서가 어긋나면 어떤
    # 구간이 자정을 한 바퀴 돌아 TWAK가 23시간이 되거나 SPT가 TIB를 넘는다.
    # `SPT > TIB` 만 보면 (예: 취침 02:00 / 소등 01:00 처럼) 순서가 뒤집혔는데도
    # 합이 우연히 맞아떨어지는 밤을 놓치므로, 포함관계 자체를 검사한다.
    elif abs(forward_minutes(night.bedtime, night.lights_off)
             + night.spt + night.twak - night.tib) > 1e-6:
        night.errors.append(
            "시각 순서 불일치 — 잠자리에 든 시각 → 소등 → 최종기상 → 침대에서 나온 "
            f"시각 순서가 아닙니다 (TIB {night.tib:.0f}분, SPT {night.spt:.0f}분, "
            f"TWAK {night.twak:.0f}분)")
    if night.tst <= 0:
        night.errors.append(
            f"TST ≤ 0 (SPT {night.spt:.0f}분 < SOL {night.sol:.0f} + WASO {night.waso:.0f})")
    if night.errors:
        return

    night.se = night.tst / night.tib * 100.0
    # 포함관계 검사를 통과하면 TST ≤ SPT ≤ TIB 이므로 여기 걸릴 일은 사실상
    # 없지만, 부동소수점 여유분으로 100%를 스칠 때를 대비한 마지막 방어선이다.
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

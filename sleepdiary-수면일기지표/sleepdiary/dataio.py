"""CSV 읽기 + 열 자동인식.

실제 수면일기 CSV는 한글/영문 열이름이 섞이고, 엑셀에서 저장되어 cp949이거나
BOM이 붙는 일이 흔하다. 여기서는 인코딩을 순서대로 시도하고, 열이름을
정규화해 표준 필드로 매핑한다. **모호하면 추측하지 않고 오류를 낸다** —
잘못 매핑된 열로 계산된 수면효율이 조용히 논문에 들어가는 것보다 낫다.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Iterable, Optional

ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr", "latin-1")

# 논리필드 → 열이름 후보 (정규화된 형태: 소문자, 영숫자/한글만)
ALIASES: dict[str, tuple[str, ...]] = {
    "subject": ("subject", "subjectid", "subjid", "subject명", "id", "participant",
                "participantid", "pid", "record", "recordid", "대상자", "대상자id",
                "피험자", "피험자번호", "참가자", "환자", "환자id", "아이디"),
    "date": ("date", "diarydate", "recorddate", "day", "날짜", "일자", "기록일"),
    "period": ("period", "phase", "visit", "timepoint", "condition", "arm", "group",
               "stage", "시기", "구분", "차수", "방문", "군"),
    "bedtime": ("bedtime", "timetobed", "intobed", "gotobed", "bed", "취침시각",
                "취침시간", "잠자리든시각", "잠자리에든시각"),
    "lights_off": ("lightsoff", "lightsout", "lightoff", "trytosleep", "sleepattempt",
                   "attemptsleep", "소등시각", "소등", "불끈시각", "잠자려한시각"),
    "sol": ("sol", "sleeplatency", "sleeponsetlatency", "latency", "onsetlatency",
            "minutestofallasleep", "입면잠복기", "잠드는데걸린시간", "입면시간"),
    "waso": ("waso", "wakeaftersleeponset", "wakeafteronset", "awakeminutes",
             "minutesawake", "nightwakeminutes", "중도각성시간", "중간에깬시간",
             "깨어있던시간", "야간각성시간"),
    "awakenings": ("awakenings", "nawakenings", "numawakenings", "numberofawakenings",
                   "awakeningcount", "nwake", "각성횟수", "깬횟수", "중도각성횟수"),
    "final_awake": ("finalawake", "finalawakening", "finalwake", "waketime",
                    "wakeuptime", "awaketime", "timewokeup", "최종기상시각",
                    "최종각성시각", "기상시각", "깬시각"),
    "out_of_bed": ("outofbed", "getup", "getuptime", "gotup", "risetime", "rise",
                   "timeoutofbed", "leftbed", "기상후침대에서나온시각",
                   "침대에서나온시각", "일어난시각", "기상후일어난시각"),
}

REQUIRED = ("lights_off", "final_awake", "out_of_bed")
_NORM = re.compile(r"[^0-9a-z가-힣]")


class DataError(Exception):
    """읽기/매핑 실패."""


def normalize(name: str) -> str:
    return _NORM.sub("", str(name).strip().lower())


def read_csv(path: str) -> tuple[list[dict], list[str], str]:
    """CSV → (행 리스트, 원래 열이름, 사용된 인코딩).

    구분자는 `,` `\\t` `;` `|` 중에서 자동 추정한다.
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise DataError(f"파일을 열 수 없습니다: {exc}") from exc
    if not raw.strip():
        raise DataError("빈 파일입니다")

    text = None
    used = ENCODINGS[-1]
    for enc in ENCODINGS:
        try:
            text = raw.decode(enc)
            used = enc
            break
        except UnicodeDecodeError:
            continue
    if text is None:  # pragma: no cover - latin-1은 모든 바이트를 받는다
        raise DataError("인코딩을 판별할 수 없습니다")

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise DataError("헤더 행이 없습니다")
    fieldnames = [f.strip() if f else "" for f in reader.fieldnames]

    rows = []
    for row in reader:
        # 빈 줄(모든 값이 공백)은 건너뛴다.
        if all(v is None or str(v).strip() == "" for v in row.values()):
            continue
        rows.append({(k.strip() if k else ""): v for k, v in row.items()})
    if not rows:
        raise DataError("데이터 행이 없습니다 (헤더만 있음)")
    return rows, fieldnames, used


def resolve_columns(fieldnames: Iterable[str], overrides: dict) -> dict:
    """열이름 목록 + 사용자가 지정한 열 → 논리필드 매핑.

    사용자가 `--sol` 등으로 직접 지정한 열이 항상 우선한다.
    자동인식에서 한 필드에 후보가 2개 이상이면 오류 (조용한 오매핑 방지).
    """
    names = [f for f in fieldnames if f]
    by_norm: dict[str, list[str]] = {}
    for name in names:
        by_norm.setdefault(normalize(name), []).append(name)

    cols: dict[str, Optional[str]] = {}
    taken: set[str] = set()

    for field, given in overrides.items():
        if not given:
            continue
        if given not in names:
            raise DataError(
                f"--{field.replace('_', '-')} 로 지정한 열 '{given}' 이(가) 없습니다. "
                f"사용 가능한 열: {', '.join(names)}")
        cols[field] = given
        taken.add(given)

    for field, aliases in ALIASES.items():
        if cols.get(field):
            continue
        hits = []
        for alias in aliases:
            for name in by_norm.get(alias, []):
                if name not in taken and name not in hits:
                    hits.append(name)
        if len(hits) > 1:
            raise DataError(
                f"'{field}' 후보 열이 여러 개입니다: {', '.join(hits)}. "
                f"--{field.replace('_', '-')} 로 직접 지정하세요.")
        cols[field] = hits[0] if hits else None
        if hits:
            taken.add(hits[0])

    # lights_off 가 없으면 bedtime 으로 대체 (많은 일기가 한 시각만 적는다).
    if not cols.get("lights_off") and cols.get("bedtime"):
        cols["lights_off"] = cols["bedtime"]
    if not cols.get("bedtime") and cols.get("lights_off"):
        cols["bedtime"] = cols["lights_off"]
    if not cols.get("out_of_bed") and cols.get("final_awake"):
        cols["out_of_bed"] = cols["final_awake"]
    if not cols.get("final_awake") and cols.get("out_of_bed"):
        cols["final_awake"] = cols["out_of_bed"]

    missing = [f for f in REQUIRED if not cols.get(f)]
    if missing:
        raise DataError(
            "필수 열을 찾지 못했습니다: " + ", ".join(missing) +
            f"\n  발견된 열: {', '.join(names)}"
            "\n  --lights-off / --final-awake / --out-of-bed 로 직접 지정하세요.")
    return cols


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def sanitize_cell(value) -> str:
    """CSV 수식 주입 방지 — 숫자가 아닌데 =,+,-,@ 로 시작하면 앞에 '를 붙인다."""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    text = str(value)
    if text[:1] in _FORMULA_START:
        try:
            float(text)
        except ValueError:
            return "'" + text
    return text

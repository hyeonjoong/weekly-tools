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

# latin-1은 어떤 바이트열이든 받아들이므로 "성공"해도 오독일 수 있다.
# UTF-16은 BOM으로 먼저 가려낸다 (엑셀의 '유니코드 텍스트' 저장 형식).
_UTF16_BOMS = ((b"\xff\xfe\x00\x00", "utf-32"), (b"\x00\x00\xfe\xff", "utf-32"),
               (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16"))

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


# "sleep_latency_min", "WASO (분)", "취침_시각" 처럼 단위·형식 꼬리표가 붙은
# 열이름이 흔하다. 꼬리표만 다른 경우까지 인식하되, 후보가 둘 이상이면 여전히
# 오류를 낸다 (조용한 오매핑 방지).
_UNIT_SUFFIX = re.compile("(?:mins?|minutes?|hrs?|hours?|hhmm|clock)+$")


def strip_unit(norm: str) -> str:
    """정규화된 이름에서 단위 꼬리표를 뗀다. 다 떼서 비면 원래 값을 쓴다."""
    stripped = _UNIT_SUFFIX.sub("", norm)
    return stripped or norm


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
    for bom, enc in _UTF16_BOMS:
        if raw.startswith(bom):
            try:
                text, used = raw.decode(enc), enc
            except (UnicodeDecodeError, LookupError):
                raise DataError(
                    f"{enc} 파일로 보이는데 읽지 못했습니다. 엑셀에서 "
                    "'CSV UTF-8'로 다시 저장해 주세요.")
            break
    for enc in ENCODINGS if text is None else ():
        try:
            text = raw.decode(enc)
            used = enc
            break
        except UnicodeDecodeError:
            continue
    if text is None:  # pragma: no cover - latin-1은 모든 바이트를 받는다
        raise DataError("인코딩을 판별할 수 없습니다")

    # 옛 Mac 엑셀은 줄 끝을 CR 하나로 쓴다. StringIO 기본값(newline='\n')으로는
    # csv 모듈이 "new-line character seen in unquoted field"로 죽는다.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        if not reader.fieldnames:
            raise DataError("헤더 행이 없습니다")
        fieldnames = [f.strip() if f else "" for f in reader.fieldnames]

        # 이름이 같은 열이 둘 있으면 csv.DictReader가 뒤엣것만 남긴다. 앞의 열에
        # 든 진짜 값이 조용히 사라지므로(예: SOL 45분이 빈칸으로 바뀜) 거부한다.
        seen = [f for f in fieldnames if f]
        dupes = sorted({f for f in seen if seen.count(f) > 1})
        if dupes:
            raise DataError(
                "열 이름이 중복됩니다: " + ", ".join(dupes) +
                "\n  같은 이름의 열이 둘이면 뒤엣것만 읽히고 앞엣것의 값은 사라집니다."
                "\n  엑셀에서 열 이름을 서로 다르게 고친 뒤 다시 저장하세요.")

        rows = []
        for row in reader:
            # 빈 줄(모든 값이 공백)은 건너뛴다.
            if all(v is None or str(v).strip() == "" for v in row.values()):
                continue
            rows.append({(k.strip() if k else ""): v for k, v in row.items()})
    except csv.Error as exc:
        raise DataError(
            f"CSV를 읽는 중 오류가 났습니다: {exc}\n"
            "  따옴표가 닫히지 않았거나 줄 끝이 깨진 파일일 수 있습니다.") from exc
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
    by_stripped: dict[str, list[str]] = {}
    for name in names:
        norm = normalize(name)
        by_norm.setdefault(norm, []).append(name)
        by_stripped.setdefault(strip_unit(norm), []).append(name)

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
        # 2차: 단위 꼬리표를 뗀 형태끼리 비교 ("sleep_latency_min" ↔ "sleeplatency").
        # 1차에서 이미 찾았더라도 **반드시 함께** 모아야 한다. 그러지 않으면
        # 'latency' 와 'sleep_latency_min' 이 나란히 있을 때 1차가 먼저 성공해
        # 나머지 후보를 조용히 버리게 된다 — 바로 이 도구가 막겠다고 한 오매핑이다.
        for alias in aliases:
            for name in by_stripped.get(strip_unit(alias), []):
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
    # 앞의 공백을 무시하고 판정한다 — 엑셀은 " =1+1" 도 수식으로 읽는다.
    if text.lstrip("\u00a0 \t\r\n")[:1] in _FORMULA_START:
        try:
            float(text)
        except ValueError:
            return "'" + text
    return text

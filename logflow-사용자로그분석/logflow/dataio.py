"""이벤트 로그 로딩(CSV/JSONL, gzip 가능)과 타임스탬프 파싱.

이벤트 로그는 최소 3개 열을 가진다고 가정한다: 사용자 ID, 이벤트 이름, 타임스탬프.
선택적으로 군(arm/group) 열을 더 줄 수 있다. 열 이름은 CLI에서 바꿀 수 있다.
타임스탬프는 ISO-8601 문자열이나 epoch(초/밀리초)을 모두 받아들인다.

입력 형식은 확장자로 자동 판별한다: `.jsonl`/`.ndjson` 은 한 줄에 JSON 객체 하나,
그 외는 CSV. 어느 쪽이든 `.gz` 로 압축돼 있으면 투명하게 푼다(내용의 gzip 매직바이트로
확인하므로 확장자가 없어도 동작).
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

# 결측을 뜻하는 흔한 토큰 (pandas/엑셀 CSV 내보내기에서 자주 나옴). 이 값들은 빈 칸처럼 건너뛴다.
NULL_TOKENS = {"", "nan", "null", "none", "na", "n/a", "#n/a"}

# 구분자 자동감지에 시도할 후보들 (엑셀 유럽 로케일의 ';', TSV 의 '\t' 등).
_DELIM_CANDIDATES = ",;\t|"

# gzip 파일의 매직바이트.
_GZIP_MAGIC = b"\x1f\x8b"

# 지원하는 입력 형식.
INPUT_FORMATS = ("auto", "csv", "jsonl")

# JSONL 열 이름을 잡을 때 훑어볼 앞부분 줄 수 (줄마다 키가 다를 수 있으므로).
_JSONL_PEEK_LINES = 200


@dataclass(frozen=True)
class Event:
    """로그 한 줄: 누가(user), 무엇을(name), 언제(ts), (선택) 어느 군(group)."""

    user: str
    name: str
    ts: datetime
    group: Optional[str] = None


def parse_timestamp(raw: str) -> datetime:
    """타임스탬프 문자열을 tz-naive(UTC 기준) datetime으로 변환.

    지원 형식:
      - epoch 초     : "1735718400"
      - epoch 밀리초 : "1735718400000"
      - ISO-8601     : "2025-01-01T08:00:00", "2025-01-01 08:00:00",
                       "2025-01-01T08:00:00Z", "2025-01-01T08:00:00+09:00"

    오프셋이 있으면 UTC로 변환한 뒤 tzinfo를 제거해 비교를 단순화한다.
    """
    s = (raw or "").strip()
    if not s:
        raise ValueError("빈 타임스탬프")

    # epoch (정수/소수, 부호 없음으로 가정)
    if _looks_numeric(s):
        value = float(s)
        # 초(~1.7e9) vs 밀리초(~1.7e12) 구분: 1e11 이상이면 밀리초로 본다.
        if abs(value) >= 1e11:
            value /= 1000.0
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)

    iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _looks_numeric(s: str) -> bool:
    if not s:
        return False
    # 부호 없는 숫자 + 소수점 한 개까지만 epoch로 취급 (날짜의 '-'/':', 음수 epoch는 제외).
    # 음수/부호 붙은 값은 epoch로 보지 않으므로 아래 ISO 파싱에서 명확한 오류가 난다.
    return s.replace(".", "", 1).isdigit()


def load_events(
    path: str,
    user_col: str = "user_id",
    event_col: str = "event",
    time_col: str = "timestamp",
    encoding: str = "utf-8-sig",
    tz_offset_hours: float = 0.0,
    skip_bad_rows: bool = False,
    counters: Optional[Dict[str, int]] = None,
    delimiter: Optional[str] = None,
    dedup: bool = False,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    group_col: Optional[str] = None,
    input_format: str = "auto",
    max_rows: Optional[int] = None,
) -> List[Event]:
    """CSV/JSONL 파일을 읽어 Event 리스트로 반환 (타임스탬프 오름차순 정렬).

    - tz_offset_hours: 파싱된 시각에 더할 시간(시 단위). 날짜 버킷팅이 UTC가 아니라
      현지시각 기준이 되도록 보정할 때 쓴다. 예: 로그가 UTC인데 KST(+9) 기준으로
      날짜를 끊고 싶으면 9 를 준다.
    - skip_bad_rows: True 면 파싱 불가한 타임스탬프 행을 오류 없이 건너뛴다(기본은 오류).
    - counters: 주어지면 {'skipped_missing','skipped_bad','deduped','filtered'} 카운트를 채운다.
    - delimiter: None(기본)이면 구분자를 자동감지(',', ';', 탭, '|')한다. 지정하면 그대로.
    - dedup: True 면 (user, event, ts) 가 완전히 같은 중복 행을 하나만 남긴다.
    - date_from/date_to: (tz 보정 후) 이 달력 날짜 구간[포함] 밖의 이벤트를 제외한다.
    - group_col: 주어지면 그 열을 군(arm/그룹) 라벨로 읽어 Event.group 에 담는다.
      값이 비었거나 결측 토큰이면 group=None (해당 행은 버리지 않는다).
    - input_format: "auto"(기본, 확장자로 판별) / "csv" / "jsonl".
      `.gz` 압축은 내용의 gzip 매직바이트로 감지해 투명하게 푼다.
    - max_rows: 유효 이벤트가 이 수를 넘으면 오류로 중단한다(기본 None = 제한 없음).
      압축 로그는 작아 보여도 풀면 수십 배가 되므로, 메모리를 소진하기 전에 멈추는 장치.

    열 이름은 앞뒤 공백을 무시하고 매칭하며, 정확히 못 찾으면 대소문자 무시로 재시도한다.
    필수 열이 없으면 명확한 오류를 던진다.
    """
    if counters is None:
        counters = {}
    for key in ("skipped_missing", "skipped_bad", "deduped", "filtered"):
        counters.setdefault(key, 0)
    if input_format not in INPUT_FORMATS:
        raise ValueError(
            f"input_format 은 {INPUT_FORMATS} 중 하나여야 합니다 (받은 값: {input_format!r})"
        )
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError(f"date_from({date_from}) 이 date_to({date_to}) 보다 늦습니다")
    shift = timedelta(hours=tz_offset_hours)

    needed = [user_col, event_col, time_col]
    if group_col:
        needed.append(group_col)

    with _open_text(path, encoding) as fh:
        fmt = input_format if input_format != "auto" else _detect_format(path, fh)
        if fmt == "jsonl":
            rows, fieldnames = _jsonl_reader(fh, path, skip_bad_rows, counters)
        else:
            sample = fh.read(8192)
            fh.seek(0)
            delim = delimiter or _detect_delimiter(sample)
            reader = csv.DictReader(fh, delimiter=delim)
            if reader.fieldnames is None:
                raise ValueError(f"빈 파일이거나 헤더가 없습니다: {path}")
            fieldnames = reader.fieldnames
            rows = enumerate(reader, start=2)  # 2 = 헤더 다음 첫 데이터 행
        colmap = _resolve_columns(fieldnames, needed, path, jsonl_peeked=(fmt == "jsonl"))
        events = _rows_to_events(
            rows, colmap[user_col], colmap[event_col], colmap[time_col],
            shift, skip_bad_rows, counters, date_from, date_to,
            colmap.get(group_col) if group_col else None, max_rows,
        )

    if dedup:
        events = _dedup(events, counters)
    if not events:
        raise ValueError(f"유효한 데이터 행이 없습니다: {path}")
    events.sort(key=lambda e: (e.ts, e.user))
    return events


def _open_text(path: str, encoding: str):
    """텍스트 핸들을 연다. 내용이 gzip 이면(매직바이트) 투명하게 푼다.

    확장자가 아니라 내용으로 판정하므로 `.gz` 가 아닌 이름의 압축 파일도 읽힌다.
    반환 핸들은 `seek(0)` 가능해야 하므로 gzip 도 `GzipFile` 을 감싼 TextIOWrapper 로 준다.
    """
    with open(path, "rb") as probe:
        magic = probe.read(2)
    if magic == _GZIP_MAGIC:
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding=encoding, newline="")
    return open(path, "r", newline="", encoding=encoding)


def _detect_format(path: str, fh=None) -> str:
    """입력 형식 판별: 확장자 우선, 애매하면 내용의 첫 글자로.

    `.gz` 는 벗겨내고 본다. `.jsonl`/`.ndjson` 이면 JSONL. 그 밖의 이름(예: 압축된
    로그를 그냥 `log.csv.gz` 로 저장한 경우)이라도 첫 비공백 문자가 '{' 이면 JSONL 로
    본다 — 그러지 않으면 JSON 조각이 열 이름으로 잡힌 엉뚱한 오류가 난다.
    """
    name = path.lower()
    if name.endswith(".gz"):
        name = name[:-3]
    if name.endswith((".jsonl", ".ndjson")):
        return "jsonl"
    if fh is not None:
        try:
            head = fh.read(4096)
            fh.seek(0)
        except (OSError, ValueError):
            return "csv"
        stripped = head.lstrip()
        if stripped.startswith("{"):
            return "jsonl"
    return "csv"


def _jsonl_reader(
    fh, path: str, skip_bad_rows: bool, counters: Dict[str, int]
) -> Tuple[Iterator[Tuple[int, dict]], List[str]]:
    """JSONL(한 줄에 JSON 객체 하나)을 (행번호, dict) 스트림과 열 이름 목록으로.

    열 이름은 앞쪽 최대 `_JSONL_PEEK_LINES` 줄의 키 합집합으로 잡는다 — JSONL 은
    줄마다 키가 달라질 수 있어 첫 줄만 보면 있는 열을 놓칠 수 있기 때문이다.
    빈 줄은 건너뛴다. 파싱 실패/객체가 아닌 줄은 skip_bad_rows 면 건너뛰고
    아니면 행 번호가 담긴 오류를 던진다.
    """
    peeked: List[Tuple[int, dict]] = []
    fieldnames: List[str] = []
    seen_keys = set()
    lines = enumerate(fh, start=1)

    def parse(lineno: int, line: str) -> Optional[dict]:
        if not line.strip():
            return None
        try:
            obj = json.loads(line)
        except ValueError as exc:
            if skip_bad_rows:
                counters["skipped_bad"] += 1
                return None
            raise ValueError(
                f"{lineno}행 JSON 파싱 실패: {exc}. "
                f"손상된 행을 건너뛰려면 --skip-bad-rows 를 쓰세요."
            ) from exc
        if not isinstance(obj, dict):
            if skip_bad_rows:
                counters["skipped_bad"] += 1
                return None
            raise ValueError(
                f"{lineno}행이 JSON 객체가 아닙니다 (한 줄에 객체 하나여야 함): {line.strip()[:60]!r}"
            )
        return obj

    for lineno, line in lines:
        obj = parse(lineno, line)
        if obj is None:
            continue
        peeked.append((lineno, obj))
        for k in obj:
            if k not in seen_keys:
                seen_keys.add(k)
                fieldnames.append(k)
        if len(peeked) >= _JSONL_PEEK_LINES:
            break
    if not fieldnames:
        raise ValueError(f"빈 파일이거나 JSON 객체가 없습니다: {path}")

    def stream() -> Iterator[Tuple[int, dict]]:
        yield from peeked
        for lineno, line in lines:
            obj = parse(lineno, line)
            if obj is not None:
                yield lineno, obj

    return stream(), fieldnames


def _detect_delimiter(sample: str) -> str:
    """헤더/본문 샘플에서 구분자를 추정. 실패하면 콤마."""
    if not sample:
        return ","
    try:
        return csv.Sniffer().sniff(sample, delimiters=_DELIM_CANDIDATES).delimiter
    except csv.Error:
        # 첫 줄에서 가장 많이 등장하는 후보를 고른다 (동률이면 콤마 우선).
        first = sample.splitlines()[0] if sample.splitlines() else ""
        best, best_n = ",", 0
        for cand in ",;\t|":
            n = first.count(cand)
            if n > best_n:
                best, best_n = cand, n
        return best


def _resolve_columns(
    fieldnames: Iterable[str], needed: Iterable[str], path: str,
    jsonl_peeked: bool = False,
) -> Dict[str, str]:
    """요청한 열 이름을 실제 헤더 키로 매핑. 공백/대소문자 관용 매칭.

    반환: {요청이름: 실제헤더키}. 못 찾은 열이 있으면 오류.
    """
    actual = list(fieldnames)
    # 여러 헤더가 공백/대소문자만 다른 경우 모호하므로 후보를 모아 둔다.
    strip_groups: Dict[str, List[str]] = {}
    lower_groups: Dict[str, List[str]] = {}
    for fn in actual:
        strip_groups.setdefault((fn or "").strip(), []).append(fn)
        lower_groups.setdefault((fn or "").strip().lower(), []).append(fn)
    resolved: Dict[str, str] = {}
    missing = []
    for want in needed:
        w = want.strip()
        if w in strip_groups:
            group = strip_groups[w]
        elif w.lower() in lower_groups:
            group = lower_groups[w.lower()]
        else:
            missing.append(want)
            continue
        if len(group) > 1:
            raise ValueError(
                f"열 이름이 모호합니다: {want!r} 에 대응하는 헤더가 여러 개입니다 "
                f"({group}). 헤더의 공백/대소문자를 정리하거나 정확한 열 이름을 지정하세요."
            )
        resolved[want] = group[0]
    if missing:
        shown = sorted((fn or "").strip() for fn in actual)
        hint = (
            f" (JSONL 은 앞 {_JSONL_PEEK_LINES}줄에서만 열 이름을 찾습니다 — "
            f"그 뒤에만 나오는 열은 인식되지 않습니다)"
            if jsonl_peeked
            else ""
        )
        raise ValueError(
            f"필수 열이 없습니다: {missing} (파일 {path} 의 열: {shown}){hint}"
        )
    return resolved


def _cell(value) -> str:
    """행의 셀 값을 문자열로 정규화 (JSONL 은 숫자/불리언/None 이 그대로 올 수 있음).

    CSV DictReader 는 값이 없으면 None, 열이 남으면 list 를 주므로 둘 다 방어한다.
    float 는 `repr` 이 아니라 정수형이면 정수 표기로 — epoch 초가 1.7e9 로 뭉개지지 않게.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return "nan"  # NaN/Inf → 결측 토큰으로 흘려보내 행을 건너뛰게 한다
        if value == int(value):
            return str(int(value))
        return format(value, ".6f").rstrip("0").rstrip(".")
    if isinstance(value, (list, tuple, dict)):
        # 여분 필드(csv restkey)나 중첩 JSON 객체 — 파이썬 repr 이 리포트에 새지 않도록
        # 값으로 쓰지 않고 결측 처리한다.
        return ""
    return str(value).strip()


def _rows_to_events(
    rows, user_col, event_col, time_col, shift, skip_bad_rows, counters,
    date_from, date_to, group_col=None, max_rows=None,
) -> List[Event]:
    events: List[Event] = []
    for i, row in rows:
        user = _cell(row.get(user_col))
        name = _cell(row.get(event_col))
        raw_ts = _cell(row.get(time_col))
        # 핵심 값이 비었거나 결측 토큰(nan/null/...)이면 그 행은 건너뛴다 (로그에는 흔함).
        if (
            user.lower() in NULL_TOKENS
            or name.lower() in NULL_TOKENS
            or raw_ts.lower() in NULL_TOKENS
        ):
            counters["skipped_missing"] += 1
            continue
        try:
            ts = parse_timestamp(raw_ts) + shift
        except (ValueError, OSError, OverflowError) as exc:
            if skip_bad_rows:
                counters["skipped_bad"] += 1
                continue
            raise ValueError(
                f"{i}행 타임스탬프 파싱 실패 ({raw_ts!r}): {exc}. "
                f"손상된 행을 건너뛰려면 --skip-bad-rows 를 쓰세요."
            ) from exc
        d = ts.date()
        if (date_from is not None and d < date_from) or (
            date_to is not None and d > date_to
        ):
            counters["filtered"] += 1
            continue
        group = None
        if group_col is not None:
            g = _cell(row.get(group_col))
            # 군 라벨이 비었으면 그 행만 군 미상으로 둔다 (행 자체는 버리지 않는다).
            group = None if g.lower() in NULL_TOKENS else g
        events.append(Event(user=user, name=name, ts=ts, group=group))
        if max_rows is not None and len(events) > max_rows:
            raise ValueError(
                f"이벤트가 --max-rows({max_rows})를 넘었습니다 ({i}행에서 중단). "
                f"한도를 올리거나 --from/--to 로 기간을 좁히세요."
            )
    return events


def _dedup(events: List[Event], counters: Dict[str, int]) -> List[Event]:
    """완전 중복 행 제거. 군(group) 라벨까지 키에 포함한다.

    (user, event, ts) 만 키로 쓰면 군 라벨만 다른 두 행 — 즉 배정이 서로 어긋난다는
    증거 — 이 '중복' 으로 조용히 사라져 충돌 경고까지 함께 없어진다. 그런 행은 중복이
    아니라 모순이므로 남겨 두고 군 비교에서 경고하게 한다.
    """
    seen = set()
    out: List[Event] = []
    for e in events:
        key = (e.user, e.name, e.ts, e.group)
        if key in seen:
            counters["deduped"] += 1
            continue
        seen.add(key)
        out.append(e)
    return out

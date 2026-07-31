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

단위 자동 감지:
  1) 열 이름에 단위가 명시돼 있으면 그것을 신뢰 ("rr_ms"→ms, "rr_s"→s, "hr_bpm"→bpm)
  2) 없으면 값의 중앙값으로 추정
       - 중앙값 < 10      → 초(s)   → ×1000 하여 ms
       - 중앙값 < 300     → 분당 심박수(bpm) → 60000/x 하여 ms
       - 그 외            → 밀리초(ms)
`--unit ms|s|bpm` 로 수동 지정할 수 있습니다. 이름과 중앙값 규칙이 엇갈리면
경고합니다(단위 오라벨 익스포트 탐지).

지저분한 실측 CSV에 대한 강건성(robustness):
  - 인코딩: UTF-16(BOM 판별) → UTF-8(BOM) → cp949/euc-kr → latin-1 폴백
    (한국·윈도우 환경 임상 CSV 대응).
  - 구분자 자동 감지: 쉼표(,)·세미콜론(;)·탭·파이프(|).
  - 숫자 표기: 파일 로케일로 해석 — 유럽식 파일이면 쉼표는 항상 소수점("0,803"→0.803,
    "1.234,5"→1234.5), 쉼표 구분 파일이면 따옴표 안의 "1,010"→1010(천단위).
    inf/nan/1e400 같은 비유한 값은 버립니다.
  - 주석: '#' 로 시작하는 줄은 건너뜁니다(Polar·Kubios 메타 헤더).
  - 값 열 추정: 헤더를 토큰으로 쪼개 **정확 일치**로 점수화합니다(부분일치 금지 —
    "valid"가 "val"로, "annotation"이 "nn"으로 잡히던 오답 방지). 한글 헤더도
    토큰화되며, 어떤 열도 완전히 배제하지 않고(그러면 시간축이 뽑히는 더 나쁜 일이
    생김) 점수가 같을 때만 생리적으로 그럴듯한 열을 고릅니다.
  - 박동 발생시각 입력: --timestamps(beat_times=True) 로 누적 발생시각 열을
    차분하여 RR을 계산.
"""

from __future__ import annotations

import csv
import math
import os
import re
import statistics
from typing import List, Optional, Sequence, Tuple

__all__ = ["parse_float", "load_series", "to_rr_ms", "detect_unit",
           "load_manifest", "load_group_manifest"]

_NA_LABELS = {"NA", "N/A", "NAN", "NULL", ".", "-", "--", "?", "NONE"}

# 값 열/시간 열 추정용 키워드. **토큰 단위 정확 일치**로만 씁니다.
#
# 왜 부분일치(substring)를 쓰지 않는가 — 실제로 오답을 냈기 때문입니다:
#   "valid"      에 "val" 이 들어있어 품질 플래그 열이 값 열로 뽑힘
#   "annotation" 에 "nn"  이 들어있어 주석 코드 열이 값 열로 뽑힘
#   "thr"        에 "hr"  이 들어있어 임계값 열이 값 열로 뽑힘
# 예: `valid,rr_ms` 헤더의 디바이스 익스포트에서 전부 1인 valid 열이 선택되면
# 단위 자동감지가 s로 보고 RR=1000 ms 상수 → "SDNN 0.0, HR 60" 을 경고 없이 출력.
_VALUE_TOKENS = frozenset((
    "rr", "rri", "rrs", "ibi", "nn", "nni", "interval", "intervals",
    "hr", "bpm", "beat", "beats", "value", "val", "heart", "heartrate",
    "pulse", "peak", "peaks",
    # 한국어 헤더 (문서·UI가 한국어이므로 실제로 들어옵니다)
    "간격", "박동간격", "심박", "심박수", "맥박", "박동", "심박간격",
))
# 값 열이 아닐 가능성이 높은 토큰(플래그·주석·식별자·품질 열).
_NONVALUE_TOKENS = frozenset((
    "valid", "invalid", "flag", "flags", "quality", "qc", "artifact",
    "artifacts", "ectopic", "annotation", "annot", "ann", "label", "labels",
    "status", "ok", "good", "bad", "id", "subject", "subj", "index", "idx",
    "count", "n", "note", "notes", "comment", "comments", "type", "class",
    "code", "marker", "mark", "epoch", "stage", "sleep", "condition", "group",
    "피험자", "라벨", "상태", "품질", "주석", "번호",
))
# **시간축** 토큰. 단위 토큰(ms/s)은 여기 넣지 않습니다 — "rr_ms" 가 시간 열로
# 오인되면 안 되기 때문입니다. 단위는 _UNIT_NAME_TOKENS 가 따로 봅니다.
_TIME_TOKENS = frozenset((
    "time", "timestamp", "timestamps", "sec", "secs", "second", "seconds",
    "elapsed", "clock", "t", "datetime", "date", "onset", "offset", "cumtime",
    "시간", "시각", "경과", "경과시간",
))


def _tokens(name: str) -> List[str]:
    """헤더 이름을 소문자 토큰 리스트로 분해.

    구분자(_ - . 공백 / 괄호)와 camelCase 경계에서 쪼갭니다. **유니코드를 보존**하므로
    한글 헤더도 토큰이 됩니다(과거엔 [^A-Za-z0-9] 로 쪼개 "간격" → [] 이 되어 점수 0 →
    아무 영어 열에나 밀렸습니다).
      "rr_ms"        → ["rr", "ms"]
      "RR Interval"  → ["rr", "interval"]
      "heartRate"    → ["heart", "rate"]
      "valid"        → ["valid"]      ("val" 로 쪼개지지 않음 → 값 열로 오인 안 함)
      "간격"          → ["간격"]
    """
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)      # camelCase 경계
    return [t for t in re.split(r"[\W_]+", s.lower(), flags=re.UNICODE) if t]

_ENCODINGS = ("utf-8-sig", "cp949", "euc-kr", "latin-1")
_DELIMS = (",", ";", "\t", "|")


# 숫자 표기 패턴. 쉼표가 천단위 구분인지 소수점인지는 **자릿수 그룹**으로 가릅니다.
_THOUSANDS_COMMA = re.compile(r"[-+]?\d{1,3}(?:,\d{3})+")          # 1,010 / 12,345,678
_EURO_THOUSANDS = re.compile(r"[-+]?\d{1,3}(?:\.\d{3})+,\d+")      # 1.234,5
_DECIMAL_COMMA = re.compile(r"[-+]?\d+,\d+")                        # 0,82 / 812,5


def parse_float(token: str, decimal_comma: bool = False) -> Optional[float]:
    """셀을 float로 변환. 빈칸/NA/비수치는 None.

    쉼표 해석은 **파일의 로케일**로 결정합니다. decimal_comma 는 "구분자가 쉼표가
    아니다" = 유럽식 표기 파일이라는 뜻이고, 유럽식에서 쉼표는 **항상 소수점**이며
    천단위 구분은 점(.)입니다. 토큰만 보고 자릿수로 추측하면 안 됩니다 —
    "0,803"(=0.803)과 "1,010"(=1.010)은 둘 다 `\\d+,\\d{3}` 이라 구분이 불가능합니다.

      decimal_comma=True (유럽식 파일: 세미콜론/탭/파이프 구분)
        1.234,5 → 1234.5    (점=천단위, 쉼표=소수점)
        0,803   → 0.803
        1,010   → 1.010
      decimal_comma=False (쉼표 구분 파일)
        쉼표는 구분자라 토큰 안에 남아 있을 수 없습니다. 따옴표로 감싼 "1,010" 만
        토큰에 쉼표를 품는데, 이 경우는 영미식 천단위 구분입니다 → 1010.

    inf/Infinity/1e400 같은 비유한 값은 None 으로 버립니다 — float()는 이들을 받아
    주지만 지표 계산을 통째로 오염시킵니다(median/평균이 inf/nan 이 됨).
    """
    t = token.strip()
    if t == "" or t.upper() in _NA_LABELS:
        return None
    if "," in t:
        if decimal_comma:
            if _EURO_THOUSANDS.fullmatch(t):
                t = t.replace(".", "").replace(",", ".")
            elif _DECIMAL_COMMA.fullmatch(t):
                t = t.replace(",", ".")
        elif _THOUSANDS_COMMA.fullmatch(t):
            t = t.replace(",", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _short(s: str, limit: int = 40) -> str:
    """오류 메시지에 넣을 문자열을 안전하게 축약.

    헤더/셀 내용을 그대로 메시지에 넣으면, 사용자가 실수로 CSV가 아닌 파일(.env,
    /etc/passwd 등)을 지정했을 때 그 첫 줄이 stderr(→ CI 로그·버그 리포트)로
    새어 나갑니다. 길이를 자르고 제어문자를 지웁니다.
    """
    s = re.sub(r"[\x00-\x1f\x7f]", "", str(s))
    return s if len(s) <= limit else s[:limit] + "…"


def _short_header(header: Optional[Sequence[str]], limit: int = 8) -> str:
    """오류 메시지용으로 헤더 목록을 축약(열 이름도 파일 내용이므로 자릅니다)."""
    if header is None:
        return "(없음)"
    names = [_short(h, 20) for h in header[:limit]]
    if len(header) > limit:
        names.append(f"…(+{len(header) - limit})")
    return "[" + ", ".join(names) + "]"


def _read_text(path: str) -> str:
    """인코딩 폴백으로 파일 텍스트를 읽습니다.

    UTF-16 은 BOM 으로 먼저 판별합니다 — cp949/latin-1 은 UTF-16 바이트를 예외 없이
    (NUL 섞인 쓰레기로) 디코드해버려 폴백 순서만으로는 절대 도달할 수 없습니다.
    """
    raw = open(path, "rb").read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        for enc in ("utf-16", "utf-16-le", "utf-16-be"):
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
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
    # '#' 로 시작하는 줄은 주석/메타데이터로 보고 건너뜁니다 — Polar·Kubios 등 실제
    # 기기 익스포트가 파일 앞머리에 '# Device: ...' 같은 메타 블록을 붙입니다.
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    n_comments = len(text.splitlines()) - len(lines)
    delimiter = _sniff_delimiter(lines)
    decimal_comma = delimiter != ","

    reader = csv.reader(lines, delimiter=delimiter)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise ValueError(
            f"'{path}' 에 데이터가 없습니다"
            + (f" (주석 {n_comments}줄만 있음)." if n_comments else "."))
    first = rows[0]
    # 첫 행에 수치로 해석되지 않는 셀이 하나라도 있으면 헤더로 간주.
    has_header = any(parse_float(c, decimal_comma) is None for c in first)
    meta = {"delimiter": delimiter, "decimal_comma": decimal_comma,
            "n_comment_lines": n_comments}
    if has_header:
        return [c.strip() for c in first], rows[1:], meta
    return None, rows, meta


def _is_increasing(vals: Sequence[float]) -> bool:
    return all(vals[i + 1] >= vals[i] for i in range(len(vals) - 1))


def _plausible_frac(vals: Sequence[float]) -> float:
    """값들이 생리적 RR로 해석될 수 있는 비율(0..1) — 열 선택의 안전망.

    단위를 자동 감지해 ms로 바꾼 뒤 300–2000 ms 범위에 드는 비율을 봅니다.
    **상수 열은 0** 으로 봅니다: 실제 RR/HR은 항상 변동하므로, 전부 같은 값인 열은
    (전부 1인 품질 플래그처럼) 값 열이 아닙니다. 이 규칙이 없으면 all-1 플래그가
    's' 로 감지돼 1000 ms 상수 → "완벽히 생리적"으로 보입니다.
    """
    if len(vals) < 2:
        return 0.0
    if len(set(vals)) == 1:
        return 0.0
    try:
        rr = to_rr_ms(vals, detect_unit(vals))
    except (ValueError, ZeroDivisionError):
        return 0.0
    if not rr:
        return 0.0
    return sum(1 for v in rr if 300.0 <= v <= 2000.0) / len(rr)


def _name_score(name: str) -> int:
    """헤더 이름의 값 열 적합도(0~3, 클수록 값 열에 가까움).

    토큰 **정확 일치**만 인정합니다(부분일치 금지 — 위 _VALUE_TOKENS 주석 참조).

    어떤 열도 완전히 배제하지 않는 이유: 예전엔 비값 토큰이 하나라도 있으면 후보에서
    빼버렸는데, `time_s,Pulse rate (count/min)` 같은 헤더에서 "count" 때문에 진짜 값
    열이 탈락하고 **시간축이 값 열로 뽑히는** 더 나쁜 결과가 났습니다. 점수로 낮추되
    남겨두고, 동점은 생리적 그럴듯함(_plausible_frac)으로 가립니다.
    """
    toks = _tokens(name)
    if not toks:
        return 2                                   # 이름에서 정보 없음 → 중립
    has_val = any(t in _VALUE_TOKENS for t in toks)
    has_non = any(t in _NONVALUE_TOKENS for t in toks)
    has_time = any(t in _TIME_TOKENS for t in toks)
    if has_val and has_time:
        return 1        # "peak_time" — 박동 '발생시각' 열일 가능성이 큼
    if has_val and not has_non:
        return 3        # "rr_ms", "간격" — 값 열로 명시된 이름
    if has_val and has_non:
        return 2        # "pulse rate (count/min)" — 값 토큰과 비값 토큰이 공존
    if has_non:
        return 0        # "valid", "annotation", "subject_id" — 플래그/식별자
    if has_time:
        return 1        # "time_s" — 순수 시간 열
    return 2            # 알 수 없는 이름 → 중립


def _pick_column(header: Optional[List[str]], data: List[List[str]],
                 col: Optional[str], decimal_comma: bool = False
                 ) -> Tuple[int, str]:
    """사용할 값 열의 (인덱스, 이름)을 결정.

    자동 추정 규칙(헤더 있을 때): 이름 점수(_name_score)가 가장 높은 열을 고르고,
    동점이면 **생리적으로 그럴듯한 비율**이 높은 열을 고릅니다. 이름만으로 첫 일치를
    잡던 과거 방식은 `valid,rr_ms` 같은 헤더에서 플래그 열을 뽑아 조용히 틀린 값을
    냈습니다.
    """
    n_cols = max(len(r) for r in data)

    def column(i):
        out = []
        for r in data:
            if i < len(r):
                v = parse_float(r[i], decimal_comma)
                if v is not None:
                    out.append(v)
        return out

    # 명시적 지정
    if col is not None:
        if header is not None and col in header:
            if header.count(col) > 1:
                raise ValueError(
                    f"열 이름 '{_short(col)}' 이 {header.count(col)}번 중복됩니다 — "
                    f"어느 열을 쓸지 알 수 없습니다. 0-based 인덱스로 지정하세요 "
                    f"(예: --col {header.index(col)}). header={_short_header(header)}")
            return header.index(col), col
        # 정수 인덱스로도 허용. int()는 '1_0'(→10)·' 1 ' 같은 표기도 받아들이므로
        # 순수 숫자만 인덱스로 인정합니다.
        stripped = col.strip()
        if not stripped.isdigit():
            raise ValueError(
                f"열 '{_short(col)}' 을 찾을 수 없습니다. 이름이 헤더에 없고 "
                f"0-based 정수 인덱스도 아닙니다. header={_short_header(header)}")
        idx = int(stripped)
        if not (0 <= idx < n_cols):
            raise ValueError(f"열 인덱스 {idx} 가 범위를 벗어났습니다(0..{n_cols-1}).")
        name = header[idx] if header and idx < len(header) else f"col{idx}"
        return idx, name

    # 단일 열
    if n_cols == 1:
        name = header[0] if header else "value"
        return 0, name

    # 헤더가 있으면 이름 점수 + 생리적 그럴듯함으로 값 열 추정
    if header is not None:
        n_named = min(len(header), n_cols)
        scores = [_name_score(header[i]) for i in range(n_named)]
        best_score = max(scores)
        top = [i for i in range(n_named) if scores[i] == best_score]
        if len(top) == 1:
            return top[0], header[top[0]]
        # 이름 점수가 동점 → 생리적으로 그럴듯한 열을 고릅니다.
        # (_plausible_frac 은 열 전체를 파싱하므로 동점일 때만 계산합니다.)
        best_i = max(top, key=lambda i: (_plausible_frac(column(i)), -i))
        return best_i, header[best_i]

    # 헤더 없음, 다열: 첫 열이 단조 증가면 시간으로 보고 마지막 열을 값으로.
    if n_cols == 2:
        c0 = column(0)
        if c0 and _is_increasing(c0):
            return 1, "col1"
        return n_cols - 1, f"col{n_cols - 1}"
    return n_cols - 1, f"col{n_cols - 1}"


# 열 이름에서 단위를 읽어내는 토큰(모호하지 않은 것만).
# "hr" 은 값이 ms일 수도 bpm일 수도 있어 제외합니다.
_UNIT_NAME_TOKENS = {
    "ms": "ms", "msec": "ms", "msecs": "ms", "millisecond": "ms",
    "milliseconds": "ms",
    "s": "s", "sec": "s", "secs": "s", "second": "s", "seconds": "s",
    "bpm": "bpm",
}


def unit_from_name(name: str) -> Optional[str]:
    """열 이름의 단위 토큰에서 단위를 읽어냅니다 (없으면 None).

    "rr_ms" → ms, "rr_s" → s, "hr_bpm" → bpm, "rr" → None.
    """
    if not name:
        return None
    found = {_UNIT_NAME_TOKENS[t] for t in _tokens(name)
             if t in _UNIT_NAME_TOKENS}
    return found.pop() if len(found) == 1 else None


def detect_unit(values: Sequence[float], name: Optional[str] = None) -> str:
    """단위를 추정: 's' | 'bpm' | 'ms'.

    열 이름에 단위가 명시돼 있으면(rr_ms/rr_s/hr_bpm) **그 이름을 신뢰**하고,
    없을 때만 값의 중앙값으로 추정합니다(<10 → s, <300 → bpm, 그 외 ms).

    이름을 우선하는 이유: 중앙값 규칙은 성인 기준이라 신생아·빈맥 기록에서 깨집니다.
    RR 270 ms(HR 222 bpm)인 신생아 기록은 중앙값 270 < 300 → 'bpm' 으로 오판돼
    60000/270 = 222 ms 라는 조용한 오답이 됐습니다 — 헤더에 'rr_ms' 라고 적혀
    있는데도 무시하고서. 이름이 틀린 경우(단위 오라벨 익스포트)에는 이상박동
    100% 경고가 크게 뜨므로 사용자가 알아챌 수 있습니다.
    """
    if not values:
        raise ValueError("단위를 추정할 값이 없습니다.")
    named = unit_from_name(name) if name else None
    if named:
        return named
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

    # 헤더 이름 수보다 데이터 열 수가 많으면, 값 안에 따옴표 없는 구분자가 들어 있을
    # 가능성이 큽니다 — 예: 헤더 `rr_ms` 인데 "1,010"(=1010)을 따옴표 없이 쓴 파일은
    # csv가 두 열로 쪼개, 첫 열의 "1" 이 RR=1.0 ms 상수로 읽힙니다(조용한 오답).
    n_cols = max(len(r) for r in data)
    header_short = header is not None and len(header) < n_cols
    col_note = None
    if header_short and col is None:
        col_note = (
            f"헤더에는 열 이름이 {len(header)}개인데 데이터 행에는 열이 {n_cols}개까지 "
            f"있습니다. 값 안에 따옴표 없는 구분자('{pmeta['delimiter']}')가 들어 있을 "
            f"수 있습니다(예: 1,010 을 \"1,010\" 으로 감싸지 않은 경우) — 숫자가 잘려 "
            f"읽힐 수 있으니 --col 로 값 열을 명시하거나 파일을 확인하세요.")

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
            f"열 #{idx} '{_short(name, 16)}' 에서 사용할 수치를 찾지 못했습니다 "
            f"(열/형식을 확인하세요 — CSV가 맞는지, --col 이 필요한지).")

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

    unit_note = None
    if unit == "auto":
        resolved = detect_unit(series, name)
        named = unit_from_name(name)
        by_median = detect_unit(series)
        if named:
            source = "column-name"
            # 이름과 중앙값 규칙이 엇갈리면 알려줍니다(단위 오라벨 익스포트 탐지).
            if by_median != named:
                unit_note = (
                    f"열 이름 '{_short(name)}' 은 단위를 '{named}' 로 명시하지만 값의 "
                    f"중앙값 규칙은 '{by_median}' 로 봅니다. 이름을 따랐습니다 — "
                    f"이상박동 비율이 비정상적으로 높으면 --unit 로 직접 지정하세요.")
        else:
            source = "auto-detected"
    else:
        resolved, source = unit, "user-specified"
    rr_ms = to_rr_ms(series, resolved)
    if not rr_ms:
        raise ValueError("단위 변환 후 남은 값이 없습니다 (bpm에 0/음수만 있었을 수 있음).")

    meta = {
        "unit": resolved,
        "unit_source": source,
        "unit_note": unit_note,
        "column": name,
        "n_raw": len(raw),
        "n_dropped": dropped,
        "delimiter": pmeta["delimiter"],
        "decimal_comma": decimal_comma,
        "n_comment_lines": pmeta.get("n_comment_lines", 0),
        "column_note": col_note,
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
        # 토큰 정확 일치로 찾습니다. 부분일치는 오답을 냈습니다 — "condition_id" 의
        # "id" 가 걸려 조건 열이 피험자 라벨로 뽑히고, 모든 행이 같은 조건이라
        # "피험자 라벨 중복" 으로 멀쩡한 매니페스트가 거부됐습니다. "baseline_id" 도
        # 경로 열인데 라벨 열로 뽑혔습니다.
        toks = [set(_tokens(h)) for h in header]
        base_k = {"base", "baseline", "pre", "rest", "기저", "안정"}
        int_k = {"interv", "intervention", "post", "slow", "treat", "treatment",
                 "개입", "처치"}
        # 피험자를 명시하는 토큰, 또는 이름 전체가 순수 식별자인 경우만 라벨 열.
        # "id" 만으로 판단하면 "condition_id"(조건 코드)가 라벨로 뽑혀, 모든 행이
        # 같은 조건인 멀쩡한 매니페스트가 "라벨 중복"으로 거부됩니다.
        subj_k = {"subject", "subj", "피험자", "대상자"}
        pure_id = {"id", "subjid", "label", "라벨", "이름", "name"}
        for i, t in enumerate(toks):
            if t & base_k:
                bcol = i
            if t & int_k:
                icol = i
            # 라벨 열은 경로 열(기저/개입)과 겹치면 안 됩니다.
            if (t & base_k) or (t & int_k):
                continue
            if (t & subj_k) or (t and t <= pure_id):
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

    # 같은 피험자가 여러 행에 있으면 짝들이 독립이 아니므로(유사반복,
    # pseudo-replication) Wilcoxon n·p값·CI가 모두 낙관적으로 부풀려집니다.
    # 조용히 통과시키지 않고 거부합니다 — 의도된 반복측정이라면 피험자별로
    # 먼저 집계해서 한 행으로 만들어야 합니다.
    dupes = _duplicates([lab for _, _, lab in pairs if lab])
    if dupes:
        raise ValueError(
            f"매니페스트 '{path}' 에 피험자 라벨이 중복됩니다: "
            f"{', '.join(_short(d, 20) for d in sorted(dupes)[:5])}"
            f"{' …' if len(dupes) > 5 else ''}. 짝지은 검정은 각 행이 독립인 "
            f"피험자여야 합니다(중복 = 유사반복 → n·p값이 부풀려짐). 피험자당 한 "
            f"행으로 정리하거나 라벨을 구분하세요.")
    dupe_pairs = _duplicates([(b, i) for b, i, _ in pairs])
    if dupe_pairs:
        raise ValueError(
            f"매니페스트 '{path}' 에 완전히 동일한 (기저,개입) 파일 짝이 "
            f"{len(dupe_pairs)}건 중복됩니다. 같은 기록을 두 번 세면 n 이 부풀려집니다.")
    return pairs


def _duplicates(items: Sequence) -> set:
    """두 번 이상 등장하는 항목의 집합."""
    seen, dup = set(), set()
    for it in items:
        if it in seen:
            dup.add(it)
        seen.add(it)
    return dup


def load_group_manifest(path: str) -> List[Tuple[str, str, str]]:
    """평행군(독립 2군) 매니페스트 CSV를 (파일경로, 군, 라벨) 목록으로 로드.

    형식: 최소 2개 열. 헤더가 있으면 이름으로 파일/군 열을 추정
    (file/path/csv/파일/기록 vs group/arm/condition/treatment/군/그룹/조건),
    없으면 1열=파일, 2열=군. 선택적 3번째 열은 피험자 라벨.
    상대경로는 매니페스트 파일 디렉터리 기준으로 해석합니다.

    load_manifest(짝지은) 와 달리 **각 행이 한 기록 = 한 피험자**입니다.
    독립 2군 검정은 각 관측이 서로 다른 피험자여야 하므로:
      - 같은 파일이 두 번 나오면 거부(같은 기록을 두 번 세면 n 이 부풀려짐).
      - 피험자 라벨이 중복되면 거부(유사반복 → p값이 낙관적).
      - 군이 정확히 2개가 아니면 거부(3군 이상은 Kruskal–Wallis 가 필요한데
        이 도구는 제공하지 않습니다 — 조용히 2군만 쓰지 않고 알립니다).
    """
    header, data, _ = _read_table(path)
    base_dir = os.path.dirname(os.path.abspath(path))

    def _resolve(p: str) -> str:
        p = p.strip()
        return p if os.path.isabs(p) else os.path.join(base_dir, p)

    def _looks_like_path(c: str) -> bool:
        """셀이 (존재하지 않더라도) 파일 경로처럼 보이는지.

        존재 여부만으로 판단하면 **오타 난 첫 행이 통째로 사라집니다**: 헤더가
        없는 매니페스트의 첫 행은 `경로,군` 인데, 경로에 오타가 있으면 어느 셀도
        파일이 아니라서 그 행이 헤더로 오인돼 조용히 버려지고, 군의 n 이 하나
        줄어든 채 통계가 나옵니다(실측: 3대3 이 2대3 으로). 확장자·구분자도 함께
        봅니다.
        """
        c = c.strip()
        return bool(c) and (os.path.isfile(_resolve(c))
                            or c.lower().endswith((".csv", ".tsv", ".txt"))
                            or os.sep in c)

    # load_manifest 와 같은 이유로 헤더 오인을 되돌립니다 — 첫 행 셀이 파일
    # 경로처럼 보이면 그 행은 헤더가 아니라 데이터입니다.
    if header is not None and any(_looks_like_path(c) for c in header):
        data = [header] + data
        header = None

    if not data:
        raise ValueError(f"군 매니페스트 '{path}' 에 데이터 행이 없습니다.")

    fcol, gcol, lcol = 0, 1, None
    if header:
        toks = [set(_tokens(h)) for h in header]
        file_k = {"file", "path", "filename", "csv", "record", "recording",
                  "파일", "경로", "기록"}
        group_k = {"group", "arm", "cohort", "condition", "treatment", "trt",
                   "군", "그룹", "조건", "처치", "배정"}
        subj_k = {"subject", "subj", "피험자", "대상자"}
        pure_id = {"id", "subjid", "label", "라벨", "이름", "name"}
        for i, t in enumerate(toks):
            if t & file_k:
                fcol = i
            if t & group_k:
                gcol = i
        for i, t in enumerate(toks):
            if i in (fcol, gcol):
                continue
            if (t & subj_k) or (t and t <= pure_id):
                lcol = i
    if fcol == gcol:
        raise ValueError(
            f"군 매니페스트 '{path}' 에서 파일 열과 군 열이 같은 열로 잡혔습니다. "
            f"헤더를 'file,group[,subject]' 로 지정하세요. "
            f"header={_short_header(header)}")

    # 헤더가 없어도 3번째 열이 있으면 피험자 라벨로 씁니다 — 그래야 라벨 중복
    # (유사반복) 검사가 헤더 유무와 무관하게 돕니다.
    if header is None and data and len(data[0]) >= 3 and lcol is None:
        lcol = 2

    rows: List[Tuple[str, str, str]] = []
    skipped: List[int] = []
    first_data_line = 2 if header is not None else 1
    for offset, r in enumerate(data):
        line_no = first_data_line + offset
        if max(fcol, gcol) >= len(r) or not r[fcol].strip() \
                or not r[gcol].strip():
            skipped.append(line_no)
            continue
        f, gname = r[fcol].strip(), r[gcol].strip()
        label = r[lcol].strip() if (lcol is not None and lcol < len(r)) else ""
        rows.append((_resolve(f), gname, label))
    # 조용히 건너뛰면 군 n 이 사용자가 쓴 것과 달라집니다 — 중복을 큰소리로
    # 거부하면서(=n 부풀리기) 누락은 조용히 넘기는 것은 앞뒤가 맞지 않습니다.
    if skipped:
        raise ValueError(
            f"군 매니페스트 '{path}' 의 {len(skipped)}개 행에 파일 또는 군이 "
            f"비어 있거나 열이 모자랍니다 (행 "
            f"{', '.join(str(n) for n in skipped[:5])}"
            f"{' …' if len(skipped) > 5 else ''}). 행을 지우거나 채우세요 — "
            f"조용히 건너뛰면 군의 n 이 달라집니다.")
    if not rows:
        raise ValueError(
            f"군 매니페스트 '{path}' 에서 유효한 (파일,군) 행을 찾지 못했습니다.")
    missing = [f for f, _, _ in rows if not os.path.isfile(f)]
    if missing:
        raise ValueError(
            f"군 매니페스트 '{path}' 가 가리키는 파일 {len(missing)}개를 찾을 수 "
            f"없습니다: "
            f"{', '.join(_short(os.path.basename(m), 24) for m in missing[:5])}"
            f"{' …' if len(missing) > 5 else ''}. 경로는 매니페스트 파일 위치를 "
            f"기준으로 해석합니다.")

    dupe_files = _duplicates([f for f, _, _ in rows])
    if dupe_files:
        raise ValueError(
            f"군 매니페스트 '{path}' 에 같은 파일이 여러 번 나옵니다: "
            f"{', '.join(_short(os.path.basename(d), 24) for d in sorted(dupe_files)[:5])}"
            f"{' …' if len(dupe_files) > 5 else ''}. 독립 2군 검정은 각 행이 "
            f"서로 다른 기록이어야 합니다(중복 = n 부풀리기).")
    dupe_lab = _duplicates([lab for _, _, lab in rows if lab])
    if dupe_lab:
        raise ValueError(
            f"군 매니페스트 '{path}' 에 피험자 라벨이 중복됩니다: "
            f"{', '.join(_short(d, 20) for d in sorted(dupe_lab)[:5])}"
            f"{' …' if len(dupe_lab) > 5 else ''}. 독립 2군 검정은 각 행이 독립인 "
            f"피험자여야 합니다(중복 = 유사반복 → p값이 부풀려짐).")

    groups = []
    for _, gname, _ in rows:
        if gname not in groups:
            groups.append(gname)
    if len(groups) != 2:
        raise ValueError(
            f"군 매니페스트 '{path}' 의 군이 {len(groups)}개입니다 "
            f"({', '.join(_short(g, 16) for g in groups[:6])}"
            f"{' …' if len(groups) > 6 else ''}). --groups 는 정확히 2군만 "
            f"비교합니다 — 3군 이상은 Kruskal–Wallis 등 다른 검정이 필요하며 "
            f"이 도구는 제공하지 않습니다.")
    for gname in groups:
        n_g = sum(1 for _, g, _ in rows if g == gname)
        if n_g < 2:
            raise ValueError(
                f"군 '{_short(gname, 16)}' 에 기록이 {n_g}개뿐입니다. 군 비교에는 "
                f"군마다 최소 2개가 필요합니다.")
    return rows

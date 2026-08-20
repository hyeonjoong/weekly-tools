"""숫자 토큰 추출 + 건너뜀 규칙.

이 툴의 1번 사망 원인은 **크라잉울프**입니다. Results 절에도 분석 출력에서
오지 않은 숫자가 잔뜩 있습니다 — 인용 문헌 번호, 표·그림 참조, 등록번호,
`p < 0.05` 같은 관용구, `95% CI` 의 95, `week 8` 의 8. 이것들을 그대로
대조하면 첫 실행에 '출처 없음' 30건이 뜨고 툴은 죽습니다.

그래서 건너뜀은 **사유마다 이름이 붙어 있고 전부 세어서 리포트에 자백**합니다.
조용히 빼는 규칙은 하나도 없습니다. 몇 개를 안 봤는지 말하지 않는 체커는
없느니만 못합니다.
"""

import bisect
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Tuple

from .manuscript import Block
from .textnorm import original_slice

# 사유 이름 — 리포트 출력 순서이기도 합니다.
SKIP_YEAR = "연도"
SKIP_CITE = "인용 표기 내부"
SKIP_TABREF = "표·그림 번호 참조"
SKIP_ALPHA = "유의수준 관용구"
SKIP_RANGE = "척도 범위 선언"
SKIP_TIME = "시점·기간 표기"
SKIP_DATE = "날짜·시각"
SKIP_RATIO = "배정비 표기"
SKIP_SMALL = "자명한 소정수(본문)"
SKIP_IDENT = "순수 식별자"
SKIP_INSTRUMENT = "척도·진단명 안의 숫자"
SKIP_PRECISION = "자릿수 상한 초과"

# 사유 우선순위 = 이 목록의 순서. 겹치면 앞쪽 사유로 셉니다.
SKIP_ORDER = [SKIP_YEAR, SKIP_CITE, SKIP_TABREF, SKIP_ALPHA, SKIP_RANGE,
              SKIP_TIME, SKIP_DATE, SKIP_RATIO, SKIP_INSTRUMENT, SKIP_SMALL,
              SKIP_IDENT, SKIP_PRECISION]

MAX_DECIMALS = 8
MAX_DIGITS = 20

_NUM = re.compile(
    r"(?P<op>[<>]=?|[≤≥])?\s*"
    # 부호는 '앞이 글자/숫자/점이 아닐 때'만 부호로 봅니다.
    # 안 그러면 `0-28`(척도 범위)의 28 이 -28 로, `COVID-19` 의 19 가 -19 로 읽힙니다.
    r"(?:(?<![\w.])(?P<sign>[-+]))?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
    # R 의 write.csv 와 파이썬 repr 은 작은 p 값을 `1.5e-05` 로 씁니다.
    # 지수를 안 읽으면 이게 `1.5` 와 `5` 두 개의 유령 숫자로 쪼개집니다.
    r"(?P<exp>[eE][-+]?\d{1,4})?"
    r"(?P<pct>\s?%)?"
)

_RE_DATE = re.compile(
    r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"
    r"|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\b"
    r"|\d{4}\s*년\s*\d{1,2}\s*월(?:\s*\d{1,2}\s*일)?"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+(?:19|20)\d{2}\b",
    re.IGNORECASE)

_RE_IDENT = re.compile(
    r"\b(?:NCT|ISRCTN|KCT|UMIN|ChiCTR|JPRN|PMID|PMCID|PMC|EudraCT)\s*[-:]?\s*[\dA-Z-]*\d"
    r"|\bIRB\s*[-:#]?\s*[A-Za-z0-9-]*\d"
    r"|\b10\.\d{4,9}/\S+"
    r"|\bBELL\s*-?\s*\d+"
    r"|\bv(?:er)?\.?\s*\d+(?:\.\d+)+"
    r"|\b\d+(?:\.\d+){2,}\b",
    re.IGNORECASE)

_RE_CITE_BRACKET = re.compile(r"\[\s*\d+(?:\s*[,;\-]\s*\d+)*\s*\]")
_RE_CITE_PAREN = re.compile(
    r"\([^()]{0,120}?[A-Za-z가-힣][^()]{0,120}?,\s*(?:19|20)\d{2}[a-z]?[^()]{0,60}?\)")

_RE_TABREF = re.compile(
    r"\b(?:supplementary\s+|suppl\.?\s+|appendix\s+)?"
    r"(?:tables?|tbls?\.?|figs?\.?|figures?|panels?|eq(?:uation)?s?\.?)\s*"
    r"S?\s*\d+(?:\s*[-,]\s*S?\s*\d+)*"
    r"|(?:표|그림|부록\s*표|식)\s*\d+(?:\s*[-,]\s*\d+)*",
    re.IGNORECASE)

_RE_ALPHA = re.compile(
    r"(?:\bp\b|\bP\b|α|alpha|유의\s*수준|significance\s+(?:level|was\s+set))"
    r"\s*(?:values?\s*)?[<≤=]\s*0?\.(?:05|01|10|025|1)\b",
    re.IGNORECASE)
_RE_ALPHA2 = re.compile(r"\b(?:two|one)[-\s]?(?:sided|tailed)\s+5\s*%")
_RE_CILEVEL = re.compile(
    r"\b(?:9[05]|99|9\d\.\d)\s*%\s*(?:CI|confidence\s+intervals?|신뢰\s*구간|CrI|credible)"
    r"|(?:CI|신뢰\s*구간)\s*[:(]?\s*(?:9[05]|99)\s*%",
    re.IGNORECASE)

_RE_RANGE = re.compile(r"(?<![\d.])(\d{1,4})\s*(?:-|~|to)\s*(\d{1,4})(?![\d.])",
                       re.IGNORECASE)
_RANGE_CUE = re.compile(
    r"(range|scale|scores?|possible|points?|total\s+score|범위|척도|점수|만점|총점)",
    re.IGNORECASE)

# 1:1 배정, 2:1 무작위배정 — 설계 상수이지 분석 결과가 아닙니다.
_RE_RATIO = re.compile(r"(?<![\d.:])\d{1,3}\s*:\s*\d{1,3}(?![\d.:])")

# 시점 표기만 좁게 잡습니다. "mean age was 43 years", "median stay 5 days" 는
# 분석 결과값이므로 절대 삼키면 안 됩니다(2라운드에서 실제로 삼켰습니다).
_RE_TIMEPOINT = re.compile(
    # 단위가 앞에 오는 형태: "week 8", "visit 3", "V2"
    r"\b(?:weeks?|wks?|days?|months?|years?|visits?|timepoints?|V)\s*\.?\s*(\d{1,3})\b"
    # 전치사가 앞에 붙은 형태: "at 8 weeks", "over 12 months"
    r"|\b(?:at|after|over|within|through|until|from|by|during|post|to)\s+"
    r"(\d{1,3})\s*-?\s*(?:weeks?|wks?|days?|months?|years?)\b"
    # 형용사형: "8-week programme"
    r"|(?<![\d.])(\d{1,3})-(?:week|wk|day|month|year)s?\b"
    # 한국어: 뒤에 시점을 뜻하는 말이 붙은 경우만 ("8주 시점", "12개월 추적")
    r"|(?<![\d.])(\d{1,3})\s*(?:주|일|개월|년|회)\s*"
    r"(?:차|째|간|후|시점|추적|방문|평가|경과|간격)"
    r"|제\s*(\d{1,3})\s*(?:주|일|개월|년|회)",
    re.IGNORECASE)

# PHQ-9, GAD-7, SF-36, DSM-5, ICD-10, EQ-5D, COVID-19 — 도구·진단 이름이지 값이 아닙니다.
# 대문자 약어만 봅니다. `Change-3.5`(대시가 마이너스인 경우)나 `Na-138` 을
# 삼키면 진짜 결과값이 조용히 사라집니다. 뒤에 소수점이 오면 값으로 봅니다.
_RE_INSTRUMENT = re.compile(r"\b[A-Z]{2,10}-\d{1,3}(?!\.\d)[A-Za-z]?\b")

_SENT_SPLIT = re.compile(r"(?<=[.!?。])\s+")


@dataclass
class Number:
    """원고에서 뽑은 숫자 하나."""
    block: Block
    raw: str                     # 원문 그대로의 토큰
    context: str                 # 원문 문장(발췌)
    op: str                      # '', '<', '<=', '>', '>='
    value: Decimal
    decimals: int
    is_percent: bool
    start: int
    end: int
    skip: Optional[str] = None

    @property
    def line(self) -> int:
        return self.block.line

    @property
    def section(self) -> str:
        return self.block.section

    @property
    def loc(self) -> str:
        return self.block.loc

    @property
    def target_key(self) -> str:
        return self.block.target_key

    @property
    def text(self) -> str:
        """부등호까지 포함한 표기 (`<0.001`)."""
        return "%s%s" % (self.op, plain(self.value))


def plain(value: Decimal) -> str:
    text = format(value, "f")
    if text.startswith("-") and value == 0:
        return text[1:]
    return text


def skip_regions(text: str) -> List[Tuple[int, int, str]]:
    """토큰을 뽑기 전에, '여기 있는 숫자는 대조 대상이 아니다' 구간을 먼저 표시합니다."""
    regions: List[Tuple[int, int, str]] = []

    def add(pattern, reason, group=0):
        for m in pattern.finditer(text):
            if group and m.group(group) is not None:
                regions.append((m.start(group), m.end(group), reason))
            else:
                regions.append((m.start(), m.end(), reason))

    add(_RE_DATE, SKIP_DATE)
    add(_RE_RATIO, SKIP_RATIO)
    add(_RE_IDENT, SKIP_IDENT)
    add(_RE_CITE_BRACKET, SKIP_CITE)
    add(_RE_CITE_PAREN, SKIP_CITE)
    add(_RE_TABREF, SKIP_TABREF)
    add(_RE_ALPHA, SKIP_ALPHA)
    add(_RE_ALPHA2, SKIP_ALPHA)
    add(_RE_CILEVEL, SKIP_ALPHA)
    add(_RE_INSTRUMENT, SKIP_INSTRUMENT)
    for m in _RE_TIMEPOINT.finditer(text):
        for group in (1, 2, 3, 4, 5):
            if m.group(group) is not None:
                regions.append((m.start(group), m.end(group), SKIP_TIME))
    for m in _RE_RANGE.finditer(text):
        left = max(0, m.start() - 45)
        window = text[left:m.end() + 45]
        if _RANGE_CUE.search(window):
            regions.append((m.start(), m.end(), SKIP_RANGE))
    return regions


def _reason_map(text: str, regions: List[Tuple[int, int, str]]) -> bytearray:
    """문자 위치별 '건너뜀 사유 순위+1' 지도.

    구간마다 토큰을 전부 훑으면 (숫자 × 구간) 이 되어, 줄바꿈 없이 1MB 로 붙어 온
    `.txt` 원고에서 몇 시간이 걸립니다(실제로 재현했습니다). 구간 길이 합에
    비례하는 지도를 한 번 만들어 두고 토큰 길이만큼만 조회합니다.
    """
    marks = bytearray(len(text))
    for start, end, reason in regions:
        rank = SKIP_ORDER.index(reason) + 1
        start = max(0, start)
        end = min(len(text), end)
        for i in range(start, end):
            if marks[i] == 0 or marks[i] > rank:
                marks[i] = rank
    return marks


def _reason_at(marks: bytearray, start: int, end: int) -> Optional[str]:
    best = 0
    for i in range(max(0, start), min(len(marks), end)):
        rank = marks[i]
        if rank and (best == 0 or rank < best):
            best = rank
    return SKIP_ORDER[best - 1] if best else None


def _sentence_bounds(text: str) -> List[int]:
    return [m.end() for m in _SENT_SPLIT.finditer(text)]


def extract_numbers(block: Block) -> List[Number]:
    """블록 하나에서 숫자 토큰을 전부 뽑고, 각 토큰에 건너뜀 사유를 답니다.

    **조용히 버리는 토큰은 하나도 없습니다.** 값으로 못 읽는 것까지 전부
    `Number` 로 만들어 사유를 달아 돌려주고, 그 개수가 커버리지 자백에 들어갑니다.
    """
    text = block.norm
    if not text:
        return []
    marks = _reason_map(text, skip_regions(text))
    bounds = _sentence_bounds(text)
    found: List[Number] = []
    for match in _NUM.finditer(text):
        parsed = _parse_token(match)
        if parsed is None:
            continue
        value, decimals, forced = parsed
        op = (match.group("op") or "").replace("≤", "<=").replace("≥", ">=")
        start = match.start("num")
        if match.group("sign") is not None:
            start = match.start("sign")
        end = match.end("exp") if match.group("exp") else match.end("num")
        reason = forced or _reason_at(marks, start, end)
        if reason is None:
            reason = _token_reason(block, value, decimals, match, text, start, end)
        raw_text = original_slice(block.text, block.idx_map, match.start(), match.end())
        found.append(Number(
            block=block,
            raw=raw_text.strip() or text[match.start():match.end()].strip(),
            context=_context(block, bounds, start, end),
            op=op,
            value=value,
            decimals=decimals,
            is_percent=bool(match.group("pct")),
            start=start,
            end=end,
            skip=reason,
        ))
    return found


def _parse_token(match):
    """정규식 매치 → (값, 소수자릿수, 강제 건너뜀 사유). 값으로 못 읽으면 None."""
    digits = match.group("num").replace(",", "")
    sign = match.group("sign") or ""
    exponent = match.group("exp") or ""
    if len(digits.replace(".", "")) > MAX_DIGITS:
        # 20자리가 넘는 숫자열은 값이 아니라 코드·식별자입니다.
        return Decimal(0), 0, SKIP_IDENT
    try:
        value = Decimal(sign + digits + exponent)
    except (InvalidOperation, ValueError):
        return None
    decimals = max(0, -value.as_tuple().exponent)
    if decimals > MAX_DECIMALS:
        return value, decimals, SKIP_PRECISION
    return value, decimals, None


def _token_reason(block: Block, value: Decimal, decimals: int, match,
                  text: str, start: int, end: int) -> Optional[str]:
    """구간 규칙에 안 걸린 토큰에 대한 마지막 판단."""
    is_int = (decimals == 0 and match.group("sign") is None
              and not match.group("pct") and not match.group("exp"))
    if is_int and not match.group("op"):
        # 연도 — 1900~2100 의 단독 정수.
        if 1900 <= value <= 2100 and _standalone(text, start, end):
            return SKIP_YEAR
        # 본문의 자명한 소정수. 표 셀 안이면 실제 값이므로 대조합니다.
        if block.kind != "table" and value in (0, 1, 2) and _standalone(text, start, end):
            return SKIP_SMALL
    return None


def _standalone(text: str, start: int, end: int) -> bool:
    """숫자가 다른 토큰의 일부가 아닌지.

    문장 끝의 `.` 와 목록의 `,` 는 붙어 있어도 단독으로 봅니다 — 이걸 막았더니
    "January 2026, 412 individuals" 의 2026 이 연도로 안 걸려 치명이 됐습니다.
    """
    before = text[start - 1] if start > 0 else " "
    after = text[end] if end < len(text) else " "
    return before not in ":/-" and after not in ":/-%"


def _context(block: Block, bounds: List[int], start: int, end: int) -> str:
    """토큰이 들어 있는 문장을 원문에서 잘라 옵니다(최대 120자)."""
    text = block.norm
    position = bisect.bisect_right(bounds, start)
    left = bounds[position - 1] if position else 0
    # 슬라이스로 뒤를 훑으면 숫자마다 목록 꼬리를 복사해 다시 2차가 됩니다.
    after = bisect.bisect_left(bounds, end)
    right = bounds[after] if after < len(bounds) else len(text)
    if right - left > 160:
        left = max(left, start - 70)
        right = min(right, end + 70)
    excerpt = original_slice(block.text, block.idx_map, left, right).strip()
    if not excerpt:
        excerpt = text[left:right].strip()
    excerpt = re.sub(r"\s+", " ", excerpt)
    if len(excerpt) > 120:
        excerpt = excerpt[:117].rstrip() + "…"
    return excerpt


def _looks_like_label(text: str, start: int) -> bool:
    """`phq9_change`·`SF-36 PCS` 처럼 숫자가 **이름의 일부**인지.

    바로 앞이 영문자/밑줄이면 이름입니다. 하이픈·점 앞이 영문자면 그것도
    이름입니다(`SF-36`, `ICD-10`, `HAM-D-17` — 엑셀 헤더에 그대로 쓰입니다).
    단위가 뒤에 붙은 진짜 값(`12.4mmHg`)은 앞이 숫자가 아니므로 값으로 봅니다.
    """
    if start <= 0:
        return False
    before = text[start - 1]
    if before == "_" or (before.isalpha() and before.isascii()):
        return True
    if before in "-." and start >= 2:
        earlier = text[start - 2]
        return earlier.isalpha() and earlier.isascii()
    return False


def cell_numbers(text: str) -> List[Tuple[Decimal, int, str, bool, bool]]:
    """번들 셀에서 (값, 소수자릿수, 원문토큰, 백분율여부, 라벨내부여부) 를 뽑습니다.

    셀에 `12.44 (4.08)` 처럼 두 값이 든 출력이 흔하므로 전부 뽑습니다.
    번들 쪽에는 건너뜀 규칙을 적용하지 않습니다 — 색인이 넓을수록 원고 숫자의
    출처를 찾을 확률이 올라갑니다. 다만 `phq9_change`, `week8` 처럼 **글자에
    붙어 있는 숫자**는 값이 아니라 이름의 일부이므로 표시해 둡니다. 표시가 없으면
    원고의 "PHQ-9" 가 열 이름 `phq9_change` 에 '출처 확인'돼 버립니다.
    """
    out: List[Tuple[Decimal, int, str, bool, bool]] = []
    if not text:
        return out
    for match in _NUM.finditer(text):
        parsed = _parse_token(match)
        if parsed is None:
            continue
        value, decimals, forced = parsed
        if forced == SKIP_IDENT:
            continue
        if decimals > MAX_DECIMALS and abs(value) >= Decimal(1).scaleb(-MAX_DECIMALS):
            # 부동소수 잔재(0.30000000000000004)는 8자리로 잘라 색인합니다.
            # 다만 `2.2e-16` 처럼 진짜로 작은 값은 0 으로 만들지 않습니다 —
            # 그러면 원고의 표 셀 `0` 이 그 값에 '출처 확인' 돼 버립니다.
            value = value.quantize(Decimal(1).scaleb(-MAX_DECIMALS))
            decimals = MAX_DECIMALS
        start = match.start("num")
        in_label = _looks_like_label(text, start)
        out.append((value, decimals, match.group(0).strip(),
                    bool(match.group("pct")), in_label))
    return out

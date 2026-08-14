"""인용 문구 실존 검증 — 이 툴의 존재 이유 ②.

응답서에 "다음과 같이 수정했습니다"라며 붙여 넣은 문구가 **개정본에 문자 그대로**
있는지 본다. 그 뒤에 공저자 코멘트를 반영해 원고를 두 번 더 고쳤다면, 응답서의
인용문은 이제 원고에 없다. 리뷰어 눈에는 저자가 거짓말을 한 것으로 보인다.

판정 규칙 (오탐을 만들지 않는 것이 최우선이다)
    ① 정규화 후 개정본 **한 문단 안에** 그대로 들어 있으면 → 통과
    ② 없으면 가장 가까운 문장을 찾는다.
       - 그 문장과 **숫자가 다르면 → 치명** (리비전 사고 중 가장 값비싼 것)
       - 숫자는 같고 표현만 다르면(일치율 ≥ 임계) → **경고** + 나란히 출력
    ③ 가까운 문장조차 없으면 → **치명**

    ④ 인용문이 개정본에 없고 **제출본에는 그대로 있으면** → 경고(``제출본문구``).
       개정 전 문장을 인용했을 수 있으므로 치명으로 올리지 않는다. 다만 가장
       가까운 개정본 문장과 숫자가 다르면 그때는 치명이다.

검사에서 빼는 것(그리고 왜 뺐는지 커버리지에 자백하는 것)
    - 15자 미만: 우연 일치가 너무 많다.
    - ``[...]`` · ``…`` 로 줄인 인용: 저자가 일부러 생략한 것이므로 문자 대조가 불가능.
    - 응답서 표에서 **셀 경계를 걸친** 인용(셀 안에 온전히 든 인용은 대조한다).
    - 응답 본문이 리뷰어의 말을 되풀이한 인용("the reviewer asks for …").
    - 블록의 따옴표 개수가 홀수라 짝을 지을 수 없는 경우.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .normalize import canonical, norm_compare, numbers_in_prose
from .textutil import Candidate, best_match, overlap_ratio

__all__ = ["Quote", "QuoteVerdict", "extract_quotes", "verify_quotes"]

MIN_QUOTE_CHARS = 15
MAX_QUOTE_CHARS = 1200
DEFAULT_RATIO = 0.80
# 대조할 인용 후보 수 상한. 넘으면 커버리지에 그대로 자백한다.
MAX_QUOTES = 500
# 인용이 원고 문장의 축약형인지 보는 기준(짧은 쪽이 긴 쪽에 얼마나 들어 있는가).
ABRIDGED_COVERAGE = 0.90
ABRIDGED_MIN_CHARS = 30

_RE_DQUOTE = re.compile(r'"([^"\n]{3,%d})"' % MAX_QUOTE_CHARS)
_RE_CORNER = re.compile(r"[『「]([^』」\n]{3,%d})[』」]" % MAX_QUOTE_CHARS)
_RE_REVISED_LEAD = re.compile(
    r"(?:revised\s+(?:text|sentence|version|as\s+follows)|"
    r"(?:now|it)\s+reads(?:\s+as\s+follows)?|new\s+text|수정(?:된)?\s*문장|"
    r"개정\s*(?:후|문)|바뀐\s*문장|수정문)\s*[:：]",
    re.IGNORECASE,
)
_RE_MD_ITALIC = re.compile(r"^[*_]([^*_].{10,})[*_]$")
_RE_ELLIPSIS = re.compile(r"\[\s*\.\.\.\s*\]|\[…\]|\.\.\.|…|\[\s*중략\s*\]")


@dataclass
class Quote:
    """응답서에서 뽑은 '개정 후 문구' 후보 한 건."""

    text: str
    comment_label: str
    para_no: int
    source: str  # 따옴표 / 블록인용 / Revised text / 이탤릭
    norm: str = ""
    skipped: str = ""  # 비어 있지 않으면 검사에서 제외된 사유

    def __post_init__(self) -> None:
        if not self.norm:
            self.norm = norm_compare(self.text)


@dataclass
class QuoteVerdict:
    quote: Quote
    status: str  # 일치 / 숫자불일치 / 표현불일치 / 축약인용 / 제출본문구 / 없음
    ratio: float = 0.0
    closest: Optional[Candidate] = None
    in_old: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "일치"


@dataclass
class QuoteScan:
    quotes: List[Quote] = field(default_factory=list)
    verdicts: List[QuoteVerdict] = field(default_factory=list)
    skipped: Dict[str, int] = field(default_factory=dict)

    @property
    def checked(self) -> int:
        return len(self.verdicts)

    @property
    def total(self) -> int:
        return len(self.quotes)


def _revised_tail(joined: str) -> Optional[Tuple[str, str]]:
    """``Revised text:`` 뒤의 문장을 조심스럽게 떼어 낸다.

    응답 블록이 줄로 잘려 들어오므로 '문단의 나머지'라는 개념이 없다. 그래서
    뒤쪽을 400자까지만 보고, **문장 끝에서 끊는다.** 끊을 자리가 없으면 인용 범위를
    알 수 없다는 뜻이므로 아예 검사하지 않는다(억지로 대조하면 오탐이 된다).
    """
    m = _RE_REVISED_LEAD.search(joined)
    if not m:
        return None
    tail = joined[m.end():].strip()
    if not tail or tail.startswith('"'):
        return None  # 따옴표가 붙어 있으면 따옴표 규칙이 이미 뽑았다
    tail = tail[:400]
    cut = max(tail.rfind(". "), tail.rfind("다. "), tail.rfind("? "), tail.rfind("! "))
    if tail.endswith((".", "?", "!", "다")):
        return tail, ""
    if cut <= 0:
        return None  # 어디까지가 인용인지 알 수 없다 — 대조하지 않는다
    return tail[: cut + 1], ""


# 저자가 리뷰어의 말을 되풀이하는 관용구. 그 뒤의 따옴표는 '개정 후 문구'가 아니다.
_RE_REVIEWER_VOICE = re.compile(
    r"(?:reviewer|referee|editor|심사위원|리뷰어|검토자|편집)\w*\s*"
    r"(?:'s)?\s*(?:asks?|asked|notes?|noted|suggests?|suggested|states?|stated|writes?|"
    r"wrote|comments?|commented|points? out|raises?|requests?|requested|concerns?|"
    r"지적|요청|언급|질문|의견)",
    re.IGNORECASE,
)
# 인용 직전에 이 정도 앞까지 훑어 리뷰어 인용인지 본다.
_VOICE_WINDOW = 90


def _is_reviewer_voice(joined: str, start: int) -> bool:
    """인용 앞이 '리뷰어가 이렇게 말했다' 인가.

    단, 그 사이에 ``now reads:`` / ``수정문:`` 같은 **개정문 표지**가 끼어 있으면
    리뷰어 말이 아니라 개정 후 문구다("As the reviewer suggested, it now reads: …").
    """
    head = joined[max(0, start - _VOICE_WINDOW):start]
    m = _RE_REVIEWER_VOICE.search(head)
    if not m:
        return False
    return not _RE_REVISED_LEAD.search(head[m.end():])


def _candidates_from_block(block: Sequence, label: str) -> List[Quote]:
    """응답 블록에서 '개정 후 문구' 후보를 뽑는다.

    ``.md``/``.txt`` 응답서는 한 문장이 여러 줄로 접혀 있다(하드 랩). 그래서
    따옴표 인용은 **블록 전체를 이어 붙인 문자열**에서 찾는다 — 줄 단위로 찾으면
    두 줄에 걸친 인용이 통째로 안 잡힌다. 표 셀은 ``|`` 로 이어 붙느라 문장이
    깨지므로 이어 붙이기에서 빼고, 따로 뽑아 '표 안 인용'으로 제외한다.
    """
    found: List[Quote] = []
    if not block:
        return found
    first_no = block[0].no
    joined = canonical(" ".join(p.text for p in block if p.kind != "table"))
    # 따옴표 개수가 홀수면 짝을 지을 수 없다 — 억지로 짝지으면 문장 조각이
    # '인용'으로 잡혀 치명이 쏟아진다. 그럴 때는 따옴표 인용을 통째로 포기하고
    # 커버리지에 자백한다.
    unbalanced = joined.count('"') % 2 == 1
    for m in _RE_DQUOTE.finditer(joined):
        quote = Quote(m.group(1).strip(), label, first_no, "따옴표")
        if unbalanced:
            quote.skipped = "따옴표 짝이 맞지 않음"
        elif _is_reviewer_voice(joined, m.start()):
            quote.skipped = "리뷰어 말 인용"
        found.append(quote)
    for m in _RE_CORNER.finditer(joined):
        quote = Quote(m.group(1).strip(), label, first_no, "따옴표")
        if _is_reviewer_voice(joined, m.start()):
            quote.skipped = "리뷰어 말 인용"
        found.append(quote)
    tail = _revised_tail(joined)
    if tail is not None:
        text, skip = tail
        if text:
            quote = Quote(text.strip(), label, first_no, "Revised text")
            quote.skipped = skip
            found.append(quote)
    for para in block:
        canon = canonical(para.text)
        if not canon:
            continue
        if para.kind == "table":
            # 응답서를 두 칸짜리 표(코멘트 | 응답)로 쓰는 저널이 많다. 셀 **안에**
            # 온전히 들어 있는 인용은 문장이 깨지지 않았으므로 그대로 대조한다.
            # 셀 경계를 걸친 것만(``|`` 가 섞인 것) 제외한다.
            for m in _RE_DQUOTE.finditer(canon):
                text = m.group(1).strip()
                quote = Quote(text, label, para.no, "따옴표")
                if " | " in text:
                    quote.skipped = "표 셀 경계를 걸친 인용"
                found.append(quote)
            continue
        if para.kind == "quote":
            found.append(Quote(canon, label, para.no, "블록인용"))
        elif para.italic:
            found.append(Quote(canon, label, para.no, "이탤릭"))
        else:
            im = _RE_MD_ITALIC.match(canon)
            if im:
                found.append(Quote(im.group(1).strip(), label, para.no, "이탤릭"))
    return found


def _extra_numbers(quote_text: str, target_text: str) -> List[str]:
    """인용문에는 있는데 대상 문장에는 없는 숫자(다중집합 차집합)."""
    target = list(numbers_in_prose(target_text))
    extra: List[str] = []
    for number in numbers_in_prose(quote_text):
        if number in target:
            target.remove(number)
        else:
            extra.append(number)
    return extra


def _dedupe(quotes: Sequence[Quote]) -> List[Quote]:
    seen = set()
    out: List[Quote] = []
    for quote in quotes:
        key = (quote.comment_label, quote.norm)
        if not quote.norm or key in seen:
            continue
        seen.add(key)
        out.append(quote)
    return out


def extract_quotes(comments: Sequence, min_chars: int = MIN_QUOTE_CHARS) -> List[Quote]:
    """코멘트 블록들에서 '개정 후 문구' 후보를 뽑고, 뺄 것에 사유를 붙인다."""
    quotes: List[Quote] = []
    for comment in comments:
        block = _candidates_from_block(comment.body_paras, comment.label)
        # 응답 본문이 **리뷰어 코멘트 원문을 그대로 되풀이**한 부분은 개정 후
        # 문구가 아니다. 코멘트 원문과 대조해 뺀다.
        comment_text = norm_compare(getattr(comment, "question", ""))
        if comment_text:
            for quote in block:
                if not quote.skipped and quote.norm and quote.norm in comment_text:
                    quote.skipped = "리뷰어 말 인용"
        quotes.extend(block)
    quotes = _dedupe(quotes)
    for quote in quotes:
        if quote.skipped:
            continue
        if len(quote.norm) < min_chars:
            quote.skipped = f"{min_chars}자 미만"
        elif _RE_ELLIPSIS.search(quote.text):
            quote.skipped = "생략부호 포함"
        elif len(quote.norm) > MAX_QUOTE_CHARS:
            quote.skipped = "지나치게 긺"
    return quotes


def verify_quotes(
    quotes: Sequence[Quote],
    new_paras: Sequence,
    candidates: Sequence[Candidate],
    old_norm_paras: Sequence[str],
    ratio_threshold: float = DEFAULT_RATIO,
) -> QuoteScan:
    """각 인용문이 개정본에 문자 그대로 있는지 판정한다."""
    scan = QuoteScan(quotes=list(quotes))
    new_norm = [norm_compare(p.text) for p in new_paras]
    budget = MAX_QUOTES
    for quote in quotes:
        if quote.skipped:
            scan.skipped[quote.skipped] = scan.skipped.get(quote.skipped, 0) + 1
            continue
        if budget <= 0:
            quote.skipped = "대조 상한 초과"
            scan.skipped[quote.skipped] = scan.skipped.get(quote.skipped, 0) + 1
            continue
        budget -= 1
        in_old = any(quote.norm in para for para in old_norm_paras)
        if any(quote.norm in para for para in new_norm):
            scan.verdicts.append(QuoteVerdict(quote, "일치", 1.0, None, in_old))
            continue
        closest, ratio = best_match(quote.norm, candidates)
        if closest is None:
            status = "제출본문구" if in_old else "없음"
            scan.verdicts.append(QuoteVerdict(quote, status, ratio, None, in_old))
            continue
        # 인용문에만 있고 개정본 문장에는 없는 숫자 = **어긋난 숫자**.
        # 반대 방향(개정본에만 있는 숫자)은 저자가 인용을 줄여 쓴 것이므로
        # 어긋남이 아니다 — 이걸 구분하지 않으면 "(SD 0.41) 을 뺀 인용" 마다
        # "숫자가 다릅니다" 치명이 뜬다.
        conflicting = _extra_numbers(quote.text, closest.text)
        # 비율 게이트를 **먼저** 통과해야 '숫자가 다르다'고 말할 수 있다.
        # 45% 짜리 남의 문장을 붙들고 "숫자가 다릅니다"라고 하면 저자는
        # 있지도 않은 데이터 불일치를 찾아 헤맨다.
        # 인용이 원고 문장의 **축약형**인가(저자가 (SD …)·p 값을 빼고 인용).
        # 길이가 달라 일치율은 낮지만, 짧은 쪽이 긴 쪽에 거의 그대로 들어 있다.
        abridged = (
            len(quote.norm) >= ABRIDGED_MIN_CHARS
            and overlap_ratio(quote.norm, closest.norm) >= ABRIDGED_COVERAGE
        )
        if ratio >= ratio_threshold or abridged:
            if conflicting:
                status = "숫자불일치"
            else:
                # 문자 그대로는 없지만 어긋난 값은 없다 — 치명이 아니라 경고다.
                status = "표현불일치" if ratio >= ratio_threshold else "축약인용"
        elif in_old and conflicting:
            # 제출본 문장을 '개정 후 문구'라며 인용했는데, 개정본의 같은 자리
            # 숫자가 다르다 — 이건 이 툴이 잡아야 할 바로 그 사고다.
            status = "숫자불일치"
        else:
            status = "없음"
        if in_old and status != "숫자불일치":
            # 개정본에는 없고 **제출본에는 그대로 있는** 문구. 개정 전 문장을
            # 인용했을 가능성이 크므로 치명이 아니라 경고로 남긴다.
            status = "제출본문구"
        scan.verdicts.append(QuoteVerdict(quote, status, ratio, closest, in_old))
    return scan

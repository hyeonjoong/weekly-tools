"""대조 항목 12종 추출 — 닫힌 목록이다(늘리지 않는다).

각 추출기는 "이 문서가 이 항목에 대해 무슨 값을 말하는가"만 뽑는다. 값의 의미를
해석하거나 어느 쪽이 옳은지 판단하지 않는다(강제 장치 5). 못 뽑으면 추측하지 않고
비워 둔다 — 비어 있는 것은 `대조불가` 로 자백된다.

추출을 좁게 유지하는 두 장치:
  * **문맥 게이트** — 항목마다 그 값이 나올 법한 단어가 같은 블록에 있어야 뽑는다
    ('분당서울대병원' 의 '분당' 을 소요시간으로 읽지 않기 위해서다).
  * **인용 블록 배제** — 'Gifford 등(2008) ... 156명' 같은 참고문헌·배경 서술은
    대상자 수로 세지 않는다.
"""

import re

from .normalize import (canon, compact, norm_amount, norm_count_word, norm_date, norm_minutes,
                        norm_version)
from .safeio import mask_then_truncate

# (키, 리포트 표기, 기본 심각도)
ITEMS = [
    ("title", "연구제목", "치명"),
    ("version", "문서 버전·날짜", "치명"),
    ("pi", "연구책임자·기관", "치명"),
    ("contact", "연락처", "경고"),
    ("subjects", "대상자 수", "치명"),
    ("age", "연령 범위", "치명"),
    ("visits", "방문·회기 횟수 / 참여기간", "치명"),
    ("duration", "1회 및 총 소요시간", "치명"),
    ("payment", "보상", "치명"),
    ("retention", "개인정보 보관기간", "치명"),
    ("criteria", "선정·제외기준", "경고"),
    ("assessments", "평가·검사 항목", "경고"),
]
ITEM_LABEL = dict((key, label) for key, label, _ in ITEMS)
ITEM_SEVERITY = dict((key, severity) for key, _, severity in ITEMS)
ITEM_KEYS = [key for key, _, _ in ITEMS]

# 값 자리가 비어 있는 서식(동의서 서명란 등)은 값이 아니다.
_BLANK = re.compile(r"[_]{2,}|\(\s*\)|[·\.]{4,}")
# 참고문헌·선행연구 서술 — 숫자를 여기서 뽑으면 전부 오탐이 된다.
_CITATION = re.compile(r"\[\d{1,3}\]|et\s+al|등\s*\(\s*(?:19|20)\d\d\s*\)|\((?:19|20)\d\d\)")


class Mention(object):
    """한 문서가 한 항목에 대해 말한 값 하나."""

    __slots__ = ("doc", "item", "facet", "value", "display", "block_idx", "where", "quote")

    def __init__(self, doc, item, facet, value, display, block):
        self.doc = doc
        self.item = item
        self.facet = facet
        self.value = value            # 비교에 쓰는 정규화 키
        self.display = display        # 사람이 보는 값
        self.block_idx = block.idx
        self.where = block.where()
        self.quote = _quote(block.text)

    def __repr__(self):  # pragma: no cover
        return "Mention(%r, %s/%s, %r)" % (self.doc, self.item, self.facet, self.value)


def _quote(text, limit=160):
    """리포트에 실릴 근거 문장 — 마스킹을 **먼저** 하고 자른다."""
    return mask_then_truncate(canon(text), limit)


def _is_blank(text):
    return bool(_BLANK.search(text))


def _is_citation(text):
    return bool(_CITATION.search(text))


def _has(text, words):
    return any(word in text for word in words)


# 한 문단 안에서도 "보관 기간을 말하는 절"과 "연구 기간을 말하는 절"은 다르다.
# 문단 전체를 게이트로 쓰면 옆 절의 숫자를 끌어온다(리뷰 라운드1에서 실제로 잡힌 오탐).
_CLAUSE_SPLIT = re.compile(r"[.,;:·]|\band\b|(?<=[가-힣])(?:며|이며|이고|하고|하며)\s")


def gated_clauses(text, gate_words, value_pattern=None):
    """게이트 단어가 든 절과, **그 바로 다음 절**(레이블: 값 형태)을 돌려준다.

    라운드2 회귀: `검사 소요시간: 약 60분` 을 절로 쪼개면 게이트 단어('소요')는 앞 절에,
    숫자는 뒤 절에 남아 값이 통째로 사라졌다. 그래서 **자기 값이 없는 게이트 절**은
    다음 절까지 게이트를 넘겨준다(레이블 뒤에 값이 오는 형태). 이미 값을 가진 절은
    넘기지 않는다 — `3년간 보관 후 파기한다. 연구는 12개월 진행된다` 에서 12개월을
    보관기간으로 끌어오지 않기 위해서다.
    """
    carry = False
    for clause, offset in _clauses(text):
        gated = _has(clause, gate_words)
        if gated or carry:
            yield clause, offset
        carry = gated and not (value_pattern.search(clause) if value_pattern else True)


def _clauses(text):
    """(절 텍스트, 문단 내 시작 위치) 목록."""
    out = []
    position = 0
    for piece in _CLAUSE_SPLIT.split(text):
        if piece is None:
            continue
        start = text.find(piece, position) if piece else position
        if start < 0:
            start = position
        out.append((piece, start))
        position = start + len(piece)
    return out


# ------------------------------------------------------------------ 1. 연구제목

_TITLE_LABEL = re.compile(
    r"(?:연구\s*제목|연구\s*과제\s*명|과제\s*명|임상시험\s*명|연구\s*명|study\s*title|protocol\s*title|title)"
    r"\s*[:：|]\s*(.+)",
    re.I,
)
_TITLE_TRIM = re.compile(r"[^0-9A-Za-z가-힣]")


def _title_key(text):
    """따옴표·낫표·대시·마침표 차이로 같은 제목이 달라 보이지 않게 한다."""
    return _TITLE_TRIM.sub("", canon(text)).lower()


_TITLE_PREFIX = re.compile(r"^\(?\s*(국문|영문|한글|영어|korean|english)\s*(명|title)?\s*\)?\s*[:：]?\s*", re.I)


def _extract_title(doc):
    out = []
    for block in doc.blocks:
        match = _TITLE_LABEL.search(block.text)
        if not match:
            continue
        raw = _TITLE_PREFIX.sub("", match.group(1).strip())
        raw = raw.split("|")[0].strip()
        if len(raw) < 6 or _is_blank(raw):
            continue
        out.append(Mention(doc.name, "title", "제목", _title_key(raw), canon(raw), block))
    return out


# ------------------------------------------------------------------ 2. 버전·날짜

_VERSION_LABEL = re.compile(r"(?:version\s*no\.?|version|ver\.?|버전|개정\s*판)\s*[:：|]?\s*[vV]?\s*(\d{1,3}(?:\.\d{1,3})*)", re.I)
_VERSION_BARE = re.compile(r"(?<![A-Za-z0-9])[vV](\d{1,3}(?:\.\d{1,3})+)(?![\d.])")
_DATE_TOKEN = re.compile(r"(?<!\d)((?:19|20)\d{2}[.\-/]\s?\d{1,2}[.\-/]\s?\d{1,2}|(?:19|20)\d{6}|\d{6})(?!\d)")
_DATE_KOR = re.compile(r"((?:19|20)\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_DATE_CONTEXT = ("버전", "version", "작성", "개정", "승인", "날짜", "date", "일자")

_HEADER_BLOCKS = 60          # 버전·날짜는 문서 앞머리에서만 찾는다


def _filename_versions(name):
    versions = []
    for match in re.finditer(
            r"(?<![A-Za-z0-9])[vV](?:er)?\.?\s?(\d{1,3}(?:[._]\d{1,2}(?![\d]))*)", name):
        value = norm_version(match.group(1).replace("_", "."))
        if value:
            versions.append((value, match.group(0)))
    return versions


def _filename_dates(name):
    dates = []
    for match in _DATE_TOKEN.finditer(name):
        value = norm_date(match.group(1).replace(" ", ""))
        if value:
            dates.append((value, match.group(1)))
    return dates


class _PseudoBlock(object):
    """파일명에서 뽑은 값의 근거 위치."""

    __slots__ = ("idx", "kind", "text", "section")

    def __init__(self, text):
        self.idx = 0
        self.kind = "f"
        self.text = text
        self.section = ""

    def where(self):
        return "파일명"


# 개정 이력 표는 그 문서가 "지금 몇 판인지"를 말하는 곳이 아니다.
# 여기서 버전을 걷으면 문서의 버전 집합이 {1.0, 1.1, 1.2} 가 되어, 구버전이 섞인 패킷을
# "전부 일치" 로 인증해 버린다(리뷰 라운드1에서 실제로 재현된 사고).
_REVISION_HISTORY = re.compile(r"개정\s*이력|변경\s*이력|버전\s*이력|개정\s*연혁|revision\s*history", re.I)
# 서명란의 생년월일 6자리를 문서 날짜로 읽지 않기 위한 배제어.
_BIRTHDATE = re.compile(r"생년월일|birth|주민등록")


def _extract_version(doc):
    """문서의 **신원**(몇 판인지·언제 판인지)만 뽑는다. 문서마다 한 값씩.

    파일명과 본문을 **다른 대조단위**로 나눈다 — 둘을 한 집합에 합치면 그 문서가
    여러 버전을 "말하는" 셈이 되어, 다른 문서의 구버전을 덮어 준다.
    """
    out = []
    name_block = _PseudoBlock(doc.name)

    filename_versions = _filename_versions(doc.name)
    if filename_versions:
        value, raw = filename_versions[0]
        out.append(Mention(doc.name, "version", "버전(파일명)", value, raw, name_block))
    filename_dates = _filename_dates(doc.name)
    if filename_dates:
        value, raw = filename_dates[-1]        # 파일명 끝쪽 날짜가 그 문서의 날짜다
        out.append(Mention(doc.name, "version", "문서날짜(파일명)", value, raw, name_block))

    body_version = None
    body_date = None
    for block in doc.blocks:
        text = block.text
        if _REVISION_HISTORY.search(text) or _REVISION_HISTORY.search(block.section or ""):
            continue
        in_header = block.idx < _HEADER_BLOCKS
        mentions_version = bool(re.search(r"version|버전|ver\.", text, re.I))
        if not (in_header or mentions_version):
            continue
        if body_version is None:
            for match in list(_VERSION_LABEL.finditer(text)) + list(_VERSION_BARE.finditer(text)):
                if "_" in text[match.end():match.end() + 3]:
                    continue                    # 'v ______' 같은 빈 서식
                value = norm_version(match.group(1))
                if value:
                    body_version = Mention(doc.name, "version", "버전", value,
                                           match.group(0).strip(), block)
                    break
        if body_date is None and not _BIRTHDATE.search(text):
            if mentions_version or _has(text.lower(), _DATE_CONTEXT):
                for match in _DATE_KOR.finditer(text):
                    value = norm_date("%s-%s-%s" % match.groups())
                    if value:
                        body_date = Mention(doc.name, "version", "문서날짜", value,
                                            match.group(0), block)
                        break
                if body_date is None:
                    for match in _DATE_TOKEN.finditer(text):
                        value = norm_date(match.group(1).replace(" ", ""))
                        if value:
                            body_date = Mention(doc.name, "version", "문서날짜", value,
                                                match.group(1), block)
                            break
        if body_version is not None and body_date is not None:
            break
    for mention in (body_version, body_date):
        if mention is not None:
            out.append(mention)
    return out


# ------------------------------------------------------------------ 3. 연구책임자·기관

_PI_LABEL = re.compile(
    r"(?:연구\s*책임자|책임\s*연구자|시험\s*책임자|principal\s*investigator|(?<![A-Za-z])PI)\s*"
    r"(?:이름|성명)?\s*[:：|]\s*([^|]{1,60})",
    re.I,
)
_SITE_LABEL = re.compile(
    r"(?:연구\s*실시\s*기관|실시\s*기관\s*명칭|실시\s*기관|시험\s*기관|기관\s*명칭|책임\s*연구자\s*소속|주관\s*연구\s*기관)"
    r"\s*[:：|]\s*([^|]{2,60})"
)
_NAME_STOP = {
    "연구책임자", "책임연구자", "담당자", "공동연구자", "성명", "이름", "소속", "직위", "교수", "박사",
    "연구소장", "해당", "없음", "서명", "서명일", "기관", "부서", "전화", "이메일", "연구자", "확인",
}
_NAME = re.compile(r"[가-힣]{2,4}")


_NAME_AFTER = re.compile(r"^\s*(교수|박사|선생님|선생|님|연구원|원장|소장|과장|,|\)|/|;|$)")


def _person_name(text):
    """'이비인후과 최병윤 교수' 에서 최병윤만. 뒤에 직함·구분자가 와야 이름으로 본다."""
    short_field = len(text.strip()) <= 6
    for match in _NAME.finditer(text):
        token = match.group(0)
        if token in _NAME_STOP:
            continue
        if re.search(r"(과|실|원|팀|부|국|학교|병원|센터|대학|대병)$", token):
            continue
        if short_field or _NAME_AFTER.match(text[match.end():]):
            return token
    return None


def _extract_pi(doc):
    out = []
    for block in doc.blocks:
        for match in _PI_LABEL.finditer(block.text):
            captured = match.group(1)
            if _is_blank(captured):
                continue
            name = _person_name(captured)
            if not name:
                continue
            out.append(Mention(doc.name, "pi", "연구책임자", name, name, block))
        for match in _SITE_LABEL.finditer(block.text):
            site = canon(match.group(1)).split(",")[0].strip(" .")
            if len(site) < 3 or _is_blank(site):
                continue
            out.append(Mention(doc.name, "pi", "실시기관", compact(site), site, block))
    return out


# ------------------------------------------------------------------ 4. 연락처

_PHONE = re.compile(r"(?<![\d\-])(0\d{1,2})[-.\s]?(\d{3,4})[-.\s]?(\d{4})(?![\d\-])")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_CONTACT_CONTEXT = ("전화", "연락", "문의", "tel", "phone", "mail", "메일", "@", "담당")


def _extract_contact(doc):
    out = []
    for block in doc.blocks:
        text = block.text
        low = text.lower()
        if not _has(low, _CONTACT_CONTEXT):
            continue
        if _is_citation(text):
            continue
        for match in _PHONE.finditer(text):
            digits = "".join(match.groups())
            display = "%s-%s-%s" % match.groups()
            out.append(Mention(doc.name, "contact", "전화", digits, display, block))
        for match in _EMAIL.finditer(text):
            value = match.group(0).lower()
            out.append(Mention(doc.name, "contact", "이메일", value, match.group(0), block))
    return out


def is_mobile(digits):
    """개인 휴대전화 번호인가 (01X-)."""
    return bool(re.match(r"^01[016789]", digits or ""))


# ------------------------------------------------------------------ 5. 대상자 수

_TOTAL_N = re.compile(r"총\s*([\d,]{1,6})\s*명")
_LABEL_N = re.compile(r"(?:연구\s*)?(?:대상자|참여자|피험자|표본)\s*수[^|]{0,12}[:：|]\s*(?:총\s*)?([\d,]{1,6})\s*명")
_ANY_N = re.compile(r"(?<![\d,])([\d,]{1,6})\s*명")
MAX_GROUP_N = 5000        # 군별 대상자 수로 인정하는 상한
# '연구책임자 1명과 연구간호사 2명이 참여합니다' 는 대상자 수가 아니다.
_STAFF_WORDS = ("연구책임자", "책임연구자", "공동연구자", "연구원", "연구간호사", "간호사", "검사자",
                "평가자", "담당자", "모니터", "연구자", "시험자", "심의위원", "위원")
_N_CONTEXT = ("대상자", "참여", "모집", "그룹", "군", "n =", "n=", "명이", "명을", "피험자", "예정", "사용자", "아동", "성인")


def _extract_subjects(doc):
    out = []
    for block in doc.blocks:
        text = block.text
        if _is_citation(text) or _is_blank(text):
            continue
        if not _has(text.lower(), _N_CONTEXT):
            continue
        if _has(text, _STAFF_WORDS) and not _has(text, ("대상자", "피험자", "참여자 수", "모집")):
            continue
        totals = []
        for pattern in (_TOTAL_N, _LABEL_N):
            for match in pattern.finditer(text):
                value = norm_amount(match.group(1))
                if value:
                    totals.append((match.span(), value, match.group(0)))
        for span, value, raw in totals:
            out.append(Mention(doc.name, "subjects", "총N", str(value), raw, block))
        spans = [span for span, _, _ in totals]
        staff_spans = [span for clause, offset in _clauses(text) if _has(clause, _STAFF_WORDS)
                       for span in [(offset, offset + len(clause))]]
        for match in _ANY_N.finditer(text):
            if any(match.start() >= start and match.end() <= end for start, end in spans):
                continue
            if any(start <= match.start() < end for start, end in staff_spans):
                continue        # 연구진 인원을 말하는 절
            value = norm_amount(match.group(1))
            if value is None or value == 0 or value > MAX_GROUP_N:
                continue        # 배경 서술의 인구 통계(예: 국내 환자 30,000명)를 군 수로 세지 않는다
            out.append(Mention(doc.name, "subjects", "군별N", str(value), match.group(0).strip(), block))
    return out


# ------------------------------------------------------------------ 6. 연령

_AGE_RANGE = re.compile(r"만?\s*(?<![\d,.])(\d{1,2})\s*세?\s*[~\-]\s*(\d{1,2})\s*세")
# "만 19세 이상 (만) 65세 이하/미만" — 국문 선정기준에서 가장 흔한 두 번째 표기.
_AGE_BOUND_PAIR = re.compile(
    r"만?\s*(\d{1,3})\s*세\s*이상[^.]{0,14}?만?\s*(\d{1,3})\s*세\s*(이하|미만)")
_AGE_OPEN = re.compile(r"만\s*(?<![\d,.])(\d{1,3})\s*세\s*(이상|이하|초과|미만)")
_AGE_SIGN = {"이상": "+", "초과": "+", "이하": "-", "미만": "-"}


def _extract_age(doc):
    out = []
    for block in doc.blocks:
        text = block.text
        if _is_citation(text):
            continue
        spans = []
        for match in _AGE_BOUND_PAIR.finditer(text):
            low, high = int(match.group(1)), int(match.group(2))
            if match.group(3) == "미만":
                high -= 1
            if low >= high or high > 120:
                continue
            spans.append(match.span())
            out.append(Mention(doc.name, "age", "연령범위", "%d-%d세" % (low, high),
                               match.group(0), block))
        for match in _AGE_RANGE.finditer(text):
            if any(match.start() >= start and match.end() <= end for start, end in spans):
                continue
            low, high = int(match.group(1)), int(match.group(2))
            if low >= high or high > 120:
                continue
            spans.append(match.span())
            out.append(Mention(doc.name, "age", "연령범위", "%d-%d세" % (low, high), match.group(0), block))
        for match in _AGE_OPEN.finditer(text):
            if any(match.start() >= start and match.end() <= end for start, end in spans):
                continue
            value = "%d%s세" % (int(match.group(1)), _AGE_SIGN[match.group(2)])
            out.append(Mention(doc.name, "age", "연령범위", value, match.group(0), block))
    return out


# ------------------------------------------------------------------ 7. 방문·회기 / 참여기간

_VISIT_CONTEXT = ("방문", "내원", "회기", "visit", "세션", "session")
_VISIT = re.compile(
    r"(?P<range>(?<![\d.])(?P<rlow>\d{1,2})\s*[~\-]\s*(?P<rhigh>\d{1,2})\s*회)"
    r"|(?P<multi>다\s*회기|여러\s*회기|여러\s*번|multi[-\s]?session)"
    r"|(?P<form>(?:visit|회기)\s*(?<![\d])(?P<fnum>\d{1,2})(?!\s*(?:개|명|시간|분)))"
    r"|(?P<single>(?<![\d.])(?P<snum>\d{1,2})\s*회(?![기당]))"
    r"|(?P<word>(?P<wnum>한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*번(?![에째]))",
    re.I,
)
# "8주 동안 매주 1회 방문" 의 '1회' 는 총 횟수가 아니라 빈도다.
_FREQUENCY_HEAD = re.compile(r"(매주|매월|매달|매일|매\s*회기?|주당|월당|하루|한\s*주에|1주일?에|주\s*\d?\s*)$")
# "1회 방문 소요시간은 90분" 의 '1회' 는 방문 횟수가 아니라 1회당 기준이다.
_PER_VISIT_TAIL = re.compile(r"\s*(방문|회기|세션)?\s*(당|의|에)?\s*(검사\s*)?(소요|시간은|시간이|시간 |시간$)")
_PERIOD_CONTEXT = ("연구기간", "연구 기간", "참여기간", "참여 기간", "승인일", "진행됩니다", "진행될", "수행기간", "예상연구기간", "총 연구")
_PERIOD = re.compile(r"(?<![\d,.])(\d{1,3})\s*(개월|주|년)(?!령)")


def _extract_visits(doc):
    out = []
    form_numbers = {}
    for block in doc.blocks:
        text = block.text
        low = text.lower()
        if _has(low, _VISIT_CONTEXT) and not _is_citation(text):
            for match in _VISIT.finditer(text):
                if match.group("range"):
                    first, second = int(match.group("rlow")), int(match.group("rhigh"))
                    if first >= second or second > 60:
                        continue
                    value = "%d-%d회" % (first, second)
                    out.append(Mention(doc.name, "visits", "방문횟수", value, match.group(0), block))
                elif match.group("multi"):
                    out.append(Mention(doc.name, "visits", "방문횟수", "다회기", match.group(0), block))
                elif match.group("form"):
                    number = int(match.group("fnum"))
                    if 0 < number <= 60:
                        form_numbers.setdefault(number, block)
                elif match.group("word"):
                    number = norm_count_word(match.group("wnum"))
                    head = text[max(0, match.start() - 8):match.start()]
                    if number and not _FREQUENCY_HEAD.search(head):
                        out.append(Mention(doc.name, "visits", "방문횟수", "%d회" % number,
                                           match.group(0), block))
                elif match.group("single"):
                    number = int(match.group("snum"))
                    head = text[max(0, match.start() - 8):match.start()]
                    if (0 < number <= 60
                            and not _PER_VISIT_TAIL.match(text[match.end():match.end() + 16])
                            and not _FREQUENCY_HEAD.search(head)):
                        out.append(Mention(doc.name, "visits", "방문횟수", "%d회" % number, match.group(0), block))
        if _has(text, _PERIOD_CONTEXT) and not _is_citation(text):
            for match in _PERIOD.finditer(text):
                number, unit = int(match.group(1)), match.group(2)
                months = {"개월": number, "주": None, "년": number * 12}.get(unit)
                if unit == "주":
                    value = "%d주" % number
                elif months:
                    value = "%d개월" % months
                else:
                    continue
                out.append(Mention(doc.name, "visits", "참여기간", value, match.group(0), block))

    # 회기 폼(Visit 1 … Visit 6)이 2개 이상이면 "폼이 몇 회기까지 있는가" 를 값으로 본다.
    if len(form_numbers) >= 2:
        highest = max(form_numbers)
        block = form_numbers[highest]
        out.append(Mention(doc.name, "visits", "방문횟수", "%d회" % highest,
                           "Visit/회기 폼 1~%d (= %d회기)" % (highest, highest), block))
    return out


# ------------------------------------------------------------------ 8. 소요시간

_DURATION_CONTEXT = ("소요", "걸려", "걸립", "검사 시간", "참여 시간", "시간은", "총 약", "약 ", "분", "시간")
_DURATION_GATE = ("소요", "걸려", "걸립", "검사", "참여", "진행", "방문", "시간은", "휴식", "실시")
_DURATION = re.compile(
    r"(?P<mixed>(?<![\d,.])(?P<xlow>\d{1,3})\s*분\s*(?:에서|~|-)\s*(?P<xhigh>\d{1,3})\s*시간)"
    r"|(?P<hrange>(?<![\d,.])(?P<hlow>\d{1,3})\s*[~\-]\s*(?P<hhigh>\d{1,3})\s*시간)"
    r"|(?P<mrange>(?<![\d,.])(?P<mlow>\d{1,3})\s*[~\-]\s*(?P<mhigh>\d{1,3})\s*분)"
    r"|(?P<hm>(?<![\d,.])(?P<hmh>\d{1,3})\s*시간\s*(?P<hmm>\d{1,2})\s*분)"
    r"|(?P<hour>(?<![\d,.])(?P<honly>\d{1,3})\s*시간)"
    r"|(?P<minute>(?<![\d,.])(?P<monly>\d{1,3})\s*분(?!당))"
)


def _extract_duration(doc):
    out = []
    for block in doc.blocks:
        text = block.text
        if not _has(text, ("분", "시간")) or not _has(text, _DURATION_GATE):
            continue
        if _is_citation(text) or _is_blank(text):
            continue
        for clause, _offset in gated_clauses(text, _DURATION_GATE, _DURATION):
            _collect_duration(doc, clause, block, out)
    return out


_ELIGIBILITY_TAIL = re.compile(r"\s*(미만|이상|이하|초과)")


def _collect_duration(doc, text, block, out):
    for match in _DURATION.finditer(text):
        groups = match.groupdict()
        if groups["mixed"]:
            low = norm_minutes(groups["xlow"], "분")
            high = norm_minutes(groups["xhigh"], "시간")
        elif groups["hrange"]:
            low = norm_minutes(groups["hlow"], "시간")
            high = norm_minutes(groups["hhigh"], "시간")
        elif groups["mrange"]:
            low = norm_minutes(groups["mlow"], "분")
            high = norm_minutes(groups["mhigh"], "분")
        elif groups["hm"]:
            low = high = (norm_minutes(groups["hmh"], "시간") or 0) + (norm_minutes(groups["hmm"], "분") or 0)
        elif groups["hour"]:
            low = high = norm_minutes(groups["honly"], "시간")
        else:
            low = high = norm_minutes(groups["monly"], "분")
        if not low or not high or low > high:
            continue
        if _ELIGIBILITY_TAIL.match(text[match.end():match.end() + 6]):
            continue          # '6시간 미만인 성인' = 선정기준이지 소요시간이 아니다
        value = "%d분" % low if low == high else "%d-%d분" % (low, high)
        out.append(Mention(doc.name, "duration", "소요시간", value, match.group(0).strip(), block))


# ------------------------------------------------------------------ 9. 보상

_PAY_GATE = ("보상", "사례비", "사례금", "교통비", "지급", "비용", "무상", "지원금", "상품권")
_AMOUNT_WON = re.compile(r"(?<![\d,])([\d,]{2,9})\s*원")
_AMOUNT_MAN = re.compile(r"(?<![\d,])(\d{1,3})\s*만\s*원")
_PAY_FORMS = [
    ("실비", ("실비", "실비지급", "실제 비용")),
    ("정액·사례비", ("시간당", "정액", "차등지급", "차등 지급", "사례비", "사례금")),
    ("교통비", ("교통비",)),
    ("상품권", ("상품권", "기프트", "쿠폰")),
    ("무상·없음", ("지급하지 않", "보상은 없", "제공되지 않", "무상")),
]


def _extract_payment(doc):
    out = []
    for block in doc.blocks:
        text = block.text
        if not _has(text, _PAY_GATE) or _is_citation(text):
            continue
        for match in _AMOUNT_MAN.finditer(text):
            value = norm_amount(match.group(1), unit_man=True)
            if value:
                out.append(Mention(doc.name, "payment", "금액", "%d원" % value, match.group(0), block))
        man_spans = [match.span() for match in _AMOUNT_MAN.finditer(text)]
        for match in _AMOUNT_WON.finditer(text):
            if any(match.start() >= start and match.end() <= end for start, end in man_spans):
                continue
            value = norm_amount(match.group(1))
            if value and value >= 100:
                out.append(Mention(doc.name, "payment", "금액", "%d원" % value, match.group(0), block))
        for label, words in _PAY_FORMS:
            if _has(text, words):
                out.append(Mention(doc.name, "payment", "형태", label, label, block))
    return out


# ------------------------------------------------------------------ 10. 보관기간

_RETENTION_GATE = ("보관", "폐기", "파기", "보존")
_RETENTION = re.compile(r"(?<![\d,.])(\d{1,3})\s*(년|개월)(?:간|\s*동안)?")


def _extract_retention(doc):
    out = []
    for block in doc.blocks:
        text = block.text
        if not _has(text, _RETENTION_GATE) or _is_citation(text):
            continue
        for clause, _offset in gated_clauses(text, _RETENTION_GATE, _RETENTION):
            _collect_retention(doc, clause, block, out)
    return out


def _collect_retention(doc, clause, block, out):
    for match in _RETENTION.finditer(clause):
        number, unit = int(match.group(1)), match.group(2)
        months = number * 12 if unit == "년" else number
        if not 0 < months <= 1200:
            continue
        out.append(Mention(doc.name, "retention", "보관기간", "%d개월" % months, match.group(0), block))


# ------------------------------------------------------------------ 11. 선정·제외기준(군 구성)

_GROUP = re.compile(r"(?<![A-Za-z])[Gg]\s?([1-9])(?![0-9A-Za-z])|그룹\s*([1-9])|(?:^|[\s\[(])군\s*([1-9])")


def _extract_criteria(doc):
    out = []
    for block in doc.blocks:
        text = block.text
        if _is_citation(text):
            continue
        for match in _GROUP.finditer(text):
            number = match.group(1) or match.group(2) or match.group(3)
            out.append(Mention(doc.name, "criteria", "군구성", "G%s" % number, match.group(0).strip(), block))
    return out


# ------------------------------------------------------------------ 12. 평가·검사 항목

_ASSESS = re.compile(r"([가-힣A-Za-z]{2,12}(?:검사|평가|설문|척도))")
_ASSESS_STOP = {
    "검사일자", "검사환경", "본검사", "이검사", "해당검사", "상기검사", "유효성평가", "안전성평가",
    "종합평가", "자가평가", "재평가", "중간평가", "평가", "검사", "설문", "척도", "사전검사", "사후검사",
    "추가검사", "각검사", "모든검사", "위검사",
}


def _extract_assessments(doc):
    """검사 이름 토큰. 문서마다 표기가 달라 값 대조는 하지 않고(대조불가로 자백),
    CRF 에만 있는 검사 폼을 찾는 용도로만 쓴다."""
    out = []
    for block in doc.blocks:
        text = block.text
        if _is_citation(text):
            continue
        is_header = block.kind == "p" and len(text) <= 30
        for match in _ASSESS.finditer(text):
            token = compact(match.group(1))
            if token in _ASSESS_STOP or len(token) < 4:
                continue
            facet = "폼제목" if is_header else "언급"
            out.append(Mention(doc.name, "assessments", facet, token, match.group(1), block))
    return out


EXTRACTORS = {
    "title": _extract_title,
    "version": _extract_version,
    "pi": _extract_pi,
    "contact": _extract_contact,
    "subjects": _extract_subjects,
    "age": _extract_age,
    "visits": _extract_visits,
    "duration": _extract_duration,
    "payment": _extract_payment,
    "retention": _extract_retention,
    "criteria": _extract_criteria,
    "assessments": _extract_assessments,
}

# 항목별 대조 단위(facet). 리포트·대조불가 표에 이 순서로 나온다.
FACETS = {
    "title": ["제목"],
    "version": ["버전", "버전(파일명)", "문서날짜", "문서날짜(파일명)"],
    "pi": ["연구책임자", "실시기관"],
    "contact": ["전화", "이메일"],
    "subjects": ["총N", "군별N"],
    "age": ["연령범위"],
    "visits": ["방문횟수", "참여기간"],
    "duration": ["소요시간"],
    "payment": ["금액", "형태"],
    "retention": ["보관기간"],
    "criteria": ["군구성"],
    "assessments": [],           # 값 대조 안 함 — compare.py 의 CRF 전용 규칙만
}


# ------------------------------------------------------------------ 주제 접촉 판정
#
# "그 문서가 이 항목을 **말하고는 있는데** 값이 안 잡힌" 경우를 구분하기 위한 키워드다.
# 이게 없으면, 표현이 달라 정규식이 놓친 값이 조용히 사라지고 나머지 문서끼리 "일치"로
# 인쇄된다 — 리포트가 "안 나왔으니 맞는 것"으로 읽히는, 이 툴이 만들 수 있는 가장 큰 사고.
TOPIC_KEYWORDS = {
    "title": ("연구제목", "연구 제목", "과제명", "임상시험명", "study title"),
    "version": ("version", "버전", "개정판"),
    "pi": ("연구책임자", "책임연구자", "실시기관", "시험기관"),
    "contact": ("연락처", "전화", "문의", "e-mail", "이메일"),
    "subjects": ("대상자 수", "대상자수", "참여할 예정", "참여합니다", "모집 인원", "모집 대상",
                 "목표 대상자", "표본 수", "피험자 수"),
    "age": ("연령", "나이", "세 이상", "세 이하", "세 미만", "세의 "),
    "visits": ("방문", "회기", "visit", "세션"),
    "duration": ("소요시간", "소요 시간", "검사 시간", "참여 시간", "걸려", "걸립"),
    "payment": ("보상", "사례비", "사례금", "교통비"),   # 맨 '지급' 은 너무 넓다
    "retention": ("보관", "폐기", "파기"),
    "criteria": ("선정기준", "제외기준", "선정 기준", "제외 기준", "참여 대상", "참여 조건"),
    "assessments": ("평가 항목", "검사 항목", "평가항목", "검사항목"),
}


# 단어 하나로는 너무 헐거운 항목은 정규식으로 본다.
# ('연령에 맞는 방식으로 설명한다' 는 자격 연령을 고지한 문장이 아니다 — 실제 승낙서에서
#  이 한 단어 때문에 엉뚱한 전파누락 경고가 나갔다)
TOPIC_PATTERNS = {
    "age": re.compile(
        r"만\s*\d{1,2}|\d{1,2}\s*세|연령\s*(?:범위|기준|제한|은|는|이|가|:)|나이\s*\d"
        # 서술형 연령("스무 살에서 예순네 살") — 값으로 읽지는 못해도 '말하고는 있다'고 세어
        # 커버리지 자백에 남긴다(README 가 그렇게 약속한다).
        r"|(?:열|스무|스물|서른|마흔|쉰|예순|일흔|여든|아흔)\S{0,3}\s*살"),
    # '검체는 냉장고에 보관한다' 는 기록 보관기간 얘기가 아니다 (라운드2 P2-4).
    "retention": re.compile(r"(?:개인정보|개인 정보|기록|자료|데이터|문서|동의서|검체 정보)"
                            r"[^.]{0,24}(?:보관|폐기|파기)|(?:보관|폐기|파기)\s*기간"),
}


def touches_topic(doc, item):
    """이 문서가 그 항목을 **서술로** 말하고는 있는가 (값이 잡혔는지와 무관).

    빈 서식과 표 머리글은 세지 않는다. CRF 의 `| 소요시간 (분) |` 은 방문 때 적을
    칸이지 문서가 소요시간을 고지한 것이 아니어서, 이걸 세면 "우는 체커"가 된다.
    """
    if not doc.read_ok:
        return False
    pattern = TOPIC_PATTERNS.get(item)
    keywords = [keyword.lower() for keyword in TOPIC_KEYWORDS.get(item, ())]
    if not keywords and pattern is None:
        return False
    for block in doc.blocks:
        text = block.text.lower()
        if pattern is not None:
            if not pattern.search(block.text):
                continue
        elif not any(keyword in text for keyword in keywords):
            continue
        if _is_blank(block.text):
            continue
        if block.kind == "t" and not re.search(r"\d", block.text):
            continue                     # 값 없는 표 머리글
        return True
    return False


def extract_all(doc):
    """문서 하나에서 12항목 전부 추출. 읽지 못한 문서는 빈 목록."""
    if not doc.read_ok:
        return []
    mentions = []
    for key in ITEM_KEYS:
        mentions.extend(EXTRACTORS[key](doc))
    return mentions

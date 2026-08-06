"""투고 직전 원고의 **자기 정합성** 점검 7종.

여기 있는 규칙은 전부 결정론적이다 — 세고, 대조하고, 정규식으로 찾는다.
추측하지 않고, 애매하면 등급을 낮추거나(경고/정보) 아예 '점검 불가'로 보고한다.
조용히 통과시키는 체커는 없느니만 못하기 때문이다.

점검 항목
    1 인용 ↔ 참고문헌 교차 대조 (누락·미인용·순서·번호 결함)
    2 그림/표 번호 정합 (미언급·유령 번호·건너뜀·순서)
    3 표본수(N) 일관성 — 초록의 N이 본문/표 어디에도 없을 때만
    4 통계 보고 완결성 (p=0.000, 임계값만 보고, 효과크기/CI 누락, 표기 혼재)
    5 약어 (정의 전 사용, 미정의, 재정의, 미사용)
    6 분량 (제목/초록/본문/참고문헌/그림표 계수 ↔ 저널 한도)
    7 인식 실패 자체를 보고 (인용 0개, 참고문헌 목록 없음 → '점검 불가')
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .docio import Line, Manuscript, Sections, _to_int, caption_of

# ── 자료형 ───────────────────────────────────────────────────────────────────

CRITICAL = "치명"
WARNING = "경고"
INFO = "정보"
_ORDER = {CRITICAL: 0, WARNING: 1, INFO: 2}


@dataclass
class Finding:
    severity: str
    kind: str
    line: Optional[int]
    target: str
    message: str
    advice: str = ""

    def sort_key(self) -> Tuple[int, int]:
        return (_ORDER.get(self.severity, 9), self.line if self.line else 0)


@dataclass
class RefEntry:
    number: Optional[int]
    line: int
    raw: str
    authors: str = ""
    year: str = ""
    title: str = ""
    journal: str = ""
    doi: str = ""
    pmid: str = ""
    key: str = ""  # \bibitem{key}
    parse_ok: bool = False

    @property
    def surname(self) -> str:
        return _first_surname(self.authors)


@dataclass
class Result:
    ms: Manuscript
    sections: Sections
    style: str = "판별불가"  # numeric | author-year | cite-key | 판별불가
    style_source: str = "auto"
    findings: List[Finding] = field(default_factory=list)
    refs: List[RefEntry] = field(default_factory=list)
    counts: Dict[str, object] = field(default_factory=dict)
    limit_rows: List[Tuple[str, int, Optional[int], bool]] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)  # 점검 불가 사유
    coverage: List[str] = field(default_factory=list)
    limits_name: str = ""

    @property
    def unverifiable(self) -> bool:
        return bool(self.blockers)

    def by_severity(self, sev: str) -> List[Finding]:
        return [f for f in self.findings if f.severity == sev]

    @property
    def n_critical(self) -> int:
        return len(self.by_severity(CRITICAL))

    @property
    def n_warning(self) -> int:
        return len(self.by_severity(WARNING))


# ── 공통 유틸 ────────────────────────────────────────────────────────────────

_MAX_PER_CHECK = 12  # 한 항목이 리포트를 도배하지 않게

# 체계적 문헌고찰은 참고문헌이 300개를 넘는 일이 흔하다. 자릿수를 좁게 잡으면
# "[350]이 목록에 없다"는 이 툴의 대표 검출이 조용히 사라진다.
MAX_CITATION_NUMBER = 2000
MAX_CITATION_RANGE = 300

_CITE_NUM_RE = re.compile(r"\[\s*(\d{1,4}(?:\s*[-–—,;]\s*\d{1,4})*)\s*\]")
_TEX_CITE_RE = re.compile(r"\\(?:cite[a-zA-Z]*|footcite)\s*(?:\[[^\]]*\])*\s*\{([^{}]+)\}")
_BIBITEM_RE = re.compile(r"\\bibitem(?:\[[^\]]*\])?\s*\{([^{}]+)\}")
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>,;\]]+)", re.IGNORECASE)
_PMID_RE = re.compile(r"\bPMID\s*:?\s*(\d{4,9})", re.IGNORECASE)

_ABBREV_NOSPLIT = re.compile(
    r"(?:\b(?:et al|e\.g|i\.e|vs|cf|Fig|Figs|No|Dr|Prof|approx|ca|etc|Ref|Eq)\.)\s*$"
)


def sentences(text: str) -> List[str]:
    """문장 단위로 자른다. 'et al.' 류 약어 뒤에서는 자르지 않는다.

    이어 붙일 때 **직전 조각만** 검사한다(정규식이 접미사 검사이므로 결과는 같다).
    누적 문자열을 다시 훑으면 "A vs. B vs. C …" 가 길게 이어진 한 줄에서
    O(n²)가 되어 1 MB 원고 한 줄에 27초가 걸렸다.
    """
    out: List[str] = []
    buffer: List[str] = []
    for part in re.split(r"(?<=[.!?])\s+", text):
        if buffer and _ABBREV_NOSPLIT.search(buffer[-1]):
            buffer.append(part)
            continue
        if buffer:
            out.append(" ".join(buffer))
        buffer = [part]
    if buffer:
        out.append(" ".join(buffer))
    return [s for s in out if s.strip()]


# ── 한국어 조사 ──────────────────────────────────────────────────────────────
# "표 2을", "48가" 같은 틀린 조사는 리포트를 기계가 쓴 것처럼 보이게 만든다.
_DIGIT_HAS_FINAL = {"0": True, "1": True, "2": False, "3": True, "4": False,
                    "5": False, "6": True, "7": True, "8": True, "9": False}


def _has_final(word: str) -> bool:
    """마지막 글자에 받침이 있는가 (숫자는 한국어 읽기 기준: 2=이, 8=팔)."""
    text = word.strip()
    if not text:
        return True
    ch = text[-1]
    if ch in ")]）］":  # "(그림 3)" 처럼 괄호로 닫힌 경우 안쪽 글자로 판단
        text = text[:-1].rstrip()
        ch = text[-1] if text else ch
    if ch.isdigit():
        return _DIGIT_HAS_FINAL[ch]
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    return True


def josa(word: str, with_final: str, without_final: str) -> str:
    """받침에 맞는 조사를 붙인다. ``josa("표 2", "을", "를")`` → ``"표 2를"``."""
    return f"{word}{with_final if _has_final(word) else without_final}"


_WORD_STRIP = ".,;:!?()[]{}\"'“”‘’*_`<>–—"


def count_words(text: str) -> int:
    """단어 수 세기 규칙 (README에 그대로 적혀 있는 것과 동일).

    * 마크다운 제목 표시(``#``)·표 구분자(``|``)·LaTeX 명령은 지운다
    * ``[3]`` / ``[3-5]`` 같은 **번호형 인용은 세지 않는다**
    * 공백으로 나눈 뒤 양끝 문장부호를 떼고, 영숫자/한글이 하나라도 있으면 1단어
    * 하이픈어(``non-contact``)·숫자(``48``)·``p<0.05`` 는 각각 1단어
    * 한글은 띄어쓰기 단위(어절)로 1단어
    """
    text = re.sub(r"^\s{0,3}#{1,6}\s*", " ", text)
    # LaTeX 인용/참조 명령은 번호형 인용과 같은 것이므로 인자까지 통째로 뺀다.
    text = re.sub(
        r"\\(?:cite[a-zA-Z]*|footcite|ref|autoref|eqref|label|pageref)\s*"
        r"(?:\[[^\]]*\])*\s*\{[^{}]*\}",
        " ",
        text,
    )
    text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{([^{}]*)\})?", r" \1 ", text)
    text = text.replace("|", " ")
    text = _CITE_NUM_RE.sub(" ", text)
    n = 0
    for token in text.split():
        stripped = token.strip(_WORD_STRIP)
        if not stripped:
            continue
        if any(ch.isalnum() for ch in stripped):
            n += 1
    return n


def _expand_numbers(group: str) -> List[int]:
    """'3-5, 8' → [3, 4, 5, 8]. 범위가 뒤집혔거나 지나치게 넓으면 버린다."""
    out: List[int] = []
    for chunk in re.split(r"[,;]", group):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.fullmatch(r"(\d{1,4})\s*[-–—]\s*(\d{1,4})", chunk)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if 0 < lo <= hi <= MAX_CITATION_NUMBER and hi - lo <= MAX_CITATION_RANGE:
                out.extend(range(lo, hi + 1))
            continue
        if chunk.isdigit():
            value = int(chunk)
            if 0 < value <= MAX_CITATION_NUMBER:
                out.append(value)
    return out


def _first_surname(authors: str) -> str:
    """저자 문자열에서 첫 저자의 성(姓)만. Vancouver/APA 양쪽을 견딘다."""
    a = authors.strip()
    if not a:
        return ""
    a = re.split(r"[,;&]| and ", a)[0].strip()
    a = re.sub(r"\b(et al\.?|등)\b", "", a, flags=re.IGNORECASE).strip()
    tokens = [t for t in re.split(r"\s+", a) if t]
    if not tokens:
        return ""
    # "Kim HJ" → Kim / "H. J. Kim" → Kim
    if len(tokens) > 1 and re.fullmatch(r"[A-Z]{1,3}\.?", tokens[-1]):
        return tokens[0].strip(".,").lower()
    if len(tokens) > 1 and all(re.fullmatch(r"[A-Z]\.", t) for t in tokens[:-1]):
        return tokens[-1].strip(".,").lower()
    return tokens[0].strip(".,").lower()


def _cap(findings: List[Finding], kind: str, what: str) -> List[Finding]:
    if len(findings) <= _MAX_PER_CHECK:
        return findings
    kept = findings[:_MAX_PER_CHECK]
    kept.append(
        Finding(
            INFO,
            kind,
            None,
            "요약",
            f"{what} {len(findings)}건 중 {_MAX_PER_CHECK}건만 표시했습니다.",
            "앞의 건들을 고치고 다시 실행하면 나머지가 보입니다.",
        )
    )
    return kept


def _citing_lines(ms: Manuscript, sec: Sections) -> List[Line]:
    """인용이 등장할 수 있는 줄 = 참고문헌 목록을 뺀 전부(캡션·각주 포함)."""
    ref_nos = {ln.no for ln in sec.references}
    return [ln for ln in ms.lines if ln.no not in ref_nos]


# ── 인용 토큰 추출 ───────────────────────────────────────────────────────────


@dataclass
class Citation:
    line: int
    raw: str
    number: Optional[int] = None
    surname: str = ""
    year: str = ""
    key: str = ""


def extract_numeric_citations(lines: Sequence[Line]) -> List[Citation]:
    out: List[Citation] = []
    for ln in lines:
        for m in _CITE_NUM_RE.finditer(ln.text):
            for num in _expand_numbers(m.group(1)):
                out.append(Citation(ln.no, m.group(0), number=num))
    return out


def extract_key_citations(lines: Sequence[Line]) -> List[Citation]:
    out: List[Citation] = []
    for ln in lines:
        for m in _TEX_CITE_RE.finditer(ln.text):
            for key in m.group(1).split(","):
                key = key.strip()
                if key:
                    out.append(Citation(ln.no, m.group(0), key=key))
    return out


_PAREN_RE = re.compile(r"\(([^()]{3,240})\)")
_NAME = r"[A-Z\u00C0-\u024F][A-Za-z\u00C0-\u024F'’\-]{1,24}"
_PAREN_CITE = re.compile(
    rf"({_NAME})(?:\s*(?:,|&|and|et\s+al\.?|등)\s*{_NAME}?)*[\s,]+(?:\(|)((?:19|20)\d{{2}}[a-z]?)"
)
_NARRATIVE_CITE = re.compile(
    rf"({_NAME})(?:\s+(?:et\s+al\.?|and\s+{_NAME}|&\s*{_NAME}|등))?\s*\(\s*((?:19|20)\d{{2}}[a-z]?)\s*[);,]"
)


def extract_author_year_citations(lines: Sequence[Line]) -> List[Citation]:
    out: List[Citation] = []
    for ln in lines:
        seen: Set[Tuple[str, str]] = set()
        for m in _PAREN_RE.finditer(ln.text):
            inner = m.group(1)
            if not _YEAR_RE.search(inner):
                continue
            for chunk in re.split(r";", inner):
                cm = _PAREN_CITE.search(chunk)
                if cm:
                    pair = (cm.group(1).lower(), cm.group(2))
                    if pair not in seen:
                        seen.add(pair)
                        out.append(
                            Citation(ln.no, chunk.strip(), surname=pair[0], year=pair[1])
                        )
        for m in _NARRATIVE_CITE.finditer(ln.text + " "):
            pair = (m.group(1).lower(), m.group(2))
            if pair not in seen:
                seen.add(pair)
                out.append(Citation(ln.no, m.group(0), surname=pair[0], year=pair[1]))
    return out


# ── 참고문헌 목록 파싱 ───────────────────────────────────────────────────────

_REF_NUM_START = re.compile(r"^\s*(?:\[(\d{1,3})\]|\((\d{1,3})\)|(\d{1,3})\s*[.)])\s+(?=\S)")


def parse_references(sec: Sections) -> Tuple[List[RefEntry], List[str]]:
    """참고문헌 섹션의 줄들을 항목 목록으로. 줄바꿈된 항목은 이어 붙인다.

    Word에서 자동 번호 매기기(목록 서식)로 만든 참고문헌은 번호가 본문 텍스트에
    없다. 그래서 명시 번호가 거의 없으면 **등장 순서대로 1..n 번호를 부여**하고,
    그 사실을 메모로 남긴다.
    """
    notes: List[str] = []
    entries: List[RefEntry] = []
    current: Optional[RefEntry] = None
    explicit = 0
    numbered_mode = False
    for ln in sec.references:
        text = ln.text.strip()
        if not text:
            continue
        if re.match(r"^\\(?:begin|end)\{thebibliography\}", text):
            continue  # LaTeX 목록의 여닫는 줄은 문헌이 아니다
        bib = _BIBITEM_RE.search(text)
        if bib:
            current = RefEntry(None, ln.no, _BIBITEM_RE.sub("", text).strip(), key=bib.group(1))
            entries.append(current)
            continue
        m = _REF_NUM_START.match(text)
        if m:
            number = int(m.group(1) or m.group(2) or m.group(3))
            explicit += 1
            numbered_mode = True
            current = RefEntry(number, ln.no, text[m.end():].strip())
            entries.append(current)
            continue
        if current is not None and _is_continuation(ln.text, text, numbered_mode, current):
            current.raw = (current.raw + " " + text).strip()
            continue
        current = RefEntry(None, ln.no, text)
        entries.append(current)

    # \bibitem 목록은 키로 대조한다 — 번호를 붙일 필요도, "번호가 없다"고 알릴 필요도 없다.
    keyed = bool(entries) and all(e.key for e in entries)
    if not keyed and entries and explicit < max(1, int(len(entries) * 0.6)):
        if explicit:
            notes.append(
                f"참고문헌 {len(entries)}개 중 {explicit}개에만 번호가 붙어 있어 "
                "등장 순서를 번호로 사용했습니다."
            )
        else:
            notes.append(
                "참고문헌에 번호 텍스트가 없어(워드 자동 번호 목록 등) "
                "등장 순서를 번호로 사용했습니다."
            )
        for i, entry in enumerate(entries, start=1):
            entry.number = i
    for entry in entries:
        _fill_fields(entry)
    return entries, notes


def _is_continuation(raw_line: str, text: str, numbered_mode: bool, current: "RefEntry") -> bool:
    """번호가 없는 이 줄이 **앞 항목의 이어짐**인가?

    번호가 붙은 목록에서는 답이 언제나 '그렇다' — 번호 있는 항목과 없는 항목을
    섞어 쓰는 참고문헌 목록은 없다. 이 판단을 틀리면 줄바꿈된 한 항목이 두 개로
    쪼개져 "인용되지 않은 문헌"이 유령처럼 늘어난다.

    번호 없는(APA·워드 자동번호) 목록에서는 **앞 항목이 이미 연도를 가졌는지**로
    가른다. 연도가 아직 없다면 그 항목은 끝나지 않았으므로 이어지는 줄이다.
    저자 성이 소문자로 시작하는 경우(van der Berg, de Vries)도 새 항목으로 인정한다.
    """
    if numbered_mode:
        return True
    if raw_line[:1].isspace():
        return True  # 들여쓰기 = 이어지는 줄 (APA 목록의 hanging indent)
    if not _YEAR_RE.search(current.raw):
        return True  # 앞 항목에 아직 연도가 없다 = 문장이 끊긴 상태
    starts_like_a_name = bool(re.match(r"^(?:[A-ZÀ-ɏ]|van |de |von |della |da |dos )", text))
    return not (starts_like_a_name and bool(_YEAR_RE.search(text)))


def _fill_fields(entry: RefEntry) -> None:
    """항목 원문에서 저자/연도/제목/저널/DOI/PMID를 최대한 뽑는다(휴리스틱)."""
    raw = entry.raw.strip()
    doi = _DOI_RE.search(raw)
    if doi:
        entry.doi = doi.group(1).rstrip(".,;)")
    pmid = _PMID_RE.search(raw)
    if pmid:
        entry.pmid = pmid.group(1)

    body = _DOI_RE.sub(" ", raw)
    apa = re.match(rf"^(.{{2,220}}?)\s*\(\s*((?:19|20)\d{{2}}[a-z]?)\s*\)\s*\.?\s*(.*)$", body)
    if apa:
        entry.authors = apa.group(1).strip(" .,")
        entry.year = apa.group(2)[:4]
        rest = apa.group(3)
    else:
        year = _YEAR_RE.search(body)
        entry.year = year.group(1) if year else ""
        head, _, rest = body.partition(". ")
        # Vancouver: "Kim H, Lee S, Park J. Title. Journal. 2021;30(4):e13210."
        entry.authors = head.strip(" .,")
        if len(entry.authors) > 220 or not entry.authors:
            entry.authors = entry.authors[:220]
    pieces = [p.strip() for p in re.split(r"\.\s+", rest) if p.strip()]
    if pieces:
        entry.title = pieces[0].strip(" .,")
    if len(pieces) > 1:
        journal = pieces[1]
        journal = re.split(r"[,;]\s*\d|\s\d{4}[;,]", journal)[0]
        entry.journal = journal.strip(" .,")
    entry.parse_ok = bool(entry.authors and entry.year and entry.title)


# ── 1) 인용 ↔ 참고문헌 ───────────────────────────────────────────────────────


def check_citations(result: Result, citations: List[Citation]) -> List[Finding]:
    style = result.style
    refs = result.refs
    out: List[Finding] = []
    label = result.ms.line_label

    if style == "numeric":
        available = {r.number for r in refs if r.number}
        first_seen: Dict[int, int] = {}
        for c in citations:
            if c.number is not None:
                first_seen.setdefault(c.number, c.line)
        missing = [n for n in sorted(first_seen) if n not in available]
        out.extend(
            _cap(
                [
                    Finding(
                        CRITICAL,
                        "인용누락",
                        first_seen[n],
                        f"[{n}]",
                        f"본문 인용 [{n}]에 해당하는 참고문헌이 목록에 없습니다 "
                        f"(목록은 {len(available)}개).",
                        "번호가 밀렸는지 확인하거나 해당 문헌을 목록에 추가하세요.",
                    )
                    for n in missing
                ],
                "인용누락",
                "목록에 없는 인용 번호",
            )
        )
        uncited = [r for r in refs if r.number and r.number not in first_seen]
        out.extend(
            _cap(
                [
                    Finding(
                        CRITICAL,
                        "미인용문헌",
                        r.line,
                        f"[{r.number}]",
                        f"참고문헌 {r.number}번이 본문에서 한 번도 인용되지 않았습니다"
                        + (f" — {r.raw[:60]}…" if r.raw else "")
                        + ".",
                        "본문에 인용을 넣거나 목록에서 빼세요(리뷰어가 가장 자주 지적하는 항목).",
                    )
                    for r in uncited
                ],
                "미인용문헌",
                "인용되지 않은 참고문헌",
            )
        )
        # 첫 등장 순서(밴쿠버: 오름차순이어야 함).
        # 목록에 없는 번호(=이미 '인용누락'으로 보고한 것)는 기준에서 뺀다.
        # 빼지 않으면 [27] 하나가 그 뒤의 정상 인용 전부를 '순서 역전'으로 만든다.
        order = sorted(
            ((n, line) for n, line in first_seen.items() if n in available),
            key=lambda kv: kv[1],
        )
        highest = 0
        reversals: List[Finding] = []
        for num, line_no in order:
            if num < highest:
                reversals.append(
                    Finding(
                        WARNING,
                        "인용순서",
                        line_no,
                        f"[{num}]",
                        f"[{num}]이 [{highest}]보다 뒤에 처음 등장합니다 — "
                        "번호형 스타일은 본문 첫 등장 순서대로 번호를 붙여야 합니다.",
                        "인용 번호를 다시 매기거나(EndNote는 자동) 순서를 확인하세요.",
                    )
                )
            else:
                highest = num
        if len(reversals) > 3:
            # 역전이 여러 곳이면 원인은 하나다 — "번호가 첫 등장 순서를 따르지
            # 않는다". 같은 말을 열두 번 반복하면 진짜 경고들이 묻힌다.
            targets = ", ".join(f.target for f in reversals[:5])
            out.append(
                Finding(
                    WARNING,
                    "인용순서",
                    reversals[0].line,
                    "인용 번호",
                    f"인용 번호가 본문 첫 등장 순서를 따르지 않습니다 — {len(reversals)}곳 "
                    f"(예: {targets} …).",
                    "번호형 스타일에서는 첫 등장 순서대로 번호를 다시 매겨야 합니다"
                    "(EndNote/Zotero의 번호 갱신 기능을 쓰세요).",
                )
            )
        else:
            out.extend(reversals)
        # 목록 자체의 번호 결함
        numbers = [r.number for r in refs if r.number]
        tally = Counter(numbers)
        dupes = sorted(n for n, count in tally.items() if count > 1)
        for n in dupes[:5]:
            out.append(
                Finding(
                    WARNING,
                    "목록번호",
                    next(r.line for r in refs if r.number == n),
                    f"[{n}]",
                    f"참고문헌 번호 {n}번이 두 번 이상 나옵니다.",
                    "번호를 다시 매기세요.",
                )
            )
        if numbers:
            gaps = [n for n in range(1, max(numbers) + 1) if n not in set(numbers)]
            if gaps:
                out.append(
                    Finding(
                        WARNING,
                        "목록번호",
                        refs[0].line if refs else None,
                        "참고문헌",
                        f"참고문헌 번호가 건너뜁니다: {_join_nums(gaps)}.",
                        "번호를 다시 매기세요.",
                    )
                )

    elif style == "cite-key":
        keys = {r.key for r in refs if r.key}
        first_seen_key: Dict[str, int] = {}
        for c in citations:
            if c.key:
                first_seen_key.setdefault(c.key, c.line)
        out.extend(
            _cap(
                [
                    Finding(
                        CRITICAL,
                        "인용누락",
                        line_no,
                        key,
                        rf"\cite{{{key}}} 에 해당하는 \bibitem 이 없습니다.",
                        "키 오타이거나 문헌이 누락됐습니다.",
                    )
                    for key, line_no in sorted(first_seen_key.items())
                    if key not in keys
                ],
                "인용누락",
                "목록에 없는 인용 키",
            )
        )
        out.extend(
            _cap(
                [
                    Finding(
                        CRITICAL,
                        "미인용문헌",
                        r.line,
                        r.key,
                        rf"\bibitem{{{r.key}}} 이 본문에서 인용되지 않았습니다.",
                        "본문에 인용을 넣거나 목록에서 빼세요.",
                    )
                    for r in refs
                    if r.key and r.key not in first_seen_key
                ],
                "미인용문헌",
                "인용되지 않은 참고문헌",
            )
        )

    elif style == "author-year":
        # 저자-연도는 표기 변형이 많아 2급 지원 — 등급을 한 단계 낮춰 보고한다.
        ref_pairs = {(r.surname, r.year) for r in refs if r.surname and r.year}
        ref_surnames = {r.surname for r in refs if r.surname}
        matched = 0
        unmatched: List[Finding] = []
        seen_pairs: Set[Tuple[str, str]] = set()
        for c in citations:
            base_year = c.year[:4]
            if (c.surname, base_year) in ref_pairs:
                matched += 1
                continue
            if (c.surname, base_year) in seen_pairs:
                continue
            seen_pairs.add((c.surname, base_year))
            hint = "" if c.surname in ref_surnames else " (같은 성의 문헌 자체가 없습니다)"
            unmatched.append(
                Finding(
                    WARNING,
                    "인용누락",
                    c.line,
                    f"{c.surname.title()} {base_year}",
                    f"본문 인용 '{c.raw[:48]}'에 맞는 참고문헌을 목록에서 찾지 못했습니다{hint}.",
                    "목록에 있는데도 안 잡혔다면 표기 변형일 수 있습니다 — 눈으로 한 번 확인하세요.",
                )
            )
        out.extend(_cap(unmatched, "인용누락", "목록과 매칭되지 않은 인용"))
        cited_pairs = {(c.surname, c.year[:4]) for c in citations}
        uncited = [
            r for r in refs if r.surname and r.year and (r.surname, r.year) not in cited_pairs
        ]
        out.extend(
            _cap(
                [
                    Finding(
                        WARNING,
                        "미인용문헌",
                        r.line,
                        f"{r.surname.title()} {r.year}",
                        f"참고문헌 '{r.raw[:60]}…'이 본문에서 인용된 흔적이 없습니다.",
                        "인용하거나 목록에서 빼세요(저자-연도 매칭은 2급 지원이라 오탐 가능).",
                    )
                    for r in uncited
                ],
                "미인용문헌",
                "인용되지 않은 참고문헌",
            )
        )
        total = len(citations)
        result.coverage.append(
            f"저자-연도 인용 {total}개 중 {matched}개가 목록과 매칭되었습니다"
            + (f" ({matched / total * 100:.0f}%)" if total else "")
            + " — 저자-연도는 2급 지원입니다."
        )
    return out


def _join_nums(nums: Sequence[int], limit: int = 10) -> str:
    text = ", ".join(str(n) for n in nums[:limit])
    return text + (f" 외 {len(nums) - limit}개" if len(nums) > limit else "")


# ── 2) 그림 / 표 ─────────────────────────────────────────────────────────────

# 언급 인식에서 조심할 것 세 가지:
#   · 한글 "지표 3개", "발표 2회" 의 '표'가 표 번호로 잡히면 안 된다 → 앞뒤를 모두 막는다
#   · LaTeX 의 묶음 공백 "Figure~1" 도 언급이다
#   · "Figures 5; tables 2" 같은 앞머리 계수 줄은 언급이 아니다 → 복수형은 번호 2개 이상 요구
_MENTION_RE = re.compile(
    r"(?<![A-Za-z가-힣])(?P<word>Figures|Figure|Figs?\.|Figs|Fig|Tables|Table|그림|표)"
    r"[\s~ ]*"
    r"(?P<nums>\d{1,3}(?:\s*(?:,|–|—|-|and|to|&|과|와|및)\s*\d{1,3})*|[IVXivx]{1,5}(?![A-Za-z]))"
    r"(?!\s*(?:회|개|명|번|건|년|월|일|시간|점|차|위|%))",
    re.IGNORECASE,
)
_SUPPL_BEFORE = re.compile(r"(?i)(supplement\w*|suppl\.?|online|보충)\s*$")
# 그림 범례 밑의 유의수준 표기(*p < 0.05, **p < 0.01)는 결과 문장이 아니다.
_SIG_LEGEND = re.compile(r"^\s*[*†‡§¶]+\s*[pP]\s*[<≤]")


def check_figures_tables(result: Result) -> List[Finding]:
    ms, sec = result.ms, result.sections
    out: List[Finding] = []
    caption_lines = {ln.no for ln in sec.captions}
    captions: Dict[str, Dict[int, int]] = {"figure": {}, "table": {}}
    dup: List[Finding] = []
    for ln in sec.captions:
        parsed = caption_of(ln.text)
        if not parsed:  # pragma: no cover - sec.captions 는 이미 걸러진 목록
            continue
        kind, num = parsed
        if num in captions[kind]:
            dup.append(
                Finding(
                    WARNING,
                    "그림표",
                    ln.no,
                    f"{_kr(kind)} {num}",
                    f"{_kr(kind)} {num} 캡션이 두 번 나옵니다 "
                    f"({ms.line_label} {captions[kind][num]}, {ln.no}).",
                    "중복 캡션을 지우거나 번호를 고치세요.",
                )
            )
        else:
            captions[kind][num] = ln.no
    out.extend(dup)

    ref_lines = {ln.no for ln in sec.references}
    mentions: Dict[str, Dict[int, int]] = {"figure": {}, "table": {}}
    for ln in ms.lines:
        if ln.no in caption_lines or ln.no in ref_lines:
            continue
        for m in _MENTION_RE.finditer(ln.text):
            if _SUPPL_BEFORE.search(ln.text[max(0, m.start() - 18) : m.start()]):
                continue
            word = m.group("word").lower().rstrip(".")
            kind = "table" if word.startswith(("table", "표")) else "figure"
            raw = m.group("nums")
            roman = _to_int(raw) if not raw[:1].isdigit() else None
            if roman is not None:
                numbers = [roman]
            else:
                numbers = _expand_numbers(
                    raw.replace("and", ",").replace("to", "-")
                    .replace("과", ",").replace("와", ",").replace("및", ",")
                )
            # "Figures 5; tables 2" 같은 앞머리 계수 줄 방어: 복수형인데 번호가
            # 하나뿐이면 언급이 아니라 개수 보고일 가능성이 높다.
            if word.endswith("s") and word not in ("figs", "figs.") and len(numbers) < 2:
                continue
            for num in numbers:
                mentions[kind].setdefault(num, ln.no)

    for kind in ("figure", "table"):
        caps, ments = captions[kind], mentions[kind]
        name = _kr(kind)
        if not caps and not ments:
            continue
        if not caps:
            mentioned = josa(f"{name} {_join_nums(sorted(ments))}", "을", "를")
            result.coverage.append(
                f"{name} 캡션을 원고에서 찾지 못해 번호 대조를 하지 못했습니다 "
                f"(본문은 {mentioned} 언급). "
                + josa(name, "을", "를")
                + " 별도 파일로 내는 저널이면 정상입니다."
            )
            out.append(
                Finding(
                    INFO,
                    "그림표",
                    None,
                    name,
                    f"{name} 캡션이 원고 안에 없어 '본문 언급 ↔ 캡션' 대조를 건너뛰었습니다.",
                    f"캡션(범례)을 원고 끝에 붙이면 {name} 번호까지 점검됩니다.",
                )
            )
            continue
        never = sorted(n for n in caps if n not in ments)
        out.extend(
            _cap(
                [
                    Finding(
                        CRITICAL,
                        "그림표",
                        caps[n],
                        f"{name} {n}",
                        josa(f"{name} {n}", "이", "가") + " 본문에서 한 번도 언급되지 않았습니다.",
                        "본문 적절한 위치에 "
                        + josa(f"({name} {n})", "을", "를")
                        + " 언급하세요 — 리뷰어 단골 지적입니다.",
                    )
                    for n in never
                ],
                "그림표",
                f"미언급 {name}",
            )
        )
        ghost = sorted(n for n in ments if n not in caps)
        out.extend(
            _cap(
                [
                    Finding(
                        CRITICAL,
                        "그림표",
                        ments[n],
                        f"{name} {n}",
                        "본문이 " + josa(f"{name} {n}", "을", "를") + " 언급하는데 그 캡션이 없습니다 "
                        f"(캡션은 {_join_nums(sorted(caps))}).",
                        "번호를 고치거나 빠진 캡션을 추가하세요.",
                    )
                    for n in ghost
                ],
                "그림표",
                f"캡션 없는 {name} 언급",
            )
        )
        gaps = [n for n in range(1, max(caps) + 1) if n not in caps]
        if gaps:
            out.append(
                Finding(
                    WARNING,
                    "그림표",
                    caps[min(caps)],
                    name,
                    f"{name} 번호가 건너뜁니다: {_join_nums(gaps)} (캡션은 {_join_nums(sorted(caps))}).",
                    "번호를 연속으로 다시 매기세요.",
                )
            )
        seq = sorted(((line, n) for n, line in ments.items() if n in caps))
        highest = 0
        rev: List[Finding] = []
        for line_no, n in seq:
            if n < highest:
                rev.append(
                    Finding(
                        WARNING,
                        "그림표",
                        line_no,
                        f"{name} {n}",
                        josa(f"{name} {n}", "이", "가") + f" {name} {highest}보다 뒤에 처음 언급됩니다 — "
                        "번호는 본문 첫 언급 순서를 따라야 합니다.",
                        f"{name} 번호를 언급 순서대로 다시 매기세요.",
                    )
                )
            else:
                highest = n
        out.extend(_cap(rev, "그림표", f"{name} 언급 순서 역전"))
        result.counts[f"n_{kind}s"] = len(caps)
    return out


def _kr(kind: str) -> str:
    return "그림" if kind == "figure" else "표"


# ── 3) 표본수(N) 일관성 ──────────────────────────────────────────────────────
#
# 거짓 양성이 한 건이라도 나오면 이 툴은 두 번 다시 안 열린다. 그래서 라벨을
# **아주 좁게** 잡고(명시적 N 라벨만), "초록의 값이 본문·표 어디에도 없다"는
# 한 방향만 치명으로 본다. 군별 N(24/21 등)은 총 N의 부분집합이므로 걸리지 않는다.

_N_PATTERNS = (
    # 문장 끝의 "N = 45." 도 잡아야 한다. 소수점(45.6)만 배제한다.
    re.compile(r"(?<![A-Za-z0-9])[Nn]\s*=\s*(\d{1,6})(?!\s*[\d]|\.\d)"),
    re.compile(
        r"(?<![\d,.])(\d{1,6})\s+(?:participants|patients|subjects|adults|"
        r"individuals|volunteers|caregivers)\b",
        re.IGNORECASE,
    ),
    re.compile(r"(?:참가자|참여자|환자|대상자|피험자)\s*(\d{1,6})\s*명"),
    re.compile(r"(\d{1,6})\s*명의\s*(?:참가자|참여자|환자|대상자|피험자)"),
)

# 초록에만 나오는 것이 정상인 수 — 선별/제외/평가 인원은 보통 CONSORT 그림에만 있고
# 본문 어디에도 다시 적히지 않는다. 이걸 표본수로 세면 정상 원고에 거짓 치명이 뜬다.
_SCREENING_CONTEXT = re.compile(
    r"(?i)(screen\w*|eligib\w*|assessed for|approach\w*|invit\w*|contact\w*|"
    r"exclud\w*|refus\w*|declin\w*|선별|스크리닝|제외|모집)\W{0,30}$"
)


def _n_values(lines: Sequence[Line]) -> Dict[int, int]:
    """값 -> 처음 등장한 줄번호."""
    found: Dict[int, int] = {}
    for ln in lines:
        for pattern in _N_PATTERNS:
            for m in pattern.finditer(ln.text):
                value = int(m.group(1))
                if not 0 < value <= 999999:
                    continue
                if _SCREENING_CONTEXT.search(ln.text[max(0, m.start() - 40) : m.start()]):
                    continue
                found.setdefault(value, ln.no)
    return found


def check_numbers(result: Result) -> List[Finding]:
    sec = result.sections
    out: List[Finding] = []
    abstract_ns = _n_values(sec.abstract)
    ref_lines = {ln.no for ln in sec.references}
    abstract_lines = {ln.no for ln in sec.abstract}
    elsewhere = _n_values(
        [
            ln
            for ln in result.ms.lines
            if ln.no not in ref_lines and ln.no not in abstract_lines
        ]
    )
    result.counts["n_values_abstract"] = sorted(abstract_ns)
    result.counts["n_values_body"] = sorted(elsewhere)
    if not abstract_ns or not elsewhere:
        return out
    for value, line_no in sorted(abstract_ns.items()):
        if value not in elsewhere:
            near = sorted(elsewhere, key=lambda v: abs(v - value))[:3]
            out.append(
                Finding(
                    CRITICAL,
                    "숫자불일치",
                    line_no,
                    f"N = {value}",
                    "초록의 표본수 " + josa(str(value), "이", "가") + " 본문·표 어디에서도 확인되지 않습니다 "
                    f"(본문/표의 표본수: {_join_nums(near)}).",
                    "초록과 본문·Table 1의 N을 맞추세요. 탈락자 반영 후 초록만 옛 숫자로 남는 일이 흔합니다.",
                )
            )
    if not out and abstract_ns:
        out.append(
            Finding(
                INFO,
                "숫자불일치",
                None,
                "표본수",
                f"초록의 표본수 {_join_nums(sorted(abstract_ns))}가 본문·표에서도 확인됩니다.",
                "",
            )
        )
    return out


# ── 4) 통계 보고 완결성 ──────────────────────────────────────────────────────

_P_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?P<p>[pP])\s*(?:[-‐]?\s*values?)?\s*"
    r"(?P<op>[<>=≤≥]{1,2})\s*(?P<val>\.\d+|\d+(?:\.\d+)?)"
)
_THRESHOLD_RE = re.compile(
    r"(?i)(significance (?:was|level)|level of significance|alpha|α|set at|"
    r"two-?(?:tailed|sided)|considered (?:statistically )?significant|were considered|"
    r"taken to indicate|regarded as significant|deemed significant|threshold|"
    r"유의\s?수준|유의하다고|유의한 것으로|기준으로|양측검정)"
)
_EFFECT_RE = re.compile(
    r"(?i)(95\s*%\s*(?:CI|confidence)|confidence interval|신뢰구간|\bCI\b|"
    r"cohen|hedges|\bd\s*=|\bg\s*=|\br\s*=|\brho\s*=|η|eta[- ]?squared|ω|omega[- ]?squared|"
    r"\bOR\s*=|\bRR\s*=|\bHR\s*=|odds ratio|risk ratio|hazard ratio|\bβ\s*=|\bbeta\s*=|"
    r"effect size|효과\s?크기|Δ|difference of|mean difference|MD\s*=|SMD\s*=)"
)


def check_stats(result: Result) -> List[Finding]:
    ms, sec = result.ms, result.sections
    ref_lines = {ln.no for ln in sec.references}
    out: List[Finding] = []
    incomplete: List[Finding] = []
    thresholds: List[Finding] = []
    leading_zero: Set[str] = set()
    decimals: Set[int] = set()
    total = 0

    caption_lines = {ln.no for ln in sec.captions}
    for ln in ms.lines:
        if ln.no in ref_lines or not ln.stripped:
            continue
        # 표 셀·캡션·유의수준 범례는 "결과 문장"이 아니다. 임상 논문의 모든 그림에
        # "*p < 0.05" 각주가 붙으므로, 여기에 경고를 내면 리포트가 통째로 소음이 된다.
        # (p = 0.000 처럼 문맥과 무관한 치명 오류는 아래에서 계속 잡는다.)
        prose = ln.kind != "table" and ln.no not in caption_lines and not _SIG_LEGEND.match(ln.text)
        for sentence in sentences(ln.text):
            matches = list(_P_RE.finditer(sentence))
            if not matches:
                continue
            is_threshold = bool(_THRESHOLD_RE.search(sentence)) or not prose
            has_effect = bool(_EFFECT_RE.search(sentence))
            for m in matches:
                total += 1
                raw_val = m.group("val")
                op = m.group("op")
                try:
                    value = float(raw_val)
                except ValueError:  # pragma: no cover - 정규식이 보장
                    continue
                leading_zero.add("없음" if raw_val.startswith(".") else "있음")
                if "." in raw_val:
                    decimals.add(len(raw_val.split(".")[1]))
                if value > 1:
                    out.append(
                        Finding(
                            CRITICAL,
                            "통계보고",
                            ln.no,
                            m.group(0),
                            f"p 값이 1을 넘습니다 ({m.group(0)}) — 확률일 수 없습니다.",
                            "검정 통계량과 자릿수를 다시 확인하세요.",
                        )
                    )
                    continue
                if op in ("=", "≤") and value == 0:
                    out.append(
                        Finding(
                            CRITICAL,
                            "통계보고",
                            ln.no,
                            m.group(0),
                            f"'{m.group(0)}' — p 값은 정확히 0이 될 수 없습니다.",
                            "'p < 0.001'로 보고하세요(대부분의 저널 통계 지침).",
                        )
                    )
                    continue
                if not is_threshold and op in ("<", "≤") and value in (0.05, 0.01, 0.1):
                    thresholds.append(
                        Finding(
                            WARNING,
                            "통계보고",
                            ln.no,
                            m.group(0),
                            f"결과 문장에 '{m.group(0)}'처럼 임계값만 있습니다.",
                            "정확한 p 값을 소수 셋째 자리까지 보고하세요 (0.001 미만이면 'p < 0.001').",
                        )
                    )
            if not is_threshold and not has_effect:
                incomplete.append(
                    Finding(
                        WARNING,
                        "통계보고",
                        ln.no,
                        matches[0].group(0),
                        "p 값만 있고 효과크기나 95% CI가 없습니다: "
                        f"'{sentence.strip()[:70]}…'",
                        "효과크기(d, g, η², OR 등)와 95% 신뢰구간을 함께 보고하세요.",
                    )
                )
    out.extend(_cap(thresholds, "통계보고", "임계값만 보고한 p 값"))
    out.extend(_cap(incomplete, "통계보고", "효과크기·CI 없는 p 값"))
    if len(leading_zero) > 1:
        out.append(
            Finding(
                WARNING,
                "통계보고",
                None,
                "p 표기",
                "p 값 표기가 섞여 있습니다 (앞자리 0을 쓴 것과 안 쓴 것이 함께 있음: "
                "예 'p = 0.030' vs 'p = .03').",
                "저널 지침에 맞춰 하나로 통일하세요 (APA는 앞자리 0 없이 'p = .03').",
            )
        )
    if len(decimals) > 2:
        out.append(
            Finding(
                INFO,
                "통계보고",
                None,
                "p 표기",
                f"p 값 소수 자릿수가 {len(decimals)}가지({_join_nums(sorted(decimals))}자리)로 섞여 있습니다.",
                "보통 소수 둘째~셋째 자리로 통일합니다.",
            )
        )
    result.counts["p_values"] = total
    return out


# ── 5) 약어 ──────────────────────────────────────────────────────────────────

_DEF_RE = re.compile(r"\(\s*([A-Za-z][A-Za-z0-9\-]{1,9})\s*[;,)]?\s*\)")
# 접미부까지 한 토큰으로 잡는다: CBT-I, HAM-D, EQ-5D, BELL-001.
# 예전 패턴(`-?\d{1,2}`)은 'BELL-001'을 'BELL-00'으로 잘라, 원고에 존재하지도 않는
# 토큰을 보고하고 --abbrev-ok 로도 끌 수 없게 만들었다.
_USE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,8}(?:-?[A-Z0-9]{1,4})?)(?![a-z])")
# 이 횟수 미만으로 쓰인 미정의 약어는 보고하지 않는다(고유명사 소음 방지).
_UNDEFINED_MIN_USES = 3

# 정의 없이 써도 되는 약어(누구나 아는 것 + 표기 단위). 필요하면 --abbrev-ok 로 추가.
DEFAULT_KNOWN = {
    "DNA", "RNA", "PCR", "HIV", "AIDS", "WHO", "FDA", "EMA", "USA", "UK", "EU",
    "MRI", "CT", "PET", "EEG", "ECG", "EKG", "BMI", "IQ", "ICU", "ER",
    "SD", "SE", "SEM", "CI", "IQR", "OR", "RR", "HR", "ANOVA", "ANCOVA", "MANOVA",
    "SPSS", "SAS", "CSV", "PDF", "HTML", "URL", "DOI", "PMID", "ID", "API",
    "IRB", "GCP", "ICH", "CONSORT", "PRISMA", "STROBE", "STARD", "ITT", "PP",
    "RCT", "AE", "SAE", "CRF", "eCRF", "IEC", "USB", "AI", "ML",
}
_ABBR_STOP = {
    "ABSTRACT", "INTRODUCTION", "METHODS", "METHOD", "RESULTS", "RESULT",
    "DISCUSSION", "CONCLUSION", "CONCLUSIONS", "REFERENCES", "BACKGROUND",
    "OBJECTIVE", "OBJECTIVES", "TABLE", "FIGURE", "APPENDIX", "SUPPLEMENTARY",
    "AND", "OR", "NOT", "THE", "ALL", "NO", "YES", "FOR", "WITH", "FROM",
    "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII",
    "AM", "PM", "USD", "KRW", "NA", "TRUE", "FALSE", "NULL",
}


def check_abbreviations(result: Result, extra_known: Set[str]) -> List[Finding]:
    """약어 정의/사용 점검.

    **초록과 본문은 별개의 정의 범위로 다룬다.** 대부분의 저널이 초록에서 한 번,
    본문에서 다시 한 번 정의하라고 요구하므로, 둘 다 정의한 것을 '재정의'로
    잡으면 정상 원고가 경고 범벅이 된다(실제로 그렇게 만들어 봤고, 그 순간
    이 툴은 못 쓰게 된다).

    표 안의 ``mean (SD)``, ``n (%)`` 같은 표기도 정의로 세지 않는다.
    """
    ms, sec = result.ms, result.sections
    # 참고문헌뿐 아니라 **감사의 글·저자 기여·이해상충** 절도 제외한다.
    # 저자 이니셜(CM, DJ, EC …)이 전부 '정의 없는 약어'로 보고돼, 실제 원고에서
    # 경고 34건 중 27건이 이 소음이었다.
    skip_lines = {ln.no for ln in sec.references} | {ln.no for ln in sec.tail}
    abstract_lines = {ln.no for ln in sec.abstract}
    known = {a.upper() for a in DEFAULT_KNOWN | extra_known}
    # abbr -> scope('초록'|'본문') -> 줄번호 목록
    definitions: Dict[str, Dict[str, List[int]]] = {}
    uses: Dict[str, Dict[str, List[int]]] = {}

    from .docio import heading_key

    for ln in ms.lines:
        if ln.no in skip_lines or not ln.stripped:
            continue
        if heading_key(ln.text):
            continue
        scope = "초록" if ln.no in abstract_lines else "본문"
        if ln.kind != "table":  # 표 셀의 (SD)/(%)는 정의가 아니라 표기다
            for m in _DEF_RE.finditer(ln.text):
                token = m.group(1)
                upper = token.upper()
                if token != upper or len(upper) < 2 or upper in _ABBR_STOP:
                    continue
                before = ln.text[: m.start()].strip()
                if len(before.split()) < 2:
                    continue  # "(ISI)" 앞에 풀네임이 없으면 정의로 보지 않는다
                definitions.setdefault(upper, {}).setdefault(scope, []).append(ln.no)
        for m in _USE_RE.finditer(ln.text):
            token = m.group(1).upper()
            if token in _ABBR_STOP or token.isdigit():
                continue
            if re.fullmatch(r"[IVXLC]+", token):
                continue
            uses.setdefault(token, {}).setdefault(scope, []).append(ln.no)

    out: List[Finding] = []
    early: List[Finding] = []
    undefined: List[Finding] = []
    for abbr, scoped_uses in sorted(uses.items()):
        scoped_defs = definitions.get(abbr, {})
        # "CBT-I"가 "(CBT)"로 정의된(또는 그 반대) 경우를 정의된 것으로 인정한다.
        stem = abbr.split("-")[0]
        defined_anywhere = bool(scoped_defs) or (
            stem != abbr and (stem in definitions or stem in known)
        )
        for scope, use_lines in sorted(scoped_uses.items()):
            def_lines = scoped_defs.get(scope, [])
            if def_lines:
                first_def = min(def_lines)
                before_def = [n for n in use_lines if n < first_def]
                if before_def:
                    early.append(
                        Finding(
                            WARNING,
                            "약어",
                            min(before_def),
                            abbr,
                            f"{scope}에서 약어 '{abbr}'가 정의보다 먼저 사용되었습니다 "
                            f"(첫 정의는 {ms.line_label} {first_def}).",
                            "첫 등장 위치에서 풀어 쓰고 괄호로 약어를 정의하세요.",
                        )
                    )
                if len(def_lines) > 1 and abbr not in known:
                    out.append(
                        Finding(
                            WARNING,
                            "약어",
                            def_lines[1],
                            abbr,
                            f"{scope} 안에서 약어 '{abbr}'가 {len(def_lines)}번 정의됩니다 "
                            f"({ms.line_label} {_join_nums(def_lines)}).",
                            "같은 섹션 안에서는 처음 한 번만 정의하세요.",
                        )
                    )
            elif (
                not defined_anywhere
                and abbr not in known
                and stem not in known
                and len(use_lines) >= _UNDEFINED_MIN_USES
            ):
                # 등급은 '정보'다. 이 규칙은 학회명·데이터베이스명·기관 약어
                # (PROSPERO, MEDLINE, AASM …)를 구별할 방법이 없어서, 경고로 두면
                # 진짜 경고들이 묻힌다. 반대로 '정의보다 먼저 사용'은 확실하므로 경고다.
                undefined.append(
                    Finding(
                        INFO,
                        "약어",
                        use_lines[0],
                        abbr,
                        f"약어 '{abbr}'가 {len(use_lines)}번 쓰였지만 원고 어디에도 정의가 없습니다 "
                        "(고유명사라면 무시하세요).",
                        "첫 등장에서 '풀어 쓴 이름 (약어)' 형태로 정의하거나 "
                        "--abbrev-ok 로 예외 처리하세요.",
                    )
                )
            elif (
                defined_anywhere
                and abbr not in known
                and scope == "본문"
                and len(use_lines) >= 2
            ):
                out.append(
                    Finding(
                        INFO,
                        "약어",
                        use_lines[0],
                        abbr,
                        f"약어 '{abbr}'가 초록에서만 정의되고 본문에서는 정의 없이 쓰입니다.",
                        "많은 저널이 초록과 본문에서 각각 한 번씩 정의하도록 요구합니다.",
                    )
                )
        # 정의만 하고 어디에서도 쓰지 않은 약어
        for scope, def_lines in sorted(scoped_defs.items()):
            first_def = min(def_lines)
            all_uses = [n for lines in scoped_uses.values() for n in lines]
            # 정의한 줄(문단) 안에서 다시 쓰인 경우도 '사용'이다. 괄호 안의 정의
            # 자체가 한 번 잡히므로, 같은 줄에 두 번 이상이면 실제로 쓰인 것이다.
            used_later = any(n > first_def for n in all_uses) or (
                sum(1 for n in all_uses if n == first_def) >= 2
            )
            if not used_later:
                out.append(
                    Finding(
                        INFO,
                        "약어",
                        first_def,
                        abbr,
                        f"{scope}에서 약어 '{abbr}'를 정의했지만 그 뒤로 쓰지 않았습니다.",
                        "쓰지 않는 약어는 정의하지 않는 편이 읽기 좋습니다.",
                    )
                )
    out.extend(_cap(early, "약어", "정의 전 사용된 약어"))
    out.extend(_cap(undefined, "약어", "정의 없는 약어"))
    result.counts["abbreviations"] = len(definitions)
    return out


# ── 6) 분량 ──────────────────────────────────────────────────────────────────

# 저널은 초록 단어수에 키워드 줄을 넣지 않는다. 초록 안에 라벨로 들어 있는
# "Keywords. a; b; c" 줄은 계수에서 뺀다.
_KEYWORDS_LINE = re.compile(r"^\s*[*_]{0,2}\s*(keywords?|key words|주제어|키워드)\b", re.IGNORECASE)

LIMIT_FIELDS = (
    ("title_chars_max", "제목 문자수", "title_chars"),
    ("abstract_words_max", "초록 단어수", "abstract_words"),
    ("body_words_max", "본문 단어수", "body_words"),
    ("references_max", "참고문헌 개수", "ref_entries"),
    ("figures_tables_max", "그림+표 개수", "figures_tables"),
)


def check_length(result: Result, limits: Dict[str, object]) -> List[Finding]:
    sec = result.sections
    caption_lines = {ln.no for ln in sec.captions}
    body_words = sum(
        count_words(ln.text)
        for ln in sec.body
        if ln.kind == "body" and ln.no not in caption_lines
    )
    abstract_words = sum(
        count_words(ln.text)
        for ln in sec.abstract
        if ln.kind != "footnote" and not _KEYWORDS_LINE.match(ln.text)
    )
    counts = result.counts
    counts["title_chars"] = len(sec.title)
    counts["abstract_words"] = abstract_words
    counts["body_words"] = body_words
    counts["figures_tables"] = counts.get("n_figures", 0) + counts.get("n_tables", 0)

    out: List[Finding] = []
    for key, label, count_key in LIMIT_FIELDS:
        actual = int(counts.get(count_key, 0) or 0)
        raw_limit = limits.get(key)
        limit = None
        if isinstance(raw_limit, (int, float)) and not isinstance(raw_limit, bool):
            limit = int(raw_limit)
        result.limit_rows.append((label, actual, limit, limit is None or actual <= limit))
        if limit is not None and actual > limit:
            out.append(
                Finding(
                    WARNING,
                    "분량",
                    sec.headings.get("abstract") if "초록" in label else None,
                    label,
                    f"{label} {actual} — 한도 {limit} ({actual - limit} 초과).",
                    "투고 시스템에서 자동 반려되는 항목입니다. 먼저 줄이세요.",
                )
            )
    return out


# ── 7) 인식 실패 보고 + 전체 실행 ────────────────────────────────────────────


def detect_style(citing: Sequence[Line]) -> Tuple[str, Dict[str, int]]:
    numeric = extract_numeric_citations(citing)
    keys = extract_key_citations(citing)
    author_year = extract_author_year_citations(citing)
    tally = {"numeric": len(numeric), "cite-key": len(keys), "author-year": len(author_year)}
    if keys:
        return "cite-key", tally
    if len(numeric) >= 3 and len(numeric) >= len(author_year):
        return "numeric", tally
    if len(author_year) >= 3:
        return "author-year", tally
    if numeric:
        return "numeric", tally
    if author_year:
        return "author-year", tally
    return "판별불가", tally


def run_checks(
    ms: Manuscript,
    sec: Sections,
    limits: Optional[Dict[str, object]] = None,
    style: str = "auto",
    extra_known: Optional[Set[str]] = None,
) -> Result:
    """모든 점검을 돌리고 :class:`Result` 를 돌려준다."""
    result = Result(ms, sec)
    limits = limits or {}
    result.limits_name = str(limits.get("journal", "")) if limits else ""
    label = ms.line_label
    citing = _citing_lines(ms, sec)

    detected, tally = detect_style(citing)
    if style == "auto":
        result.style = detected
        result.style_source = "자동 판별"
    else:
        result.style = style
        result.style_source = "사용자 지정"
        if detected != style and detected != "판별불가":
            result.coverage.append(
                f"지정한 스타일은 {style}이지만 자동 판별은 {detected}였습니다 "
                f"(번호형 {tally['numeric']}건 / 저자-연도 {tally['author-year']}건)."
            )

    if result.style == "numeric":
        citations = extract_numeric_citations(citing)
    elif result.style == "cite-key":
        citations = extract_key_citations(citing)
    elif result.style == "author-year":
        citations = extract_author_year_citations(citing)
    else:
        citations = []

    refs, ref_notes = parse_references(sec)
    result.refs = refs
    result.coverage.extend(ref_notes)
    result.coverage.extend(sec.notes)
    result.counts["citations"] = len(citations)
    result.counts["ref_entries"] = len(refs)
    # 자기 커버리지 보고는 **항상** 낸다. 예전에는 교차 대조를 못 할 때
    # (refs 0개 등) 이 줄이 통째로 사라져서, 사용자가 "목록이 딴 파일이라 정상"인지
    # "파서가 깨진 것"인지 구별할 수 없었다 — 하필 가장 필요한 순간에.
    style_names = {
        "numeric": "번호형", "author-year": "저자-연도",
        "cite-key": r"\cite 키", "판별불가": "인식된",
    }
    unique = len({c.number or c.key or (c.surname, c.year) for c in citations})
    result.coverage.append(
        f"본문에서 {style_names.get(result.style, result.style)} 인용 표기 "
        f"{len(citations)}개(고유 {unique}개)를 인식했고, "
        f"참고문헌 목록에서 항목 {len(refs)}개를 읽었습니다. ({label} 번호 기준)"
    )

    # ── 항목 7: 조용히 통과하지 않기 ──────────────────────────────────────
    if not sec.references_found:
        result.blockers.append(
            "참고문헌 섹션(References / 참고문헌 제목)을 찾지 못했습니다 — "
            "인용 교차 대조를 수행하지 못했습니다."
        )
    elif not refs:
        result.blockers.append(
            "참고문헌 제목은 찾았지만 항목을 하나도 읽지 못했습니다 — "
            "인용 교차 대조를 수행하지 못했습니다."
        )
    if not citations:
        result.blockers.append(
            "본문에서 인용 표기를 하나도 찾지 못했습니다 "
            f"(번호형 {tally['numeric']}건 / 저자-연도 {tally['author-year']}건 / "
            rf"\cite {tally['cite-key']}건) — 인용 점검을 수행하지 못했습니다."
        )
    if not sec.abstract_found:
        result.coverage.append(
            "초록 섹션 제목을 찾지 못해 초록 단어수·표본수 대조를 건너뛰었습니다."
        )

    findings: List[Finding] = []
    if refs and citations:
        findings += check_citations(result, citations)
    findings += check_figures_tables(result)
    findings += check_numbers(result)
    findings += check_stats(result)
    findings += check_abbreviations(result, extra_known or set())
    findings += check_length(result, limits)

    for reason in result.blockers:
        findings.append(
            Finding(
                CRITICAL,
                "점검불가",
                None,
                "점검 불가",
                reason,
                "이 항목은 '이상 없음'이 아니라 '확인하지 못함'입니다. 눈으로 확인하세요.",
            )
        )
    findings.sort(key=lambda f: f.sort_key())
    result.findings = findings
    return result

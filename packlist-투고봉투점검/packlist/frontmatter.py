"""봉투 안 docx 들 사이의 **표제 3종** 동기화.

비교 항목은 정확히 셋이다 — 제목 · 저자 순서 · 원고번호.
넓히지 않는다. 내용 정합(같은 항목의 값이 문서마다 같은가)은 ``irbpack``
의 축이고, 여기서 그것까지 하면 같은 툴을 두 번 만드는 셈이 된다.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .docxpkg import DocxInfo

#: 비교 항목. **정확히 3개** — 테스트로 고정되어 있다.
FRONTMATTER_FIELDS = ("제목", "저자순서", "원고번호")

MATCH = "일치"
MISMATCH = "불일치"
UNKNOWN = "대조불가"

_MS_PATTERNS = (
    re.compile(r"\bMS\s*(?:No\.?|Number|#)?\s*[:\-]?\s*([A-Z]{2,}[A-Z0-9\-\.]{2,})"),
    re.compile(r"\bManuscript\s*(?:No\.?|Number|ID)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-\.]{3,})", re.IGNORECASE),
    re.compile(r"원고\s*번호\s*[:\-]?\s*(\S{4,})"),
)

_NAME_TAIL = re.compile(r"[\d\*†‡§¶#]+$")

#: 다른 문서가 '원고 제목'이라고 명시적으로 인용한 부분. 커버레터·추천
#: 리뷰어 문서는 대부분 제목을 따옴표로 묶거나 ``entitled`` 뒤에 적는다.
_QUOTED_TITLE = re.compile(r"[“\"'‘]([^”\"'’\n]{25,300})[”\"'’]")
_LABELLED_TITLE = re.compile(
    r"(?:entitled|titled|Manuscript\s*[:\-]|Title\s*[:\-])\s*[“\"'‘]?([^”\"'’\n(]{25,300})",
    re.IGNORECASE)


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", text.lower())


@dataclass
class FieldCheck:
    field: str
    verdict: str
    reference: str = ""
    found: str = ""
    note: str = ""


@dataclass
class DocFrontmatter:
    filename: str
    title: str = ""
    authors: List[str] = field(default_factory=list)
    ms_number: str = ""


@dataclass
class FrontmatterResult:
    reference: Optional[DocFrontmatter] = None
    checks: Dict[str, List[FieldCheck]] = field(default_factory=dict)

    def mismatches(self) -> List[FieldCheck]:
        return [c for checks in self.checks.values() for c in checks if c.verdict == MISMATCH]


def _first_meaningful(info: DocxInfo, min_len: int = 20, limit: int = 8) -> str:
    for para in info.paragraphs[:200]:
        text = para.text.strip()
        if len(text) >= min_len:
            return text
        limit -= 1
        if limit <= 0:
            break
    return ""


def _author_line(info: DocxInfo, title: str) -> str:
    seen_title = not title
    for para in info.paragraphs[:200]:
        text = para.text.strip()
        if not text:
            continue
        if not seen_title:
            seen_title = text == title
            continue
        if "," in text and len(text) <= 600 and sum(ch.isalpha() for ch in text) > 10:
            return text
        return ""
    return ""


def _split_authors(line: str) -> List[str]:
    names = []
    for chunk in re.split(r"[,;]", line):
        piece = chunk.strip().strip("*†‡§¶#")
        piece = _NAME_TAIL.sub("", piece).strip()
        piece = re.sub(r"\s+and\s+", " ", piece, flags=re.IGNORECASE).strip()
        if len(piece) < 2 or len(piece.split()) > 5:
            continue
        if not re.search(r"[A-Za-z가-힣]", piece):
            continue
        # 'Kim a' 처럼 뒤에 붙는 소속 표시 한 글자를 떼어 낸다.
        tokens = piece.split()
        while len(tokens) >= 2 and len(tokens[-1]) == 1 and tokens[-1].isalpha():
            tokens.pop()
        piece = " ".join(tokens)
        if len(piece) < 2:
            continue
        names.append(piece)
    return names


def _surname(name: str) -> str:
    parts = [p for p in re.split(r"[\s\-]+", name) if p]
    return _norm(parts[-1]) if parts else ""


def read_frontmatter(info: DocxInfo) -> DocFrontmatter:
    title = info.title.strip() or _first_meaningful(info)
    authors = _split_authors(_author_line(info, title))
    ms_number = ""
    text = info.text
    for pattern in _MS_PATTERNS:
        match = pattern.search(text)
        if match:
            ms_number = match.group(1).strip().rstrip(".")
            break
    return DocFrontmatter(info.filename, title, authors, ms_number)


def _title_candidates(text: str) -> List[Tuple[str, bool]]:
    """다른 문서가 제목이라고 명시한 문자열들.

    두 번째 값은 '끝이 분명한가'다. 따옴표로 묶인 후보는 어디서 끝나는지
    알 수 있으므로 원고 제목과 **정확히** 같아야 한다. 반면
    ``entitled …`` 뒤의 후보는 문장 나머지까지 딸려 오므로, 원고 제목을
    통째로 품고 있으면 일치로 본다.
    """
    found = []
    for pattern, bounded in ((_QUOTED_TITLE, True), (_LABELLED_TITLE, False)):
        for match in pattern.finditer(text):
            candidate = match.group(1).strip().rstrip(".,;")
            if len(candidate) >= 25:
                found.append((candidate, bounded))
    return found


def _common_prefix_len(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index


def _check_title(ref: DocFrontmatter, other: DocFrontmatter, info: DocxInfo) -> FieldCheck:
    if not ref.title:
        return FieldCheck("제목", UNKNOWN, note="원고에서 제목을 찾지 못했습니다")
    ref_norm = _norm(ref.title)
    target = _norm(info.text)

    # 1) 이 문서가 '제목'이라고 따옴표/`entitled` 로 명시한 문자열이 있으면
    #    그것과 직접 비교한다. 원고가 제목에서 단어를 뺀 경우(옛 제목이
    #    남아 있는 경우)는 단순 포함 검사로는 잡히지 않는다.
    candidates = _title_candidates(info.text)
    if candidates:
        normalised = [(raw, _norm(raw), bounded) for raw, bounded in candidates]
        for _, norm, bounded in normalised:
            if norm == ref_norm or (not bounded and ref_norm in norm):
                return FieldCheck("제목", MATCH, ref.title)
        best, best_len = "", 0
        for raw, norm, bounded in normalised:
            if not bounded:
                continue
            shared = _common_prefix_len(norm, ref_norm)
            if shared > best_len:
                best, best_len = raw, shared
        if best and best_len >= max(25, int(len(ref_norm) * 0.55)):
            return FieldCheck("제목", MISMATCH, ref.title, best,
                              "이 문서가 제목이라고 적어 둔 문자열이 원고 제목과 다릅니다")

    # 2) 명시적 인용이 없으면 본문에 제목이 통째로 들어 있는지 본다.
    if ref_norm and ref_norm in target:
        return FieldCheck("제목", MATCH, ref.title)
    for ratio in (0.85, 0.7, 0.55):
        head = ref_norm[: int(len(ref_norm) * ratio)]
        if len(head) >= 25 and head in target:
            excerpt = _excerpt(info.text, ref.title[: int(len(ref.title) * ratio)])
            return FieldCheck("제목", MISMATCH, ref.title, excerpt,
                              f"앞 {int(ratio * 100)}% 는 같고 뒷부분이 다릅니다")
    return FieldCheck("제목", UNKNOWN, ref.title, note="이 문서에서 제목을 찾지 못했습니다")


def _excerpt(text: str, head: str, extra: int = 80) -> str:
    compact_map = []
    compact = []
    for index, ch in enumerate(text):
        if re.match(r"[0-9A-Za-z가-힣]", ch):
            compact.append(ch.lower())
            compact_map.append(index)
    joined = "".join(compact)
    needle = _norm(head)
    position = joined.find(needle)
    if position < 0:
        return ""
    start = compact_map[position]
    end_index = min(position + len(needle) + extra, len(compact_map) - 1)
    end = compact_map[end_index] + 1
    return text[start:end].strip()


def _check_authors(ref: DocFrontmatter, info: DocxInfo) -> FieldCheck:
    """원고의 저자 순서가 이 문서에도 그 순서대로 나오는가.

    문서 전체에서 성(姓)이 나오는 **순서열**을 만들고, 원고의 순서가 그
    안에 부분수열로 들어 있으면 일치로 본다. 같은 성이 두 번 나오거나
    (`Kim … Kim`) 인사말에 저자 성이 먼저 나와도(`Dear Professor Park,`)
    순서를 거꾸로 읽지 않는다.
    """
    surnames = [s for s in (_surname(n) for n in ref.authors) if len(s) >= 3]
    if len(surnames) < 2:
        return FieldCheck("저자순서", UNKNOWN, note="원고에서 저자 목록을 찾지 못했습니다")
    # **토큰 단위**로 본다. 구분자를 다 지운 한 덩어리에서 부분문자열을 찾으면
    # `c(han)ges` · `s(park)ed` · `rea(son)s` 같은 평범한 단어가 저자 등장으로
    # 잡혀, 순서가 틀린 문서를 '일치'라고 말하게 된다.
    tokens = [_norm(tok) for tok in re.findall(r"[0-9A-Za-z가-힣]+", info.text)]
    wanted = set(surnames)
    sequence = [tok for tok in tokens if tok in wanted]

    present = {name for name in surnames if name in sequence}
    if len(present) < max(2, int(len(set(surnames)) * 0.6)):
        return FieldCheck("저자순서", UNKNOWN, ", ".join(ref.authors),
                          note="이 문서에 저자 이름이 충분히 나오지 않습니다")

    expected = [name for name in surnames if name in present]
    cursor = 0
    for name in expected:
        try:
            cursor = sequence.index(name, cursor) + 1
        except ValueError:
            return FieldCheck("저자순서", MISMATCH, ", ".join(ref.authors),
                              " → ".join(list(dict.fromkeys(sequence))[:10]),
                              "원고와 등장 순서가 다릅니다")
    return FieldCheck("저자순서", MATCH, ", ".join(ref.authors))


def _check_ms(ref: DocFrontmatter, other: DocFrontmatter) -> FieldCheck:
    if not ref.ms_number and not other.ms_number:
        return FieldCheck("원고번호", UNKNOWN, note="어느 문서에도 원고번호가 없습니다")
    if not other.ms_number:
        return FieldCheck("원고번호", UNKNOWN, ref.ms_number, note="이 문서에 원고번호가 없습니다")
    if not ref.ms_number:
        return FieldCheck("원고번호", UNKNOWN, "", other.ms_number, note="원고에 원고번호가 없습니다")
    if _norm(ref.ms_number) == _norm(other.ms_number):
        return FieldCheck("원고번호", MATCH, ref.ms_number, other.ms_number)
    return FieldCheck("원고번호", MISMATCH, ref.ms_number, other.ms_number)


def compare_frontmatter(manuscript: DocxInfo, others: List[DocxInfo]) -> FrontmatterResult:
    """원고를 기준으로 봉투 안 다른 docx 들의 표제 3종을 대조한다."""
    result = FrontmatterResult(reference=read_frontmatter(manuscript))
    ref = result.reference
    for info in others:
        other = read_frontmatter(info)
        result.checks[info.filename] = [
            _check_title(ref, other, info),
            _check_authors(ref, info),
            _check_ms(ref, other),
        ]
    return result

"""원고 본문에서 '약속'을 뽑아낸다 — 그림·표 번호와 보충자료 토큰.

원칙은 하나다: **못 읽은 것은 넓히지 말고 자백한다.** 정규식을 느슨하게
풀어 억지로 잡아내면 오탐이 늘고, 오탐이 한 번 나면 사용자는 리포트를
두 번 열지 않는다.
"""

import re
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .docxpkg import Paragraph

#: 본문 그림 언급. `Fig. 4` · `Fig 4` · `Figure 4` · `Figs. 2` 를 모두 잡는다.
#: 패널 글자를 `[a-h]` 로 좁히면 `Figure 2I` 처럼 i 이후 패널을 쓰는 원고에서
#: **그림 번호까지 통째로 사라진다**. 글자는 한 자만 받고, 패널로 기록할지는
#: 따로 판단한다(`Fig. 2nd` 같은 서수는 뒤 글자가 더 붙으므로 매칭되지 않는다).
FIG_RE = re.compile(r"\bFig(?:ure)?s?\.?\s*(\d{1,3})(?![0-9])([A-Za-z])?(?![A-Za-z0-9])",
                    re.IGNORECASE)

#: 패널로 인정하는 글자. 다중 패널 그림은 보통 a~l 안에서 끝난다.
PANEL_LETTERS = "abcdefghijkl"
#: `Figs. 2-4` · `Fig. 2 and 3` 처럼 한 번에 여러 그림을 부르는 꼬리.
#: 이걸 안 보면 Fig. 3·4 가 '캡션은 있는데 아무도 안 부르는 그림'으로 잘못 걸린다.
FIG_TAIL_RE = re.compile(r"\s*(?:(?P<dash>[-–—~])|,|\s*and\b|,\s*and\b)\s*"
                         r"(?P<number>\d{1,3})(?![0-9])(?P<panel>[A-Za-z])?(?![A-Za-z0-9])",
                         re.IGNORECASE)

#: 본문 표 언급. 보충자료(`Table S3`)는 여기서 제외하고 따로 센다.
TABLE_RE = re.compile(r"\bTable\s*(\d{1,3})\b", re.IGNORECASE)

#: 캡션 문단 = 문단이 `Fig. 4.` / `Table 2:` 처럼 **번호 뒤 구두점**으로 시작한다.
#: 구두점을 요구하지 않으면 `Table 1 lists the sample.` 같은 평범한 본문
#: 첫 문장이 전부 캡션으로 잡혀 인용 수가 0이 된다. 넓히지 않고 좁힌다.
FIG_CAPTION_RE = re.compile(r"^\s*Fig(?:ure)?s?\.?\s*(\d{1,3})\s*[.:|)\-–]\s*", re.IGNORECASE)
TABLE_CAPTION_RE = re.compile(r"^\s*(?:Supplementary\s+)?Table\s*(\d{1,3})\s*[.:|)\-–]\s*", re.IGNORECASE)

#: 보충자료 토큰. 저널마다 표기가 달라 두 어순을 모두 받는다.
_SUPP_PATTERNS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bS(\d{1,2})\s+(Table|Fig(?:ure)?s?|Data|Text|Note|Movie|Video|Material|Methods|Information|Appendix)\b", re.IGNORECASE), "prefix"),
    (re.compile(r"\b(?:Supplementary\s+|Supporting\s+|Additional\s+)?(Table|Fig(?:ure)?s?|Data|Text|Note|Movie|Video|Material|Methods|Information|Appendix)\.?\s+S(\d{1,2})\b", re.IGNORECASE), "suffix"),
    (re.compile(r"\bSupplementary\s+(Table|Fig(?:ure)?s?|Data|Text|Note|Movie|Video|Material|Methods|Information)\s*(\d{1,2})?\b", re.IGNORECASE), "worded"),
    (re.compile(r"\bAppendix\s+([A-Z]|\d{1,2})\b"), "appendix"),
)

_KIND_CANON = {
    "table": "Table", "fig": "Fig", "figs": "Fig", "figure": "Fig", "figures": "Fig",
    "data": "Data", "text": "Text", "note": "Note", "movie": "Movie", "video": "Video",
    "material": "Material", "methods": "Methods", "information": "Information",
    "appendix": "Appendix",
}

#: 봉투에 흔히 들어가는 표준 제출물. 본문이 이름으로 부르지 않는 게 정상이라
#: '본문이 부르지 않는 파일' 경고에서 뺀다.
STANDARD_PART_TOKENS = (
    "cover", "커버", "letter", "레터", "highlight", "하이라이트", "reviewer", "리뷰어",
    "title", "표지", "titlepage", "response", "응답", "rebuttal", "checklist", "체크리스트",
    "consort", "graphical", "abstract", "readme", "가이드", "manuscript", "원고",
    "supplement", "supporting", "보충", "figure", "figures", "declaration",
    "conflict", "coi", "ethic", "윤리", "license", "copyright",
    "icmje", "strobe", "prisma", "spirit", "contribution", "availability",
    "funding", "permission", "별첨", "declarations",
)


@dataclass
class SuppToken:
    """본문이 부른 보충자료 한 종."""

    canonical: str
    count: int = 0
    samples: List[str] = field(default_factory=list)


@dataclass
class TextRefs:
    fig_citations: "Counter[int]" = field(default_factory=Counter)
    fig_captions: "OrderedDict[int, str]" = field(default_factory=OrderedDict)
    fig_caption_dups: List[int] = field(default_factory=list)
    fig_first_order: List[int] = field(default_factory=list)
    table_citations: "Counter[int]" = field(default_factory=Counter)
    table_captions: "OrderedDict[int, str]" = field(default_factory=OrderedDict)
    supp: "OrderedDict[str, SuppToken]" = field(default_factory=OrderedDict)
    body_panels: Dict[int, List[str]] = field(default_factory=dict)
    caption_panels: Dict[int, List[str]] = field(default_factory=dict)

    @property
    def fig_citation_total(self) -> int:
        return sum(self.fig_citations.values())


def _canon_supp(kind: str, number: Optional[str]) -> str:
    kind_key = _KIND_CANON.get(kind.lower(), kind.title())
    if number is None or number == "":
        return f"Supplementary {kind_key}"
    if kind_key == "Appendix":
        return f"Appendix {number}"
    return f"S{int(number)} {kind_key}"


def _panels_in_caption(text: str) -> List[str]:
    """캡션의 `(a-b)` `(a, b)` `(c)` 에서 패널 라벨을 뽑는다."""
    panels = []
    for chunk in re.findall(r"\(([a-l](?:\s*[,\-–]\s*[a-l])*)\)", text):
        letters = re.findall(r"[a-l]", chunk)
        if len(letters) == 2 and re.search(r"[\-–]", chunk):
            start, end = ord(letters[0]), ord(letters[1])
            if start <= end:
                letters = [chr(c) for c in range(start, end + 1)]
        panels.extend(letters)
    return sorted(set(panels))


def _record_figure(refs: "TextRefs", number: int, panel) -> None:
    refs.fig_citations[number] += 1
    if number not in refs.fig_first_order:
        refs.fig_first_order.append(number)
    if panel and panel.lower() in PANEL_LETTERS:
        refs.body_panels.setdefault(number, [])
        if panel.lower() not in refs.body_panels[number]:
            refs.body_panels[number].append(panel.lower())


def extract_refs(paragraphs: List[Paragraph]) -> TextRefs:
    """원고 문단 목록에서 그림/표/보충자료 약속을 모은다.

    캡션 문단의 **머리 라벨**은 인용으로 세지 않는다. 캡션은 그림이 있다는
    선언이지 본문이 그림을 부른 것이 아니기 때문이다. 같은 문단 뒷부분에
    나오는 `Fig. 2` 는 인용으로 센다.
    """
    refs = TextRefs()
    seen_fig_caption = set()
    for para in paragraphs:
        text = para.text
        if not text.strip():
            continue
        fig_start = 0
        cap = FIG_CAPTION_RE.match(text)
        if cap:
            number = int(cap.group(1))
            fig_start = cap.end()
            if number in seen_fig_caption:
                refs.fig_caption_dups.append(number)
            else:
                seen_fig_caption.add(number)
                refs.fig_captions[number] = text.strip()
            panels = _panels_in_caption(text)
            if panels:
                refs.caption_panels.setdefault(number, [])
                refs.caption_panels[number] = sorted(set(refs.caption_panels[number]) | set(panels))
        table_start = 0
        tcap = TABLE_CAPTION_RE.match(text)
        if tcap:
            number = int(tcap.group(1))
            table_start = tcap.end()
            refs.table_captions.setdefault(number, text.strip())

        supp_spans: List[Tuple[int, int]] = []
        for pattern, mode in _SUPP_PATTERNS:
            for match in pattern.finditer(text):
                if mode == "prefix":
                    canonical = _canon_supp(match.group(2), match.group(1))
                elif mode == "suffix":
                    canonical = _canon_supp(match.group(1), match.group(2))
                elif mode == "worded":
                    canonical = _canon_supp(match.group(1), match.group(2))
                else:
                    canonical = f"Appendix {match.group(1)}"
                if any(s <= match.start() < e for s, e in supp_spans):
                    continue
                supp_spans.append((match.start(), match.end()))
                token = refs.supp.setdefault(canonical, SuppToken(canonical))
                token.count += 1
                if len(token.samples) < 3:
                    token.samples.append(match.group(0).strip())

        for match in FIG_RE.finditer(text[fig_start:]):
            offset = fig_start + match.start()
            if any(s <= offset < e for s, e in supp_spans):
                continue    # `Fig S3` 은 본문 그림이 아니라 보충자료다
            _record_figure(refs, int(match.group(1)), match.group(2))
            # `Figs. 2-4` · `Fig. 2, 3 and 4` 의 꼬리를 이어서 읽는다.
            cursor = fig_start + match.end()
            previous = int(match.group(1))
            while True:
                tail = FIG_TAIL_RE.match(text, cursor)
                if tail is None:
                    break
                number = int(tail.group("number"))
                if tail.group("dash") and previous < number <= previous + 20:
                    for middle in range(previous + 1, number):
                        _record_figure(refs, middle, None)
                _record_figure(refs, number, tail.group("panel"))
                previous = number
                cursor = tail.end()
        for match in TABLE_RE.finditer(text[table_start:]):
            offset = table_start + match.start()
            if any(s <= offset < e for s, e in supp_spans):
                continue
            refs.table_citations[int(match.group(1))] += 1
    return refs


def normalize_filename(name: str) -> str:
    """파일명 비교용 정규화: 소문자 + 구분자 제거."""
    return re.sub(r"[\s_\-.()\[\]]+", "", name.lower())


def token_matches_file(canonical: str, filename: str) -> bool:
    """보충자료 토큰 하나가 봉투 안 파일명과 맞는지.

    두 가지 표기를 모두 받는다 — `S1_Table.pdf` 형과
    `Supplementary Table 1.pdf` 형. 뒤쪽은 본문에서 가장 흔한 표기
    ("Supplementary Table 1")를 그대로 파일명으로 쓴 경우다.
    번호 뒤에 숫자가 더 붙으면(`S11`) 다른 항목이므로 맞지 않는다.
    """
    # 확장자를 떼고 비교한다. `Appendix-A.pdf` 를 통째로 압축하면
    # `appendixapdf` 가 되어 `appendixa` 뒤의 경계 판정이 어긋난다.
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    compact = normalize_filename(stem)
    match = re.match(r"^S(\d+)\s+(\w+)$", canonical)
    if match:
        number, kind = match.group(1), match.group(2).lower()
        kinds = {"fig": ("fig", "figure"), "table": ("table", "tab")}.get(kind, (kind,))
        for k in kinds:
            for tag in (rf"s0*{number}{k}", rf"{k}s0*{number}"):
                if re.search(tag + r"(?![0-9])", compact):
                    return True
        has_kind = any(k in compact for k in kinds)
        if not has_kind:
            return False
        if re.search(rf"s0*{number}(?![0-9])", compact):
            return True
        # `supplementarytable1.pdf` — S 없이 종류 바로 뒤에 번호가 붙는 형
        if "supp" in compact or "supporting" in compact or "보충" in stem:
            return any(re.search(rf"{k}0*{number}(?![0-9])", compact) for k in kinds)
        return False
    if canonical.startswith("Appendix "):
        letter = canonical.split()[1].lower()
        return bool(re.search(rf"appendix{letter}(?![0-9a-z])", compact))
    kind = canonical.replace("Supplementary ", "").lower()
    return "supp" in compact and (kind[:4] in compact or kind == "material")


#: 보충자료를 한 파일에 몰아 담은 '통합본' 으로 보이는 파일명.
_CONTAINER_HINTS = ("supplementary", "supplemental", "supporting", "보충자료", "부록")


def looks_like_supplement_container(filename: str) -> bool:
    """`Supplementary_Material.docx` 처럼 S-항목을 통째로 담은 파일인가."""
    compact = normalize_filename(filename)
    if not any(hint in compact for hint in _CONTAINER_HINTS):
        return False
    # `Supplementary_Table_S1.pdf` / `Supplementary_Fig_S1.pdf` 처럼 특정 항목만
    # 담은 파일은 통합본이 아니다. 두 어순을 모두 본다.
    specific = (r"(?:s\d{1,2}(?:table|fig|figure|data|note)"
                r"|(?:table|fig|figure|data|note)s?\d{1,2})")
    return re.search(specific, compact) is None


def token_text_pattern(canonical: str) -> "re.Pattern":
    """통합본 문서 **안에서** 그 항목이 실제로 나오는지 찾을 정규식."""
    match = re.match(r"^S(\d+)\s+(\w+)$", canonical)
    if match:
        number, kind = match.group(1), match.group(2)
        kind_re = r"Fig(?:ure)?s?" if kind.lower() == "fig" else re.escape(kind)
        # 종류와 `S` 사이에는 공백이 반드시 있어야 한다. 없으면 복수형
        # `Tables 1` 의 s 가 `S` 로 읽혀 없는 항목을 있다고 말하게 된다.
        return re.compile(
            rf"(?:\bS\s?0*{number}\s+{kind_re}\b"
            rf"|\b(?:{kind_re})s?\.?\s+S\s?0*{number}(?![0-9])"
            rf"|\bSupplement(?:ary|al)\s+(?:{kind_re})s?\.?\s*0*{number}(?![0-9]))",
            re.IGNORECASE)
    return re.compile(re.escape(canonical), re.IGNORECASE)


#: `Fig3.tif` · `Table_2.pdf` · `그림4.eps` 처럼 번호가 붙은 제출물 파일.
_NUMBERED_FILE_RE = re.compile(r"(fig(?:ure)?|table|tbl|그림|표)0*(\d{1,3})(?![0-9])")


def numbered_items_in_filename(filename: str):
    """파일명이 가리키는 (종류, 번호) 쌍. 종류는 ``fig`` 또는 ``table``."""
    out = []
    for kind, number in _NUMBERED_FILE_RE.findall(normalize_filename(filename)):
        canonical = "table" if kind in ("table", "tbl", "표") else "fig"
        out.append((canonical, int(number)))
    return out


def looks_standard_part(filename: str) -> bool:
    compact = normalize_filename(filename)
    return any(token in compact for token in STANDARD_PART_TOKENS)

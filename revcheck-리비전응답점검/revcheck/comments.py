"""리뷰어 코멘트 번호 전수 점검 — 이 툴의 존재 이유 ①.

사람은 **없는 것을 보지 못한다.** 응답서에 2-4 다음이 2-6 이면, 저자는 스물여섯
번을 다 읽고도 2-5 가 통째로 빠진 것을 못 본다. 리뷰어는 자기 코멘트라서 3초 만에 본다.

그래서 여기서는 응답서에서 코멘트 표지를 **전수로 뽑아**, 리뷰어별 번호 집합을
만들고 구멍·중복·빈 응답을 센다. 번호 체계를 잡지 못하면 **추측하지 않고**
판정불가(종료코드 3)로 멈춘다 — 절반만 읽고 "모두 응답되었습니다"라고 말하는 것이
이 툴이 막으려는 바로 그 사고이기 때문이다.

지원하는 번호 체계
    Reviewer 1, Comment 3   /  R1-3  /  1-3:  /  Comment 3:  (리뷰어 블록 안)
    Reviewer #2 아래 1) 2)  /  Editor 블록  /  심사위원 1 - 3
직접 지정 모드
    ``--comments 1-1,1-2,2-1`` 로 번호를 직접 주면 파싱을 신뢰하지 않고 그 목록을
    기준으로 응답서를 뒤진다(형식이 특이한 저널용 안전장치).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .docio import Para
from .normalize import canonical, norm_compare

__all__ = ["Comment", "CommentScan", "scan_comments", "parse_comment_ids"]

EDITOR = "Editor"

# ── 표지 정규식 ─────────────────────────────────────────────────────────────
_REV_WORD = r"(?:reviewer|referee|reviewer's|심사위원|심사자|리뷰어|검토자)"
_CMT_WORD = r"(?:comment|point|question|코멘트|의견|지적|질문|사항)"
_EDITOR_WORD = r"(?:editor(?:-in-chief)?|associate\s+editor|편집위원(?:장)?|편집장|에디터)"

# 리뷰어/에디터 블록 머리
_RE_REV_HEADER = re.compile(rf"^\s*{_REV_WORD}\s*[#:]?\s*(\d{{1,3}})\b", re.IGNORECASE)
_RE_EDITOR_HEADER = re.compile(rf"^\s*{_EDITOR_WORD}\b", re.IGNORECASE)

# (리뷰어, 번호) 를 한 표지에서 모두 읽어 내는 패턴들 = 계열 A
_PAIR_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("Reviewer N, Comment M",
     re.compile(rf"^\s*{_REV_WORD}\s*[#]?\s*(\d{{1,3}})\s*[,\-–—:]?\s*{_CMT_WORD}\s*[#]?\s*(\d{{1,3}})\s*[:.)\]]?",
                re.IGNORECASE)),
    ("RN-M",
     re.compile(r"^\s*R\s*[#]?\s*(\d{1,3})\s*[-–—.]\s*(\d{1,3})\s*[:.)\]]?(?:\s|$)", re.IGNORECASE)),
    ("Comment N-M",
     re.compile(rf"^\s*{_CMT_WORD}\s*[#]?\s*(\d{{1,3}})\s*[-–—.]\s*(\d{{1,3}})\s*[:.)\]]?", re.IGNORECASE)),
    ("N-M:",
     re.compile(r"^\s*[\[(]?(\d{1,3})\s*[-–—]\s*(\d{1,3})[\])]?\s*[:.)\]]")),
    ("심사위원 N - M",
     re.compile(rf"^\s*{_REV_WORD}\s*(\d{{1,3}})\s*[-–—]\s*(\d{{1,3}})\s*[:.)\]]?", re.IGNORECASE)),
)

# 리뷰어 블록 안에서 번호만 쓰는 패턴들 = 계열 B
_SOLO_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("Comment N:", re.compile(rf"^\s*{_CMT_WORD}\s*[#]?\s*(\d{{1,3}})\s*[:.)\]]", re.IGNORECASE)),
    ("N)", re.compile(r"^\s*[\[(]?(\d{1,3})\s*[)\].:]\s+\S")),
)

# 응답 본문의 시작 표지
_RE_RESPONSE = re.compile(
    r"(?:^|\s)(?:authors?['’]?\s*)?(?:response|reply|answer|저자\s*답변|저자\s*응답|답변|응답|회신)"
    r"\s*[:：]",
    re.IGNORECASE,
)

# 응답 본문이 이보다 짧으면 '사실상 응답이 없다'로 본다.
MIN_BODY_CHARS = 30
# 코멘트를 이보다 적게 잡으면 번호 체계를 못 잡은 것으로 보고 판정불가로 멈춘다.
MIN_COMMENTS = 3
# 리뷰어 한 명의 번호 구멍이 이보다 크면 코멘트 누락이 아니라 **번호 오타**다.
# (``Comment 1-999`` 하나 때문에 없는 코멘트 996건을 지어내면 리포트가 죽는다.)
MAX_GAP = 20
# 번호가 1 이 아니라 2 부터 시작하면 1번 응답이 통째로 빠졌을 수 있다. 다만
# 저널이 0-based 이거나 이어 번호를 쓰는 경우가 있어, 시작이 이 값 이하일 때만 본다.
MAX_MISSING_HEAD = 5


@dataclass
class Comment:
    """코멘트 한 건과 그에 딸린 응답 블록."""

    reviewer: str  # "R1" / "Editor"
    number: int
    label: str  # 화면 표기: "1-3" / "Editor-2"
    para_index: int  # 응답서 문단 목록에서의 위치(0-based)
    marker: str  # 표지 원문
    marker_len: int = 0  # 표지 원문의 길이(응답 본문을 떼어 낼 때 쓴다)
    block: List = field(default_factory=list)  # Para 들 (코멘트 원문 + 응답)
    body: str = ""  # 응답 본문
    question: str = ""  # 리뷰어 코멘트 원문(응답 본문을 뺀 나머지)
    # 인용 문구는 **응답 본문에서만** 뽑는다. 코멘트 원문에는 리뷰어가 지적하려고
    # 인용한 '개정 전' 문장이 들어 있어서, 거기서 인용을 뽑으면 오탐이 쏟아진다.
    body_paras: List = field(default_factory=list)

    @property
    def block_text(self) -> str:
        return " ".join(p.text for p in self.block)

    @property
    def body_length(self) -> int:
        return len(norm_compare(self.body))


@dataclass
class CommentScan:
    comments: List[Comment] = field(default_factory=list)
    scheme: str = ""
    reviewers: List[str] = field(default_factory=list)
    per_reviewer: Dict[str, List[int]] = field(default_factory=dict)
    missing: List[Tuple[str, int, str]] = field(default_factory=list)  # (리뷰어, 번호, 근거)
    duplicates: List[Tuple[str, int]] = field(default_factory=list)
    thin: List[Comment] = field(default_factory=list)  # 응답 본문이 없거나 30자 미만
    not_found: List[str] = field(default_factory=list)  # --comments 지정분 중 못 찾은 것
    silent_blocks: List[str] = field(default_factory=list)  # 머리는 찾았으나 코멘트 0건
    wild_gaps: List[Tuple[str, int, int]] = field(default_factory=list)  # 오타로 보이는 큰 구멍
    undecidable: str = ""

    @property
    def ok(self) -> bool:
        return not self.undecidable


def _reviewer_label(key: str, number: int) -> str:
    if key == EDITOR:
        return f"Editor-{number}"
    return f"{key[1:]}-{number}"


def _reviewer_sort_key(key: str) -> Tuple[int, int]:
    if key == EDITOR:
        return (1, 0)
    try:
        return (0, int(key[1:]))
    except ValueError:  # pragma: no cover - 방어
        return (0, 0)


def _find_headers(paras: Sequence) -> Dict[int, str]:
    """리뷰어/에디터 블록 머리 위치 → 리뷰어 키."""
    headers: Dict[int, str] = {}
    for idx, para in enumerate(paras):
        text = para.text.strip()
        # 블록 머리는 **짧은 제목 줄**이다. "Reviewer 2 raised the same point, so …"
        # 같은 본문 문장을 머리로 오인하면 그 뒤의 인용이 통째로 잘려 나간다.
        if not text or len(text) > 40:
            continue
        # 표지가 곧 코멘트인 줄(``Reviewer 1, Comment 2:``)은 머리가 아니다.
        if any(pat.match(text) for _name, pat in _PAIR_PATTERNS):
            continue
        m = _RE_REV_HEADER.match(text)
        if m and _header_like(text[m.end():]):
            headers[idx] = f"R{int(m.group(1))}"
            continue
        m = _RE_EDITOR_HEADER.match(text)
        if m and _header_like(text[m.end():]):
            headers[idx] = EDITOR
    return headers


def _header_like(tail: str) -> bool:
    """머리 줄에서 표지 뒤에 남은 부분이 '제목다운가'.

    ``Reviewer 2``, ``Reviewer #2 (Statistics)``, ``Editor comments:`` 는 머리이지만
    ``Reviewer 2 raised the same point, so we answer both here.`` 는 본문이다.
    """
    tail = tail.strip(" :.-—–()[]")
    if not tail:
        return True
    if any(ch in tail for ch in ".!?,;"):
        return False
    return len(tail.split()) <= 3


def _scan_pair_family(paras: Sequence) -> List[Tuple[int, str, int, str, str]]:
    """계열 A: 표지 하나에서 리뷰어와 번호를 모두 읽는다."""
    best: List[Tuple[int, str, int, str, str]] = []
    best_name = ""
    for name, pattern in _PAIR_PATTERNS:
        hits: List[Tuple[int, str, int, str, str]] = []
        for idx, para in enumerate(paras):
            m = pattern.match(para.text.strip())
            if m:
                hits.append(
                    (idx, f"R{int(m.group(1))}", int(m.group(2)), m.group(0).strip(), name)
                )
        if len(hits) > len(best):
            best, best_name = hits, name
    return best if len(best) >= MIN_COMMENTS else []


def _scan_solo_family(
    paras: Sequence, headers: Dict[int, str]
) -> List[Tuple[int, str, int, str, str]]:
    """계열 B: 리뷰어 블록 안에서 번호만 쓰는 표지."""
    if not headers:
        return []
    best: List[Tuple[int, str, int, str, str]] = []
    for name, pattern in _SOLO_PATTERNS:
        hits: List[Tuple[int, str, int, str, str]] = []
        current = ""
        for idx, para in enumerate(paras):
            if idx in headers:
                current = headers[idx]
                continue
            if not current:
                continue
            m = pattern.match(para.text.strip())
            if m:
                hits.append((idx, current, int(m.group(1)), m.group(0).strip(), name))
        if name == "N)" and not _plausible_enumeration(hits):
            # ``1) 2) 3)`` 은 응답 본문 속 목록일 수도 있다. 리뷰어별로 1 부터
            # 증가하는 모양이 아니면 코멘트 번호로 인정하지 않는다.
            continue
        if len(hits) > len(best):
            best = hits
        if len(best) >= MIN_COMMENTS and name != "N)":
            break
    return best if len(best) >= MIN_COMMENTS else []


def _plausible_enumeration(hits: Sequence[Tuple[int, str, int, str, str]]) -> bool:
    if len(hits) < MIN_COMMENTS:
        return False
    per: Dict[str, List[int]] = {}
    for _idx, reviewer, number, _marker, _name in hits:
        per.setdefault(reviewer, []).append(number)
    for numbers in per.values():
        if numbers != sorted(numbers) or len(numbers) != len(set(numbers)):
            return False
        if numbers[0] != 1:
            return False
        # 구멍이 3개를 넘으면 코멘트 번호가 아니라 남의 목록일 가능성이 크다.
        if numbers[-1] - numbers[0] + 1 > len(numbers) + 3:
            return False
    return True


def _split_blocks(
    paras: Sequence,
    hits: Sequence[Tuple[int, str, int, str, str]],
    headers: Dict[int, str],
) -> List[Comment]:
    comments: List[Comment] = []
    starts = [h[0] for h in hits]
    for pos, (idx, reviewer, number, marker, _name) in enumerate(hits):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(paras)
        # 다음 리뷰어 블록 머리를 넘어가지 않는다.
        for head_idx in sorted(headers):
            if idx < head_idx < end:
                end = head_idx
                break
        block = list(paras[idx:end])
        comments.append(
            Comment(
                reviewer=reviewer,
                number=number,
                label=_reviewer_label(reviewer, number),
                para_index=idx,
                marker=canonical(marker),
                marker_len=len(marker),
                block=block,
            )
        )
    for comment in comments:
        _extract_body(comment)
    return comments


def _trimmed(para: Para, cut: int) -> Para:
    """``Response:`` 표지 뒤부터만 남긴 문단 사본(원본은 건드리지 않는다)."""
    return Para(
        no=para.no,
        text=para.text[cut:].strip(),
        kind=para.kind,
        line_start=para.line_start,
        line_end=para.line_end,
        italic=para.italic,
        section=para.section,
    )


def _extract_body(comment: Comment) -> None:
    """블록에서 **응답 본문**만 떼어 낸다(코멘트 원문은 뺀다)."""
    for pos, para in enumerate(comment.block):
        m = _RE_RESPONSE.search(para.text)
        if m:
            head = _trimmed(para, m.end())
            comment.body_paras = ([head] if head.text else []) + list(
                comment.block[pos + 1:]
            )
            comment.question = " ".join(
                [p.text for p in comment.block[:pos]] + [para.text[: m.start()]]
            ).strip()
            break
    else:
        if len(comment.block) > 1:
            comment.body_paras = list(comment.block[1:])
            comment.question = comment.block[0].text
        elif comment.block:
            head = comment.block[0]
            trimmed = _trimmed(head, comment.marker_len)
            comment.body_paras = [trimmed] if trimmed.text else []
            comment.question = head.text[: comment.marker_len]
    comment.body = " ".join(p.text for p in comment.body_paras).strip()


def parse_comment_ids(spec: str) -> List[Tuple[str, int]]:
    """``--comments "1-1,1-2,E-1"`` → ``[("R1",1), ("R1",2), ("Editor",1)]``."""
    out: List[Tuple[str, int]] = []
    for chunk in re.split(r"[,\s]+", spec.strip()):
        if not chunk:
            continue
        m = re.fullmatch(r"(?:R)?(\d+)[-–—.](\d+)", chunk, re.IGNORECASE)
        if m:
            out.append((f"R{int(m.group(1))}", int(m.group(2))))
            continue
        m = re.fullmatch(r"(?:E|Editor|편집)[-–—.]?(\d+)", chunk, re.IGNORECASE)
        if m:
            out.append((EDITOR, int(m.group(1))))
            continue
        raise ValueError(
            f"코멘트 번호를 알아볼 수 없습니다: '{chunk}'. "
            "1-1,1-2,2-1,E-1 형식으로 적어 주세요."
        )
    if not out:
        raise ValueError("--comments 에 번호가 하나도 없습니다.")
    return out


def _locate_ids(
    paras: Sequence, ids: Sequence[Tuple[str, int]]
) -> Tuple[List[Tuple[int, str, int, str, str]], List[str]]:
    """직접 지정 모드: 주어진 번호를 응답서에서 찾아 표지 위치를 만든다."""
    hits: List[Tuple[int, str, int, str, str]] = []
    not_found: List[str] = []
    used: set = set()
    for reviewer, number in ids:
        label = _reviewer_label(reviewer, number)
        if reviewer == EDITOR:
            pattern = re.compile(
                rf"(?:^|\s)(?:{_EDITOR_WORD}[^\n]{{0,20}}?)?{_CMT_WORD}?\s*[#]?\s*{number}\b",
                re.IGNORECASE,
            )
        else:
            rev_no = reviewer[1:]
            pattern = re.compile(
                rf"(?:^|\s)(?:R\s*{rev_no}|{_REV_WORD}\s*[#]?\s*{rev_no}\s*[,\-–—:]?\s*"
                rf"(?:{_CMT_WORD}\s*[#]?\s*)?|{_CMT_WORD}\s*[#]?\s*{rev_no})"
                rf"\s*[-–—.]?\s*{number}\b",
                re.IGNORECASE,
            )
        found = False
        for idx, para in enumerate(paras):
            if idx in used:
                continue
            if pattern.search(para.text.strip()):
                hits.append((idx, reviewer, number, label, "직접지정"))
                used.add(idx)
                found = True
                break
        if not found:
            not_found.append(label)
    hits.sort(key=lambda h: h[0])
    return hits, not_found


def scan_comments(
    response_paras: Sequence, ids: Optional[Sequence[Tuple[str, int]]] = None
) -> CommentScan:
    """응답서 문단들 → 코멘트 전수 점검 결과."""
    scan = CommentScan()
    headers = _find_headers(response_paras)

    if ids:
        hits, not_found = _locate_ids(response_paras, ids)
        scan.scheme = "직접 지정(--comments)"
        scan.not_found = not_found
        if not hits:
            scan.undecidable = (
                "--comments 로 지정한 번호를 응답서에서 하나도 찾지 못했습니다. "
                "번호 표기가 응답서와 같은지 확인하세요."
            )
            return scan
    else:
        hits = _scan_pair_family(response_paras)
        if hits:
            scan.scheme = hits[0][4]
        else:
            hits = _scan_solo_family(response_paras, headers)
            scan.scheme = hits[0][4] + " (리뷰어 블록 기준)" if hits else ""
        if len(hits) < MIN_COMMENTS:
            scan.undecidable = (
                f"응답서에서 리뷰어 코멘트 번호를 {len(hits)}건밖에 찾지 못했습니다"
                f"(최소 {MIN_COMMENTS}건 필요). 번호 체계를 추측하지 않고 멈춥니다 — "
                "`--comments 1-1,1-2,...` 로 번호를 직접 알려 주면 그 목록으로 점검합니다."
            )
            return scan

    scan.comments = _split_blocks(response_paras, hits, headers)

    per: Dict[str, List[int]] = {}
    for comment in scan.comments:
        per.setdefault(comment.reviewer, []).append(comment.number)
    scan.per_reviewer = per
    scan.reviewers = sorted(per, key=_reviewer_sort_key)

    for reviewer in scan.reviewers:
        numbers = per[reviewer]
        seen = set()
        for number in numbers:
            if number in seen:
                scan.duplicates.append((reviewer, number))
            seen.add(number)
        if not seen:
            continue
        low, high = min(seen), max(seen)
        if high - low > MAX_GAP + len(seen):
            # 구멍이 비정상적으로 크다 — 없는 코멘트를 수백 건 지어내지 않는다.
            scan.wild_gaps.append((reviewer, low, high))
            continue
        # 저널에 따라 리뷰어를 가로질러 번호를 **이어서** 매긴다(R1: 1~3, R2: 4~6).
        # 그때 앞 번호는 남의 코멘트이므로 결번이 아니다.
        continued = (low - 1) in {n for r, nums in per.items() if r != reviewer for n in nums}
        if 1 < low <= MAX_MISSING_HEAD and not continued:
            # 번호는 보통 1 부터 시작한다. 앞쪽이 통째로 빠진 것도 누락이다.
            for number in range(1, low):
                scan.missing.append(
                    (reviewer, number, f"응답서의 첫 번호가 {_reviewer_label(reviewer, low)} 입니다")
                )
        for number in range(low, high + 1):
            if number in seen:
                continue
            before = max((n for n in seen if n < number), default=None)
            after = min((n for n in seen if n > number), default=None)
            reason = (
                f"응답서에서 {_reviewer_label(reviewer, before)} 다음이 "
                f"{_reviewer_label(reviewer, after)} 입니다"
                if before is not None and after is not None
                else "번호가 이어지지 않습니다"
            )
            scan.missing.append((reviewer, number, reason))

    covered = {c.reviewer for c in scan.comments}
    for reviewer in dict.fromkeys(headers.values()):
        if reviewer not in covered:
            scan.silent_blocks.append(reviewer)

    scan.thin = [c for c in scan.comments if c.body_length < MIN_BODY_CHARS]
    return scan

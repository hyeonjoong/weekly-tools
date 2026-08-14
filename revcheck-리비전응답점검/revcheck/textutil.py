"""문장 쪼개기와 근접 문장 찾기 — 인용 문구 대조의 뼈대.

여기에는 유사도 **판정**이 없다. 유사도는 "개정본에 없다"고 말할 때
사람에게 **가장 가까운 문장을 나란히 보여 주기 위한 보조 정보**로만 쓴다.
판정 자체는 정규화 후 문자열 포함 여부다.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from .normalize import norm_compare

__all__ = ["Candidate", "split_sentences", "best_match", "build_candidates",
           "overlap_ratio"]

# 문장 끝. 약어(e.g., i.e., Fig., vs., et al., 소수점)에서 끊기지 않도록
# 뒤에 공백+대문자/한글이 오는 경우만 자른다.
_SENT_END = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z가-힣])|(?<=[。！？])\s*")
_ABBREV = re.compile(
    r"\b(?:e\.g|i\.e|cf|vs|et al|Fig|Figs|Tab|No|Dr|Prof|approx|ca|St|Mr|Ms)\.$",
    re.IGNORECASE,
)

# 한 번의 대조에서 비교할 후보 창의 최대 개수. 원고가 아무리 커도
# 대조 시간이 폭발하지 않도록 막는다(초과분은 커버리지에 자백한다).
MAX_CANDIDATES = 20_000


@dataclass(frozen=True)
class Candidate:
    """개정본에서 뽑은 비교 후보 한 조각(문장 또는 연속 문장 묶음)."""

    text: str  # 원문 그대로 (화면 출력용)
    norm: str  # 비교용 정규화
    para_no: int
    section: str = ""


def split_sentences(text: str) -> List[str]:
    """문단 하나를 문장 목록으로 쪼갠다. 빈 조각은 버린다."""
    if not text or not text.strip():
        return []
    pieces = _SENT_END.split(text)
    out: List[str] = []
    for piece in pieces:
        if piece is None:
            continue
        piece = piece.strip()
        if not piece:
            continue
        # 약어에서 잘렸으면 앞 조각에 도로 붙인다.
        if out and _ABBREV.search(out[-1]):
            out[-1] = f"{out[-1]} {piece}"
        else:
            out.append(piece)
    return out


def build_candidates(paras: Sequence, max_window: int = 3) -> List[Candidate]:
    """개정본 문단들에서 (문장 1~max_window개) 연속 창을 만들어 후보로 쌓는다.

    인용문이 문장 하나보다 길거나 짧을 수 있으므로 창 크기를 넓혀 둔다.
    문단 경계는 넘지 않는다 — 서로 다른 문단에 걸친 '일치'는 실제 일치가 아니다.
    """
    candidates: List[Candidate] = []
    for para in paras:
        sentences = split_sentences(para.text)
        if not sentences:
            continue
        for size in range(1, max_window + 1):
            for start in range(0, len(sentences) - size + 1):
                chunk = " ".join(sentences[start : start + size])
                norm = norm_compare(chunk)
                if not norm:
                    continue
                candidates.append(
                    Candidate(chunk, norm, para.no, getattr(para, "section", ""))
                )
                if len(candidates) >= MAX_CANDIDATES:
                    return candidates
            if len(sentences) < size:
                break
    return candidates


def best_match(
    needle_norm: str,
    candidates: Iterable[Candidate],
    floor: float = 0.45,
) -> Tuple[Optional[Candidate], float]:
    """``needle_norm`` 과 가장 비슷한 후보와 그 일치율을 돌려준다.

    ``difflib.SequenceMatcher`` 의 ``real_quick_ratio``/``quick_ratio`` 로 먼저
    거르므로, 후보 수천 개에서도 실제 ratio 계산은 몇 십 번만 일어난다.
    """
    if not needle_norm:
        return None, 0.0
    matcher = difflib.SequenceMatcher(autojunk=False)
    matcher.set_seq2(needle_norm)
    best: Optional[Candidate] = None
    best_ratio = 0.0
    for cand in candidates:
        matcher.set_seq1(cand.norm)
        if matcher.real_quick_ratio() <= best_ratio or matcher.quick_ratio() <= best_ratio:
            continue
        ratio = matcher.ratio()
        if ratio > best_ratio:
            best_ratio, best = ratio, cand
    if best_ratio < floor:
        return None, best_ratio
    return best, best_ratio


def overlap_ratio(a: str, b: str) -> float:
    """짧은 쪽이 긴 쪽에 **얼마나 흡수되는가** (0~1).

    리비전에서 흔한 모양은 "원래 문장 + 새 문장 한두 개"다. 이때
    ``SequenceMatcher.ratio`` 는 길이 차이 때문에 0.5 아래로 떨어지지만, 짧은 쪽은
    긴 쪽에 거의 그대로 들어 있다. 문단 짝짓기에는 이 비율이 훨씬 정확하다.
    """
    if not a or not b:
        return 0.0
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / min(len(a), len(b))

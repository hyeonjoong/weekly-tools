"""제출본 → 개정본 문단 단위 diff.

``difflib.SequenceMatcher`` 로 **정규화된 문단 텍스트** 열을 정렬한 뒤,
바뀐/추가된/삭제된 문단을 뽑는다. 유사도로 판정하는 곳은 여기가 아니다 —
여기서는 "무엇이 달라졌는가"만 만들고, 그것이 신고됐는지는 stealth.py 가 본다.

문단 안에서 **숫자가 달라졌는지**를 함께 계산한다. 리비전 사고 중 가장 값비싼 것은
"리뷰어가 요청하지도 않았는데 Results 숫자가 조용히 바뀐" 경우이기 때문이다.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .normalize import norm_compare, numbers_in_prose
from .textutil import overlap_ratio, split_sentences

__all__ = ["ParaChange", "DiffResult", "diff_documents"]

# replace 블록이 이보다 크면 문단끼리 최적 짝짓기를 포기하고 순서대로 짝짓는다
# (O(n²) 방지). 실제 원고에서 이만한 연속 교체 블록은 전면 재작성뿐이다.
MAX_PAIRING_BLOCK = 60
# 문단 짝으로 인정할 최소 유사도. 이보다 낮으면 '삭제 + 추가'로 본다.
PAIR_FLOOR = 0.55
# 길이가 크게 달라져도(문장 두 개를 덧붙인 리비전) 짧은 쪽이 긴 쪽에 이만큼
# 들어 있으면 같은 문단의 개정으로 본다.
PAIR_OVERLAP = 0.65
PAIR_OVERLAP_MIN_CHARS = 40


@dataclass
class ParaChange:
    kind: str  # 변경 | 추가 | 삭제
    section: str
    para_kind: str = "body"  # body | heading | table | quote (개정본 기준)
    old_no: int = 0
    new_no: int = 0
    old_text: str = ""
    new_text: str = ""
    numbers_changed: bool = False
    # 있던 숫자가 **사라지거나 다른 값으로 바뀐** 경우. 숫자가 새로 덧붙기만 한
    # 것(리뷰어 요청으로 한계 문장을 추가)과 값이 바뀐 것은 전혀 다른 사건이다.
    numbers_dropped: bool = False
    old_numbers: List[str] = field(default_factory=list)
    new_numbers: List[str] = field(default_factory=list)
    line_start: int = 0  # 개정본에서의 줄 번호(텍스트 포맷만)
    line_end: int = 0
    declared: bool = False
    declared_by: str = ""

    @property
    def target(self) -> str:
        where = self.section or "본문"
        if self.kind == "삭제":
            return f"{where} (제출본 문단 {self.old_no})"
        return f"{where} (개정본 문단 {self.new_no})"

    def sentence_detail(self, limit: int = 2) -> List[Tuple[str, str]]:
        """바뀐 문장만 (제출본, 개정본) 짝으로 뽑는다 — 문단 전체를 쏟지 않는다."""
        if self.kind != "변경":
            return []
        old_s = split_sentences(self.old_text)
        new_s = split_sentences(self.new_text)
        matcher = difflib.SequenceMatcher(
            None, [norm_compare(s) for s in old_s], [norm_compare(s) for s in new_s],
            autojunk=False,
        )
        pairs: List[Tuple[str, str]] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            pairs.append((" ".join(old_s[i1:i2]), " ".join(new_s[j1:j2])))
            if len(pairs) >= limit:
                break
        return pairs


@dataclass
class DiffResult:
    changes: List[ParaChange] = field(default_factory=list)
    old_count: int = 0
    new_count: int = 0

    @property
    def identical(self) -> bool:
        return not self.changes

    @property
    def changed_ratio(self) -> float:
        base = max(self.old_count, self.new_count, 1)
        return len(self.changes) / base

    @property
    def numeric_changes(self) -> List[ParaChange]:
        return [c for c in self.changes if c.numbers_changed]


def _dropped(old_nums: Sequence[str], new_nums: Sequence[str]) -> bool:
    """옛 숫자 중 개정본에서 사라진 것이 있는가(다중집합 기준)."""
    remaining = list(new_nums)
    for number in old_nums:
        if number in remaining:
            remaining.remove(number)
        else:
            return True
    return False


def _numbers_changed(old_text: str, new_text: str) -> Tuple[bool, List[str], List[str]]:
    """데이터 숫자가 달라졌는가. 인용 번호·표 번호는 세지 않는다.

    순서만 바뀐 경우(``30 … 45`` → ``45 … 30``)는 여기서 잡지 않는다 — 절을
    옮겨 쓴 정상 리비전과 구분할 수 없어서 오탐이 더 비싸다(README 의 알려진 취약점).
    """
    old_nums = numbers_in_prose(old_text)
    new_nums = numbers_in_prose(new_text)
    return sorted(old_nums) != sorted(new_nums), old_nums, new_nums


def _make_change(kind: str, old: Optional[object], new: Optional[object]) -> ParaChange:
    old_text = old.text if old is not None else ""
    new_text = new.text if new is not None else ""
    changed, old_nums, new_nums = _numbers_changed(old_text, new_text)
    section = (new.section if new is not None else old.section) or ""
    para_kind = (new.kind if new is not None else old.kind) or "body"
    return ParaChange(
        kind=kind,
        section=section,
        para_kind=para_kind,
        old_no=old.no if old is not None else 0,
        new_no=new.no if new is not None else 0,
        old_text=old_text,
        new_text=new_text,
        numbers_changed=changed,
        numbers_dropped=_dropped(old_nums, new_nums),
        old_numbers=old_nums,
        new_numbers=new_nums,
        line_start=new.line_start if new is not None else 0,
        line_end=new.line_end if new is not None else 0,
    )


def _pair_block(old_block: Sequence, new_block: Sequence) -> List[ParaChange]:
    """교체 블록 안에서 문단끼리 가장 비슷한 것끼리 짝짓는다."""
    changes: List[ParaChange] = []
    if len(old_block) > MAX_PAIRING_BLOCK or len(new_block) > MAX_PAIRING_BLOCK:
        for i in range(max(len(old_block), len(new_block))):
            old = old_block[i] if i < len(old_block) else None
            new = new_block[i] if i < len(new_block) else None
            kind = "변경" if old is not None and new is not None else (
                "삭제" if new is None else "추가"
            )
            changes.append(_make_change(kind, old, new))
        return changes

    used_old = set()
    matcher = difflib.SequenceMatcher(autojunk=False)
    for new in new_block:
        new_norm = norm_compare(new.text)
        matcher.set_seq2(new_norm)
        best_idx, best_ratio = -1, 0.0
        for idx, old in enumerate(old_block):
            if idx in used_old:
                continue
            old_norm = norm_compare(old.text)
            matcher.set_seq1(old_norm)
            if matcher.real_quick_ratio() <= best_ratio:
                continue
            ratio = matcher.ratio()
            if (
                ratio < PAIR_FLOOR
                and min(len(old_norm), len(new_norm)) >= PAIR_OVERLAP_MIN_CHARS
                and overlap_ratio(old_norm, new_norm) >= PAIR_OVERLAP
            ):
                ratio = PAIR_FLOOR  # 덧붙여 쓴 문단 — 같은 문단의 개정으로 본다
            if ratio > best_ratio:
                best_idx, best_ratio = idx, ratio
        if best_idx >= 0 and best_ratio >= PAIR_FLOOR:
            used_old.add(best_idx)
            changes.append(_make_change("변경", old_block[best_idx], new))
        else:
            changes.append(_make_change("추가", None, new))
    for idx, old in enumerate(old_block):
        if idx not in used_old:
            changes.append(_make_change("삭제", old, None))
    changes.sort(key=lambda c: (c.new_no or 10**9, c.old_no))
    return changes


def diff_documents(old_doc, new_doc, skip_sections: Sequence[str] = ("References",)) -> DiffResult:
    """제출본 → 개정본 문단 diff.

    참고문헌 절은 여기서 제외한다 — 증감은 inventory.py 가 따로 세며,
    문헌 목록 40줄이 미신고 변경 목록을 삼켜 버리면 아무도 안 읽는다.
    """
    old_paras = [p for p in old_doc.paras if p.section not in skip_sections]
    new_paras = [p for p in new_doc.paras if p.section not in skip_sections]
    old_keys = [norm_compare(p.text) for p in old_paras]
    new_keys = [norm_compare(p.text) for p in new_paras]

    matcher = difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False)
    result = DiffResult(old_count=len(old_paras), new_count=len(new_paras))
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            result.changes.extend(_pair_block(old_paras[i1:i2], new_paras[j1:j2]))
        elif tag == "delete":
            result.changes.extend(_make_change("삭제", p, None) for p in old_paras[i1:i2])
        elif tag == "insert":
            result.changes.extend(_make_change("추가", None, p) for p in new_paras[j1:j2])
    # 통째로 들어오거나 빠진 문단은 한쪽 숫자 목록이 비어 있으므로,
    # 그 안에 숫자가 하나라도 있으면 _make_change 가 이미 '숫자 변경'으로 표시한다.
    return result

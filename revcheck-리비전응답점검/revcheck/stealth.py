"""미신고 변경(stealth edit) 판정 — "응답서 어디에도 없는데 바뀐 곳".

**숫자가 바뀐 문단에는 더 엄격한 문턱이 있다.** 그 문단을 어떤 식으로든 언급했다는
사실만으로는 부족하고, **바뀐 숫자 자체가 응답서 어딘가에 적혀 있어야** 신고로 본다.
그러지 않으면 "문장을 다듬었습니다(줄 43)" 한 줄로 5.2 → 5.9 를 덮을 수 있다.

신고된 것으로 인정하는 근거는 **문자 대조**뿐이다(의미 매칭 없음).
    ① 응답서에서 뽑은 인용문이 그 문단 안에 들어 있다
    ② 그 문단의 문장 하나가 응답서 본문에 그대로 들어 있다(따옴표 없이 붙여넣은 경우)
    ③ 검증된 위치 참조가 그 문단의 줄 범위를 가리킨다
    ④ 인용문의 '가장 가까운 문장'이 그 문단에 있다 — 응답서는 그 문단을 분명히
      가리켰고 문구만 어긋난 것이므로, 같은 사고를 '인용 불일치'와 '미신고 변경'
      두 번으로 세지 않는다(인용 불일치 쪽이 더 정확한 지적이다).

등급
    있던 값이 **다른 값으로 바뀜** → **치명** (절대 정보로 내리지 않는다)
    숫자가 **덧붙기만** 함        → Results/Abstract 면 경고, 아니면 정보
        (리뷰어가 요청한 한계 문장을 넣으면 그 안에 숫자가 있는 게 당연하다)
    Results/Abstract 문단이 통째로 삭제되며 숫자가 사라짐 → **치명**
    그 밖의 문단 추가/삭제        → Results/Abstract 면 경고, 아니면 정보
    Results/Abstract 의 변경  → 경고
    그 밖의 문장 다듬기        → 정보

소음 방지 (이 항목이 300줄을 쏟으면 아무도 안 읽는다)
    변경률 > 30% → 숫자 변경 문단만 개별 나열하고 나머지는 **절별 카운트 요약**
    변경률 > 60% → 전면 재작성으로 보고 미신고 변경 목록을 **요약 한 줄로 강등**
                   (그 사실을 리포트에 명시한다)
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .docio import NUMERIC_SENSITIVE_SECTIONS
from .model import CRITICAL, INFO, WARNING
from .normalize import norm_compare, numbers_in_prose
from .quotes import MIN_QUOTE_CHARS
from .textutil import overlap_ratio, split_sentences

__all__ = ["StealthScan", "mark_declared", "grade_changes"]

# 응답서에 그대로 붙여 넣은 문장을 '신고됨'으로 인정할 최소 길이.
MIN_SENTENCE_CHARS = 25
# 문단을 크게 고쳐 쓰면 diff 는 '삭제 + 추가' 두 건으로 본다. 새 문단이 응답서에
# 인용돼 있으면 그 삭제도 신고된 것이다 — 그러지 않으면 정상적인 리비전마다
# "미신고 삭제"가 한 건씩 따라붙어 툴이 소음이 된다.
DELETION_COVER = 0.50
SUMMARY_RATIO = 0.30
REWRITE_RATIO = 0.60
# 요약 모드에서도 개별로 남기는 숫자 변경 문단의 최대 수(나머지는 CSV 로).
# 전면 재작성(60% 초과)에서는 더 좁힌다 — 목록이 리포트를 삼키면 아무도 안 읽는다.
# 그래도 0 으로 만들지는 않는다: 숫자가 조용히 바뀐 곳은 이 툴이 찾는 것 중
# 가장 값비싼 것이라 통째로 감출 수 없다. 잘라 낸 건수는 항상 명시한다.
MAX_NUMERIC_LISTED = 20
MAX_NUMERIC_LISTED_REWRITE = 5


@dataclass
class StealthScan:
    undeclared: List = field(default_factory=list)  # ParaChange 들
    listed: List = field(default_factory=list)  # 리포트에 개별로 실을 것
    section_counts: Dict[str, int] = field(default_factory=dict)  # 요약으로 강등된 것
    collapsed: bool = False  # 변경률 30% 초과 — 비숫자 변경을 요약으로 강등
    rewrite: bool = False  # 변경률 60% 초과 — 전면 재작성
    truncated_numeric: int = 0  # 개별 나열에서 잘라 낸 숫자 변경 수

    @property
    def summary_line(self) -> str:
        if not self.section_counts:
            return ""
        parts = ", ".join(
            f"{section or '본문'} {count}건"
            for section, count in sorted(
                self.section_counts.items(), key=lambda kv: (-kv[1], kv[0])
            )
        )
        return parts


def mark_declared(
    changes: Sequence,
    quotes: Sequence,
    response_text: str,
    loc_refs: Sequence,
    near_paras: Optional[Dict[int, str]] = None,
    declared_tables: Optional[Dict[int, str]] = None,
) -> None:
    """각 변경 문단이 응답서에 신고돼 있는지 표시한다."""
    response_norm = norm_compare(response_text)
    # 응답서에 적힌 숫자들(정규화된 토큰). "3.0" 과 "3" 은 같은 숫자로 본다.
    response_numbers = set(numbers_in_prose(response_text))
    near_paras = near_paras or {}
    declared_tables = declared_tables or {}
    # **검사에서 제외된 인용은 신고 근거가 될 수 없다.** 15자 미만이라 믿을 수
    # 없다고 뺀 인용이 치명을 덮어 버리면, 툴이 스스로 눈을 가리는 셈이다.
    quote_norms = [
        (q.norm, q.comment_label)
        for q in quotes
        if q.norm and not q.skipped and len(q.norm) >= MIN_QUOTE_CHARS
    ]
    verified_spans = [
        (r.line_start, r.line_end or r.line_start, r.comment_label)
        for r in loc_refs
        if r.status == "일치" and r.line_start
    ]
    for change in changes:
        target_norm = norm_compare(change.new_text or change.old_text)
        if not target_norm:
            change.declared = True
            change.declared_by = "빈 문단"
            continue
        if (
            change.new_no
            and change.new_no in declared_tables
            and not (change.kind == "변경" and change.numbers_changed)
        ):
            # 표 셀은 문장이 아니라 인용될 수 없다. 응답서가 그 표 번호를
            # 언급했으면 신고된 것으로 본다. 다만 **기존 표의 숫자가 바뀐 것**은
            # 표 번호를 언급했다는 이유로 면제해 주지 않는다(가장 값비싼 사고다).
            change.declared = True
            change.declared_by = f"응답서가 {declared_tables[change.new_no]} 을 언급함"
            continue
        if change.new_no and change.new_no in near_paras:
            # 인용문이 이 문단을 거의 맞혔다 — 어긋남은 '인용 불일치' 쪽에서
            # 더 정확하게 지적되므로 여기서 또 세지 않는다.
            change.declared = True
            change.declared_by = f"응답 {near_paras[change.new_no]} 의 인용문(문구 불일치는 별도 지적)"
            continue
        if _numbers_undisclosed(change, response_numbers):
            # 바뀐 숫자가 응답서 어디에도 적혀 있지 않다. 이럴 때는 그 문단을
            # 언급했다는 사실(다른 문장 인용·줄 번호)만으로 신고로 보지 않는다 —
            # "문장을 다듬었습니다(줄 43)" 한 줄로 5.2 → 5.9 가 덮이면 안 된다.
            continue
        for qnorm, label in quote_norms:
            if len(qnorm) >= 12 and qnorm in target_norm:
                change.declared = True
                change.declared_by = f"응답 {label} 의 인용문"
                break
        if change.declared:
            continue
        for sentence in split_sentences(change.new_text or change.old_text):
            snorm = norm_compare(sentence)
            if len(snorm) >= MIN_SENTENCE_CHARS and snorm in response_norm:
                change.declared = True
                change.declared_by = "응답서 본문에 그대로 들어 있음"
                break
        if change.declared:
            continue
        for start, end, label in verified_spans:
            if change.line_start and change.line_start <= end and change.line_end >= start:
                change.declared = True
                change.declared_by = f"응답 {label} 의 위치 참조"
                break

    _absorb_deletions(changes)


def _numbers_undisclosed(change, response_numbers) -> bool:
    """숫자가 바뀐 문단인데, **새 숫자가 응답서에 적혀 있지 않은가**.

    응답서에 그 숫자가 어디든(인용이든 산문이든) 적혀 있으면 저자가 밝힌 것으로
    본다. 적혀 있지 않다면, 그 문단을 언급했다는 사실만으로는 신고가 아니다.
    """
    if change.kind != "변경" or not change.numbers_dropped:
        return False
    old_numbers = list(change.old_numbers)
    for number in change.new_numbers:
        if number in old_numbers:
            old_numbers.remove(number)
            continue
        if number not in response_numbers:
            return True
    return False


def _absorb_deletions(changes: Sequence) -> None:
    """크게 고쳐 쓴 문단의 '삭제'쪽을, 신고된 '추가'쪽이 덮게 한다."""
    declared_new = [
        (norm_compare(c.new_text), c.section) for c in changes if c.declared and c.new_text
    ]
    if not declared_new:
        return
    matcher = difflib.SequenceMatcher(autojunk=False)
    for change in changes:
        if change.declared or change.kind != "삭제":
            continue
        old_norm = norm_compare(change.old_text)
        if not old_norm:
            continue
        matcher.set_seq2(old_norm)
        # **같은 절 안에서만** 흡수한다. 초록과 결과에 같은 문장이 겹쳐 있는 것은
        # 흔한 일이라, 절을 가리지 않으면 초록에서 조용히 지운 숫자가 결과의
        # 신고된 변경에 먹혀 사라진다.
        for candidate, section in declared_new:
            if section != change.section:
                continue
            matcher.set_seq1(candidate)
            if matcher.real_quick_ratio() < DELETION_COVER:
                continue
            if (
                matcher.ratio() >= DELETION_COVER
                or overlap_ratio(candidate, old_norm) >= 0.65
            ):
                change.declared = True
                change.declared_by = "같은 자리의 신고된 변경에 흡수됨"
                break


def severity_of(change) -> str:
    if change.kind == "삭제":
        # 있던 숫자가 통째로 사라진 것은 Results/Abstract 에서 치명이다
        # (일차 결과 문단을 말없이 지운 경우).
        if change.old_numbers and change.section in NUMERIC_SENSITIVE_SECTIONS:
            return CRITICAL
        return WARNING if change.section in NUMERIC_SENSITIVE_SECTIONS else INFO
    if change.kind == "추가":
        # 리뷰어가 요청한 한계 문단을 새로 쓰면 그 안에 숫자가 있는 게 당연하다.
        # 여기에 "데이터 조작 의심" 을 붙이면 정상 리비전마다 치명이 뜬다.
        return WARNING if change.section in NUMERIC_SENSITIVE_SECTIONS else INFO
    if change.numbers_dropped:
        # 있던 값이 다른 값으로 바뀌었다 — 이 툴이 가장 비싸게 보는 사건.
        return CRITICAL
    if change.numbers_changed:
        # 숫자가 덧붙기만 했다(문장을 새로 넣음). 값이 바뀐 것이 아니다.
        return WARNING if change.section in NUMERIC_SENSITIVE_SECTIONS else INFO
    if change.section in NUMERIC_SENSITIVE_SECTIONS:
        return WARNING
    return INFO


def grade_changes(changes: Sequence, changed_ratio: float) -> StealthScan:
    """신고되지 않은 변경들을 등급 매기고, 소음이 되지 않게 묶는다."""
    scan = StealthScan()
    scan.undeclared = [c for c in changes if not c.declared]
    scan.collapsed = changed_ratio > SUMMARY_RATIO
    scan.rewrite = changed_ratio > REWRITE_RATIO

    numeric = [c for c in scan.undeclared if c.numbers_changed]
    others = [c for c in scan.undeclared if not c.numbers_changed]

    if not scan.collapsed:
        scan.listed = list(scan.undeclared)
        return scan

    # 변경률이 높으면 숫자 변경만 개별로 남긴다 — 가치는 거기 거의 전부 몰려 있다.
    limit = MAX_NUMERIC_LISTED_REWRITE if scan.rewrite else MAX_NUMERIC_LISTED
    scan.listed = numeric[:limit]
    scan.truncated_numeric = max(0, len(numeric) - limit)
    for change in others + numeric[limit:]:
        key = change.section or "본문"
        scan.section_counts[key] = scan.section_counts.get(key, 0) + 1
    return scan

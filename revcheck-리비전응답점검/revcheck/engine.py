"""세 문서를 받아 판정 결과(Result)를 만드는 곳 — 검사 순서와 등급이 여기 모인다.

검사 순서는 리포트에 나오는 순서이기도 하다.
    ① 제출본 오첨부 사고 (old == new 인데 "고쳤다"는 주장이 있다)
    ② 코멘트 번호 전수 점검
    ③ 인용 문구 실존 검증
    ④ 미신고 변경
    ⑤ 검증 불가한 변경 주장
    ⑥ 참고문헌·그림·표 증감
    ⑦ 위치 참조
    ⑧ 분량 변화(정보)
    ⑨ 커버리지 자백 (없으면 리포트를 내지 않는다)
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from . import comments as comments_mod
from . import inventory as inventory_mod
from . import locations as loc_mod
from . import quotes as quotes_mod
from . import stealth as stealth_mod
from .diffpair import diff_documents
from .docio import Document
from .model import (
    CRITICAL,
    INFO,
    WARNING,
    CoverageLine,
    Finding,
    Result,
)
from .normalize import canonical, norm_compare, norm_display
from .textutil import build_candidates

__all__ = ["Options", "run_check"]

# "고쳤다"는 주장 — 이게 있는데 검증 수단(인용문·위치 참조)이 하나도 없으면
# 그 응답은 사람이 눈으로 확인해야 한다.
# 표/그림 캡션. 짧고 번호로 시작하는 줄만 캡션으로 본다.
_CAPTION = re.compile(
    r"^\s*(Table|Tbl|Figure|Fig|표|그림)\.?\s*(\d{1,3})\b", re.IGNORECASE
)

_CLAIM_WORDS = re.compile(
    r"\b(?:revised|changed|modified|rewritten|rewrote|added|amended|updated|corrected|"
    r"clarified|expanded|removed|deleted|replaced|incorporated)\b"
    r"|수정(?:했|하였|함|되었)|추가(?:했|하였|함|되었)|반영(?:했|하였|함|되었)|"
    r"보완(?:했|하였|함)|삭제(?:했|하였|함)|고쳤|바꾸었|바꿨",
    re.IGNORECASE,
)


@dataclass
class Options:
    tracked: str = "accept"
    ratio: float = quotes_mod.DEFAULT_RATIO
    min_quote_chars: int = quotes_mod.MIN_QUOTE_CHARS
    comment_ids: Optional[List[Tuple[str, int]]] = None


def _reading_header(old: Document, new: Document, resp: Document) -> List[str]:
    def describe(doc: Document) -> str:
        unit = "문단" if doc.fmt == "docx" else "줄"
        count = len(doc.paras) if doc.fmt == "docx" else doc.total_lines
        return f"{doc.path.name}({unit} {count})"

    lines = [
        f"읽기: {describe(old)} / {describe(new)} / {resp.path.name}"
    ]
    for doc, role in ((old, "제출본"), (new, "개정본"), (resp, "응답서")):
        if doc.tracked.present:
            lines.append(
                f"      ※ {role}에 변경내용 추적 흔적이 있습니다"
                f"(삽입 {doc.tracked.ins} / 삭제 {doc.tracked.dele}) — "
                f"{doc.tracked.state_label}로 읽었습니다."
            )
    return lines


def _comment_findings(scan: comments_mod.CommentScan, result: Result) -> None:
    for label in scan.not_found:
        result.add(
            Finding(
                CRITICAL,
                "코멘트누락",
                label,
                f"--comments 로 지정한 코멘트 {label} 을 응답서에서 찾지 못했습니다.",
                advice="응답서에 그 번호로 된 응답 블록이 있는지 확인하세요.",
            )
        )
    for reviewer, number, reason in scan.missing:
        label = comments_mod._reviewer_label(reviewer, number)
        result.add(
            Finding(
                CRITICAL,
                "코멘트누락",
                label,
                f"코멘트 {label} 에 대한 응답 블록이 없습니다. ({reason})",
                advice="응답을 추가하거나, 번호 체계가 저널 양식과 다른지 확인하세요.",
            )
        )
    for reviewer, number in scan.duplicates:
        label = comments_mod._reviewer_label(reviewer, number)
        result.add(
            Finding(
                WARNING,
                "코멘트중복",
                label,
                f"코멘트 번호 {label} 이 응답서에 두 번 이상 나옵니다.",
                advice="번호를 나누거나(2-3a/2-3b) 하나로 합치세요.",
            )
        )
    for reviewer, low, high in scan.wild_gaps:
        result.add(
            Finding(
                WARNING,
                "코멘트번호이상",
                reviewer,
                f"{reviewer} 의 코멘트 번호가 {low}번에서 {high}번으로 건너뜁니다 — "
                f"번호 오타로 보여 그 사이를 누락으로 세지 않았습니다.",
                advice="번호를 확인하고, 정말 빠진 응답이 있으면 --comments 로 지정하세요.",
            )
        )
    for block in scan.silent_blocks:
        result.add(
            Finding(
                WARNING,
                "번호없는블록",
                block,
                f"{block} 블록을 찾았지만 번호가 붙은 코멘트가 하나도 없어 "
                f"점검하지 못했습니다.",
                advice="번호를 붙이거나 --comments 로 알려 주면 그 블록도 점검합니다.",
            )
        )
    for comment in scan.thin:
        result.add(
            Finding(
                WARNING,
                "빈응답",
                comment.label,
                f"코멘트 {comment.label} 의 응답 본문이 "
                f"{comment.body_length}자뿐입니다(기준 {comments_mod.MIN_BODY_CHARS}자).",
                detail=[f"응답: {norm_display(comment.body, 100) or '(없음)'}"],
                advice="무엇을 어떻게 고쳤는지 한 문장이라도 적으세요.",
            )
        )


def _quote_findings(scan: quotes_mod.QuoteScan, result: Result) -> None:
    for verdict in scan.verdicts:
        if verdict.ok:
            continue
        quote = verdict.quote
        detail = [f'응답서: "{norm_display(quote.text, 180)}"']
        if verdict.closest is not None:
            detail.append(
                f"개정본에서 가장 가까운 문장(일치율 {verdict.ratio * 100:.0f}%, "
                f"문단 {verdict.closest.para_no}):"
            )
            detail.append(f'        "{norm_display(verdict.closest.text, 180)}"')
        else:
            detail.append("개정본에서 비슷한 문장을 찾지 못했습니다.")
        if verdict.status == "숫자불일치":
            result.add(
                Finding(
                    CRITICAL,
                    "인용불일치",
                    quote.comment_label,
                    f"응답 {quote.comment_label} 의 인용 문구가 개정본의 해당 문장과 "
                    f"「숫자가 다릅니다」.",
                    detail=detail,
                    advice="응답서와 원고 중 어느 쪽이 최신인지 확인하세요.",
                )
            )
        elif verdict.status == "없음":
            result.add(
                Finding(
                    CRITICAL,
                    "인용불일치",
                    quote.comment_label,
                    f"응답 {quote.comment_label} 의 인용 문구가 개정본에 없습니다.",
                    detail=detail,
                    advice="인용한 뒤에 원고를 또 고쳤는지 확인하세요.",
                )
            )
        elif verdict.status == "축약인용":
            result.add(
                Finding(
                    WARNING,
                    "인용축약",
                    quote.comment_label,
                    f"응답 {quote.comment_label} 의 인용 문구는 개정본 문장의 "
                    f"축약형입니다(어긋난 숫자는 없음).",
                    detail=detail,
                    advice="리뷰어가 그대로 검색해도 찾을 수 있게 문장 전체를 인용하면 좋습니다.",
                )
            )
        elif verdict.status == "제출본문구":
            result.add(
                Finding(
                    WARNING,
                    "인용출처의심",
                    quote.comment_label,
                    f"응답 {quote.comment_label} 의 인용 문구는 제출본에는 있고 "
                    f"개정본에는 없습니다.",
                    detail=detail,
                    advice="개정 전 문장을 인용한 것인지, 고친 뒤 되돌아간 것인지 확인하세요.",
                )
            )
        else:  # 표현불일치
            result.add(
                Finding(
                    WARNING,
                    "인용표현차이",
                    quote.comment_label,
                    f"응답 {quote.comment_label} 의 인용 문구가 개정본과 표현이 다릅니다"
                    f"(숫자는 같음, 일치율 {verdict.ratio * 100:.0f}%).",
                    detail=detail,
                    advice="리뷰어가 그대로 검색해도 찾을 수 있게 맞춰 두면 좋습니다.",
                )
            )


def _claim_findings(
    scan: comments_mod.CommentScan,
    quotes: Sequence[quotes_mod.Quote],
    loc_refs: Sequence[loc_mod.LocRef],
    result: Result,
    table_labels: Optional[set] = None,
) -> int:
    """변경을 주장했는데 검증 수단이 하나도 없는 응답을 센다."""
    quoted_labels = {q.comment_label for q in quotes if not q.skipped}
    skipped_labels = {q.comment_label: q.skipped for q in quotes if q.skipped}
    located_labels = {r.comment_label for r in loc_refs}
    quoted_labels |= (table_labels or set())
    unverifiable = 0
    for comment in scan.comments:
        if not _CLAIM_WORDS.search(comment.body):
            continue
        if comment.label in quoted_labels or comment.label in located_labels:
            continue
        unverifiable += 1
        if comment.label in skipped_labels:
            # 인용은 **있었지만** 대조 대상에서 뺀 경우다. "인용도 없다"고 말하면
            # 이미 인용을 적어 둔 저자에게 거짓말을 하는 셈이다.
            result.add(
                Finding(
                    WARNING,
                    "검증불가주장",
                    comment.label,
                    f"응답 {comment.label} 의 인용 문구는 대조하지 않았습니다"
                    f"({skipped_labels[comment.label]}) — 변경 주장을 기계로 확인하지 "
                    f"못했습니다.",
                    detail=[f"응답: {norm_display(comment.body, 120)}"],
                    advice="사람이 직접 확인하세요.",
                )
            )
            continue
        result.add(
            Finding(
                WARNING,
                "검증불가주장",
                comment.label,
                f"응답 {comment.label} 의 변경 주장을 기계로 확인할 수단이 없습니다"
                f"(인용 문구도, 위치 참조도 없음).",
                detail=[f"응답: {norm_display(comment.body, 120)}"],
                advice="고친 문장을 한 줄 인용하거나 위치를 적어 두면 리뷰어가 바로 확인합니다.",
            )
        )
    return unverifiable


_STEALTH_WORDS = {
    "변경": "응답서의 인용문·위치 참조로 연결되지 않은 수정입니다",
    "추가": "응답서의 인용문·위치 참조로 연결되지 않은 새 문단입니다",
    "삭제": "응답서의 인용문·위치 참조로 연결되지 않은 삭제입니다",
}


def _stealth_message(change) -> str:
    head = _STEALTH_WORDS.get(change.kind, "응답서에 연결되지 않은 변경입니다")
    if change.numbers_dropped and change.kind == "변경":
        return head + " — 숫자가 다른 값으로 바뀌었습니다."
    return head + "."


def _stealth_findings(scan: stealth_mod.StealthScan, result: Result) -> None:
    for change in scan.listed:
        severity = stealth_mod.severity_of(change)
        detail: List[str] = []
        if change.kind == "변경":
            for old_s, new_s in change.sentence_detail():
                detail.append(
                    f"제출본: {norm_display(old_s, 150) or '(이 문장 없음 — 새로 넣은 문장)'}"
                )
                detail.append(f"개정본: {norm_display(new_s, 150) or '(삭제됨)'}")
        elif change.kind == "추가":
            detail.append(f"추가된 문단: {norm_display(change.new_text, 150)}")
        else:
            detail.append(f"삭제된 문단: {norm_display(change.old_text, 150)}")
        if change.numbers_changed:
            detail.append(
                f"숫자: {', '.join(change.old_numbers) or '(없음)'} → "
                f"{', '.join(change.new_numbers) or '(없음)'}"
            )
        result.add(
            Finding(
                severity,
                "미신고변경",
                change.target,
                _stealth_message(change),
                detail=detail,
                advice=(
                    "재분석했거나 값을 고쳤다면 응답서에 한 줄로 밝히세요. "
                    "말없이 바뀐 숫자는 리뷰어에게 가장 나쁘게 읽힙니다."
                    if change.numbers_dropped and change.kind == "변경"
                    else "응답서에 산문으로만 적었다면 그대로 두어도 됩니다."
                ),
            )
        )
    if scan.section_counts:
        note = (
            "전면 재작성으로 보입니다(문단 변경률 60% 초과) — "
            if scan.rewrite
            else "변경이 많아(문단 변경률 30% 초과) "
        )
        extra = (
            f" 숫자 변경 {scan.truncated_numeric}건도 개별 나열에서 제외했습니다."
            if scan.truncated_numeric
            else ""
        )
        result.add(
            Finding(
                WARNING,
                "미신고변경요약",
                "요약",
                note
                + f"응답서에 연결되지 않은 나머지 변경은 개별로 나열하지 않고 "
                f"절별 건수로 줄였습니다: {scan.summary_line}.{extra} "
                "전체 목록은 변경목록.csv 에 있습니다.",
                advice="나머지는 변경목록.csv 에서 '신고여부=미신고' 로 걸러 보세요.",
                order=1,
            )
        )


def _inventory_findings(
    inv: inventory_mod.InventoryDiff, claims: Sequence[int], result: Result
) -> None:
    actual = len(inv.added_refs)
    # 응답마다 "2편 추가" "1편 추가" 라고 나눠 적는 일이 흔하다 — 합계로 본다.
    if claims and actual not in (sum(claims), max(claims)):
        for claimed in [sum(claims)]:
            result.add(
                Finding(
                    WARNING,
                    "참고문헌수량",
                    "참고문헌",
                    f"응답서는 참고문헌 {claimed}편 추가라고 했으나 실제 증가는 "
                    f"{actual}편입니다.",
                    detail=[
                        f"추가된 문헌: {', '.join(norm_display(r.raw, 70) for r in inv.added_refs[:3]) or '(없음)'}"
                    ],
                    advice="추가문헌.csv 를 확인하고, 빠진 문헌이 있으면 넣으세요.",
                )
            )
            break
    if inv.removed_refs:
        result.add(
            Finding(
                INFO,
                "참고문헌삭제",
                "참고문헌",
                f"제출본에 있던 문헌 {len(inv.removed_refs)}편이 개정본에 없습니다.",
                detail=[norm_display(r.raw, 90) for r in inv.removed_refs[:3]],
            )
        )


def _location_findings(refs: Sequence[loc_mod.LocRef], result: Result) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for ref in refs:
        counts[ref.status] = counts.get(ref.status, 0) + 1
    unverifiable = counts.get("확인불가", 0)
    if unverifiable:
        reasons = sorted({r.reason for r in refs if r.status == "확인불가"})
        result.add(
            Finding(
                INFO,
                "위치참조확인불가",
                "위치 참조",
                f"위치 참조 {unverifiable}건은 확인할 수 없습니다 "
                f"({'; '.join(reasons)}) — 건수만 보고합니다.",
                advice="확인불가를 '이상 없음'으로 보지 마세요.",
            )
        )
    for ref in refs:
        if ref.status == "범위초과":
            result.add(
                Finding(
                    WARNING,
                    "위치참조오류",
                    ref.comment_label,
                    f"응답 {ref.comment_label} 의 위치 참조가 개정본 범위를 벗어납니다 — "
                    f"'{ref.raw}' ({ref.reason})",
                )
            )
        elif ref.status == "변경없음":
            result.add(
                Finding(
                    WARNING,
                    "위치참조오류",
                    ref.comment_label,
                    f"응답 {ref.comment_label} 의 위치 참조가 가리키는 곳은 개정본에서 "
                    f"바뀌지 않았습니다 — '{ref.raw}' ({ref.reason})",
                    advice="문단이 밀려 줄 번호가 어긋났을 수 있습니다.",
                )
            )
    return counts


def _declared_tables(new_doc: Document, response_text: str) -> Dict[int, str]:
    """응답서가 번호를 언급한 표/그림에 속한 **표 문단**들을 신고된 것으로 본다."""
    figures, tables = inventory_mod.mentioned_labels(response_text)
    current: Optional[str] = None
    declared: Dict[int, str] = {}
    for para in new_doc.paras:
        if para.kind != "table":
            label = _caption_label(para, figures, tables)
            current = label
            if label:
                # 캡션 줄("Table 2. Completers by arm") 자체도 신고된 것이다.
                declared[para.no] = label
            continue
        if current:
            declared[para.no] = current
    return declared


def _caption_label(para, figures, tables) -> Optional[str]:
    """이 문단이 응답서가 언급한 표/그림의 **캡션**인가.

    캡션은 짧고 ``Table 2`` / ``Figure 1`` 로 **시작**한다. 본문 문단이 표를
    언급하는 것과 구분해야 한다 — 본문은 숫자가 조용히 바뀔 수 있는 곳이라
    표 번호를 언급했다는 이유로 면제해 주면 안 된다.
    """
    text = canonical(para.text)
    if len(text) > 120:
        return None
    m = _CAPTION.match(text)
    if not m:
        return None
    number = int(m.group(2))
    kind = m.group(1).lower()
    if kind.startswith(("t", "표")) and number in tables:
        return f"Table {number}"
    if kind.startswith(("f", "그")) and number in figures:
        return f"Figure {number}"
    return None


def _change_rows(changes: Sequence) -> List[List[str]]:
    from .report import CHANGE_HEADER

    rows = [list(CHANGE_HEADER)]
    for change in changes:
        span = (
            f"{change.line_start}-{change.line_end}"
            if change.line_start and change.line_end
            else ""
        )
        rows.append([
            str(change.old_no or ""),
            str(change.new_no or ""),
            span,
            change.section or "",
            change.kind,
            "예" if change.numbers_changed else "아니오",
            "신고됨" if change.declared else "미신고",
            change.declared_by,
            canonical(change.old_text),
            canonical(change.new_text),
        ])
    return rows


def run_check(old: Document, new: Document, resp: Document, opts: Options) -> Result:
    """세 문서를 대조해 Result 를 만든다. 파일은 쓰지 않는다."""
    result = Result()
    result.header_lines = _reading_header(old, new, resp)
    for doc, role in ((old, "제출본"), (new, "개정본"), (resp, "응답서")):
        result.notes.extend(f"[{role}] {note}" for note in doc.notes)

    # ② 코멘트 번호 전수 점검
    scan = comments_mod.scan_comments(resp.paras, opts.comment_ids)
    if scan.undecidable:
        result.undecidable = scan.undecidable
        result.coverage.append(
            CoverageLine("리뷰어 코멘트 식별", len(scan.comments), 0, note=scan.undecidable)
        )
        return result

    # 문단 diff (①·④·⑦ 이 모두 이것을 쓴다)
    diff = diff_documents(old, new)

    # ⑥ 참고문헌·그림·표 (오첨부 판정에 필요하므로 먼저 센다)
    inv = inventory_mod.diff_inventory(old, new)

    # ① 제출본 오첨부 사고 — 본문도 참고문헌도 하나도 안 달라졌을 때만이다.
    claim_count = sum(1 for c in scan.comments if _CLAIM_WORDS.search(c.body))
    if diff.identical and not inv.added_refs and not inv.removed_refs and claim_count:
        result.add(
            Finding(
                CRITICAL,
                "원고오첨부",
                "개정본",
                f"제출본과 개정본의 본문이 완전히 같은데 응답서에는 변경 주장이 "
                f"{claim_count}건 있습니다.",
                detail=[
                    f"제출본: {old.path.name}",
                    f"개정본: {new.path.name}",
                ],
                advice="개정본 대신 제출본을 첨부하지 않았는지 확인하세요.",
            )
        )

    _comment_findings(scan, result)

    # ③ 인용 문구 실존 검증
    quote_list = quotes_mod.extract_quotes(scan.comments, opts.min_quote_chars)
    candidates = build_candidates(new.paras)
    old_norms = [norm_compare(p.text) for p in old.paras]
    pending = sum(1 for q in quote_list if not q.skipped)
    if pending > 50:  # 오래 걸릴 수 있으니 잠자코 있지 않는다
        sys.stderr.write(f"인용 문구 {pending}건을 개정본과 대조하는 중입니다…\n")
        sys.stderr.flush()
    qscan = quotes_mod.verify_quotes(
        quote_list, new.paras, candidates, old_norms, opts.ratio
    )
    _quote_findings(qscan, result)

    # ⑦ 위치 참조 (④ 의 신고 판정에 쓰이므로 먼저 계산한다)
    loc_refs = loc_mod.extract_locations(scan.comments)
    loc_refs = loc_mod.verify_locations(loc_refs, new, diff.changes)

    # ④ 미신고 변경
    # 인용문이 '거의' 맞은 문단은 응답서가 분명히 가리킨 문단이다. 같은 사고를
    # 인용 불일치와 미신고 변경으로 두 번 세지 않는다.
    near_paras = {
        v.closest.para_no: v.quote.comment_label
        for v in qscan.verdicts
        if v.closest is not None and v.ratio >= 0.6
    }
    declared_tables = _declared_tables(new, resp.text())
    stealth_mod.mark_declared(
        diff.changes, quote_list, resp.text(), loc_refs, near_paras, declared_tables
    )
    sscan = stealth_mod.grade_changes(diff.changes, diff.changed_ratio)
    _stealth_findings(sscan, result)

    # ⑤ 검증 불가한 변경 주장
    # 응답서가 표 번호를 말하고 그 표가 실제로 들어왔다면, 그것도 확인 수단이다.
    table_claim_labels = {
        c.label
        for c in scan.comments
        if declared_tables and inventory_mod.mentioned_labels(c.body)[1]
    }
    unverifiable_claims = _claim_findings(
        scan, quote_list, loc_refs, result, table_claim_labels
    )

    claims = inventory_mod.claimed_counts(resp.text())
    _inventory_findings(inv, claims, result)
    result.added_ref_rows = inventory_mod.reference_rows(inv.added_refs)

    loc_counts = _location_findings(loc_refs, result)

    # ⑧ 분량 변화(정보)
    result.info_lines.extend(_volume_lines(old, new, inv))
    result.change_rows = _change_rows(diff.changes)

    # ⑨ 커버리지 자백
    _coverage(result, scan, qscan, loc_refs, loc_counts, diff, sscan, inv, unverifiable_claims)
    return result


def _volume_lines(old: Document, new: Document, inv: inventory_mod.InventoryDiff) -> List[str]:
    lines: List[str] = []
    old_words, new_words = old.word_count(), new.word_count()
    delta = new_words - old_words
    lines.append(
        f"본문 단어수 {old_words:,} → {new_words:,} ({delta:+,}), "
        f"참고문헌 {len(inv.old_refs)} → {len(inv.new_refs)}, "
        f"Figure {len(inv.old_figures)} → {len(inv.new_figures)}, "
        f"Table {len(inv.old_tables)} → {len(inv.new_tables)}"
    )
    if inv.added_tables or inv.added_figures:
        bits = []
        if inv.added_tables:
            bits.append("Table " + ", ".join(str(n) for n in inv.added_tables))
        if inv.added_figures:
            bits.append("Figure " + ", ".join(str(n) for n in inv.added_figures))
        lines.append("새로 등장한 번호: " + " / ".join(bits))
    if inv.skipped_entries:
        lines.append(
            f"참고문헌 절에서 연도·DOI 가 없는 {inv.skipped_entries}줄은 문헌으로 세지 "
            "않았습니다(표 캡션·판권 문구 등)."
        )
    if not inv.found_section:
        lines.append(
            "참고문헌 절을 한쪽 이상에서 찾지 못해 문헌 증감은 대조하지 않았습니다."
        )
    return lines


def _coverage(
    result: Result,
    scan: comments_mod.CommentScan,
    qscan: quotes_mod.QuoteScan,
    loc_refs: Sequence[loc_mod.LocRef],
    loc_counts: Dict[str, int],
    diff,
    sscan: stealth_mod.StealthScan,
    inv: inventory_mod.InventoryDiff,
    unverifiable_claims: int,
) -> None:
    per = " / ".join(
        f"{'Editor' if r == 'Editor' else 'R' + r[1:]} {len(scan.per_reviewer[r])}건"
        for r in scan.reviewers
    )
    result.coverage.append(
        CoverageLine(
            "리뷰어 코멘트 식별",
            len(scan.comments),
            len(scan.comments),
            note=f"{per} — 번호 체계: {scan.scheme}"
            + " (응답서에 아예 안 적힌 마지막 번호는 알 수 없습니다)"
            + (
                f" / 번호가 없어 점검하지 못한 블록: {', '.join(scan.silent_blocks)}"
                if scan.silent_blocks
                else ""
            ),
        )
    )
    thin_note = (
        f" (그중 {len(scan.thin)}건은 본문이 너무 짧아 경고로 실었습니다)" if scan.thin else ""
    )
    result.coverage.append(
        CoverageLine(
            "응답 본문 확인",
            len(scan.comments),
            len(scan.comments),
            custom=f"응답 본문: {len(scan.comments)}건 모두 읽었습니다{thin_note}",
        )
    )
    result.coverage.append(
        CoverageLine("인용 문구 대조", qscan.total, qscan.checked, dict(qscan.skipped))
    )
    verified_locs = sum(1 for r in loc_refs if r.status in ("일치", "변경없음", "범위초과"))
    result.coverage.append(
        CoverageLine(
            "위치 참조 검증",
            len(loc_refs),
            verified_locs,
            {"확인불가": loc_counts.get("확인불가", 0)} if loc_counts.get("확인불가") else {},
        )
    )
    listed = len(sscan.listed)
    rate = f"문단 변경률 {diff.changed_ratio * 100:.0f}%"
    if not sscan.undeclared:
        changes_line = (
            f"변경 문단 {len(diff.changes)}건을 모두 대조했습니다 — 응답서에 "
            f"연결되지 않은 변경은 없습니다. {rate}."
        )
    else:
        dropped = sum(1 for c in sscan.undeclared if c.numbers_dropped)
        changes_line = (
            f"변경 문단 {len(diff.changes)}건을 모두 대조했습니다. 이 중 응답서에 "
            f"연결되지 않은 것이 {len(sscan.undeclared)}건"
            + (f"(있던 값이 바뀐 것 {dropped}건)" if dropped else "")
            + f"이고, 그중 {listed}건을 위에 개별로 실었습니다. {rate}."
        )
    result.coverage.append(
        CoverageLine("변경 문단", len(diff.changes), len(diff.changes), custom=changes_line)
    )
    result.coverage.append(
        CoverageLine(
            "기계로 확인할 수 없는 변경 주장",
            unverifiable_claims,
            unverifiable_claims,
            custom=(
                f"기계로 확인할 수 없는 변경 주장 {unverifiable_claims}건 — "
                "사람이 직접 봐야 합니다"
                if unverifiable_claims
                else "기계로 확인할 수 없는 변경 주장: 없음"
            ),
        )
    )

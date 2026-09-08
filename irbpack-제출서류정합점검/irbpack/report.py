"""리포트 — 콘솔 · 정합점검.md · CSV 3종. **[커버리지 자백] 블록 없이는 출력되지 않습니다.**

`render_console` 은 커버리지가 비어 있거나 12항목의 행방(대조 성립 / 대조불가)을 다 설명하지
못하면 `ReportIntegrityError` 를 던집니다. 리포트가 조금 못생겨지는 것보다 "다 봤다"고
거짓말하는 쪽이 훨씬 비쌉니다.

리포트로 나가는 모든 문자열은 `mask.mask`(전화·주민번호·이메일) 와 `safeio.sanitize_line`
(개행·제어문자) 을 거칩니다.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from . import __version__, mask, safeio, textnorm
from .model import (CRITICAL, Coverage, Doc, Evidence, Extraction, ITEM_KEYS, ITEM_NAMES, Issue,
                    MATCH, Result, UNCOMPARABLE, WARNING)

COVERAGE_HEADER = "[커버리지 자백]"
ROLE_HEADER = "[문서 역할]"
DISCLAIMER = ("이 툴은 IRB·식약처 심의 결과를 예측하지 않고, 규정 준수 여부를 판정하지 않으며, "
              "어느 문서의 값을 채택할지 정하지 않습니다. 문서 사이가 다르다는 사실과 근거 위치만 제시합니다.")


class ReportIntegrityError(Exception):
    """커버리지 자백이 없거나 불완전하면 리포트를 내지 않습니다."""


def _s(text: object) -> str:
    return safeio.sanitize_line(mask.mask("" if text is None else str(text)))


def check_coverage(cov: Coverage, docs: Sequence[Doc]) -> None:
    if cov is None:
        raise ReportIntegrityError("커버리지 자백이 없습니다")
    if cov.n_docs <= 0:
        raise ReportIntegrityError("문서 수가 0 인 커버리지")
    if cov.n_read + len(cov.unread) != cov.n_docs:
        raise ReportIntegrityError("읽은 문서 {} + 못 읽은 문서 {} ≠ 전체 {}".format(cov.n_read, len(cov.unread), cov.n_docs))
    accounted = set(cov.items_compared) | {name for name, _ in cov.items_uncomparable}
    missing = [ITEM_NAMES[k] for k in ITEM_KEYS if ITEM_NAMES[k] not in accounted]
    if missing:
        raise ReportIntegrityError("행방이 설명되지 않은 항목: {}".format(", ".join(missing)))
    valid = set(ITEM_NAMES.values())
    for name in list(cov.items_compared) + [n for n, _ in cov.items_uncomparable]:
        if name not in valid:
            raise ReportIntegrityError("12항목이 아닌 이름이 항목 집계에 섞임: {}".format(name))
    both = set(cov.items_compared) & {n for n, _ in cov.items_uncomparable}
    if both:
        raise ReportIntegrityError("대조 성립과 대조 불가에 동시에 있는 항목: {}".format(", ".join(sorted(both))))
    if len(cov.items_compared) + len(cov.items_uncomparable) != len(ITEM_KEYS):
        raise ReportIntegrityError("대조 성립 {} + 대조 불가 {} ≠ 12".format(len(cov.items_compared), len(cov.items_uncomparable)))
    if len(docs) != cov.n_docs:
        raise ReportIntegrityError("문서 목록과 커버리지 문서 수가 다릅니다")


def _pad(text: str, width: int) -> str:
    w = sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)
    return text + " " * max(0, width - w)


SENTENCE_MAX = 120


def _issue_lines(no: int, issue: Issue) -> List[str]:
    lines = ["  {}. {}".format(no, _s(issue.title))]
    for ev in issue.evidence:
        loc = "({})".format(_s(ev.where)) if ev.where else ""
        mark = "  ← 다름" if ev.differs else ""
        lines.append("       {} {}  {}{}".format(_pad(_s(ev.label), 14), _s(ev.value), loc, mark))
        if ev.sentence:
            sent = _s(ev.sentence)
            if len(sent) > SENTENCE_MAX:
                sent = sent[:SENTENCE_MAX] + "…"
            lines.append("       {} 「{}」".format(" " * 14, sent))
    if issue.note:
        lines.append("       · {}".format(_s(issue.note)))
    return lines


_ROLE_RANK = {"프로토콜": 0, "동의서": 1, "동의서(소아)": 2, "CRF": 3, "모집공고": 4, "등록정보": 5}


def ordered_docs(docs: Sequence[Doc]) -> List[Doc]:
    """[문서 역할] 표시 순서 — 프로토콜 → 동의서 → 승낙서 → CRF → 공고. 미판별·못 읽음은 뒤로."""
    return sorted(docs, key=lambda d: (_ROLE_RANK.get(d.role, 9), not d.readable, d.name))


def render_console(res: Result, input_label: str) -> str:
    check_coverage(res.coverage, res.docs)
    cov = res.coverage
    L: List[str] = []
    L.append("irbpack {} — 제출서류 정합점검".format(__version__))
    L.append("입력: {}  (문서 {}개)".format(_s(input_label), cov.n_docs))
    L.append("")
    L.append(ROLE_HEADER)
    for d in ordered_docs(res.docs):
        tag = ""
        if not d.readable:
            tag = "  [읽지 못함: {}]".format(_s(d.unread_reason))
        elif d.tracked_changes:
            tag = "  (변경내용 추적 있음 — 모두 수락한 상태로 읽음)"
        label = d.label or d.role or "역할 미판별"
        L.append("  {} {}   ({}){}".format(_pad(_s(label), 12), _s(d.name), _s(d.role_reason), tag))
    L.append("")
    crit = [i for i in res.issues if i.severity == CRITICAL]
    warn = [i for i in res.issues if i.severity == WARNING]
    match = [i for i in res.issues if i.severity == MATCH]
    no = 0
    L.append("[치명] {}건".format(len(crit)))
    for i in crit:
        no += 1
        L.extend(_issue_lines(no, i))
    L.append("")
    L.append("[경고] {}건".format(len(warn)))
    for i in warn:
        no += 1
        L.extend(_issue_lines(no, i))
    L.append("")
    L.append("[정보] 일치 확인 {}건".format(len(match)))
    for i in match:
        vals = i.evidence[0].value if i.evidence else ""
        L.append("  {} — {}".format(_s(i.title), _s(vals)) if vals else "  {}".format(_s(i.title)))
    L.append("")
    L.append(COVERAGE_HEADER)
    L.append("  문서       {}개 중 {}개 읽음 (읽지 못한 문서 {})".format(cov.n_docs, cov.n_read, len(cov.unread)))
    for name, why in cov.unread:
        L.append("             · {} — {}".format(_s(name), _s(why)))
    L.append("  대조 항목  12개 중 {}개를 2개 이상 문서에서 찾아 대조".format(len(cov.items_compared)))
    L.append("  대조 불가  {}개".format(len(cov.items_uncomparable)) + (" — " + " / ".join("{}({})".format(_s(n), _s(w)) for n, w in cov.items_uncomparable) if cov.items_uncomparable else ""))
    if cov.sub_gaps:
        L.append("  하위 항목 빠짐  {}".format(" / ".join("{}({})".format(_s(n), _s(w)) for n, w in cov.sub_gaps)))
    if cov.manual_only:
        L.append("  사람이 볼 것  {} — 서술형이라 항목추출표.csv 를 눈으로 대조".format(", ".join(_s(x) for x in cov.manual_only)))
    L.append("  정규화 적용 {} — 정규화 덕에 같다고 본 쌍 {}개 (규칙은 정합점검.md 부록)".format(
        ", ".join(cov.norm_rules_used) if cov.norm_rules_used else "없음", cov.norm_equal_pairs))
    L.append("  자동판별   문서 {}개 자동, --role 지정 {}개".format(cov.auto_roles, cov.forced_roles))
    if cov.baseline_note:
        L.append("  개정 축    {}".format(_s(cov.baseline_note)))
    L.append("  마스킹     리포트의 전화·주민번호·이메일은 항상 가림 ({})".format(mask.RULES[0].split(" (")[0]))
    L.append("")
    L.append("치명 {}건. → exit {}{}".format(len(crit), res.exit_code, ("  ({})".format(_s(res.exit_reason)) if res.exit_reason else "")))
    return "\n".join(L) + "\n"


def render_md(res: Result, input_label: str, console: str) -> str:
    check_coverage(res.coverage, res.docs)
    cov = res.coverage
    L: List[str] = ["# irbpack 정합점검 리포트", ""]
    L.append("> {}".format(DISCLAIMER))
    L.append("")
    L.append("```")
    L.append(console.rstrip("\n"))
    L.append("```")
    L.append("")
    L.append("## 항목 × 문서 대조표")
    L.append("")
    docs = [d for d in res.docs if d.readable]
    L.append("| 항목 | " + " | ".join(_md(d.label or d.name) for d in docs) + " |")
    L.append("|---|" + "---|" * len(docs))
    by: Dict[Tuple[str, str], List[Extraction]] = {}
    for e in res.extractions:
        by.setdefault((e.item, e.doc), []).append(e)
    for key in ITEM_KEYS:
        cells = []
        for d in docs:
            exts = by.get((key, d.name), [])
            if not exts:
                cells.append("—")
                continue
            vals: List[str] = []
            for e in exts[:6]:
                v = _md(mask.mask(e.raw))
                if e.sub:
                    v = "{}: {}".format(_md(e.sub), v)
                if v not in vals:
                    vals.append(v)
            cells.append("<br>".join(vals) + ("<br>…" if len(exts) > 6 else ""))
        L.append("| {} | {} |".format(ITEM_NAMES[key], " | ".join(cells)))
    L.append("")
    L.append("## 문서 역할 판별 근거")
    L.append("")
    for d in ordered_docs(res.docs):
        L.append("- **{}** `{}` — {}{}".format(_md(d.label or "미판별"), _md(d.name), _md(d.role_reason),
                                            "" if d.readable else " — 읽지 못함: " + _md(d.unread_reason)))
    L.append("")
    L.append("## 부록 A. 정규화 규칙 (적용된 것만)")
    L.append("")
    for name in cov.norm_rules_used:
        L.append("- **{}** — {}".format(name, textnorm.RULES.get(name, "")))
    L.append("")
    L.append("## 부록 B. 마스킹 규칙")
    L.append("")
    for r in mask.RULES:
        L.append("- {}".format(r))
    L.append("")
    L.append("## 부록 C. 판정 규칙")
    L.append("")
    L.append("- **값 충돌**: 둘 이상의 문서가 같은 항목을 말하는데 정규화 후 값이 다르다(서로 부분집합이 아니다) → 항목별 심각도.")
    L.append("- **전파 누락**: 한 문서의 값이 다른 문서 값의 진부분집합이다 → 경고. 값이 아예 없는 문서는 울지 않습니다.")
    L.append("- **동의 범위 밖**: CRF 폼 이름이 프로토콜·동의서 본문 어디에도 없다 → 경고.")
    L.append("- **공고 연락처**: 모집공고에 개인 휴대전화(01X)가 있는데 동의서·프로토콜 연락처에 없다 → 경고.")
    L.append("- **한 문서에만 있는 값**: 대조불가로 자백. 치명이 아닙니다.")
    L.append("- 어느 값을 채택할지는 판정하지 않습니다.")
    L.append("")
    return "\n".join(L) + "\n"


def _md(text: object) -> str:
    return _s(text).replace("|", "\\|").replace("`", "'")


def issue_rows(res: Result) -> List[List[str]]:
    """불일치목록.csv — 기준 문서(다르지 않은 첫 문서) 대 '다름' 문서 쌍만. 같은 값끼리의 쌍은 적지 않습니다."""
    rows: List[List[str]] = []

    def plain(v: str) -> str:
        return _s(v).replace('"', "")

    for issue in res.issues:
        if issue.severity == MATCH:
            continue
        ev = issue.evidence
        if not ev:
            rows.append([ITEM_NAMES.get(issue.item, issue.item), issue.severity, _s(issue.title), "", "", "", "", "", ""])
            continue
        ref = next((e for e in ev if not e.differs), ev[0])
        others = [e for e in ev if e is not ref and (e.differs or not any(x.differs for x in ev))]
        if not others:
            rows.append([ITEM_NAMES.get(issue.item, issue.item), issue.severity, _s(issue.title),
                         _s(ref.label), plain(ref.value), _s(ref.where), "", "", ""])
            continue
        for b in others:
            rows.append([ITEM_NAMES.get(issue.item, issue.item), issue.severity, _s(issue.title),
                         _s(ref.label), plain(ref.value), _s(ref.where), _s(b.label), plain(b.value), _s(b.where)])
    return rows


def extraction_rows(res: Result) -> List[List[str]]:
    return [[_s(e.label), _s(e.doc), ITEM_NAMES.get(e.item, e.item), _s(e.sub), _s(e.raw), _s(e.norm),
             str(e.para) if e.para >= 0 else "파일명", _s(e.where), _s(e.sentence)] for e in res.extractions]


def uncomparable_rows(res: Result) -> List[List[str]]:
    return [[_s(n), _s(w)] for n, w in res.coverage.items_uncomparable] + [[_s(n), _s(w)] for n, w in res.coverage.sub_gaps]


def write_all(res: Result, out_dir: str, md_text: str) -> List[str]:
    check_coverage(res.coverage, res.docs)
    if COVERAGE_HEADER not in md_text:
        raise ReportIntegrityError("정합점검.md 에 커버리지 자백 블록이 없습니다")
    written = [safeio.write_text(out_dir, "정합점검.md", md_text)]
    written.append(safeio.write_csv(out_dir, "불일치목록.csv",
                                    ["항목", "심각도", "제목", "문서A", "값A", "근거위치A", "문서B", "값B", "근거위치B"], issue_rows(res)))
    written.append(safeio.write_csv(out_dir, "항목추출표.csv",
                                    ["역할", "문서", "항목", "하위항목", "추출값", "정규화값", "문단번호", "근거위치", "원문문장"], extraction_rows(res)))
    written.append(safeio.write_csv(out_dir, "대조불가.csv", ["항목", "사유"], uncomparable_rows(res)))
    return written

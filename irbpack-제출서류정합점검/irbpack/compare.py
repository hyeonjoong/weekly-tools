"""문서 사이 대조 — 결정론적 규칙 5개. 어느 값을 채택할지는 판단하지 않습니다.

- **값 충돌**: 둘 이상의 문서가 같은 항목을 말하는데 정규화 후 값이 다르다 (서로 부분집합이
  아니다) → 항목별 심각도(치명/경고).
- **전파 누락**: 한 문서의 값이 다른 문서 값의 진부분집합이다 (예: 모집공고에 군별 인원만 있고
  총 인원이 없다) → 경고. **값이 아예 없는 문서는 울지 않습니다** — 안 적는 게 맞는 문서도 있습니다.
- **동의 범위 밖**: CRF 에 폼이 있는데 프로토콜·동의서 어디에도 그 이름이 없다 → 경고.
- **공고 연락처**: 모집공고에 개인 휴대전화(01X)가 있는데 동의서 연락처에 없다 → 경고.
- **한 문서에만 있는 값**: 치명이 아니라 `대조불가` 로 자백.

`--baseline` 개정 축은 **프로토콜에서 값이 바뀐 항목만** 봅니다. 전체 diff 가 되면 이 툴은
죽습니다(기획서).
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Dict, List, Optional, Sequence, Set, Tuple

from . import mask, textnorm
from .model import (CRITICAL, Coverage, Doc, Evidence, Extraction, ITEM_KEYS, ITEM_NAMES,
                    ITEM_SEVERITY, Issue, MATCH, ROLE_AD, ROLE_ASSENT, ROLE_CRF, ROLE_ICF,
                    ROLE_PROTOCOL, UNCOMPARABLE, WARNING)

#: 서술형이라 값 단위 비교가 약한 항목 — 항목 단위 차이는 '대조불가' 로 넘기고 개수만 봅니다.
NARRATIVE_ITEMS = ("criteria", "assessments")


class Table:
    """항목 → 하위키 → 문서 → [Extraction]."""

    def __init__(self, extractions: Sequence[Extraction], docs: Sequence[Doc]) -> None:
        self.docs = [d for d in docs if d.readable]
        self.by_name = {d.name: d for d in docs}
        self.data: Dict[str, Dict[str, "OrderedDict[str, List[Extraction]]"]] = {}
        for e in extractions:
            self.data.setdefault(e.item, {}).setdefault(e.sub, OrderedDict()).setdefault(e.doc, []).append(e)

    def subs(self, item: str) -> List[str]:
        return list(self.data.get(item, {}).keys())

    def per_doc(self, item: str, sub: str) -> "OrderedDict[str, List[Extraction]]":
        return self.data.get(item, {}).get(sub, OrderedDict())

    def docs_with(self, item: str) -> Set[str]:
        out: Set[str] = set()
        for sub in self.subs(item):
            out.update(self.per_doc(item, sub).keys())
        return out

    def label(self, doc_name: str) -> str:
        d = self.by_name.get(doc_name)
        return d.label if d else doc_name

    def role(self, doc_name: str) -> str:
        d = self.by_name.get(doc_name)
        return d.role if d else ""


def _norms(exts: List[Extraction]) -> Set[str]:
    return {e.norm for e in exts}


def _fmt_values(exts: List[Extraction]) -> str:
    seen: List[str] = []
    for e in exts:
        v = mask.mask(e.raw)
        if v not in seen:
            seen.append(v)
    return ", ".join('"{}"'.format(v) for v in seen[:8]) + (" …" if len(seen) > 8 else "")


def _evidence(exts: List[Extraction], table: Table, differs: bool = False) -> Evidence:
    first = exts[0]
    return Evidence(doc=first.doc, label=table.label(first.doc), value=_fmt_values(exts),
                    where=first.where, sentence=mask.mask(first.sentence), differs=differs)


def _order(table: Table, names: Sequence[str]) -> List[str]:
    rank = {ROLE_PROTOCOL: 0, ROLE_ICF: 1, ROLE_ASSENT: 2, ROLE_CRF: 3, ROLE_AD: 4}
    return sorted(names, key=lambda n: (rank.get(table.role(n), 9), n))


def _norm_equal_pairs(per_doc: "OrderedDict[str, List[Extraction]]") -> int:
    """정규화 덕분에 같다고 판정한 (raw 는 다른데 norm 이 같은) 쌍의 수."""
    count = 0
    names = list(per_doc.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a = {e.norm: e.raw for e in per_doc[names[i]]}
            b = {e.norm: e.raw for e in per_doc[names[j]]}
            for norm in set(a) & set(b):
                if textnorm.basic(a[norm]).replace(" ", "") != textnorm.basic(b[norm]).replace(" ", ""):
                    count += 1
    return count


# ----------------------------------------------------------------- 항목별 규칙

def _compare_sets(item: str, sub: str, table: Table, issues: List[Issue], cov: Coverage) -> Optional[str]:
    """집합 비교 (n·age·visits·duration·compensation·retention·pi·title·version).
    반환: 이 하위키의 판정 ('치명'/'경고'/'일치'/None=대조 불성립)."""
    per_doc = table.per_doc(item, sub)
    if len(per_doc) < 2:
        return None
    cov.norm_equal_pairs += _norm_equal_pairs(per_doc)
    names = _order(table, list(per_doc.keys()))
    sets = {n: _norms(per_doc[n]) for n in names}
    title = ITEM_NAMES[item] + (" — {}".format(sub) if sub else "")
    # 값 충돌: 서로 부분집합이 아닌 쌍이 하나라도 있으면
    conflict = False
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = sets[names[i]], sets[names[j]]
            if not (a <= b or b <= a):
                conflict = True
    if conflict:
        ref = sets[names[0]]
        ev = [_evidence(per_doc[n], table, differs=(sets[n] != ref)) for n in names]
        n_diff = len({frozenset(s) for s in sets.values()})
        issues.append(Issue(severity=ITEM_SEVERITY[item], item=item,
                            title="{} — 문서 {}곳이 {}가지 다른 값".format(title, len(names), n_diff), evidence=ev))
        return ITEM_SEVERITY[item]
    biggest = max(sets.values(), key=len)
    partial = [n for n in names if sets[n] < biggest]
    if partial:
        full = [n for n in names if sets[n] == biggest]
        ev = [_evidence(per_doc[n], table) for n in full] + [_evidence(per_doc[n], table, differs=True) for n in partial]
        missing_desc = []
        for n in partial:
            miss = biggest - sets[n]
            raws = []
            for fn in full:
                for e in per_doc[fn]:
                    if e.norm in miss and mask.mask(e.raw) not in raws:
                        raws.append(mask.mask(e.raw))
            missing_desc.append("{}에 {} 없음".format(table.label(n), " / ".join(raws[:4])))
        issues.append(Issue(severity=WARNING, item=item, title="{} 전파 누락 — {}".format(title, "; ".join(missing_desc)),
                            evidence=ev, note="값이 일부만 있습니다 (충돌은 아님)"))
        return WARNING
    issues.append(Issue(severity=MATCH, item=item, title=title, evidence=[_evidence(per_doc[n], table) for n in names]))
    return MATCH


def _compare_contact(table: Table, issues: List[Issue], cov: Coverage) -> Optional[str]:
    phones = table.per_doc("contact", "전화")
    emails = table.per_doc("contact", "이메일")
    if len(phones) < 2 and len(emails) < 2:
        return None
    worst: Optional[str] = None
    # 공고 연락처 규칙
    ad_docs = [n for n in phones if table.role(n) == ROLE_AD]
    ref_docs = [n for n in phones if table.role(n) in (ROLE_ICF, ROLE_ASSENT, ROLE_PROTOCOL)]
    ref_set: Set[str] = set()
    for n in ref_docs:
        ref_set |= _norms(phones[n])
    flagged = False
    for ad in ad_docs:
        for e in phones[ad]:
            if mask.looks_personal_mobile(e.norm) and e.norm not in ref_set and ref_docs:
                ev = [Evidence(doc=ad, label=table.label(ad), value='"{}"'.format(mask.mask(e.raw)), where=e.where,
                               sentence=mask.mask(e.sentence), differs=True)]
                for n in ref_docs:
                    ev.append(_evidence(phones[n], table))
                issues.append(Issue(severity=WARNING, item="contact",
                                    title="모집공고 연락처가 동의서·프로토콜 연락처와 다름 — 개인 휴대전화로 보임", evidence=ev))
                worst = WARNING
                flagged = True
                break
    for sub, per_doc in (("전화", phones), ("이메일", emails)):
        if len(per_doc) < 2:
            continue
        names = _order(table, list(per_doc.keys()))
        sets = {n: _norms(per_doc[n]) for n in names}
        union_any = any((sets[a] & sets[b]) for a in names for b in names if a < b)
        if not union_any and not (flagged and sub == "전화"):
            issues.append(Issue(severity=WARNING, item="contact", title="연락처({}) — 문서 간에 겹치는 값이 하나도 없음".format(sub),
                                evidence=[_evidence(per_doc[n], table, differs=True) for n in names]))
            worst = WARNING
        elif union_any and not flagged:
            if worst is None:
                worst = MATCH
    if worst == MATCH:
        names = _order(table, list(phones.keys()) or list(emails.keys()))
        src = phones if phones else emails
        issues.append(Issue(severity=MATCH, item="contact", title="연락처", evidence=[_evidence(src[n], table) for n in names if n in src]))
    return worst


def _compare_assessments(table: Table, issues: List[Issue], cov: Coverage) -> Optional[str]:
    per_doc = table.per_doc("assessments", "")
    if not per_doc:
        return None
    crf_docs = [n for n in per_doc if table.role(n) == ROLE_CRF]
    other_docs = [d for d in table.docs if d.role in (ROLE_PROTOCOL, ROLE_ICF, ROLE_ASSENT)]
    if not crf_docs or not other_docs:
        return None
    other_text = {d.name: textnorm.squash(d.text) for d in other_docs}
    worst: Optional[str] = None
    # 동의 범위 밖: CRF 폼이 프로토콜·동의서 본문 어디에도 없다
    for crf in crf_docs:
        outside: List[Extraction] = []
        for e in per_doc[crf]:
            if not any(e.norm in t for t in other_text.values()):
                outside.append(e)
        if outside:
            ev = [Evidence(doc=crf, label=table.label(crf), value=_fmt_values(outside), where=outside[0].where,
                           sentence=mask.mask(outside[0].sentence), differs=True)]
            issues.append(Issue(severity=WARNING, item="assessments",
                                title="동의 범위 밖 — CRF 폼 {}개가 프로토콜·동의서 본문에 없음".format(len(outside)), evidence=ev,
                                note="프로토콜 평가항목·동의서 설명 어디에도 같은 이름이 없습니다"))
            worst = WARNING
    # 전파 누락: 프로토콜 평가항목이 CRF 본문에 없다
    crf_text = {n: textnorm.squash(table.by_name[n].text) for n in crf_docs}
    for n in per_doc:
        if table.role(n) != ROLE_PROTOCOL:
            continue
        missing = [e for e in per_doc[n] if not any(e.norm in t for t in crf_text.values())]
        if missing:
            ev = [Evidence(doc=n, label=table.label(n), value=_fmt_values(missing), where=missing[0].where,
                           sentence=mask.mask(missing[0].sentence), differs=True)]
            issues.append(Issue(severity=WARNING, item="assessments",
                                title="평가항목 전파 누락 — 프로토콜 평가항목 {}개가 CRF 에 없음".format(len(missing)), evidence=ev))
            worst = WARNING
    if worst is None:
        names = _order(table, list(per_doc.keys()))
        issues.append(Issue(severity=MATCH, item="assessments", title="평가·검사 항목 (CRF 폼이 전부 프로토콜·동의서에 있음)",
                            evidence=[_evidence(per_doc[n], table) for n in names]))
        worst = MATCH
    return worst


def _compare_criteria(table: Table, issues: List[Issue], cov: Coverage) -> Optional[str]:
    worst: Optional[str] = None
    for sub in ("선정기준 개수", "제외기준 개수"):
        per_doc = table.per_doc("criteria", sub)
        if len(per_doc) < 2:
            continue
        names = _order(table, list(per_doc.keys()))
        vals = {n: _norms(per_doc[n]) for n in names}
        if len({frozenset(v) for v in vals.values()}) > 1:
            issues.append(Issue(severity=WARNING, item="criteria", title="{} — 문서마다 다름".format(sub),
                                evidence=[_evidence(per_doc[n], table, differs=(vals[n] != vals[names[0]])) for n in names],
                                note="항목 단위 대조는 서술형이라 사람이 봐야 합니다 (항목추출표.csv 참조)"))
            worst = WARNING
        elif worst is None:
            worst = MATCH
            issues.append(Issue(severity=MATCH, item="criteria", title=sub, evidence=[_evidence(per_doc[n], table) for n in names]))
    return worst


# ----------------------------------------------------------------- 진입점

def compare(extractions: Sequence[Extraction], docs: Sequence[Doc], cov: Coverage) -> List[Issue]:
    table = Table(extractions, docs)
    issues: List[Issue] = []
    readable = [d for d in docs if d.readable]
    for item in ITEM_KEYS:
        with_docs = table.docs_with(item)
        if not with_docs:
            cov.items_uncomparable.append((ITEM_NAMES[item], "어느 문서에서도 추출되지 않음" + (" (서술형이라 정규식이 약함)" if item in NARRATIVE_ITEMS else "")))
            continue
        if len(with_docs) == 1:
            only = next(iter(with_docs))
            cov.items_uncomparable.append((ITEM_NAMES[item], "한 문서에만 존재 ({})".format(table.label(only))))
            continue
        verdicts: List[Optional[str]] = []
        if item == "contact":
            verdicts.append(_compare_contact(table, issues, cov))
        elif item == "assessments":
            verdicts.append(_compare_assessments(table, issues, cov))
        elif item == "criteria":
            verdicts.append(_compare_criteria(table, issues, cov))
        else:
            for sub in table.subs(item):
                if item == "visits" and sub == "참여기간":
                    v = _compare_period(table, issues, cov)
                else:
                    v = _compare_sets(item, sub, table, issues, cov)
                verdicts.append(v)
                if v is None and sub and len(table.per_doc(item, sub)) == 1:
                    only = next(iter(table.per_doc(item, sub)))
                    cov.items_uncomparable.append(("{} — {}".format(ITEM_NAMES[item], sub), "한 문서에만 존재 ({})".format(table.label(only))))
        if all(v is None for v in verdicts):
            cov.items_uncomparable.append((ITEM_NAMES[item], "같은 하위 항목을 2개 이상 문서가 말하지 않음"))
            if item in NARRATIVE_ITEMS:
                cov.manual_only.append(ITEM_NAMES[item])
        else:
            cov.items_compared.append(ITEM_NAMES[item])
    rules: List[str] = []
    for e in extractions:
        for r in e.norm_rules:
            if r not in rules:
                rules.append(r)
    for r in ("유니코드 NFC", "전각→반각"):
        if r not in rules:
            rules.append(r)
    cov.norm_rules_used = rules
    return issues


def _compare_period(table: Table, issues: List[Issue], cov: Coverage) -> Optional[str]:
    per_doc = table.per_doc("visits", "참여기간")
    if len(per_doc) < 2:
        return None
    units = {n: {e.norm[-1:] if e.norm.endswith(("주", "일", "년")) else "개월" for e in per_doc[n]} for n in per_doc}
    all_units: Set[str] = set()
    for u in units.values():
        all_units |= u
    if len(all_units) > 1:
        cov.items_uncomparable.append(("참여 기간", "단위가 달라(주/개월/일) 환산하지 않고 대조하지 않음"))
        return None
    return _compare_sets("visits", "참여기간", table, issues, cov)


# ----------------------------------------------------------------- 개정 축

def compare_baseline(cur: Sequence[Extraction], cur_docs: Sequence[Doc], base: Sequence[Extraction],
                     base_docs: Sequence[Doc], cov: Coverage) -> List[Issue]:
    """프로토콜에서 바뀐 값만 보고, 부속문서가 옛 값을 그대로 갖고 있으면 치명."""
    ct, bt = Table(cur, cur_docs), Table(base, base_docs)
    cur_proto = [d.name for d in cur_docs if d.role == ROLE_PROTOCOL and d.readable]
    base_proto = [d.name for d in base_docs if d.role == ROLE_PROTOCOL and d.readable]
    issues: List[Issue] = []
    if not cur_proto or not base_proto:
        cov.baseline_note = "기준 패킷 또는 현재 패킷에 프로토콜이 없어 개정 축을 보지 않음"
        return issues
    changed: List[str] = []
    stale = 0
    for item in ITEM_KEYS:
        if item in ("contact", "assessments", "criteria"):
            continue
        for sub in set(ct.subs(item)) | set(bt.subs(item)):
            new = set()
            for n in cur_proto:
                new |= _norms(ct.per_doc(item, sub).get(n, []))
            old = set()
            for n in base_proto:
                old |= _norms(bt.per_doc(item, sub).get(n, []))
            if not new or not old or new == old:
                continue
            name = ITEM_NAMES[item] + (" — {}".format(sub) if sub else "")
            changed.append(name)
            old_raw = ", ".join(sorted({mask.mask(e.raw) for n in base_proto for e in bt.per_doc(item, sub).get(n, [])})[:4])
            new_raw = ", ".join(sorted({mask.mask(e.raw) for n in cur_proto for e in ct.per_doc(item, sub).get(n, [])})[:4])
            for doc_name, exts in ct.per_doc(item, sub).items():
                if doc_name in cur_proto:
                    continue
                vals = _norms(exts)
                if vals == old and vals != new:
                    stale += 1
                    ev = [Evidence(doc=cur_proto[0], label=ct.label(cur_proto[0]), value='"{}" (기준 패킷: "{}")'.format(new_raw, old_raw),
                                   where=ct.per_doc(item, sub)[cur_proto[0]][0].where if cur_proto[0] in ct.per_doc(item, sub) else ""),
                          Evidence(doc=doc_name, label=ct.label(doc_name), value=_fmt_values(exts), where=exts[0].where,
                                   sentence=mask.mask(exts[0].sentence), differs=True)]
                    issues.append(Issue(severity=CRITICAL, item=item, title="개정 미반영 — 프로토콜은 {} 이 바뀌었는데 {} 는 기준 패킷 값 그대로".format(name, ct.label(doc_name)),
                                        evidence=ev, note="--baseline 개정 축"))
    cov.baseline_note = "프로토콜에서 값이 바뀐 항목 {}개 ({}) · 옛 값이 남은 부속문서 {}건 · 바뀌지 않은 항목은 보지 않음".format(
        len(changed), ", ".join(changed) if changed else "없음", stale)
    return issues

"""개정 축 (`--baseline 이전패킷/`) — 프로토콜에서 **바뀐 값**만 추적한다.

전체 diff 를 내면 이 툴은 죽는다(문서 두 벌이 통째로 빨간 줄이 된다). 그래서 보는 것은
딱 하나: *"프로토콜에서 바뀐 값이 부속문서에도 따라 바뀌었는가."*
프로토콜이 그대로인 항목은 쳐다보지도 않는다.
"""

import difflib
import re

from .compare import Finding, Row
from .items import FACETS, ITEM_KEYS, ITEM_LABEL
from .roles import PROTOCOL, CONSENT, ASSENT, CRF, AD, REGISTRY

ANCILLARY = (CONSENT, ASSENT, CRF, AD, REGISTRY)
_VERSIONISH = re.compile(r"[vV]\d+(?:[._]\d+)*|\d{6,8}|final|clean|copy|사본", re.I)


def _index(mentions):
    index = {}
    for mention in mentions:
        index.setdefault((mention.item, mention.facet), {}).setdefault(mention.doc, {}) \
            .setdefault(mention.value, []).append(mention)
    return index


def _strip_versionish(name):
    return re.sub(r"[\s_\-]+", " ", _VERSIONISH.sub(" ", name)).strip().lower()


MIN_NAME_SIMILARITY = 0.85     # 이보다 안 닮았으면 '같은 문서의 이전 판'이 아니다
# 같은 역할 안에서 문서를 가르는 꼬리표 — 이게 다르면 아무리 이름이 닮아도 다른 문서다.
# ('ICF_성인용' 과 'ICF_보호자용' 은 유사도 0.78~0.88 로 임계값만으로는 갈리지 않는다)
_VARIANT_TOKENS = ("성인용", "보호자용", "소아용", "아동용", "대리인", "assent", "adult", "parent",
                   "child", "pediatric", "guardian", "성인", "소아", "보호자")


def _variants(name):
    lowered = name.lower()
    return frozenset(token for token in _VARIANT_TOKENS if token in lowered)


def match_document(doc, candidates):
    """같은 역할 문서 중 이름이 가장 비슷한 것 (버전·날짜 토큰은 지우고 비교).

    유사도 하한을 두는 이유: 이번에 새로 만든 성인용 동의서를, 이전 패킷의 보호자용
    동의서와 짝지어 '개정 미반영' 이라고 우기는 사고가 실제로 재현됐다.
    """
    same_role = [other for other in candidates if other.role == doc.role and other.read_ok]
    if not same_role:
        return None
    target = _strip_versionish(doc.name)
    target_variants = _variants(doc.name)
    best = None
    best_score = -1.0
    for other in same_role:
        if _variants(other.name) != target_variants:
            continue                       # 성인용 ↔ 보호자용 같은 다른 문서
        score = difflib.SequenceMatcher(None, target, _strip_versionish(other.name)).ratio()
        if score > best_score:
            best, best_score = other, score
    if best is None or best_score < MIN_NAME_SIMILARITY:
        return None
    return best


def compare_baseline(new_documents, new_mentions, old_documents, old_mentions):
    """(치명 findings, 안내문 목록). 프로토콜이 없거나 안 바뀌었으면 빈 목록."""
    notes = []
    new_protocols = [doc for doc in new_documents if doc.role == PROTOCOL]
    old_protocols = [doc for doc in old_documents if doc.role == PROTOCOL]
    if not old_protocols:
        return [], ["이전 패킷에서 프로토콜을 찾지 못해 개정 축 점검을 건너뛰었습니다"]
    new_protocol = new_protocols[0]
    old_protocol = old_protocols[0]

    new_index = _index(new_mentions)
    old_index = _index(old_mentions)
    findings = []
    changed_count = 0
    unpaired = []
    for doc in new_documents:
        if doc.role in ANCILLARY and doc.read_ok and match_document(doc, old_documents) is None:
            unpaired.append(doc.name)

    for item in ITEM_KEYS:
        for facet in FACETS[item]:
            new_values = set((new_index.get((item, facet), {}).get(new_protocol.name) or {}))
            old_values = set((old_index.get((item, facet), {}).get(old_protocol.name) or {}))
            if not new_values or not old_values or new_values == old_values:
                continue
            changed_count += 1
            protocol_row = _row(new_index, item, facet, new_protocol, "기준")
            old_row = _row(old_index, item, facet, old_protocol, "동일")
            old_row.role = "프로토콜(이전)"
            for doc in new_documents:
                if doc.role not in ANCILLARY or not doc.read_ok:
                    continue
                doc_new = set((new_index.get((item, facet), {}).get(doc.name) or {}))
                if not doc_new:
                    continue
                counterpart = match_document(doc, old_documents)
                if counterpart is None:
                    continue
                doc_old = set((old_index.get((item, facet), {}).get(counterpart.name) or {}))
                if not doc_old or doc_new != doc_old:
                    continue                      # 부속문서도 바뀌었으면 여기서 볼 일이 아니다
                if not (doc_new - new_values):
                    continue                      # 새 프로토콜과 여전히 맞는다
                rows = [old_row, protocol_row, _row(new_index, item, facet, doc, "다름")]
                summary = ("%s — 프로토콜이 개정되었는데 %s(%s)는 이전 값 그대로입니다"
                           % (facet, doc.name, doc.role))
                findings.append(Finding(item, "치명", ["개정미반영"], summary, rows,
                                        ["이전 패킷: %s → 현재: %s" % (
                                            " · ".join(sorted(old_values)), " · ".join(sorted(new_values)))]))
    findings = _merge_by_item(findings)
    if unpaired:
        notes.append("이전 패킷에서 짝을 찾지 못해 개정 반영 여부를 보지 못한 문서: %s "
                     "(이름이 많이 달라졌거나 이번에 새로 만든 문서입니다)" % " · ".join(unpaired))
    if not changed_count:
        notes.append("이전 패킷 대비 프로토콜에서 바뀐 값이 없어, 개정 전파는 볼 것이 없었습니다")
    else:
        notes.append("프로토콜에서 값이 바뀐 대조단위 %d개를 기준으로 부속문서 반영 여부를 봤습니다" % changed_count)
    findings.sort(key=lambda finding: ITEM_KEYS.index(finding.item))
    return findings, notes


def _merge_by_item(findings):
    """같은 항목에서 여러 대조단위가 걸려도 결론은 항목당 하나 (본 축과 같은 규칙)."""
    merged = {}
    order = []
    for finding in findings:
        if finding.item not in merged:
            merged[finding.item] = finding
            order.append(finding.item)
            continue
        first = merged[finding.item]
        first.summary = "%s / %s" % (first.summary, finding.summary)
        first.blocks.extend(finding.blocks)
        for row in finding.rows:
            if row not in first.rows:
                first.rows.append(row)
        for note in finding.notes:
            if note not in first.notes:
                first.notes.append(note)
    return [merged[item] for item in order]


def _row(index, item, facet, doc, status):
    values = index.get((item, facet), {}).get(doc.name) or {}
    keys = sorted(values)
    display = [values[key][0].display for key in keys]
    first = values[keys[0]][0] if keys else None
    return Row(doc.name, doc.role, display, first.where if first else "", first.quote if first else "", status)


def label_of(item):
    return ITEM_LABEL[item]

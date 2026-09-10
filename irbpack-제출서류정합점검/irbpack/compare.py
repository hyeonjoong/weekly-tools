"""문서 사이 대조 — 이 툴의 판단이 전부 여기 있다.

규칙은 네 개뿐이고 전부 결정론적이다.

  1. **값 충돌** — 같은 항목을 둘 이상이 말하는데, 기준 문서(프로토콜)에 없는 값을
     말하는 문서가 있다. 심각도는 항목표를 따른다.
  2. **전파 누락** — 프로토콜에 있는 값이, 그 항목을 다루고 있는 부속문서에 빠져 있다
     → 경고(치명 아님. 안 적는 게 맞는 항목도 있다).
  3. **동의 범위 밖** — CRF 에만 있는 검사 폼이 프로토콜·동의서 어디에도 없다 → 경고.
  4. **공고 연락처** — 모집공고의 개인 휴대전화가 동의서의 대표 연락처와 다르다 → 경고.

기준 문서를 프로토콜로 두는 이유: 부속문서가 프로토콜의 *일부만* 적는 것은 정상이고
(성인용 동의서가 소아 그룹을 안 적는 것처럼), 프로토콜에 **없는 말**을 하는 것이
비정상이기 때문이다. 그래서 부분 기재는 조용히 넘어가고, 새로 생긴 값만 잡는다.

어느 값이 옳은지는 판단하지 않는다(강제 장치 5).
"""

from .items import FACETS, ITEM_LABEL, ITEM_SEVERITY, ITEM_KEYS, is_mobile, touches_topic
from .safeio import mask_then_truncate
from .roles import AD, CONSENT, ASSENT, CRF, PROTOCOL

SEVERITY_ORDER = {"치명": 0, "경고": 1, "정보": 2}

# 범위 안에 들어가면 같은 말로 보는 항목 — **방문 횟수 하나뿐**이다.
# '1~2회 방문' 은 "1회 또는 2회"를 열거한 것이므로 CRF 의 'Visit 1·2' 는 그 안에 든다.
# 여기에 넣지 않는 것들:
#   * 연령 — '만 20~65세' 와 '만 20~64세' 는 자격 기준이 다른 것이다(1세 차를 놓치면 툴이 무용지물).
#   * 소요시간 — 실제 패킷에서 '총 약 1~3시간' 같은 넓은 범위가 '60~90분 vs 90~180분' 불일치를
#     통째로 삼켜 버렸다. 문서에 적힌 시간은 적힌 그대로 대조한다.
RANGE_SUBSUMING_ITEMS = {"visits"}

# 연구 전체에서 값이 **하나여야 하는** 대조단위.
# 여기서는 기준 문서와의 비교만으로 부족하다 — 프로토콜이 3년과 10년을 둘 다 언급하면
# 동의서(3년)와 CRF(10년)가 서로 다른 말을 해도 둘 다 "기준 안"이 되어 조용히 넘어간다.
# 그래서 이 단위에서는 부속문서끼리도 맞대 본다(라운드1 #2).
#
# **총 대상자 수와 보상 금액은 여기서 뺐다 (라운드2 P2-2).** 성인용 동의서가 60명·20,000원을,
# 소아 승낙서가 24명·10,000원을 적는 것은 각자 자기 군을 말하는 정상적인 문서다.
# 그걸 치명으로 올리면 성인+소아 패킷마다 오탐이 나고, 그 순간 이 툴은 꺼진다.
# (두 문서가 총N 을 정말 다르게 적으면 프로토콜의 총N 하나와 어긋나므로 기준 규칙이 잡는다)
SINGLE_VALUE_FACETS = {
    ("version", "버전"), ("version", "버전(파일명)"),
    ("version", "문서날짜"), ("version", "문서날짜(파일명)"),
    ("title", "제목"), ("retention", "보관기간"), ("pi", "연구책임자"),
}
# 부속문서끼리의 불일치는 **경고**다 — 툴은 두 문서가 같은 대상을 말하는지 알 수 없다.
PAIRWISE_SEVERITY = "경고"
_RANGE_VALUE = __import__("re").compile(r"^(\d+)(?:-(\d+))?(회|분)$")

# 값이 빠졌을 때 "전파 누락"으로 볼 만한 항목만. (버전·날짜·제목까지 보면 우는 체커가 된다)
PROPAGATION_FACETS = {
    ("subjects", "총N"),
    ("age", "연령범위"),
    ("visits", "방문횟수"),
    ("duration", "소요시간"),
    ("payment", "형태"),
    ("retention", "보관기간"),
}
ANCILLARY_ROLES = (CONSENT, ASSENT, CRF, AD)

# '값을 못 찾았다' 고 따로 알릴 필요가 없는 대조단위.
# 문서 버전·날짜는 형식 표기라 없는 문서가 정상이다(모집공고에 'Version No:' 줄은 잘 없다).
SILENT_EXEMPT_FACETS = {
    ("version", "버전"), ("version", "버전(파일명)"),
    ("version", "문서날짜"), ("version", "문서날짜(파일명)"),
}


class Row(object):
    """리포트의 근거 한 줄."""

    __slots__ = ("doc", "role", "values", "where", "quote", "status")

    def __init__(self, doc, role, values, where, quote, status):
        self.doc = doc
        self.role = role
        self.values = values          # 사람이 읽는 값 목록
        self.where = where
        self.quote = quote
        self.status = status          # '기준' | '다름' | '동일' | '없음'


class Finding(object):
    """항목 하나에 대한 결론. 항목당 최대 하나로 합친다."""

    def __init__(self, item, severity, kinds, summary, rows, notes=None):
        self.item = item
        self.label = ITEM_LABEL[item]
        self.severity = severity
        self.kinds = kinds
        self.summary = summary
        self.rows = rows
        self.notes = notes or []
        # 대조단위별 (요약, 근거줄) 묶음. 항목당 하나로 합칠 때 이 목록이 이어붙는다 —
        # 요약과 근거 줄이 따로 놀면 "접었습니다" 안내가 바로 위에 펼쳐 놓은 문서를
        # 가리키는 모순이 생긴다(라운드2 A-2).
        self.blocks = [(summary, rows)]

    def sort_key(self):
        return (SEVERITY_ORDER.get(self.severity, 9), ITEM_KEYS.index(self.item))


class Uncomparable(object):
    """대조가 성립하지 않은 항목·단위와 그 사유 (반드시 자백한다)."""

    __slots__ = ("item", "facet", "reason", "docs")

    def __init__(self, item, facet, reason, docs=None):
        self.item = item
        self.facet = facet
        self.reason = reason
        self.docs = docs or []


class ComparisonResult(object):
    def __init__(self):
        self.findings = []
        self.agreements = []          # (item, facet, 값, 문서 수)
        self.uncomparable = []
        self.compared_items = set()
        self.normalized_pairs = 0     # 표기가 달랐지만 정규화로 같다고 본 값의 개수
        self.normalized_examples = []


def _index(mentions):
    """(item, facet) → {문서명: {값: [mention...]}}"""
    index = {}
    for mention in mentions:
        by_doc = index.setdefault((mention.item, mention.facet), {})
        values = by_doc.setdefault(mention.doc, {})
        values.setdefault(mention.value, []).append(mention)
    return index


def _items_engaged(mentions):
    """문서별로 '이 항목을 다루고 있는가' (facet 무관)."""
    engaged = {}
    for mention in mentions:
        engaged.setdefault(mention.doc, set()).add(mention.item)
    return engaged


def _pairwise_conflicts(by_doc, item, facet, names):
    """값이 하나여야 하는 단위에서, 서로 양립하지 않는 문서들의 이름."""
    if (item, facet) not in SINGLE_VALUE_FACETS:
        return set()
    conflicting = set()
    for index, first in enumerate(names):
        for second in names[index + 1:]:
            first_values = set(by_doc[first])
            second_values = set(by_doc[second])
            covers_first = all(_covered(value, first_values, item) for value in second_values)
            covers_second = all(_covered(value, second_values, item) for value in first_values)
            if not covers_first and not covers_second:
                conflicting.add(first)
                conflicting.add(second)
    return conflicting


def _pick_anchor(by_doc, documents):
    """기준 문서: 프로토콜 우선, 없으면 값이 가장 많은 문서(동점이면 입력 순서)."""
    order = dict((doc.name, position) for position, doc in enumerate(documents))
    roles = dict((doc.name, doc.role) for doc in documents)
    candidates = sorted(
        by_doc.keys(),
        key=lambda name: (
            0 if roles.get(name) == PROTOCOL else 1,
            -len(by_doc[name]),
            order.get(name, 999),
        ),
    )
    return candidates[0]


def _covered(value, anchor_values, item):
    """기준 문서의 값들이 이 값을 포괄하는가."""
    if value in anchor_values:
        return True
    if item not in RANGE_SUBSUMING_ITEMS:
        return False
    match = _RANGE_VALUE.match(value)
    if not match:
        return False
    low = int(match.group(1))
    high = int(match.group(2) or match.group(1))
    unit = match.group(3)
    for other in anchor_values:
        other_match = _RANGE_VALUE.match(other)
        if not other_match or other_match.group(3) != unit:
            continue
        other_low = int(other_match.group(1))
        other_high = int(other_match.group(2) or other_match.group(1))
        if other_low <= low and high <= other_high:
            return True
    return False


def _short(name, limit=28):
    """요약 줄에 파일명을 넣을 때만 줄인다 (근거 줄에는 전체 이름이 그대로 남는다)."""
    return mask_then_truncate(name, limit)


def _display(values_map, value_keys):
    out = []
    for key in sorted(value_keys):
        mentions = values_map.get(key) or []
        out.append(mentions[0].display if mentions else key)
    return out


def compare(documents, mentions):
    """문서들 사이의 값 대조. mentions 는 extract_all 결과를 모두 이어붙인 것."""
    result = ComparisonResult()
    index = _index(mentions)
    engaged = _items_engaged(mentions)
    roles = dict((doc.name, doc.role) for doc in documents)
    protocol_names = [doc.name for doc in documents if doc.role == PROTOCOL]
    protocol = protocol_names[0] if protocol_names else None

    per_item = {}

    for item in ITEM_KEYS:
        for facet in FACETS[item]:
            by_doc = index.get((item, facet), {})

            # 전파 누락은 **대조 성립 여부와 무관하게** 본다.
            # (프로토콜에만 값이 있는 경우가 오히려 전형적인 누락이다)
            if (item, facet) in PROPAGATION_FACETS and protocol and protocol in by_doc:
                for document in documents:
                    if document.role not in ANCILLARY_ROLES or not document.read_ok:
                        continue
                    if document.name in by_doc:
                        continue
                    # 그 항목을 다루고 있는데(다른 대조단위에 값이 있거나, 주제어를 말하거나)
                    # 이 대조단위만 비어 있는 경우에만 묻는다. 주제어까지 보는 이유는
                    # 대조단위가 하나뿐인 항목(연령·소요시간·보관기간)에서도 규칙이 살아야 하기 때문이다.
                    if (item not in engaged.get(document.name, set())
                            and not touches_topic(document, item)):
                        continue
                    protocol_values = by_doc[protocol]
                    first = protocol_values[sorted(protocol_values)[0]][0]
                    gap_rows = [
                        Row(protocol, PROTOCOL, _display(protocol_values, protocol_values.keys()),
                            first.where, first.quote, "기준"),
                        Row(document.name, document.role, [], "", "", "없음"),
                    ]
                    per_item.setdefault(item, []).append(
                        ("전파누락", "경고",
                         "%s — 프로토콜에는 있는데 %s(%s)에는 대응 값이 없습니다" % (
                             facet, document.role, _short(document.name)),
                         gap_rows, []))

            if len(by_doc) < 2:
                if len(by_doc) == 1:
                    only = list(by_doc)[0]
                    reason = "'%s' 한 문서에만 있어 대조할 상대가 없습니다" % only
                    docs = [only]
                else:
                    reason = "어느 문서에서도 값을 찾지 못했습니다"
                    docs = []
                result.uncomparable.append(Uncomparable(item, facet, reason, docs))
                continue

            result.compared_items.add(item)

            # 이 항목을 말하고는 있는데 값이 안 잡힌 문서 — 절대 조용히 넘기지 않는다.
            # (표현이 달라 정규식이 놓친 값이 '일치'로 둔갑하는 것을 막는 장치)
            silent = [] if (item, facet) in SILENT_EXEMPT_FACETS else [
                document for document in documents
                if document.read_ok and document.name not in by_doc
                and (item in engaged.get(document.name, set())
                     or touches_topic(document, item))]
            silent_names = [document.name for document in silent]
            if silent_names:
                result.uncomparable.append(Uncomparable(
                    item, facet,
                    "이 항목을 언급하지만 값을 추출하지 못한 문서가 있습니다 — 나머지 문서끼리 값이 같아도 "
                    "'일치'가 아닙니다. 해당 문서를 직접 확인하세요",
                    silent_names))

            anchor = _pick_anchor(by_doc, documents)
            anchor_values = set(by_doc[anchor])
            ordered = [document.name for document in documents if document.name in by_doc]
            outside = dict(
                (doc, set(value for value in by_doc[doc]
                          if not _covered(value, anchor_values, item)))
                for doc in ordered)
            # 기준 문서가 여러 값을 말해 둘 다 '기준 안'이 되어 버리는 경우를 위해,
            # 기준을 통과한 문서들끼리 한 번 더 맞대 본다(값이 하나여야 하는 단위만).
            covered_docs = [doc for doc in ordered if not outside[doc]]
            pairwise = _pairwise_conflicts(by_doc, item, facet, covered_docs)

            rows = []
            differing = []
            pairwise_docs = []
            for doc in ordered:
                values_map = by_doc[doc]
                extra = outside[doc]
                if doc in pairwise:
                    pairwise_docs.append(doc)
                    extra = set(values_map)
                status = "기준" if (doc == anchor and not extra) else ("다름" if extra else "동일")
                first_mention = values_map[sorted(values_map)[0]][0]
                if extra:
                    first_mention = values_map[sorted(extra)[0]][0]
                    differing.append(doc)
                rows.append(Row(doc, roles.get(doc, ""), _display(values_map, values_map.keys()),
                                first_mention.where, first_mention.quote, status))

            for document in silent:
                rows.append(Row(document.name, document.role, [], "", "", "값못찾음"))

            # 정규화 덕분에 같다고 본 값 세기 — **문서를 가로질러** 표기가 갈렸던 값만.
            displays = {}
            holders = {}
            for name, values_map in by_doc.items():
                for key, group in values_map.items():
                    displays.setdefault(key, set()).update(mention.display for mention in group)
                    holders.setdefault(key, set()).add(name)
            for key, shown in sorted(displays.items()):
                if len(shown) > 1 and len(holders.get(key, ())) > 1:
                    result.normalized_pairs += 1
                    example = "%s/%s: %s → %s" % (
                        ITEM_LABEL[item], facet, " · ".join(sorted(shown)), key)
                    if example not in result.normalized_examples and len(result.normalized_examples) < 8:
                        result.normalized_examples.append(example)

            if differing:
                blamed = " · ".join(roles.get(name, name) or name for name in differing)
                only_pairwise = bool(pairwise_docs) and set(pairwise_docs) == set(differing)
                if only_pairwise:
                    summary = ("%s — 부속문서끼리 다른 값을 말합니다 (%s) — 같은 대상을 말하는 것이 "
                               "맞는지 확인하세요" % (facet, blamed))
                    kind, severity = "부속문서충돌", PAIRWISE_SEVERITY
                else:
                    summary = "%s — %s: 기준 문서에 없는 값 (값을 말한 문서 %d곳 중 %d곳)" % (
                        facet, blamed, len(by_doc), len(differing))
                    kind, severity = "값충돌", ITEM_SEVERITY[item]
                per_item.setdefault(item, []).append((kind, severity, summary, rows, []))
            else:
                shared = sorted(set.intersection(*[set(by_doc[doc]) for doc in by_doc]))
                display = _display(by_doc[anchor], shared) if shared else _display(by_doc[anchor], anchor_values)
                result.agreements.append(
                    (item, facet, " · ".join(display), len(by_doc), silent_names))


    result.uncomparable.append(Uncomparable(
        "assessments", "항목 목록 전체",
        "문서마다 검사 명칭 표기가 달라 v1은 값 대조를 하지 않습니다 (CRF 전용 폼 존재 여부만 확인)"))
    result.uncomparable.append(Uncomparable(
        "criteria", "기준 항목 수",
        "서술형이라 항목 경계를 기계적으로 나눌 수 없어 v1은 세지 않습니다 (군 구성만 대조)"))

    # 규칙 4 — 모집공고의 개인 연락처
    ad_note = _ad_contact_note(documents, index)
    if ad_note:
        existing = per_item.setdefault("contact", [])
        if existing:
            existing[0][4].append(ad_note)
        else:
            per_item["contact"] = [("공고연락처", "경고", "모집공고 연락처", [], [ad_note])]

    # 규칙 3 — CRF 에만 있는 검사 폼
    crf_only = _crf_only_forms(documents, mentions)
    if crf_only:
        per_item.setdefault("assessments", []).append(crf_only)

    for item, entries in per_item.items():
        severity = min((entry[1] for entry in entries), key=lambda value: SEVERITY_ORDER[value])
        kinds = []
        summaries = []
        rows = []
        notes = []
        blocks = []
        for kind, _, summary, entry_rows, entry_notes in entries:
            if kind not in kinds:
                kinds.append(kind)
            summaries.append(summary)
            rows.extend(entry_rows)
            notes.extend(entry_notes)
            blocks.append((summary, entry_rows))
        finding = Finding(item, severity, kinds, " / ".join(summaries), rows, notes)
        finding.blocks = blocks
        result.findings.append(finding)

    result.findings.sort(key=lambda finding: finding.sort_key())
    return result


def _ad_contact_note(documents, index):
    """모집공고에 개인 휴대전화가 있고 동의서 대표 연락처와 다르면 한 줄 경고."""
    ad_docs = [doc.name for doc in documents if doc.role == AD]
    consent_docs = [doc.name for doc in documents if doc.role in (CONSENT, ASSENT)]
    if not ad_docs or not consent_docs:
        return ""
    phones = index.get(("contact", "전화"), {})
    consent_numbers = set()
    for doc in consent_docs:
        consent_numbers.update(phones.get(doc, {}))
    flagged = []
    for doc in ad_docs:
        for value, mentions in (phones.get(doc) or {}).items():
            if is_mobile(value) and value not in consent_numbers:
                flagged.append("%s: %s" % (doc, mentions[0].display))
    if not flagged:
        return ""
    return ("모집공고에 동의서의 대표 연락처와 다른 개인 휴대전화가 실려 있습니다 — %s "
            "(공개 게시물입니다)" % " · ".join(flagged))


def _crf_only_forms(documents, mentions):
    """CRF 에만 있는 검사 폼 → '동의 범위 밖' 경고 한 건."""
    crf_docs = set(doc.name for doc in documents if doc.role == CRF)
    other_docs = set(doc.name for doc in documents
                     if doc.role in (PROTOCOL, CONSENT, ASSENT) and doc.read_ok)
    if not crf_docs or not other_docs:
        return None
    elsewhere = set()
    crf_forms = {}
    for mention in mentions:
        if mention.item != "assessments":
            continue
        if mention.doc in crf_docs:
            if mention.facet == "폼제목":
                crf_forms.setdefault(mention.value, mention)
        elif mention.doc in other_docs:
            elsewhere.add(mention.value)
    missing = sorted(value for value in crf_forms if value not in elsewhere)
    if not missing:
        return None
    rows = []
    for value in missing:
        mention = crf_forms[value]
        rows.append(Row(mention.doc, CRF, [mention.display], mention.where, mention.quote, "다름"))
    summary = "CRF 에만 있는 검사 폼 %d개 — 프로토콜·동의서 어디에도 설명이 없습니다" % len(missing)
    return ("동의범위밖", "경고", summary, rows, [])


def _copy_finding(finding):
    clone = Finding(finding.item, finding.severity, list(finding.kinds), finding.summary,
                    list(finding.rows), list(finding.notes))
    clone.blocks = list(finding.blocks)
    return clone


def merge_findings(primary, extra):
    """두 소견 목록을 **항목당 하나로** 합친다.

    개정 축(--baseline) 소견과 본 축 소견이 같은 항목을 가리키면, 사람 눈에는 한 문제다.
    따로 세면 '치명 2건'으로 보여 밤 11시에 두 배로 무겁게 읽힌다(라운드2 A-9).
    """
    # 원본 Finding 을 건드리지 않는다 — all_findings 는 여러 번 호출되는 프로퍼티라
    # 제자리에서 합치면 호출할 때마다 블록이 불어난다.
    merged = [_copy_finding(finding) for finding in primary]
    index = dict((finding.item, finding) for finding in merged)
    for original in extra:
        finding = _copy_finding(original)
        first = index.get(finding.item)
        if first is None:
            merged.append(finding)
            index[finding.item] = finding
            continue
        if SEVERITY_ORDER[finding.severity] < SEVERITY_ORDER[first.severity]:
            first.severity = finding.severity
        first.summary = "%s / %s" % (first.summary, finding.summary)
        first.blocks.extend(finding.blocks)
        first.rows.extend(finding.rows)
        for kind in finding.kinds:
            if kind not in first.kinds:
                first.kinds.append(kind)
        for note in finding.notes:
            if note not in first.notes:
                first.notes.append(note)
    merged.sort(key=lambda finding: finding.sort_key())
    return merged


def severity_counts(findings):
    counts = {"치명": 0, "경고": 0}
    for finding in findings:
        if finding.severity in counts:
            counts[finding.severity] += 1
    return counts

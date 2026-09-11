"""요약표(`total-최종` 류) 대조.

요약표는 사람이 피험자 시트의 %를 **손으로 옮겨 적은** 표다. 그래서 여기서는
표의 숫자를 믿지 않고, 문항에서 다시 계산한 값과 셀 단위로 맞춰 본다.

열 이름을 해석하지 못하면 **조용히 넘기지 않고** '대조하지 못한 열'로 세어
`[커버리지 자백]` 에 적는다.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from .findings import CRITICAL, INFO, WARNING, Finding
from .hangul import normalize
from .reader import Pass, parse_number

#: 시점 이름 외에 요약표 열에서 시점을 가리킬 수 있는 약어.
TIMEPOINT_ALIASES = {
    "사전": ("사전", "전", "pre", "baseline", "기저", "before"),
    "사후": ("사후", "후", "post", "after"),
}
#: 음소 채점 열을 가리키는 표시.
PHONEME_TOKENS = ("음소", "phoneme", "phon")
#: 파생값(차이) 열을 가리키는 표시.
DIFF_TOKENS = ("차이", "변화", "diff", "change", "delta")
_SEP_RE = re.compile(r"[\s_\-()\[\]{}·.:/]+")


class ColumnRole:
    __slots__ = ("test", "timepoint", "unit", "kind")

    def __init__(self, test, timepoint, unit, kind):
        self.test = test
        self.timepoint = timepoint
        self.unit = unit
        self.kind = kind          # "값" 또는 "차이"

    def __repr__(self):
        return "ColumnRole(%s/%s/%s/%s)" % (self.test, self.timepoint,
                                            self.unit, self.kind)


def parse_summary_column(name: str, tests: Sequence[str],
                         timepoints: Sequence[str]) -> Optional[ColumnRole]:
    """``'일음절(음소)_전'`` → ColumnRole(일음절, 사전, 음소, 값).

    남는 토큰이 하나라도 해석되지 않으면 **None** — 억지로 맞추지 않는다.
    """
    text = normalize(name)
    if not text:
        return None
    matched_test = None
    for test in sorted(tests, key=len, reverse=True):
        t = normalize(test)
        if t and t in text:
            matched_test = test
            text = text.replace(t, " ", 1)
            break
    if matched_test is None:
        return None
    tokens = [t for t in _SEP_RE.split(text) if t]
    unit = "단어"
    kind = "값"
    timepoint = None
    leftover = []
    for token in tokens:
        low = token.lower()
        if any(p in low for p in PHONEME_TOKENS):
            unit = "음소"
            continue
        if any(d in low for d in DIFF_TOKENS):
            kind = "차이"
            continue
        found = None
        for tp in timepoints:
            aliases = set(TIMEPOINT_ALIASES.get(tp, ())) | {normalize(tp)}
            if low in {a.lower() for a in aliases}:
                found = tp
                break
        if found:
            if timepoint is not None and timepoint != found:
                return None
            timepoint = found
            continue
        leftover.append(token)
    if leftover:
        return None
    if kind == "차이":
        return ColumnRole(matched_test, None, unit, "차이")
    if timepoint is None:
        return None
    return ColumnRole(matched_test, timepoint, unit, "값")


def find_id_column(rows: Dict[int, Dict[int, str]], header_row: int,
                   subjects: Sequence[str]) -> Optional[int]:
    """피험자 시트 이름이 가장 많이 들어 있는 열을 ID 열로 본다."""
    want = {normalize(s) for s in subjects}
    best, best_hits = None, 0
    columns = set()
    for rnum in rows:
        if rnum <= header_row:
            continue
        columns.update(rows[rnum])
    for col in sorted(columns):
        hits = sum(1 for rnum in rows if rnum > header_row
                   and normalize(rows[rnum].get(col, "")) in want)
        if hits > best_hits:
            best, best_hits = col, hits
    return best if best_hits else None


def compare(rows: Dict[int, Dict[int, str]], sheet: str,
            passes: Sequence[Pass], tests: Sequence[str],
            timepoints: Sequence[str], subjects: Sequence[str],
            overrides: Optional[Dict[str, dict]] = None):
    """요약표를 셀 단위로 대조한다.

    돌려주는 값: ``(소견목록, 통계dict, 대조못한열목록)``
    """
    overrides = overrides or {}
    header_row = min(rows) if rows else 1
    header = rows.get(header_row, {})
    id_col = find_id_column(rows, header_row, subjects)
    findings: List[Finding] = []
    stats = {"대조": 0, "불일치": 0, "값없음": 0, "재계산불가": 0,
             "문항시트없는행": 0, "요약표없는피험자": 0}
    unmapped: List[str] = []
    if id_col is None:
        findings.append(Finding(
            CRITICAL, "요약표 ID 열 없음",
            "요약 시트 '%s' 에서 피험자 시트 이름과 맞는 ID 열을 찾지 못했습니다 — "
            "이 표는 대조하지 못했습니다." % sheet,
            location=sheet))
        return findings, stats, [normalize(v) for v in header.values()]

    roles: Dict[int, ColumnRole] = {}
    for col, name in sorted(header.items()):
        if col == id_col:
            continue
        clean = normalize(name)
        if clean in overrides:
            o = overrides[clean]
            roles[col] = ColumnRole(o.get("검사"), o.get("시점"),
                                    o.get("단위", "단어"), o.get("종류", "값"))
            continue
        role = parse_summary_column(clean, tests, timepoints)
        if role is None:
            if any(normalize(t) in clean for t in tests):
                unmapped.append(clean)
            continue
        roles[col] = role

    index = {(p.subject, p.test, p.timepoint, p.unit): p for p in passes}
    known = {normalize(x): x for x in subjects}
    seen_subjects = set()
    orphan_rows = []
    per_column_mismatch = Counter()
    per_column_total = Counter()
    per_column_values = defaultdict(list)
    column_findings = defaultdict(list)
    for rnum in sorted(rows):
        if rnum <= header_row:
            continue
        subject = normalize(rows[rnum].get(id_col, ""))
        if not subject:
            continue
        if subject not in known:
            # 표에는 있는데 문항 시트가 없는 행. 조용히 넘기면 커버리지가
            # '100% 대조'라고 거짓말을 한다.
            orphan_rows.append(subject)
            continue
        seen_subjects.add(known[subject])
        for col, role in sorted(roles.items()):
            raw = normalize(rows[rnum].get(col, ""))
            if not raw:
                stats["값없음"] += 1
                continue
            reported = parse_number(raw)
            if reported is None:
                stats["값없음"] += 1
                continue
            recomputed, denom, earned = _recompute(index, known[subject], role,
                                                   timepoints)
            if recomputed is None:
                stats["재계산불가"] += 1
                continue
            stats["대조"] += 1
            per_column_total[col] += 1
            per_column_values[col].append((rnum, known[subject], reported,
                                           recomputed))
            if abs(reported - recomputed) > 0.051:
                stats["불일치"] += 1
                per_column_mismatch[col] += 1
                column_findings[col].append(len(findings))
                findings.append(Finding(
                    CRITICAL, "요약표 값 불일치",
                    "%s · %s: 표의 값 %s 와 문항 재계산 %.1f 가 다릅니다%s."
                    % (subject, normalize(header.get(col, "")), raw, recomputed,
                       "" if not isinstance(denom, int)
                       else " (%s/%d)" % (earned, denom)),
                    subject=subject,
                    location="%s %d행 %s열" % (sheet, rnum,
                                              normalize(header.get(col, ""))),
                    evidence=("분모 %s" % denom) if isinstance(denom, int)
                             else (denom or "분모를 알 수 없습니다")))
    # 한 열이 거의 전부 어긋나면 그건 자료 오류가 아니라 **매핑 오류**다.
    # 수십 건을 토해내는 대신 한 건으로 묶어 매핑을 의심하라고 말한다.
    # 열 단위로 묶을 때는 **열 번호**로 지운다. 열 이름으로 `endswith` 하면
    # `음절_전` 을 묶다가 `일음절_전` 의 진짜 소견까지 지워 버린다(실제로 그랬다).
    drop = set()
    collapsed = []
    for col, bad in sorted(per_column_mismatch.items()):
        total = per_column_total[col]
        name = normalize(header.get(col, ""))
        shifted = _detect_row_shift(per_column_values[col])
        if shifted:
            drop.update(column_findings[col])
            collapsed.append(Finding(
                CRITICAL, "요약표 열이 한 행 밀림",
                "'%s' 열은 %s행부터 %d칸이 **한 행씩 밀려** 있습니다 — 그 행의 값이 "
                "바로 아래 피험자의 재계산값과 같습니다. 붙여넣기 사고로 보입니다."
                % (name, shifted[0], len(shifted[1])),
                location="%s %s열" % (sheet, name),
                evidence="밀린 피험자: %s" % ", ".join(shifted[1][:8])))
            continue
        if total >= 5 and bad >= total * 0.8:
            drop.update(column_findings[col])
            collapsed.append(Finding(
                CRITICAL, "요약표 열 매핑 의심",
                "'%s' 열은 %d행 중 %d행이 재계산값과 어긋납니다 — 자료 오류보다 "
                "이 열을 무엇으로 봤는지(검사·시점·단위)가 틀렸을 가능성이 큽니다. "
                "--map 으로 바로잡고 다시 돌리세요." % (name, total, bad),
                location="%s %s열" % (sheet, name),
                evidence="개별 행 소견은 이 한 건으로 묶었습니다."))
    findings = [f for i, f in enumerate(findings) if i not in drop]
    findings.extend(collapsed)
    missing = sorted(set(subjects) - seen_subjects)
    if missing:
        findings.append(Finding(
            CRITICAL, "요약표에 행이 없는 피험자",
            "문항 시트는 있는데 요약표 '%s' 에 행이 없는 피험자 %d명: %s. "
            "이 표로 논문 표를 만들면 그만큼이 빠집니다."
            % (sheet, len(missing), ", ".join(missing)),
            location=sheet))
    if orphan_rows:
        names = sorted(set(orphan_rows))
        aggregate = [x for x in names if _looks_aggregate(x)]
        if aggregate and missing:
            findings.append(Finding(
                CRITICAL, "집계행이 일부 피험자만으로 계산됨",
                "요약표 '%s' 에 집계로 보이는 행(%s)이 있는데 피험자 %d명의 행이 "
                "없습니다 — 그 집계값은 %d명이 아니라 %d명으로 계산된 값입니다."
                % (sheet, ", ".join(aggregate[:6]), len(missing),
                   len(subjects), len(subjects) - len(missing)),
                location=sheet))
        else:
            findings.append(Finding(
                WARNING, "요약표에만 있는 ID",
                "요약표 '%s' 에는 있는데 문항 시트가 없는 ID %d개: %s. 대조하지 "
                "못했습니다." % (sheet, len(names), ", ".join(names[:10])),
                location=sheet))
    stats["문항시트없는행"] = len(orphan_rows)
    stats["요약표없는피험자"] = len(missing)
    return findings, stats, unmapped


def _detect_row_shift(rows):
    """표의 값이 **바로 아래 행의 재계산값**과 같은 구간을 찾는다.

    돌려주는 값은 ``(시작 피험자, [밀린 피험자들])`` 또는 None. 3칸 이상 이어질
    때만 인정한다 — 두 칸은 우연히 같을 수 있다.
    """
    run = []
    best = []
    for i in range(len(rows) - 1):
        _, subject, reported, recomputed = rows[i]
        _, _, _, next_recomputed = rows[i + 1]
        mismatched = recomputed is None or abs(reported - recomputed) > 0.051
        shifted = (next_recomputed is not None
                   and abs(reported - next_recomputed) <= 0.051)
        # **어긋난 행만** 밀림 후보다. 맞은 행까지 세면 값이 같은 정상 열이
        # 통째로 '한 행 밀림'으로 둔갑한다.
        if mismatched and shifted:
            run.append(subject)
        else:
            if len(run) > len(best):
                best = run
            run = []
    if len(run) > len(best):
        best = run
    return (best[0], best) if len(best) >= 3 else None


def _recompute(index, subject, role: ColumnRole, timepoints: Sequence[str]):
    if role.test is None:
        return None, None, None
    if role.kind == "차이":
        # 차이의 부호는 **선언한 시점 순서**로 정한다. 시트에서 블록이 나온
        # 순서(딕셔너리 삽입 순서)로 정하면, 사후 블록이 사전 블록 왼쪽에 있는
        # 파일에서 부호가 통째로 뒤집혀 멀쩡한 자료가 치명으로 나온다.
        if len(timepoints) < 2:
            return None, None, None
        pre = index.get((subject, role.test, timepoints[0], role.unit))
        post = index.get((subject, role.test, timepoints[1], role.unit))
        if not pre or not post or pre.percent is None or post.percent is None:
            return None, None, None
        detail = "%s %.1f(%s/%d) → %s %.1f(%s/%d)" % (
            timepoints[0], pre.percent, _fmt(pre.earned), pre.denominator,
            timepoints[1], post.percent, _fmt(post.earned), post.denominator)
        return round(post.percent - pre.percent, 1), detail, None
    p = index.get((subject, role.test, role.timepoint, role.unit))
    if p is None or p.percent is None:
        return None, None, None
    return p.percent, p.denominator, _fmt(p.earned)


#: 요약표 아래쪽에 흔히 붙는 집계행 표시.
_AGGREGATE_TOKENS = ("(m)", "(sd)", "mean", "평균", "표준편차", "sd", "합계",
                     "total", "n=", "median", "중앙")


def _looks_aggregate(name: str) -> bool:
    low = normalize(name).lower().replace(" ", "")
    return any(token in low for token in _AGGREGATE_TOKENS)


def _fmt(value):
    f = float(value)
    return str(int(f)) if f == int(f) else ("%.1f" % f)

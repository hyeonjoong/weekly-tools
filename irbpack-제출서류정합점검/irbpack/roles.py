"""문서 역할 자동판별 — 파일명 + 첫 1500자 키워드 가중치.

최고점과 차점의 차이가 `MARGIN` 미만이면 추측하지 않고 판별 실패(→ `--role` 요구,
exit 2). 판별 근거(어떤 키워드가 몇 점을 냈는지)는 리포트 [문서 역할] 블록에
그대로 인쇄됩니다 — 사람이 첫 줄만 보고 1초에 틀렸음을 알 수 있게.
"""
from __future__ import annotations

import fnmatch
import os
import re
from typing import Dict, List, Optional, Tuple

from . import textnorm
from .model import (Doc, ROLE_AD, ROLE_ASSENT, ROLE_CRF, ROLE_ICF, ROLE_PROTOCOL,
                    ROLE_REG, ROLES)

MARGIN = 3
MIN_SCORE = 3

#: (정규식, 파일명 점수, 본문 점수)
_KW: Dict[str, List[Tuple[str, int, int]]] = {
    ROLE_PROTOCOL: [
        (r"연구\s*계획서", 6, 4), (r"임상시험\s*계획서", 6, 4), (r"protocol", 6, 3),
        (r"계획서", 3, 1), (r"연구\s*방법", 0, 2), (r"연구\s*설계", 0, 2), (r"연구\s*배경", 0, 2),
        (r"연구\s*목적", 0, 1), (r"통계\s*분석", 0, 1), (r"^\s*(?:연구|임상시험)\s*계획서", 0, 3),
    ],
    ROLE_ICF: [
        (r"동의서", 5, 3), (r"설명문", 5, 3), (r"\bICF\b", 6, 3), (r"consent", 6, 3),
        (r"연구\s*대상자\s*설명문", 6, 4), (r"동의합니다", 0, 3), (r"자발적", 0, 2), (r"서명", 0, 1),
        (r"성인용|보호자용|대리인용", 3, 1),
    ],
    ROLE_ASSENT: [
        (r"assent", 8, 5), (r"승낙서", 8, 5), (r"소아용|아동용|어린이용", 4, 2),
        (r"동의서\s*\(?소아\)?", 6, 3), (r"친구들|어린이\s*여러분|해도 돼요|괜찮아요", 0, 3),
    ],
    ROLE_CRF: [
        (r"\bCRF\b", 8, 4), (r"증례\s*기록서", 8, 5), (r"case\s*report\s*form", 8, 5),
        (r"기록서", 3, 1), (r"\bVisit\s*[0-9]", 0, 2), (r"방문\s*[0-9]", 0, 1), (r"대상자\s*번호|Subject\s*(?:ID|No)", 0, 2),
        (r"기록지", 2, 1),
    ],
    ROLE_AD: [
        (r"모집\s*공고", 8, 5), (r"모집\s*광고", 8, 5), (r"광고안", 6, 3), (r"공고", 3, 1), (r"모집", 3, 2),
        (r"recruit", 6, 3), (r"참여자를?\s*모집|대상자를?\s*모집|모집합니다", 0, 4), (r"문의", 0, 1),
    ],
    ROLE_REG: [
        (r"등록\s*정보", 8, 5), (r"\bCRIS\b", 8, 5), (r"clinicaltrials", 8, 5), (r"registration", 6, 3),
        (r"등록번호|KCT[0-9]{7}|NCT[0-9]{8}", 0, 4),
    ],
}


def _score(doc: Doc) -> Dict[str, Tuple[int, List[str]]]:
    fname = textnorm.basic(os.path.splitext(doc.name)[0])
    head = doc.head(1500)
    out: Dict[str, Tuple[int, List[str]]] = {}
    for role, rules in _KW.items():
        total = 0
        why: List[str] = []
        for rx, f_pts, b_pts in rules:
            if f_pts and re.search(rx, fname, re.IGNORECASE):
                total += f_pts
                why.append("파일명 '{}'".format(_pretty(rx)))
            if b_pts:
                n = len(re.findall(rx, head, re.IGNORECASE | re.MULTILINE))
                if n:
                    pts = min(n, 3) * b_pts
                    total += pts
                    why.append("본문 '{}'×{}".format(_pretty(rx), n))
        out[role] = (total, why)
    return out


def _pretty(rx: str) -> str:
    return re.sub(r"\\[bs]\*?|[\^$()?:\\\[\]]", "", rx).replace("|", "/")[:24]


def sublabel(doc: Doc) -> str:
    """동의서의 대상 구분 라벨 — 성인·보호자·소아 등. 파일명 우선, 없으면 첫 1500자."""
    src = textnorm.basic(doc.name) + "\n" + doc.head(1500)
    for rx, tag in ((r"보호자|대리인|법정대리인", "보호자"), (r"성인", "성인"), (r"소아|아동|어린이", "소아"),
                    (r"환자", "환자"), (r"건강인|대조군", "대조군")):
        if re.search(rx, textnorm.basic(doc.name)):
            return tag
    for rx, tag in ((r"보호자용|대리인용", "보호자"), (r"성인용", "성인"), (r"소아용|아동용", "소아")):
        if re.search(rx, src):
            return tag
    return ""


def detect(doc: Doc) -> Tuple[Optional[str], str]:
    """(역할, 근거). 애매하면 (None, 사유)."""
    if not doc.readable:
        scores = _score(doc)
    else:
        scores = _score(doc)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1][0])
    best, (best_pts, best_why) = ranked[0]
    second, (second_pts, _) = ranked[1]
    if best == ROLE_ICF and (scores[ROLE_ASSENT][0] >= best_pts - 2 or scores[ROLE_ASSENT][0] >= 7) and scores[ROLE_ASSENT][0] >= MIN_SCORE:
        best, best_pts, best_why = ROLE_ASSENT, scores[ROLE_ASSENT][0], scores[ROLE_ASSENT][1]
        second_pts = scores[ROLE_ICF][0] if second == ROLE_ASSENT else second_pts
        second = ROLE_ICF
        return best, "{}점 ({}) · 동의서 {}점보다 소아 표식 우선".format(best_pts, ", ".join(best_why[:4]), second_pts)
    if best_pts < MIN_SCORE:
        return None, "어느 역할 키워드도 충분하지 않음 (최고 {} {}점)".format(best, best_pts)
    if best_pts - second_pts < MARGIN:
        return None, "{} {}점 vs {} {}점 — 차이 {} < {} 로 애매함".format(
            best, best_pts, second, second_pts, best_pts - second_pts, MARGIN)
    return best, "{}점 ({})".format(best_pts, ", ".join(best_why[:4]))


def parse_role_args(args: List[str]) -> List[Tuple[str, str]]:
    """`--role 프로토콜=파일패턴` 목록 → [(역할, 패턴)]. 역할이 모르는 값이면 ValueError."""
    out: List[Tuple[str, str]] = []
    aliases = {"protocol": ROLE_PROTOCOL, "icf": ROLE_ICF, "consent": ROLE_ICF, "assent": ROLE_ASSENT,
               "crf": ROLE_CRF, "ad": ROLE_AD, "recruit": ROLE_AD, "reg": ROLE_REG,
               "승낙서": ROLE_ASSENT, "모집": ROLE_AD, "공고": ROLE_AD, "계획서": ROLE_PROTOCOL,
               "연구계획서": ROLE_PROTOCOL, "증례기록서": ROLE_CRF}
    for a in args or []:
        if "=" not in a:
            raise ValueError("--role 형식은 역할=파일패턴 입니다: {!r}".format(a))
        role, pat = a.split("=", 1)
        role = textnorm.basic(role).strip()
        role = aliases.get(role.lower(), role)
        if role not in ROLES:
            raise ValueError("알 수 없는 역할 {!r} — 가능한 값: {}".format(role, " / ".join(ROLES)))
        if not pat.strip():
            raise ValueError("--role {} 의 파일 패턴이 비어 있습니다".format(role))
        out.append((role, textnorm.nfc(pat.strip())))
    return out


def assign(docs: List[Doc], forced: List[Tuple[str, str]]) -> List[str]:
    """모든 문서에 역할을 붙입니다. 반환: 실패 사유 목록(비어 있으면 성공)."""
    errors: List[str] = []
    for doc in docs:
        for role, pat in forced:
            base = os.path.basename(doc.path)
            if fnmatch.fnmatchcase(textnorm.nfc(base), pat) or textnorm.nfc(base) == pat or fnmatch.fnmatchcase(doc.name, pat):
                doc.role = role
                doc.role_forced = True
                doc.role_reason = "--role 지정"
                break
        if doc.role:
            continue
        role, why = detect(doc)
        if role is None:
            errors.append("{}: {} → --role 역할={} 로 지정해 주세요".format(doc.name, why, doc.name))
            doc.role_reason = why
            continue
        doc.role = role
        doc.role_reason = why
    for doc in docs:
        doc.label = doc.role
        if doc.role == ROLE_ICF:
            tag = sublabel(doc)
            if tag == "소아":
                tag = ""
            if tag:
                doc.label = "동의서({})".format(tag)
    # 같은 라벨이 여러 개면 파일명으로 구분
    seen: Dict[str, int] = {}
    for doc in docs:
        seen[doc.label] = seen.get(doc.label, 0) + 1
    counters: Dict[str, int] = {}
    for doc in docs:
        if seen.get(doc.label, 0) > 1:
            counters[doc.label] = counters.get(doc.label, 0) + 1
            doc.label = "{}#{}".format(doc.label, counters[doc.label])
    for pat_role, pat in forced:
        if not any(d.role_forced and d.role == pat_role and (
                fnmatch.fnmatchcase(textnorm.nfc(os.path.basename(d.path)), pat) or textnorm.nfc(os.path.basename(d.path)) == pat) for d in docs):
            errors.append("--role {}={} 에 맞는 파일이 없습니다".format(pat_role, pat))
    return errors

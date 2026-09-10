"""문서 역할 판별 — 파일명 + 첫 1500자 키워드 가중치.

조용히 틀리는 것만 막으면 된다. 그래서
  * 판별 결과와 **근거 키워드를 리포트 맨 위에 인쇄**하고,
  * 1등과 2등의 점수 차가 임계 미만이면 추측하지 않고 `--role` 을 요구한다(exit 2).

ICF 성인용을 프로토콜로 잘못 잡으면 리포트 전체가 쓰레기가 되므로, 애매하면 멈추는
쪽이 항상 옳다.
"""

import fnmatch
import os
import re

from .normalize import canon, compact
from .safeio import safe_line, safe_name

PROTOCOL = "프로토콜"
CONSENT = "동의서"
ASSENT = "동의서(소아)"
CRF = "CRF"
AD = "모집공고"
REGISTRY = "등록정보"
IRBFORM = "심의서류"
EXCLUDE = "제외"

ROLE_ORDER = [PROTOCOL, CONSENT, ASSENT, CRF, AD, REGISTRY, IRBFORM]
ROLE_ALIASES = {
    "프로토콜": PROTOCOL, "연구계획서": PROTOCOL, "계획서": PROTOCOL, "protocol": PROTOCOL,
    "동의서": CONSENT, "설명문": CONSENT, "icf": CONSENT, "consent": CONSENT,
    "동의서(소아)": ASSENT, "승낙서": ASSENT, "assent": ASSENT, "소아동의서": ASSENT,
    "crf": CRF, "증례기록서": CRF, "증례기록": CRF,
    "모집공고": AD, "공고": AD, "광고": AD, "모집": AD,
    "등록정보": REGISTRY, "등록": REGISTRY, "cris": REGISTRY,
    "심의서류": IRBFORM, "신청서": IRBFORM, "irb": IRBFORM,
    "제외": EXCLUDE, "exclude": EXCLUDE, "skip": EXCLUDE,
}

# (역할, 파일명 키워드, 본문 키워드)
_RULES = [
    (PROTOCOL,
     ("연구계획서", "계획서", "protocol", "프로토콜", "시험계획서", "sap"),
     ("연구계획서", "연구 개요", "선정기준", "제외기준", "연구 방법", "통계적 분석", "예상연구기간",
      "연구책임자 및 공동연구자", "연구의 실시기관", "중지", "탈락 기준")),
    (CONSENT,
     ("icf", "동의서", "설명문", "consent", "informed"),
     ("연구대상자 설명문", "설명문 및 동의서", "권유 받았습니다", "자발적인 결정", "동의합니다",
      "동의취득자", "귀하는", "법정대리인")),
    (ASSENT,
     ("assent", "승낙서", "아동용", "어린이용"),
     ("승낙서", "여러분", "어린이", "그만할래요", "물어봐도 좋아요")),
    (CRF,
     ("crf", "증례기록", "case report", "기록지"),
     ("증례기록서", "case report form", "대상자 식별", "visit log", "검사일자", "기록표", "종료 상태")),
    (AD,
     ("모집", "광고", "공고", "포스터", "recruit", "advert"),
     ("모집 광고", "모집공고", "참여문의", "대상자 모집", "모집 안내", "참여 대상")),
    (REGISTRY,
     ("cris", "clinicaltrials", "등록", "registration", "registry"),
     ("등록번호", "cris", "nct", "clinicaltrials.gov", "임상연구정보서비스")),
    (IRBFORM,
     ("신청서", "심의", "승인", "접수", "의뢰서", "통지"),
     ("심의의뢰서", "심의 의뢰", "승인통지", "접수증", "기관생명윤리위원회 심의", "심의결과")),
]

FILENAME_WEIGHT = 3
BODY_WEIGHT = 1
BODY_CAP = 4                # 본문 키워드로 얻을 수 있는 최대 점수
MIN_SCORE = 3               # 이보다 낮으면 판별 실패
MIN_MARGIN = 2              # 1등-2등 차이가 이보다 작으면 판별 실패

HEAD_CHARS = 1500


class RoleError(Exception):
    """역할을 확정하지 못했다. CLI 가 종료코드 2로 바꾼다."""


def _score(doc):
    name = compact(doc.name).lower()
    head = canon(doc.head_text(HEAD_CHARS)).lower()
    head_compact = compact(head)
    scores = {}
    reasons = {}
    for role, name_words, body_words in _RULES:
        score = 0
        hits = []
        for word in name_words:
            if compact(word).lower() in name:
                score += FILENAME_WEIGHT
                hits.append("파일명 '%s'" % word)
                break                                  # 파일명은 한 번만 센다
        body_score = 0
        for word in body_words:
            if compact(word).lower() in head_compact:
                body_score += BODY_WEIGHT
                hits.append("본문 '%s'" % word)
                if body_score >= BODY_CAP:
                    break
        score += body_score
        if score:
            scores[role] = score
            reasons[role] = hits
    return scores, reasons


def detect_role(doc):
    """(역할, 근거설명). 확정하지 못하면 ('', 사유)."""
    scores, reasons = _score(doc)
    if not scores:
        return "", "역할 키워드가 하나도 없습니다"
    ranked = sorted(scores.items(), key=lambda item: (-item[1], ROLE_ORDER.index(item[0])))
    best_role, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0
    if best_score < MIN_SCORE:
        return "", "근거가 약합니다(최고점 %d < %d)" % (best_score, MIN_SCORE)
    if best_score - second_score < MIN_MARGIN:
        return "", "%s(%d점)과 %s(%d점)이 비슷해 확정할 수 없습니다" % (
            ranked[0][0], best_score, ranked[1][0], second_score)
    hits = reasons[best_role][:3]
    return best_role, ", ".join(hits)


def parse_role_option(values):
    """--role 역할=패턴 목록 → [(역할, 패턴)]. 잘못된 값은 RoleError."""
    parsed = []
    for raw in values or []:
        if "=" not in raw:
            raise RoleError("--role 은 '역할=파일패턴' 형식입니다 (받은 값: %s)" % canon(raw))
        role_text, pattern = raw.split("=", 1)
        key = compact(role_text).lower()
        role = ROLE_ALIASES.get(key)
        if role is None:
            raise RoleError(
                "모르는 역할 이름입니다: '%s' — 쓸 수 있는 이름: %s"
                % (canon(role_text), ", ".join(ROLE_ORDER + [EXCLUDE]))
            )
        if not pattern.strip():
            raise RoleError("--role %s= 뒤에 파일 이름(또는 패턴)이 필요합니다" % canon(role_text))
        parsed.append((role, pattern.strip()))
    return parsed


def _matches(pattern, doc):
    name = doc.name
    if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(os.path.basename(pattern), name):
        return True
    if pattern == doc.path or os.path.abspath(pattern) == os.path.abspath(doc.path):
        return True
    return compact(pattern).lower() in compact(name).lower()


def assign_roles(documents, role_options=None):
    """문서들에 역할을 붙인다. 확정 못 한 문서가 있으면 RoleError.

    반환: (사용할 문서 목록, --role 로 제외된 문서 목록)
    """
    overrides = parse_role_option(role_options)
    excluded = []
    unresolved = []
    for doc in documents:
        forced = [role for role, pattern in overrides if _matches(pattern, doc)]
        if forced:
            if len(set(forced)) > 1:
                raise RoleError(
                    "'%s' 에 서로 다른 --role 지정이 겹칩니다: %s"
                    % (safe_name(doc.name), ", ".join(sorted(set(forced))))
                )
            doc.role = forced[0]
            doc.role_source = "--role 지정"
            doc.role_reason = "사용자 지정"
            continue
        if not doc.read_ok:
            doc.role = ""
            doc.role_source = "읽지 못함"
            doc.role_reason = doc.error
            continue
        role, reason = detect_role(doc)
        doc.role = role
        doc.role_source = "자동" if role else "판별 실패"
        doc.role_reason = reason
        if not role:
            unresolved.append(doc)

    kept = []
    for doc in documents:
        if doc.role == EXCLUDE:
            excluded.append(doc)
        else:
            kept.append(doc)

    unresolved = [doc for doc in unresolved if doc in kept]
    if unresolved:
        lines = ["문서 역할을 확정하지 못했습니다 — 추측하지 않고 멈춥니다."]
        for doc in unresolved:
            lines.append("  · %s — %s" % (safe_name(doc.name), safe_line(doc.role_reason)))
        lines.append("")
        lines.append("점검 대상이 아니면 빼고:")
        lines.append("  --role 제외=%s" % safe_name(unresolved[0].name))
        lines.append("점검 대상이면 역할을 지정해 주세요:")
        lines.append("  --role 동의서=%s" % safe_name(unresolved[0].name))
        lines.append("  (쓸 수 있는 역할: %s)" % ", ".join(ROLE_ORDER))
        raise RoleError("\n".join(lines))
    return kept, excluded


# ------------------------------------------------------------------ 원고 감지

_MANUSCRIPT_HEADINGS = [
    ("abstract", re.compile(r"^\s*(abstract|초\s*록)\s*$", re.I)),
    ("introduction", re.compile(r"^\s*(1\.?\s*)?(introduction|서\s*론)\s*$", re.I)),
    ("methods", re.compile(r"^\s*(2\.?\s*)?(materials?\s+and\s+methods?|methods?)\s*$", re.I)),
    ("results", re.compile(r"^\s*(3\.?\s*)?(results?|결\s*과)\s*$", re.I)),
    ("discussion", re.compile(r"^\s*(4\.?\s*)?(discussion|고\s*찰)\s*$", re.I)),
    ("conclusion", re.compile(r"^\s*(5\.?\s*)?(conclusions?|결\s*론)\s*$", re.I)),
    ("references", re.compile(r"^\s*(references?|참고\s*문헌)\s*$", re.I)),
    ("keywords", re.compile(r"^\s*(key\s*words?|keywords?|주제어)\s*[:：]?\s*$", re.I)),
]
MANUSCRIPT_MIN_HITS = 3
# 원고 표제어가 있어도 아래 '제출 패킷 표지'가 2개 이상이면 원고로 보지 않는다
# (영문 IRB 프로토콜에도 Introduction/Methods 가 흔하다).
_PACKET_MARKERS = [
    re.compile(r"inclusion\s+criteria", re.I), re.compile(r"exclusion\s+criteria", re.I),
    re.compile(r"선정\s*기준"), re.compile(r"제외\s*기준"),
    re.compile(r"informed\s+consent\s+form", re.I), re.compile(r"연구대상자\s*설명문"),
    re.compile(r"증례\s*기록"), re.compile(r"case\s+report\s+form", re.I),
    re.compile(r"연구계획서"), re.compile(r"version\s*no", re.I),
    re.compile(r"연구책임자"), re.compile(r"대상자\s*모집"),
]


def packet_marker_hits(doc):
    """IRB 제출 서류라는 표지가 몇 개인가."""
    text = doc.text[:60000]
    return sum(1 for pattern in _PACKET_MARKERS if pattern.search(text))


def manuscript_hits(doc):
    """원고 구조 표제어를 몇 개나 가졌는가 (짧은 단독 문단만 센다)."""
    hits = []
    for block in doc.blocks:
        if block.kind != "p" or len(block.text) > 30:
            continue
        for name, pattern in _MANUSCRIPT_HEADINGS:
            if name not in hits and pattern.match(block.text):
                hits.append(name)
    return hits


def detect_manuscript(documents):
    """논문 원고로 보이는 문서 목록 (강제 장치 1)."""
    flagged = []
    for doc in documents:
        ext = os.path.splitext(doc.path)[1].lower()
        if ext in (".tex", ".bib"):
            flagged.append((doc, ["LaTeX 원고 파일(%s)" % ext]))
            continue
        if not doc.read_ok:
            continue
        hits = manuscript_hits(doc)
        if len(hits) >= MANUSCRIPT_MIN_HITS and packet_marker_hits(doc) < 2:
            flagged.append((doc, hits))
    return flagged

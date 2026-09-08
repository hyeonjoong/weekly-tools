"""자료형 — 문서 · 추출값 · 문제 · 커버리지.

이 툴의 모든 판정은 이 네 가지 자료형으로만 표현됩니다. 특히 `Issue.severity`
는 아래 네 값 중 하나뿐이며 **규정 준수 여부나 심의 결과를 뜻하는 등급은 존재하지
않습니다** — 문서 사이가 다르다는 사실만 말하고, 어느 쪽을 채택할지는 말하지 않습니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

#: 심각도 — 닫힌 목록.
CRITICAL = "치명"
WARNING = "경고"
UNCOMPARABLE = "대조불가"
MATCH = "일치"
SEVERITIES = (CRITICAL, WARNING, UNCOMPARABLE, MATCH)

#: 문서 역할 — 닫힌 목록.
ROLE_PROTOCOL = "프로토콜"
ROLE_ICF = "동의서"
ROLE_ASSENT = "동의서(소아)"
ROLE_CRF = "CRF"
ROLE_AD = "모집공고"
ROLE_REG = "등록정보"
ROLES = (ROLE_PROTOCOL, ROLE_ICF, ROLE_ASSENT, ROLE_CRF, ROLE_AD, ROLE_REG)

#: 대조 항목 12종 — 닫힌 목록. 늘리지 않습니다 (기획서).
#: (키, 표시 이름, 불일치 시 심각도)
ITEMS: Tuple[Tuple[str, str, str], ...] = (
    ("title", "연구제목", CRITICAL),
    ("version", "문서 버전·날짜", CRITICAL),
    ("pi", "연구책임자·기관", CRITICAL),
    ("contact", "연락처", WARNING),
    ("n", "대상자 수", CRITICAL),
    ("age", "연령 범위", CRITICAL),
    ("visits", "방문·회기 횟수 / 참여 기간", CRITICAL),
    ("duration", "소요시간", CRITICAL),
    ("compensation", "보상", CRITICAL),
    ("retention", "개인정보 보관기간", CRITICAL),
    ("criteria", "선정·제외기준", WARNING),
    ("assessments", "평가·검사 항목", WARNING),
)
ITEM_KEYS = tuple(k for k, _, _ in ITEMS)
ITEM_NAMES: Dict[str, str] = {k: n for k, n, _ in ITEMS}
ITEM_SEVERITY: Dict[str, str] = {k: s for k, _, s in ITEMS}


@dataclass
class Para:
    """문서의 한 단위 — 본문 문단 하나 또는 표의 한 행."""
    idx: int                 #: 문서 내 순번 (0부터). 근거 위치로 인쇄됩니다.
    text: str                #: NFC 정규화된 본문. 표 행은 셀을 탭으로 이었습니다.
    section: str = ""        #: 소속 절 제목 (없으면 빈 문자열)
    table: int = 0           #: 표 번호 (1부터). 본문 문단은 0.
    row: int = 0             #: 표 안의 행 번호 (1부터)

    def where(self) -> str:
        loc = "문단 {}".format(self.idx)
        if self.table:
            loc = "표 {} 행 {} (문단 {})".format(self.table, self.row, self.idx)
        if self.section:
            return "§{}, {}".format(self.section, loc)
        return loc


@dataclass
class Doc:
    path: str
    name: str                              #: 새니타이즈된 표시용 파일명
    role: str = ""                         #: ROLES 중 하나 (미판별이면 빈 문자열)
    label: str = ""                        #: 표시용 역할 라벨 — 동의서(성인)·동의서(보호자)…
    role_reason: str = ""                  #: 판별 근거 (리포트에 인쇄)
    role_forced: bool = False              #: --role 로 지정됐는지
    paras: List[Para] = field(default_factory=list)
    readable: bool = True
    unread_reason: str = ""
    tracked_changes: bool = False          #: docx 에 w:ins/w:del 이 있었는지
    ext: str = ""

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.paras)

    def head(self, n: int = 1500) -> str:
        return self.text[:n]


@dataclass
class Extraction:
    item: str          #: ITEM_KEYS 중 하나
    sub: str           #: 하위 키 (예: version 항목의 "버전"/"날짜"). 없으면 ""
    doc: str           #: Doc.name
    label: str         #: Doc.label
    raw: str           #: 원문에서 잘라 온 값 (마스킹 전)
    norm: str          #: 정규화된 비교값
    para: int
    where: str         #: Para.where()
    sentence: str      #: 근거 문장 (마스킹 전)
    norm_rules: Tuple[str, ...] = ()   #: 이 값에 적용된 정규화 규칙 이름들
    source: str = ""   #: 원문에서 실제로 매치된 토큰 (raw 가 표시용으로 다듬어졌을 때 정규화 쌍 계수에 씀)


@dataclass
class Evidence:
    doc: str
    label: str
    value: str
    where: str
    sentence: str = ""
    differs: bool = False   #: 리포트에서 "← 다름" 표시


@dataclass
class Issue:
    severity: str
    item: str
    title: str
    evidence: List[Evidence] = field(default_factory=list)
    note: str = ""
    sub: str = ""   #: 하위키 (개정 축 이중 보고 억제에 씀)

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError("알 수 없는 심각도: {!r}".format(self.severity))


@dataclass
class Coverage:
    """[커버리지 자백] 블록의 재료. 리포트는 이것 없이 출력되지 않습니다."""
    n_docs: int = 0
    n_read: int = 0
    unread: List[Tuple[str, str]] = field(default_factory=list)          #: (문서, 사유)
    items_compared: List[str] = field(default_factory=list)              #: 2개 이상 문서에서 대조 성립
    items_uncomparable: List[Tuple[str, str]] = field(default_factory=list)  #: (항목, 사유) — 12항목 단위만
    sub_gaps: List[Tuple[str, str]] = field(default_factory=list)            #: (항목 — 하위, 사유) — 항목은 대조됐지만 하위키 하나가 빠진 경우
    norm_equal_pairs: int = 0                                            #: 정규화 덕에 같다고 본 쌍 수
    norm_rules_used: List[str] = field(default_factory=list)
    auto_roles: int = 0
    forced_roles: int = 0
    baseline_note: str = ""                                              #: --baseline 요약
    manual_only: List[str] = field(default_factory=list)                 #: 항목 이름 — 서술형이라 사람이 봐야 함


@dataclass
class Result:
    docs: List[Doc]
    extractions: List[Extraction]
    issues: List[Issue]
    coverage: Coverage
    exit_code: int = 0
    exit_reason: str = ""

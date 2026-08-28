"""판정 자료형 — 심각도는 **툴 자신의 방법론적 기준에만** 붙습니다.

치명이 되는 것은 넷뿐입니다(좁게 못 박습니다):
  1. 조건 간 음량 불일치 (> `--lufs-crit`, 기본 2.0 LU)
  2. 주장 불일치 (설계 JSON 의 claims 와 실측이 허용오차 밖)
  3. 클리핑 (연속 3샘플 이상 −0.1 dBFS 이상)
  4. 죽은 파일 (전 구간 무음 / 전 구간 DC)

논문 유래 수치(50 ms · 0.3 asper · 60–80 BPM · 1.5 acum)는 `refs.py` 의
`ReferenceValue` 로만 표현되며, 그 자료형에는 심각도 필드가 없습니다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

CRITICAL = "치명"
WARNING = "경고"
INFO = "정보"

#: 리포트에서 심각도를 표시하는 대괄호 표기 — 테스트가 이 목록으로 줄을 검사합니다.
SEVERITY_MARKS = ("[치명]", "[경고]")

#: 판정 유형 — CSV 의 `유형` 열에 그대로 들어갑니다.
KIND_LEVEL_MISMATCH = "음량 불일치"
KIND_LEVEL_SPREAD = "조건 내 음량 산포"
KIND_LEVEL_UNDECIDABLE = "음량 판정불가"
KIND_CLAIM_MISMATCH = "주장 불일치"
KIND_CLAIM_UNDECIDABLE = "주장 판정불가"
KIND_CLIPPING = "클리핑"
KIND_DEAD = "죽은 파일"
KIND_LR_IMBALANCE = "좌우 불균형"
KIND_DC_OFFSET = "DC 오프셋"
KIND_TRUE_PEAK = "트루피크 여유 부족"
KIND_FORMAT_MISMATCH = "포맷 불일치"
KIND_DURATION_MISMATCH = "길이 불일치"
KIND_EDGE_CLICK = "시작/끝 클릭 위험"
KIND_UNASSIGNED = "조건 미지정 파일"


@dataclass
class Finding:
    """판정 한 건."""

    severity: str
    kind: str
    subject: str        # 파일 이름 또는 "active ↔ control"
    detail: str         # 사람이 읽는 한 줄
    measured: str = ""
    reference: str = ""
    condition: str = ""
    consequence: str = ""   # 이 결함이 연구에 무엇을 뜻하는가
    action: str = ""        # 사운드 담당자에게 그대로 보낼 수 있는 한 줄

    @property
    def is_critical(self) -> bool:
        return self.severity == CRITICAL


def sort_key(f: Finding) -> tuple:
    order = {CRITICAL: 0, WARNING: 1, INFO: 2}
    return (order.get(f.severity, 3), f.kind, f.subject)


def count(findings: List[Finding], severity: str) -> int:
    return sum(1 for f in findings if f.severity == severity)


@dataclass
class Coverage:
    """커버리지 자백 — **이 블록 없이는 리포트를 출력하지 않습니다.**

    "치명 0건"이 정직한 문장이 되려면, 무엇을 못 읽었고 무엇을 안 봤는지가
    같은 화면에 있어야 합니다. `report.py` 가 코드로 강제합니다.
    """

    n_input: int = 0
    n_read: int = 0
    unreadable: List[tuple] = None          # [(파일이름, 사유)]
    #: 읽긴 읽었지만 깨끗하지 않은 경우 — 잘린 data 청크, ffmpeg 디코드 경유 등.
    read_notes: List[tuple] = None          # [(파일이름, 비고)]
    total_seconds: float = 0.0
    n_channels_total: int = 0
    axes_checked: List[str] = None
    axes_skipped: List[tuple] = None        # [(축 이름, 사유)]
    confound_note: str = ""
    design_note: str = ""
    elapsed_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.unreadable is None:
            self.unreadable = []
        if self.read_notes is None:
            self.read_notes = []
        if self.axes_checked is None:
            self.axes_checked = []
        if self.axes_skipped is None:
            self.axes_skipped = []

    @property
    def n_unreadable(self) -> int:
        return len(self.unreadable)

    @property
    def complete(self) -> bool:
        return self.n_unreadable == 0

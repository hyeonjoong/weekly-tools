"""버전 대조 — 이전 폴더와 짝지어 **무엇이 달라졌는지** 냅니다.

실제 쓰임: 사운드 담당자가 v1 을 보내고, 몇 주 뒤 "고쳤다"며 v2 를 보냅니다.
두 폴더를 번갈아 열어 Audacity 로 파형을 눈으로 보는 대신, 같은 이름끼리
짝지어 레벨·클리핑·길이·포맷이 어떻게 움직였는지 표로 봅니다.

짝짓기 규칙: 설계 JSON 의 `pairs` 가 있으면 그것을 쓰고, 없으면 **파일 이름이
같은 것끼리** 짝짓습니다. 이름이 바뀌었으면(v2 에 접두어가 붙는 등)
`pairs` 를 적어야 합니다 — 이름 유사도로 추측하지 않습니다. 추측한 짝짓기는
엉뚱한 두 파일을 "달라졌다"고 보고하는 가장 흔한 방법입니다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .analyze import FileMetrics


@dataclass
class BaselineRow:
    name: str
    baseline_name: str
    lufs_now: Optional[float]
    lufs_before: Optional[float]
    laeq_now: Optional[float]
    laeq_before: Optional[float]
    true_peak_now: Optional[float]
    true_peak_before: Optional[float]
    duration_now: float
    duration_before: float
    clip_now: int
    clip_before: int
    format_changed: bool

    @property
    def lufs_delta(self) -> Optional[float]:
        if self.lufs_now is None or self.lufs_before is None:
            return None
        return self.lufs_now - self.lufs_before

    def summary(self) -> str:
        bits: List[str] = []
        d = self.lufs_delta
        if d is not None and abs(d) >= 0.1:
            bits.append("음량 {:+.1f} LU".format(d))
        if self.clip_now != self.clip_before:
            bits.append("클리핑 {} → {}곳".format(self.clip_before, self.clip_now))
        if abs(self.duration_now - self.duration_before) > 0.05:
            bits.append("길이 {:.1f} → {:.1f}초".format(self.duration_before, self.duration_now))
        if (self.true_peak_now is not None and self.true_peak_before is not None
                and abs(self.true_peak_now - self.true_peak_before) >= 0.1):
            bits.append("트루피크 {:+.1f} dB".format(self.true_peak_now - self.true_peak_before))
        if self.format_changed:
            bits.append("포맷 변경")
        return " · ".join(bits) if bits else "변화 없음"


def compare(current: Dict[str, FileMetrics], baseline: Dict[str, FileMetrics],
            pairs: Optional[Dict[str, str]] = None
            ) -> Tuple[List[BaselineRow], List[str], List[str]]:
    """반환 = (짝지어진 행, 짝을 못 찾은 현재 파일, 안 쓰인 기준 파일)."""
    pairs = pairs or {}
    rows: List[BaselineRow] = []
    used = set()
    unmatched: List[str] = []
    for name in sorted(current):
        target = pairs.get(name, name)
        b = baseline.get(target)
        if b is None:
            unmatched.append(name)
            continue
        used.add(target)
        a = current[name]
        rows.append(BaselineRow(
            name=name, baseline_name=target,
            lufs_now=a.lufs_i, lufs_before=b.lufs_i,
            laeq_now=a.laeq_dbfs, laeq_before=b.laeq_dbfs,
            true_peak_now=a.true_peak_dbfs, true_peak_before=b.true_peak_dbfs,
            duration_now=a.duration_s, duration_before=b.duration_s,
            clip_now=a.clip_run_count, clip_before=b.clip_run_count,
            format_changed=a.info.format_key != b.info.format_key))
    leftover = sorted(set(baseline) - used)
    return rows, unmatched, leftover

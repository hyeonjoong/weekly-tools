"""주장 대조 — 설계서가 **주장한 값이 실제 신호에 있는지** 확인합니다.

이 툴이 하는 두 질문 중 두 번째입니다. 첫 번째는 "이 소리들이 서로 비교 가능한
세트인가"(음량·위생), 두 번째가 "주장한 대로 만들어졌는가"입니다.

원칙
----
* **파일 이름에서 추측하지 않습니다.** `bi_(360-400Hz).wav` 라는 이름을 보고
  40 Hz 맥놀이를 기대하지 않습니다. 설계 JSON 에 적힌 것만 검사합니다.
  추측한 기대값으로 판정하면, 이름을 잘못 붙인 파일이 이름대로 통과합니다.
* **못 재는 것은 못 잰다고 합니다.** 파일이 짧아 주기가 3번도 안 들어가면
  값을 억지로 내놓지 않고 `판정불가` 로 둡니다.
* 지원 항목은 `carrier_hz`, `beat_hz`, `mod_hz`, `duration_s` 넷뿐입니다.
  러프니스·샤프니스 같은 심리음향량은 이 툴이 재지 않습니다(DEBUSSY 소관).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .analyze import ENVELOPE_BAND_HI_HZ, FileMetrics

#: 스펙트럼 피크를 '반송음'으로 인정하는 최소 두드러짐(dB) — **국소 이웃 대비**
#: (`analyze._local_prominence_db`, 피크 ±1 옥타브의 중앙값 기준).
#: 실측 분포로 정했습니다: 잡음형 자극은 S3_pink 3.4 · S1_SO-CLAS 5.3 ·
#: 예제 핑크 2.7~6.0 dB, 음높이가 있는 자극은 S6_breath-pacing 94 ·
#: 싱잉볼 양이 자극 112~119 · 드론 100 dB, 실제 음악 트랙(uplift_01)이 17.4 dB.
#: 12 dB 는 그 사이의 빈 구간입니다 — 잡음 대조군에 **가짜 반송주파수**가
#: 붙는 것을 막는 것이 이 문턱의 존재 이유입니다(라운드 1 발견 2).
MIN_CARRIER_PROMINENCE_DB = 12.0
#: 채널별 반송음이 "같은 반송음의 좌우 쌍"이라고 볼 수 있는 최대 상대 벌어짐.
#: 양이 맥놀이 자극(예: L 360 / R 400)은 평균 380 Hz 를 반송주파수로 봅니다.
MAX_CARRIER_SPREAD = 0.15
#: 포락선 변조를 '주기적'이라 인정하는 최소 상대 강도(피크 파워 / 대역 파워).
#: 실측 기준: 잡음 구동 자극(S1_SO-CLAS 0.023 · S3_pink 0.038)은 아래,
#: 진짜 주기 변조(S2_spindle-target 0.23 · S6_breath-pacing 0.65)는 위입니다.
MIN_MOD_RATIO = 0.05
#: mod_hz 를 재려면 파일 안에 주기가 최소 몇 번 들어가야 하는가.
MIN_MOD_CYCLES = 3.0


@dataclass
class ClaimResult:
    """주장 한 건의 대조 결과."""

    file: str
    key: str
    claimed: float
    measured: Optional[float]
    unit: str
    tolerance: float
    verdict: str          # '일치' | '불일치' | '판정불가'
    note: str = ""

    @property
    def is_mismatch(self) -> bool:
        return self.verdict == "불일치"

    @property
    def is_undecidable(self) -> bool:
        return self.verdict == "판정불가"

    def measured_text(self) -> str:
        if self.measured is None:
            return "—"
        return "{:.4g} {}".format(self.measured, self.unit)

    def claimed_text(self) -> str:
        return "{:.4g} {}".format(self.claimed, self.unit)


def _tolerance(key: str, claimed: float, m: FileMetrics) -> float:
    """항목별 허용오차. 규칙은 좁게 잡습니다 — 애매하면 불일치로 몰지 않습니다."""
    if key == "carrier_hz":
        return max(1.0, 0.02 * claimed)
    if key == "beat_hz":
        return max(0.5, 0.05 * claimed)
    if key == "mod_hz":
        dur = m.duration_s or 1.0
        # 포락선 FFT 의 주파수 분해능(≈ 2/T)보다 좁은 허용오차는 거짓 정밀도입니다.
        return max(0.10 * claimed, 2.0 / dur)
    if key == "duration_s":
        return max(0.05, 0.005 * claimed)
    raise ValueError("모르는 주장 항목: {}".format(key))


def _carrier(m: FileMetrics) -> List[Optional[float]]:
    """두드러짐이 충분한 채널의 스펙트럼 피크만 돌려줍니다."""
    out: List[Optional[float]] = []
    for i, hz in enumerate(m.spectral_peak_hz):
        prom = m.spectral_peak_prominence_db[i] if i < len(m.spectral_peak_prominence_db) else None
        if hz is None or prom is None or prom < MIN_CARRIER_PROMINENCE_DB:
            out.append(None)
        else:
            out.append(hz)
    return out


def check_file(m: FileMetrics, spec: Dict[str, float]) -> List[ClaimResult]:
    """파일 하나의 주장들을 대조합니다."""
    results: List[ClaimResult] = []
    for key in sorted(spec):
        claimed = spec[key]
        tol = _tolerance(key, claimed, m)
        measured: Optional[float] = None
        note = ""
        verdict = "판정불가"
        unit = {"carrier_hz": "Hz", "beat_hz": "Hz", "mod_hz": "Hz", "duration_s": "s"}[key]

        if key == "duration_s":
            measured = m.duration_s
        elif key == "carrier_hz":
            peaks = [p for p in _carrier(m) if p is not None]
            if len(peaks) > 1:
                note = "채널별 피크: " + " / ".join("{:.1f} Hz".format(p) for p in peaks)
            if not peaks:
                note = ("뚜렷한 반송음 없음 (스펙트럼 피크 두드러짐 < {:.0f} dB) — "
                        "잡음성 자극에는 반송주파수 개념이 성립하지 않습니다"
                        .format(MIN_CARRIER_PROMINENCE_DB))
            elif len(peaks) == 1:
                measured = peaks[0]
            else:
                # **주장값에 가장 가까운 채널을 고르면 안 됩니다.** 그러면 측정값이
                # 질문에 따라 달라져, 같은 파일에 대한 모순된 두 주장이 둘 다
                # "일치"로 통과합니다(L 440 / R 1000 에 대해 440 주장도 1000 주장도
                # 통과했습니다 — 라운드 1 검토에서 발견). 이 툴이 잡아야 할
                # 확증편향을 이 툴이 저지르는 셈입니다.
                lo, hi = min(peaks), max(peaks)
                mid = sum(peaks) / len(peaks)
                if mid > 0 and (hi - lo) / mid <= MAX_CARRIER_SPREAD:
                    measured = mid          # 양이 맥놀이 쌍 → 평균이 반송주파수
                    note += " → 반송주파수 {:.1f} Hz (채널 평균)".format(mid)
                else:
                    note += (" — 채널별 반송음이 서로 너무 멀어 하나의 반송주파수로 "
                             "볼 수 없습니다. 설계에서 채널을 나누거나 beat_hz 로 "
                             "검사하십시오.")
        elif key == "beat_hz":
            if m.info.n_channels != 2:
                note = "맥놀이(beat)는 좌우 채널이 있어야 잽니다 — 이 파일은 {}채널".format(
                    m.info.n_channels)
            else:
                peaks = _carrier(m)
                if peaks[0] is None or peaks[1] is None:
                    note = "좌우 어느 한쪽에 뚜렷한 반송음이 없어 맥놀이를 정의할 수 없습니다"
                else:
                    measured = abs(peaks[0] - peaks[1])
                    note = "L {:.1f} Hz / R {:.1f} Hz".format(peaks[0], peaks[1])
        elif key == "mod_hz":
            cycles = claimed * (m.duration_s or 0.0)
            if claimed > ENVELOPE_BAND_HI_HZ:
                # 포락선은 10 ms 프레임(100 Hz)에서 뽑으므로 20 Hz 위는 신뢰할 수
                # 없습니다. 가드가 없으면 40 Hz AM 주장에 "실측 19.999 Hz" 라는
                # **치명 주장 불일치**가 붙었습니다(라운드 1 검토). Arnal 2015 의
                # 30–150 Hz AM 대역이 필요하면 DEBUSSY 의 modulation_peak_hz 를
                # --manifest 로 받아 쓰십시오.
                note = ("주장 변조율 {:.4g} Hz 는 포락선 분석 대역(≤ {:.0f} Hz) 밖입니다 — "
                        "값을 내놓지 않습니다. 더 빠른 변조는 DEBUSSY 소관입니다."
                        .format(claimed, ENVELOPE_BAND_HI_HZ))
            elif cycles < MIN_MOD_CYCLES:
                note = ("파일 길이 {:.1f}초에 주장 주기가 {:.1f}번밖에 들어가지 않습니다 "
                        "(최소 {:.0f}번 필요) — 값을 내놓지 않습니다".format(
                            m.duration_s, cycles, MIN_MOD_CYCLES))
            elif m.env_mod_hz is None:
                depth = m.env_mod_depth
                if depth is not None:
                    note = ("포락선이 사실상 평평합니다 (변조 깊이 {:.3f} %) — "
                            "이 파일에는 진폭변조가 없습니다".format(depth * 100.0))
                else:
                    note = "포락선에서 주기적 변조를 찾지 못했습니다"
            elif (m.env_mod_ratio or 0.0) < MIN_MOD_RATIO:
                note = ("포락선 변조가 뚜렷하지 않습니다 (상대강도 {:.3f} < {:.2f}) — "
                        "잡음의 요동이지 주기적 변조가 아닙니다".format(
                            m.env_mod_ratio or 0.0, MIN_MOD_RATIO))
            elif m.env_mod_hz * (m.duration_s or 0.0) < MIN_MOD_CYCLES:
                # 파일 양끝의 페이드인/아웃 자체가 '파일 길이 한 주기짜리 변조'로 보입니다.
                # 실측값이 3주기 미만이면 그것이 페이드 모양인지 진짜 변조인지 구분할
                # 방법이 없으므로 값을 내놓지 않습니다.
                note = ("실측 지배 변조율 {:.3f} Hz 는 파일 {:.1f}초에 {:.1f}주기밖에 "
                        "들어가지 않아 분해되지 않습니다 (페이드인/아웃 모양일 수 있습니다)"
                        .format(m.env_mod_hz, m.duration_s,
                                m.env_mod_hz * (m.duration_s or 0.0)))
            else:
                measured = m.env_mod_hz
                note = "포락선 변조 상대강도 {:.2f}".format(m.env_mod_ratio or 0.0)

        if measured is not None:
            verdict = "일치" if abs(measured - claimed) <= tol else "불일치"
        results.append(ClaimResult(file=m.name, key=key, claimed=claimed, measured=measured,
                                   unit=unit, tolerance=tol, verdict=verdict, note=note))
    return results


def check_all(metrics: Dict[str, FileMetrics], claims: Dict[str, Dict[str, float]]) -> List[ClaimResult]:
    """설계 JSON 의 모든 주장을 대조합니다(파일 이름 기준)."""
    out: List[ClaimResult] = []
    for fname in sorted(claims):
        m = metrics.get(fname)
        if m is None:
            continue
        out.extend(check_file(m, claims[fname]))
    return out

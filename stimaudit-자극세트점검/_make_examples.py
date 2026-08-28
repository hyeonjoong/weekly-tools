#!/usr/bin/env python3
"""`examples/` 의 합성 예제 세트를 만듭니다 (결정론적 — 매번 같은 바이트).

**실제 회사 자산은 절대 커밋하지 않습니다** — 용량과 권리 양쪽 문제입니다.
여기서 만드는 소리는 전부 이 스크립트가 계산으로 지어낸 것이고, 어떤 실험에서도
쓰인 적이 없습니다.

세 세트
-------
* `맞은세트/`     — 음량이 0.2 LU 안에 맞고 결함이 없는 세트 (종료코드 0)
* `어긋난세트/`   — 음량 3 LU 차 · 클리핑 · DC 0.05 · 1 ms 시작 클릭 ·
                    좌우 2 dB 차 · 주장과 다른 반송주파수 (종료코드 1)
* `판정불가세트/` — 읽을 수 없는 파일이 섞인 세트 + 24bit/48k 파일 (종료코드 3)

실행:  python3 _make_examples.py
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stimaudit import analyze, wavread  # noqa: E402

FS = 44100
DUR = 3.0
HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(HERE, "examples")


class LCG:
    """이식 가능한 결정론적 난수 — `random` 의 구현이 바뀌어도 바이트가 같습니다."""

    def __init__(self, seed: int = 20260828) -> None:
        self.s = seed & 0xFFFFFFFF

    def uniform(self) -> float:
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return (self.s / 0x3FFFFFFF) - 1.0


def pink(n: int, seed: int) -> list:
    """Voss–McCartney 근사(Paul Kellet 필터)로 만든 1/f 잡음."""
    rng = LCG(seed)
    b = [0.0] * 7
    out = []
    for _ in range(n):
        w = rng.uniform()
        b[0] = 0.99886 * b[0] + w * 0.0555179
        b[1] = 0.99332 * b[1] + w * 0.0750759
        b[2] = 0.96900 * b[2] + w * 0.1538520
        b[3] = 0.86650 * b[3] + w * 0.3104856
        b[4] = 0.55000 * b[4] + w * 0.5329522
        b[5] = -0.7616 * b[5] - w * 0.0168980
        out.append((b[0] + b[1] + b[2] + b[3] + b[4] + b[5] + b[6] + w * 0.5362) * 0.11)
        b[6] = w * 0.115926
    # 평균 제거 — 이 필터는 유한 구간에서 DC 가 남고, 그러면 깨끗해야 할 예제가
    # "DC 오프셋" 경고를 뱉습니다(실제로 첫 생성본이 −40.9 dBFS 로 걸렸습니다).
    return dc_free(out)


def tone(n: int, fs: int, freq: float, amp: float = 0.3) -> list:
    return [amp * math.sin(2.0 * math.pi * freq * i / fs) for i in range(n)]


def dc_free(x: list) -> list:
    """평균을 뺍니다. **페이드 뒤에** 불러야 합니다 — 페이드 창이 양끝을 비대칭으로
    깎으면서 DC 를 다시 만들기 때문입니다(첫 생성본이 −57 dBFS 로 걸렸습니다)."""
    mean = sum(x) / len(x)
    return [v - mean for v in x]


def fade(x: list, fs: int, ms: float = 200.0) -> list:
    """양끝에 코사인 페이드 — 시작/끝 클릭을 없앱니다."""
    k = max(1, int(fs * ms / 1000.0))
    k = min(k, len(x) // 2)
    y = list(x)
    for i in range(k):
        w = 0.5 - 0.5 * math.cos(math.pi * i / k)
        y[i] *= w
        y[-1 - i] *= w
    return y


def am(x: list, fs: int, rate_hz: float, depth: float = 0.9) -> list:
    """포락선 진폭변조 — SO 자극(0.8 Hz)이나 호흡 페이싱을 흉내 냅니다."""
    return [v * (1.0 - depth * 0.5 * (1.0 - math.cos(2.0 * math.pi * rate_hz * i / fs)))
            for i, v in enumerate(x)]


def _lufs(path: str):
    return analyze.analyze_file(wavread.probe(path)).lufs_i


def write_at_lufs(path: str, channels, fs: int, target: float, bits: int = 16) -> float:
    """목표 LUFS 에 맞춰 게인을 잡아 씁니다(2회 반복이면 0.01 LU 안에 들어옵니다)."""
    cur = [list(c) for c in channels]
    got = None
    for _ in range(3):
        wavread.write_wav(path, cur, fs, bits)
        got = _lufs(path)
        if got is None:
            return float("nan")
        if abs(got - target) < 0.01:
            break
        g = 10.0 ** ((target - got) / 20.0)
        cur = [[v * g for v in c] for c in cur]
    return got


def ensure(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def make_matched() -> None:
    """음량이 맞고 결함이 없는 세트 — 오탐 억제의 기준선입니다."""
    d = ensure(os.path.join(EX, "맞은세트"))
    n = int(FS * DUR)
    target = -23.0
    # 이 세트는 **전부 스테레오**입니다. 모노와 스테레오를 섞으면 "포맷 불일치"
    # 경고가 정당하게 뜨는데, 깨끗한 세트의 기준선은 0건이어야 하기 때문입니다.
    drone = dc_free(fade(tone(n, FS, 200.0, 0.25), FS))
    write_at_lufs(os.path.join(d, "A_active_drone.wav"), [drone, list(drone)], FS, target)
    pulse = dc_free(fade(am(pink(n, 11), FS, 1.2), FS))
    write_at_lufs(os.path.join(d, "A_active_so-pulse.wav"), [pulse, list(pulse)], FS, target)
    pk = dc_free(fade(pink(n, 22), FS))
    write_at_lufs(os.path.join(d, "A_control_pink.wav"), [pk, list(pk)], FS, target)
    write_at_lufs(os.path.join(d, "A_binaural_360-400.wav"),
                  [dc_free(fade(tone(n, FS, 360.0, 0.25), FS)),
                   dc_free(fade(tone(n, FS, 400.0, 0.25), FS))], FS, target)
    with open(os.path.join(d, "설계.json"), "w", encoding="utf-8") as fh:
        fh.write("""{
  "study": "예제 — 맞은 세트",
  "conditions": {
    "active":  ["A_active_drone.wav", "A_active_so-pulse.wav"],
    "control": ["A_control_pink.wav"],
    "binaural": ["A_binaural_360-400.wav"]
  },
  "contrast": "modulation_peak_hz",
  "notes": "양이 맥놀이 파일의 carrier_hz 는 좌우 평균 (360+400)/2 = 380 Hz 입니다 — 한쪽 채널 값이 아닙니다.",
  "claims": {
    "A_active_drone.wav":       { "carrier_hz": 200.0, "duration_s": 3.0 },
    "A_active_so-pulse.wav":    { "mod_hz": 1.2, "duration_s": 3.0 },
    "A_binaural_360-400.wav":   { "carrier_hz": 380.0, "beat_hz": 40.0, "duration_s": 3.0 },
    "A_control_pink.wav":       { "duration_s": 3.0 }
  }
}
""")


def make_mismatched() -> None:
    """일부러 어긋뜨린 세트 — 각 결함이 정확히 그 항목으로 잡혀야 합니다."""
    d = ensure(os.path.join(EX, "어긋난세트"))
    n = int(FS * DUR)
    base = -23.0

    # ① 대조군보다 3 LU 크고 ② 시작이 1 ms 안에 튀어 오릅니다(클릭).
    loud = dc_free(fade(tone(n, FS, 200.0, 0.25), FS, 200.0))
    k = int(FS * 0.001)
    for i in range(k):                      # 앞 200 ms 페이드를 1 ms 램프로 덮어씀
        loud[i] = tone(n, FS, 200.0, 0.25)[i] * (i / k)
    for i in range(k, int(FS * 0.2)):
        loud[i] = tone(n, FS, 200.0, 0.25)[i]
    write_at_lufs(os.path.join(d, "B_active_loud.wav"), [loud], FS, base + 3.0)

    write_at_lufs(os.path.join(d, "B_control_pink.wav"), [dc_free(fade(pink(n, 22), FS))], FS, base)

    # ③ 클리핑 주입 + ④ DC 오프셋 0.05
    # 진폭을 0.28 로 두어 전체 레벨은 다른 파일과 비슷하게 유지하고, 짧은 구간만
    # 만점을 넘겨 잘리게 합니다 — 결함이 '레벨'이 아니라 '클리핑'으로 잡혀야 하므로.
    x = fade(tone(n, FS, 250.0, 0.28), FS)
    for i in range(int(FS * 0.5), int(FS * 0.5) + 400):
        x[i] = 1.2 if x[i] >= 0 else -1.2   # write_wav 가 만점으로 잘라 클리핑을 만듭니다
    x = [v + 0.05 for v in x]
    wavread.write_wav(os.path.join(d, "B_clipped_dc.wav"), [x], FS, 16)

    # ⑤ 좌우 2 dB 차 + ⑥ 주장(360/40 Hz)과 다른 반송주파수(300/320 Hz)
    left = dc_free(fade(tone(n, FS, 300.0, 0.25), FS))
    right = dc_free(fade(tone(n, FS, 320.0, 0.25 * 10 ** (-2.0 / 20.0)), FS))
    wavread.write_wav(os.path.join(d, "B_binaural_wrong.wav"), [left, right], FS, 16)

    with open(os.path.join(d, "설계.json"), "w", encoding="utf-8") as fh:
        fh.write("""{
  "study": "예제 — 어긋난 세트",
  "conditions": {
    "active":  ["B_active_loud.wav", "B_clipped_dc.wav"],
    "control": ["B_control_pink.wav"],
    "binaural": ["B_binaural_wrong.wav"]
  },
  "claims": {
    "B_binaural_wrong.wav": { "carrier_hz": 360.0, "beat_hz": 40.0, "duration_s": 3.0 },
    "B_active_loud.wav":    { "carrier_hz": 200.0, "duration_s": 3.0 }
  },
  "pairs": {
    "B_active_loud.wav":     "A_active_drone.wav",
    "B_control_pink.wav":    "A_control_pink.wav",
    "B_binaural_wrong.wav":  "A_binaural_360-400.wav"
  }
}
""")


def make_undecidable() -> None:
    """읽을 수 없는 파일이 섞인 세트 — 종료코드 3 이 1보다 우선함을 보여줍니다."""
    d = ensure(os.path.join(EX, "판정불가세트"))
    n = int(FS * DUR)
    write_at_lufs(os.path.join(d, "C_ok_pink.wav"), [dc_free(fade(pink(n, 33), FS))], FS, -23.0)
    n48 = int(48000 * DUR)
    t48 = dc_free(fade(tone(n48, 48000, 220.0, 0.25), 48000))
    write_at_lufs(os.path.join(d, "C_ok_24bit_48k.wav"), [t48, list(t48)],
                  48000, -23.0, bits=24)
    # 헤더만 그럴듯하고 내용이 없는 파일 — "못 읽음"으로 세어야 합니다.
    with open(os.path.join(d, "C_broken.wav"), "wb") as fh:
        fh.write(b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt "
                 + (16).to_bytes(4, "little") + b"\x63\x00" + (2).to_bytes(2, "little")
                 + (44100).to_bytes(4, "little") + (176400).to_bytes(4, "little")
                 + (4).to_bytes(2, "little") + (16).to_bytes(2, "little")
                 + b"data" + (0).to_bytes(4, "little"))


def make_manifest() -> None:
    """DEBUSSY 스타일 지표 CSV 예제 — 지표를 받아 쓰는 경로를 보여줍니다.

    값은 **DEBUSSY 가 뽑았다고 가정한 가짜 숫자**입니다. stimaudit 은 이 값을
    계산하지 않으며, 받아서 조건 간 차이를 보여줄 뿐입니다.
    """
    path = os.path.join(EX, "맞은세트", "DEBUSSY지표_예시.csv")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("file,roughness_asper,sharpness_acum,spectral_centroid_hz,"
                 "modulation_peak_hz,spectral_slope_beta,hnr_db\n")
        fh.write("A_active_drone.wav,0.05,0.62,201.4,0.0,-6.1,28.4\n")
        fh.write("A_active_so-pulse.wav,0.07,0.71,940.2,1.2,-5.8,3.1\n")
        fh.write("A_control_pink.wav,0.06,0.68,930.7,0.0,-5.9,2.8\n")
        fh.write("A_binaural_360-400.wav,0.04,0.65,380.1,0.0,-6.3,27.9\n")


def main() -> int:
    ensure(EX)
    make_matched()
    make_mismatched()
    make_undecidable()
    make_manifest()
    total = 0
    for root, _dirs, files in os.walk(EX):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
    print("examples/ 생성 완료 — {:.1f} MB".format(total / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())

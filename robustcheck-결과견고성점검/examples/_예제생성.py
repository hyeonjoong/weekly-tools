"""예제 CSV 3종을 만든다 — **전부 합성 데이터**다.

실제 환자 자료는 한 줄도 들어 있지 않다. BELL-001 / SERENE 의 변수 이름과
현실적인 범위만 흉내 낸 난수이며, 시드를 고정해 언제 돌려도 같은 파일이
나온다(재현성). 이 스크립트는 저장소에 예제를 만들어 둔 기록으로 남긴다.

    python3 examples/_예제생성.py
"""

import csv
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))

HEADER = ["subject_id", "arm", "isi_baseline", "isi_week4", "rmssd_ms",
          "sleep_eff_pct"]


def _round(value, digits=1):
    return round(value, digits)


def make_robust(path):
    """효과가 명백한 자료 — 이 툴이 '견고' 를 뱉어야 하는 기준점.

    g ≈ 1.2, N = 40. 여기서도 '취약' 이 나오면 판정 기준이 너무 예민한 것이다.
    """
    rng = random.Random(20260821)
    rows = []
    for i in range(1, 41):
        active = i % 2 == 1
        sid = "S%03d" % i
        base = rng.gauss(19.5, 2.6)
        drop = rng.gauss(8.2, 3.2) if active else rng.gauss(2.8, 3.2)
        week4 = max(0.0, base - drop)
        rmssd = rng.gauss(34.0 if active else 27.5, 6.0)
        eff = rng.gauss(85.0 if active else 79.0, 4.0)
        rows.append([sid, "active" if active else "sham",
                     _round(base), _round(week4), _round(rmssd), _round(eff)])
    _write(path, rows)


def make_fragile(path):
    """p 가 .05 언저리이고 한두 명이 결론을 흔드는 자료 — 이 툴이 존재하는 이유.

    34명, 기준선 Welch p ≈ .027. 극단값 세 명(S007·S017·S018)이 결론을
    떠받치고, 그중 S007·S017 은 **한 명만 빼도** 결론이 무너진다.
    """
    rng = random.Random(131)
    rows = []
    for i in range(1, 35):
        active = i % 2 == 1
        sid = "S%03d" % i
        base = rng.gauss(19.0, 2.8)
        # 군 효과는 넣지 않는다 — 아래 세 명이 결론을 통째로 떠받친다.
        drop = rng.gauss(3.4, 2.6)
        week4 = max(0.0, base - drop)
        rmssd = rng.gauss(30.0 if active else 28.0, 7.5)
        eff = rng.gauss(82.0 if active else 80.0, 5.0)
        rows.append([sid, "active" if active else "sham",
                     _round(base), _round(week4), _round(rmssd), _round(eff)])
    by_id = {r[0]: r for r in rows}
    by_id["S007"][3] = 1.5     # active, 극단적으로 좋아진 사람
    by_id["S017"][3] = 1.5     # active, 마찬가지
    by_id["S018"][3] = 26.0    # sham, 극단적으로 나빠진 사람
    # 결측도 현실처럼 조금 넣는다(3주차에 여행 갔던 그 피험자).
    by_id["S024"][3] = ""
    by_id["S030"][4] = ""
    _write(path, rows)


def make_undecidable(path):
    """유효 N 이 6 미만 — 견고성을 논할 수 없다(종료코드 3)."""
    rows = [
        ["S001", "active", 18, 11, 42.3, 84.0],
        ["S002", "active", 21, 9, 38.1, 86.2],
        ["S003", "sham", 19, 18, 29.7, 78.4],
        ["S004", "sham", 17, "", 31.2, 80.1],
        ["S005", "active", 22, 14, "", 82.5],
    ]
    _write(path, rows)


def make_merged(path):
    """`joinaudit` 의 merged.csv 스키마 그대로(피험자당 1행, UTF-8 BOM)."""
    header = ["subject_id", "timepoint", "isi_visit", "isi_isi_total",
              "isi_arm", "hrv_rmssd_ms"]
    rng = random.Random(777)
    rows = []
    for i in range(1, 25):
        active = i % 2 == 1
        rows.append([
            "S%02d" % i, "week4", "W4",
            _round(max(0.0, rng.gauss(9.0 if active else 15.0, 4.0)), 0),
            "치료" if active else "대조",
            _round(rng.gauss(33.0 if active else 27.0, 7.0)),
        ])
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _write(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows)


def main():
    make_robust(os.path.join(HERE, "견고_예제.csv"))
    make_fragile(os.path.join(HERE, "취약_예제.csv"))
    make_undecidable(os.path.join(HERE, "판정불가_예제.csv"))
    make_merged(os.path.join(HERE, "joinaudit_merged.csv"))
    print("예제 4종을 만들었습니다:", HERE)


if __name__ == "__main__":
    main()

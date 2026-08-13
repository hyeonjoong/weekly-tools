#!/usr/bin/env python3
"""[개발 전용] examples/serene_style.docx 를 다시 만든다.

BELL-001 SERENE 을 **흉내 낸 완전 합성 문서**다 — 실제 환자 데이터도, 실제
연구 결과도 아니다. .docx 경로(표 셀 안 숫자, 추적 변경 제외, GRIM)를 실제
Word 파일에서 확인하기 위한 예제다.

    python3 dev/make_serene_docx.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from docx_fixture import build_docx, deleted_paragraph  # noqa: E402

OUT = ROOT / "examples" / "serene_style.docx"

BLOCKS = [
    "비접촉 호흡 유도 자극의 서파수면 증진 효과: SERENE 예비분석 "
    "(합성 예제 — 실제 데이터 아님)",
    "Abstract",
    "Methods: 만성 불면증 성인 46명을 능동자극군과 대조군에 1:1로 배정하였다.",
    "Results: 4주 후 반응자는 23/46 (50.0%) 이었고, 능동자극군의 ISI 평균은 "
    "14.37 (N = 23) 이었다.",
    # 추적 변경으로 지운 문장 — 여기 숫자는 최종본에 없으므로 검사되면 안 된다.
    deleted_paragraph("Results: 4주 후 반응자는 99/46 (250.0%) 이었다."),
    "Methods",
    "1차 지표는 ISI, 2차 지표는 PSQI 와 ESS 였다. 야간 호흡수와 HRV(RMSSD)는 "
    "비접촉 레이더와 손목 웨어러블로 각각 수집하였다.",
    "Results",
    "총 46명 (능동자극 23, 대조 23) 이 최종 분석에 포함되었다.",
    "능동자극군의 ISI 평균은 18.4 → 11.2 로 낮아졌다 (변화 -7.2).",
    "군간 ISI 변화의 차이는 -3.5 (95% CI -5.9 to -1.1) 로 유의하였다, "
    "t(44) = 3.05, p = .004.",
    "야간 평균 호흡수는 능동자극군에서 14.8 회/분, 대조군에서 15.6 회/분이었다, "
    "t(44) = 2.11, p = .041.",
    "RMSSD 는 능동자극군에서 유의하게 증가하였다, t(44) = 2.62, p = .012.",
    "표 1. 4주 시점 결과 (숫자는 표 셀 안에 있으며, numcheck 는 셀까지 읽는다)",
    [
        ["지표", "전체 (N = 46)", "능동자극 (n = 23)", "대조 (n = 23)"],
        ["ISI 평균", "12.8", "14.37 (N = 23)", "11.2"],
        ["반응자", "23/46 (50.0%)", "14/23 (60.9%)", "9/23 (39.1%)"],
        ["RMSSD (ms)", "34.2", "37.1", "31.3"],
    ],
    "Discussion",
    "느린 호흡 유도가 부교감 활성을 통해 서파수면을 늘린다는 가설과 일치한다.",
    "References",
    "1. Hong GD, Kim SM. Slow breathing and sleep. J Sleep Res. 2019;28(3):112-120.",
]


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    build_docx(OUT, BLOCKS)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

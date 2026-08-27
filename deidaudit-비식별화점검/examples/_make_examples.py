"""번들 예제를 다시 만드는 스크립트 (합성 데이터 전용).

    python3 examples/_make_examples.py

**여기에 실제 환자·실제 원고 자료를 넣지 마세요.** 전부 합성입니다.
예제의 주민등록번호는 성별자리 3(2000년대 출생) + 생년 88 → **2088년 출생**
으로 만들어, 체크섬은 통과하지만 **현실에서 발급될 수 없는 번호**입니다.
"""

from __future__ import annotations

import csv
import datetime as _dt
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from tests.xlsx_builder import Sheet, build_xlsx  # noqa: E402

SUBJECTS = [
    ("BELL-001-003", "김현중", "010-2345-6789", "1988-04-02", "M"),
    ("BELL-001-007", "이서연", "010-9876-5432", "1991-11-27", "F"),
    ("BELL-001-012", "박준호", "010-3141-5926", "1979-06-15", "M"),
    ("BELL-001-015", "최민아", "010-2718-2818", "1985-02-09", "F"),
    ("BELL-001-021", "정우성", "010-1618-0339", "1972-09-30", "M"),
    ("BELL-001-024", "강수진", "010-1414-2135", "1994-12-01", "F"),
    ("BELL-001-028", "윤태식", "010-1732-0508", "1968-07-22", "M"),
    ("BELL-001-033", "임하늘", "010-2236-0679", "1990-03-18", "F"),
]

DIARY_NOTES = [
    "특이사항 없음",
    "새벽에 깨서 ○○○ 간호사한테 얘기함",
    "",
    "잠들기까지 오래 걸림",
    "중간에 두 번 깼다",
    "특이사항 없음",
    "낮잠 30분 잤음",
    "기기 알림음 때문에 깼다고 함",
    "",
    "컨디션 좋음",
    "이서연 씨가 대신 기록해 줌",
    "특이사항 없음",
    "밤새 뒤척임",
    "",
    "약 복용 없음",
    "특이사항 없음",
    "주말이라 늦게 잠",
    "새벽 3시에 화장실",
    "",
    "특이사항 없음",
    "코골이 심했다고 배우자가 말함",
    "특이사항 없음",
    "출장으로 호텔에서 잠",
    "특이사항 없음",
]


def write_diary(path: Path) -> None:
    """지저분한 수면일기 원본 — 직접식별자와 자유텍스트가 그대로 있습니다."""
    rows = []
    note_iter = iter(DIARY_NOTES)
    base = _dt.date(2026, 3, 14)
    for offset, (sid, name, phone, birth, sex) in enumerate(SUBJECTS):
        for visit in range(3):
            visit_date = base + _dt.timedelta(days=offset + visit * 7)
            # 야간 귀속: 취침은 방문 전날 밤 23시대 → 자정을 넘긴 밤
            night = _dt.datetime.combine(visit_date - _dt.timedelta(days=1), _dt.time(23, 40))
            rows.append(
                [
                    sid, name, phone, birth, sex,
                    visit_date.isoformat(),
                    night.strftime("%Y-%m-%d %H:%M"),
                    380 + (offset * 7 + visit * 11) % 90,
                    next(note_iter, ""),
                ]
            )
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["subject_id", "name", "phone", "birth", "sex", "visit_date", "night_start", "TST_min", "비고"]
        )
        writer.writerows(rows)


def write_clean(path: Path) -> None:
    """완전히 깨끗한 분석용 파일 — 치명 0 · 경고 0 이 나와야 합니다.

    이 파일이 조용하지 않으면 규칙이 넓은 것입니다. (오탐 폭발 조기 경보)
    """
    rows = []
    groups = [("30대", "M"), ("30대", "F"), ("40대", "M"), ("40대", "F"), ("50대", "M"), ("50대", "F")]
    notes = ["특이사항 없음", "잠들기까지 오래 걸림", "중간에 깼다", "컨디션 좋음", "낮잠 없음"]
    n = 0
    for gi, (age_group, sex) in enumerate(groups):
        for k in range(5):  # 조합마다 5명 → min k = 5
            n += 1
            sid = f"S{n:02d}"
            for week in (0, 4, 8):
                rows.append(
                    [sid, "중재" if n % 2 == 0 else "대조", age_group, sex, week,
                     20 - week // 2 + (n % 4), 380 + (n * 7 + week) % 80, notes[(n + week) % len(notes)]]
                )
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["subject_id", "arm", "age_group", "sex", "week", "isi_total", "TST_min", "비고"])
        writer.writerows(rows)


def write_ut_log(path: Path) -> None:
    """UT 로그 XLSX — 숨김 시트·숨김 열·셀 주석·작성자 메타데이터가 들어 있습니다."""
    header = ["subject_id", "자유응답", "만족도", "visit_date", "담당자"]
    rows = [header]
    staff = ["오세라", "한지민", "서동욱"]
    answers = [
        "소리가 작아서 잘 안 들렸어요",
        "밤에 알림이 울려서 놀랐습니다",
        "문의는 010-5551-2345 로 연락 주세요",
        "전반적으로 만족합니다",
        "착용감이 불편했어요",
        "다음에도 참여하고 싶어요",
        "설명이 조금 어려웠습니다",
        "특별히 불편한 점은 없었습니다",
    ]
    base = _dt.date(2026, 3, 14)
    for i, (sid, *_rest) in enumerate(SUBJECTS):
        rows.append(
            [sid, answers[i], (i % 5) + 1, _dt.datetime.combine(base + _dt.timedelta(days=i), _dt.time()), staff[i % 3]]
        )

    roster = [["이름", "주민등록번호", "연락처"]]
    for sid, name, phone, *_ in SUBJECTS[:3]:
        roster.append([name, "880402-3123454", phone])

    build_xlsx(
        path,
        [
            Sheet(
                name="응답",
                rows=rows,
                hidden_columns=(4,),
                comments=(("B3", "hyeonjoong.k", "이 응답자는 전화로 다시 확인 필요"),),
            ),
            Sheet(name="원본명단", rows=roster, hidden=True, use_inline_strings=True),
        ],
        creator="hyeonjoong.k",
        last_modified_by="연구간호사1",
        company="BELL",
        defined_names={"명단범위": "원본명단!$A$1:$C$4"},
    )


def main() -> None:
    write_diary(HERE / "수면일기_원본.csv")
    write_clean(HERE / "깨끗한_분석용.csv")
    write_ut_log(HERE / "UT로그.xlsx")
    print("예제를 다시 만들었습니다:", ", ".join(sorted(p.name for p in HERE.glob("*") if p.suffix in (".csv", ".xlsx"))))


if __name__ == "__main__":
    main()

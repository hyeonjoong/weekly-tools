#!/usr/bin/env python3
"""번들 예제를 만든다 — **전부 합성이다.**

실제 피험자 자료·실명·Access Code 는 이 저장소에 들어오지 않는다. 여기서 만드는
워크북은 실데이터와 **구조만** 같다: 6시트 · `[요약]` 3행 + 빈 행 + 헤더 · 5열 규격 ·
단위 붙은 문자열 값(`'2회'`, `'0초'`) · `2026.03.02` 형식의 날짜.

만드는 것:

  clean/         4명 — 트래커가 로그와 정확히 일치. **조용해야 정상**(종료코드 0)
  flawed/        6명 — 실데이터에서 실제로 잡힌 결함 모양을 작게 재현(종료코드 1)
  규격불일치/     헤더 5열이 다른 워크북 하나 → 판정 없이 종료코드 2
  못읽는파일/     clean 사본 + 깨진 워크북 하나 → 판정 불가, 종료코드 3
  이벤트로그csv/  단일 이벤트 로그 CSV → `logflow` 로 안내하며 종료코드 2

다시 만들려면: `python3 examples/_예제생성.py`
"""

import datetime
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tests"))

from xlsxwrite import FIXED_TIMESTAMP, MODULES, module_sheet, write_xlsx  # noqa: E402

CUT = datetime.date(2026, 4, 27)

# 전부 지어낸 이름이다. 실명이 산출물로 새지 않는지 확인하는 테스트의 미끼로 쓰인다.
FAKE_NAMES = ["홍길동", "김철수", "이영희", "박민수", "최지훈", "정수아", "강하늘"]


def _d(text):
    return datetime.date.fromisoformat(text)


def _dot(date):
    return date.strftime("%Y.%m.%d")


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def _write_subject(folder, code, login, seq, per_module):
    """`per_module` = `{모듈이름: [(날짜, 레벨, 유형, 반복, 초), ...]}`."""
    sheets = []
    for name in MODULES:
        rows = per_module.get(name, [])
        data = [[_dot(d), str(lv), tt, "%s회" % rp, "%s초" % sc] for d, lv, tt, rp, sc in rows]
        secs = [float(sc) for _d_, _l, _t, _r, sc in rows if str(sc).replace(".", "").isdigit()]
        reps = [float(rp) for _d_, _l, _t, rp, _s in rows if str(rp).replace(".", "").isdigit()]
        sheets.append((name, module_sheet(data, _mean(secs), _mean(reps))))
    path = os.path.join(folder, "%s_%s_%d.xlsx" % (code, login, seq))
    write_xlsx(path, sheets)
    return path


def _write_tracker(path, rows, grid_days=20):
    """`rows` = `[(이름, 로그인, 코드, 가입일, 완료일수, 진행률덮어쓰기 or None), ...]`."""
    header = ["환자명", "Login ID", "Access Code", "가입일", "현재 레벨", "완료일수",
              "현재까지 일수", "진행률(%)"] + ["D%d" % i for i in range(1, grid_days + 1)]
    table = [header]
    for name, login, code, enroll, done, progress_override in rows:
        elapsed = (CUT - _d(enroll)).days + 1
        progress = progress_override if progress_override is not None else round(100 * done / elapsed)
        grid = ["참여" if (i % 3 == 0) else "미참여" for i in range(grid_days)]
        table.append([name, login, code, enroll, "5", str(done), str(elapsed), str(progress)] + grid)
    write_xlsx(path, [("참여현황", table)])


def build_clean(root):
    """조용해야 정상인 자료. 여기서 경고가 하나라도 뜨면 그건 툴의 잘못이다."""
    logs = os.path.join(root, "logs")
    os.makedirs(logs, exist_ok=True)
    plan = [
        ("AB12CD34", "alpha", 11, "2026-03-02", ["2026-03-02", "2026-03-04", "2026-03-06",
                                                 "2026-03-09", "2026-03-11", "2026-03-13"]),
        ("EF56GH78", "bravo", 12, "2026-03-02", ["2026-03-03", "2026-03-05",
                                                 "2026-03-10", "2026-03-17"]),
        ("IJ90KL10VW", "charlie", 13, "2026-03-09", ["2026-03-09", "2026-03-10", "2026-03-12",
                                                     "2026-03-16", "2026-03-18", "2026-03-20",
                                                     "2026-03-24", "2026-03-26", "2026-04-01"]),
        ("MN11OP22", "delta", 14, "2026-03-16", ["2026-03-16", "2026-03-30"]),
    ]
    tracker_rows = []
    for idx, (code, login, seq, enroll, dates) in enumerate(plan):
        per_module = {name: [] for name in MODULES}
        for j, text in enumerate(dates):
            day = _d(text)
            per_module["소리그림감상"].append((day, j + 1, "REGULAR", 2, 10 + j))
            per_module["사전자가진단"].append((day, j + 1, "REGULAR", 1, 4 + j))
            if j == 0:
                per_module["소리스케치북"].append((day, 1, "REGULAR", 1, 7))
                per_module["사후자가진단"].append((day, 1, "INDIVIDUAL", 1, 8))
                per_module["음소쌍"].append((day, 1, "REGULAR", 3, 9))
                per_module["발성훈련"].append((day, 1, "INDIVIDUAL", 2, 11))
        _write_subject(logs, code, login, seq, per_module)
        tracker_rows.append((FAKE_NAMES[idx], login, code, enroll, len(dates), None))
    _write_tracker(os.path.join(root, "참여현황.xlsx"), tracker_rows)


def build_flawed(root):
    """실데이터에서 실제로 잡힌 결함 모양을 작게 재현한 자료."""
    logs = os.path.join(root, "logs")
    os.makedirs(logs, exist_ok=True)
    plan = [
        # (코드, 로그인, seq, 가입일, 활동일, 트래커가 적어 낸 차이)
        ("QR33ST44", "echo", 21, "2026-03-02",
         ["2026-03-02", "2026-03-04", "2026-03-06", "2026-03-11", "2026-03-13"], 1),
        ("UV55WX66", "foxtrot", 22, "2026-03-02",
         ["2026-03-03", "2026-03-05", "2026-03-09", "2026-03-19"], 2),
        ("YZ77AB88", "golf", 23, "2026-03-09",
         ["2026-03-09", "2026-03-11", "2026-03-13", "2026-03-23", "2026-03-25", "2026-04-02"], 3),
        ("CD99EF00GH", "hotel", 24, "2026-03-09",
         ["2026-03-10", "2026-03-12", "2026-03-17", "2026-03-19",
          "2026-03-30", "2026-04-06"], 4),
        # 일치하는 사람 — 여기서 치명이 뜨면 오탐이다
        ("IJ11KL22", "india", 25, "2026-03-16", ["2026-03-16", "2026-03-18", "2026-03-27"], 0),
        # 트래커에 행이 없는 사람
        ("MN33OP44", "juliet", 26, "2026-03-16", ["2026-03-17", "2026-03-24"], None),
    ]
    tracker_rows = []
    for idx, (code, login, seq, enroll, dates, gap) in enumerate(plan):
        per_module = {name: [] for name in MODULES}
        for j, text in enumerate(dates):
            day = _d(text)
            per_module["소리그림감상"].append((day, j + 1, "REGULAR", 2, 12 + j))
            # 이 모듈은 전 행이 0초 — 0과 미기록을 구분할 수 없다
            per_module["소리스케치북"].append((day, j + 1, "REGULAR", 1, 0))
            per_module["사전자가진단"].append((day, j + 1, "INDIVIDUAL", 1, 5 + j))
            if j == 0:
                per_module["사후자가진단"].append((day, 1, "REGULAR", 1, 6))
                per_module["음소쌍"].append((day, 1, "REGULAR", 3, 9))
            # `발성훈련` 은 어느 파일에도 데이터가 없다 (실데이터: 30/30)
        # 지문이 완전히 같은 행을 하나 더 — 중복의심(제거하지 않는다)
        if code == "QR33ST44":
            first = per_module["소리그림감상"][0]
            per_module["소리그림감상"].append(first)
        _write_subject(logs, code, login, seq, per_module)
        if gap is not None:
            tracker_rows.append((FAKE_NAMES[idx], login, code, enroll, len(dates) - gap, None))

    # 트래커에만 있는 사람
    tracker_rows.append((FAKE_NAMES[6], "kilo", "ZZ99ZZ99", "2026-03-23", 3, None))
    # 한 명은 진행률(%)이 트래커 자신의 열에서도 재현되지 않는다
    tracker_rows[1] = tracker_rows[1][:5] + (99,)
    _write_tracker(os.path.join(root, "참여현황.xlsx"), tracker_rows)

    # 소요시간을 숫자로 읽을 수 없는 행 하나 — 조용히 버리지 않고 센다
    _inject_unparsable(os.path.join(logs, "UV55WX66_foxtrot_22.xlsx"))


def _inject_unparsable(path):
    """이미 만든 워크북의 마지막 데이터 행 소요시간을 `'--초'` 로 바꾼다."""
    import zipfile
    with zipfile.ZipFile(path) as zf:
        members = {name: zf.read(name) for name in zf.namelist()}
    key = "xl/worksheets/sheet1.xml"
    text = members[key].decode("utf-8")
    marker = "<v>12초</v>"
    if marker in text:
        text = text.replace(marker, "<v>--초</v>", 1)
    members[key] = text.encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            # `write_xlsx` 와 **같은 고정 타임스탬프**를 쓴다. 여기서만 현재 시각을
            # 찍으면 이 파일 하나가 매번 다른 바이트가 되어, 내용이 그대로인데도
            # git diff 에 올라온다.
            info = zipfile.ZipInfo(name, date_time=FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            zf.writestr(info, data)


def build_spec_mismatch(root):
    """헤더 5열이 규격과 다른 워크북 — 추론하지 않고 종료코드 2."""
    os.makedirs(root, exist_ok=True)
    bad_header = ["날짜", "레벨", "훈련유형", "반복횟수", "소요시간"]   # `(초)` 가 없다
    sheets = []
    for name in MODULES:
        rows = [[_dot(_d("2026-03-02")), "1", "REGULAR", "2회", "5초"]]
        sheets.append((name, module_sheet(rows, 5, 2, header=bad_header)))
    write_xlsx(os.path.join(root, "PQ55RS66_lima_31.xlsx"), sheets)


def build_unreadable(clean_root, root):
    """clean 사본 + 깨진 워크북 하나 — 판정 불가(종료코드 3)."""
    logs = os.path.join(root, "logs")
    if os.path.isdir(logs):
        shutil.rmtree(logs)
    shutil.copytree(os.path.join(clean_root, "logs"), logs)
    shutil.copy2(os.path.join(clean_root, "참여현황.xlsx"), os.path.join(root, "참여현황.xlsx"))
    with open(os.path.join(logs, "TU77VW88_mike_32.xlsx"), "wb") as fh:
        fh.write(b"PK\x03\x04 this is not a real workbook \x00\xff")


def build_event_csv(root):
    """단일 이벤트 로그 CSV — 이 툴의 입력이 아니다(→ `logflow`)."""
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "app_events.csv"), "w", encoding="utf-8") as fh:
        fh.write("user_id,event,timestamp\n")
        fh.write("u001,session_start,2026-03-02T09:11:00\n")
        fh.write("u001,module_done,2026-03-02T09:23:00\n")
        fh.write("u002,session_start,2026-03-02T20:02:00\n")


def main():
    clean = os.path.join(HERE, "clean")
    flawed = os.path.join(HERE, "flawed")
    for folder in (clean, flawed):
        if os.path.isdir(folder):
            shutil.rmtree(folder)
    build_clean(clean)
    build_flawed(flawed)
    build_spec_mismatch(os.path.join(HERE, "규격불일치"))
    build_unreadable(clean, os.path.join(HERE, "못읽는파일"))
    build_event_csv(os.path.join(HERE, "이벤트로그csv"))
    print("예제를 다시 만들었습니다: clean · flawed · 규격불일치 · 못읽는파일 · 이벤트로그csv")


if __name__ == "__main__":
    main()

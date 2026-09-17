"""테스트 공용 픽스처.

원칙 두 가지:

1. **완전 오프라인.** 네트워크를 쓰는 테스트는 하나도 없다.
2. **저장소에 실데이터는 없다.** 실측 회귀 테스트(`test_실측회귀.py`)는 사용자의
   로컬 경로가 있을 때만 돌고, 없으면 skip 한다. 기대값은 **숫자와 해시**로만
   적혀 있어 Access Code 가 저장소에 남지 않는다.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)   # xlsxwrite (개발용 라이터)
sys.path.insert(0, ROOT)

from xlsxwrite import MODULES, module_sheet, write_xlsx  # noqa: E402


@pytest.fixture
def examples_dir():
    return os.path.join(ROOT, "examples")


@pytest.fixture
def clean_logs(examples_dir):
    return os.path.join(examples_dir, "clean", "logs")


@pytest.fixture
def clean_tracker(examples_dir):
    return os.path.join(examples_dir, "clean", "참여현황.xlsx")


@pytest.fixture
def flawed_logs(examples_dir):
    return os.path.join(examples_dir, "flawed", "logs")


@pytest.fixture
def flawed_tracker(examples_dir):
    return os.path.join(examples_dir, "flawed", "참여현황.xlsx")


@pytest.fixture
def make_workbook(tmp_path):
    """`make_workbook("AB12CD34", {"음소쌍": [(날짜, 레벨, 유형, 반복, 초), ...]})`.

    지정하지 않은 모듈은 데이터 0행으로(헤더는 있게) 만들어진다 — 실데이터의
    `발성훈련` 과 같은 모양이다.
    """
    def _make(code, per_module, login="user", seq=1, folder=None, header=None,
              modules=MODULES, vendor=None):
        target = folder or (tmp_path / "logs")
        os.makedirs(str(target), exist_ok=True)
        sheets = []
        for name in modules:
            rows = per_module.get(name, [])
            data = [[d, str(lv), tt, "%s회" % rp, "%s초" % sc] for d, lv, tt, rp, sc in rows]
            secs = [float(sc) for *_x, sc in rows if _is_number(sc)]
            reps = [float(rp) for *_h, rp, _s in rows if _is_number(rp)]
            override = (vendor or {}).get(name)
            mean_s = override[0] if override else (sum(secs) / len(secs) if secs else 0)
            mean_r = override[1] if override else (sum(reps) / len(reps) if reps else 0)
            sheets.append((name, module_sheet(data, mean_s, mean_r, header=header)))
        path = os.path.join(str(target), "%s_%s_%d.xlsx" % (code, login, seq))
        write_xlsx(path, sheets)
        return path
    return _make


def _is_number(value):
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


@pytest.fixture
def make_tracker(tmp_path):
    """`make_tracker([(코드, 가입일, 완료일수), ...])` → 트래커 xlsx 경로."""
    def _make(rows, path=None, name_column="환자명", grid_days=10, extra_header=None,
              elapsed=None, progress=None):
        header = [name_column, "Login ID", "Access Code", "가입일", "현재 레벨",
                  "완료일수", "현재까지 일수", "진행률(%)"]
        header += ["D%d" % i for i in range(1, grid_days + 1)]
        if extra_header:
            header = extra_header
        table = [header]
        for idx, row in enumerate(rows):
            code, enroll, done = row[0], row[1], row[2]
            this_elapsed = row[3] if len(row) > 3 else (elapsed or 100)
            this_progress = row[4] if len(row) > 4 else (
                progress if progress is not None else round(100 * done / this_elapsed))
            table.append(["홍길동%d" % idx, "login%d" % idx, code, enroll, "5",
                          str(done), str(this_elapsed), str(this_progress)]
                         + ["참여"] * grid_days)
        target = path or str(tmp_path / "tracker.xlsx")
        write_xlsx(target, [("참여현황", table)])
        return target
    return _make


@pytest.fixture
def basic_rows():
    """정상적인 데이터 몇 행 — 대부분의 테스트가 이걸로 충분하다."""
    return [("2026.03.02", 1, "REGULAR", 2, 10),
            ("2026.03.04", 2, "REGULAR", 2, 12),
            ("2026.03.06", 3, "INDIVIDUAL", 1, 14)]


# ── 실데이터 회귀 ────────────────────────────────────────────────────────────
REAL_LOGS = os.environ.get(
    "DOSEAUDIT_REAL_LOGS",
    os.path.expanduser("~/Downloads/02_프로젝트/임상데이터/M&Q임상 결과/wowfit_project17_excels"))
REAL_TRACKER = os.environ.get(
    "DOSEAUDIT_REAL_TRACKER",
    os.path.expanduser("~/Downloads/02_프로젝트/임상데이터/M&Q임상 결과/참여현황_2026-06-21.xlsx"))

real_data = pytest.mark.skipif(
    not (os.path.isdir(REAL_LOGS) and os.path.isfile(REAL_TRACKER)),
    reason="실데이터가 이 기기에 없습니다 (DOSEAUDIT_REAL_LOGS / DOSEAUDIT_REAL_TRACKER 로 지정)")

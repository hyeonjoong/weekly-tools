"""연계 실행 — `피험자별노출량.csv` 가 실제로 `joinaudit`·`statwise` 에 들어간다.

이 저장소의 다른 툴 폴더는 **수정하지도 import 하지도 복사하지도 않는다.**
연계는 오직 CSV 스키마로만 이루어지고, 검증도 서브프로세스로 CLI 를 부르는
방식으로만 한다 — 설치돼 있지 않으면 skip.
"""

import csv
import os
import subprocess
import sys

import pytest

from doseaudit.artifacts import EXPOSURE_CSV, write_all
from doseaudit.audit import Config, run_audit
from doseaudit.report import render_console
from doseaudit.rules import ActiveDayRule, Window

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _installed(module):
    return subprocess.run([sys.executable, "-c", "import %s" % module],
                          capture_output=True).returncode == 0


needs_joinaudit = pytest.mark.skipif(not _installed("joinaudit"),
                                     reason="joinaudit 이 설치돼 있지 않습니다")
needs_statwise = pytest.mark.skipif(not _installed("statwise"),
                                    reason="statwise 가 설치돼 있지 않습니다")


@pytest.fixture
def exposure_csv(flawed_logs, flawed_tracker, tmp_path):
    result = run_audit(Config(
        logs=flawed_logs, tracker_path=flawed_tracker,
        rule=ActiveDayRule.parse("any-row"),
        window=Window.parse("enroll-to-cut", "2026-04-27"),
        enroll_source=None, cap=None, target_per_week=3.0,
        pool_types=False, criterion=None, out_dir=None))
    out = str(tmp_path / "결과")
    os.makedirs(out)
    write_all(result, out, render_console(result))
    return os.path.join(out, EXPOSURE_CSV)


@pytest.fixture
def arm_csv(exposure_csv, tmp_path):
    """분석에서 실제로 붙일 법한 배정표 — 합성이다."""
    with open(exposure_csv, encoding="utf-8-sig", newline="") as fh:
        codes = [row[0] for row in list(csv.reader(fh))[1:]]
    path = str(tmp_path / "배정.csv")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["subject_id", "arm"])
        for idx, code in enumerate(codes):
            writer.writerow([code, "device" if idx % 2 else "sham"])
    return path


@needs_joinaudit
def test_joinaudit_이_피험자키를_자동으로_알아본다(exposure_csv):
    """열 이름이 `subject_id` 인 것은 우연이 아니다 — 자동 탐지가 이 이름을 먹는다."""
    proc = subprocess.run([sys.executable, "-m", "joinaudit.cli", exposure_csv, "--inspect"],
                          capture_output=True, text=True, cwd=ROOT)
    assert "피험자 키: subject_id" in proc.stdout
    assert "고유 ID 6개" in proc.stdout


@needs_joinaudit
def test_날짜_열이_둘이라_joinaudit_이_지정을_요구한다(exposure_csv):
    """첫/마지막 활동일을 둘 다 내보내므로 `joinaudit` 은 어느 쪽인지 묻는다.

    이건 결함이 아니라 `joinaudit` 이 추측하지 않는다는 뜻이고, README 는
    `--date 피험자별노출량.csv=첫활동일` 한 줄을 그대로 적어 둔다.
    """
    proc = subprocess.run([sys.executable, "-m", "joinaudit.cli", exposure_csv, "--inspect"],
                          capture_output=True, text=True, cwd=ROOT)
    assert "첫활동일" in proc.stdout and "마지막활동일" in proc.stdout
    assert "--date" in proc.stdout


@needs_joinaudit
def test_joinaudit_으로_배정표와_병합된다(exposure_csv, arm_csv, tmp_path):
    out = str(tmp_path / "merged")
    proc = subprocess.run(
        [sys.executable, "-m", "joinaudit.cli", exposure_csv, arm_csv,
         "--date", "%s=첫활동일" % os.path.basename(exposure_csv),
         "--out-dir", out], capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    merged = os.path.join(out, "merged.csv")
    assert os.path.isfile(merged)
    with open(merged, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert len(rows) == 7                      # 헤더 + 피험자 6명
    assert any("활동일수" in h for h in rows[0])
    assert any(h.endswith("arm") for h in rows[0])


@needs_joinaudit
@needs_statwise
def test_statwise_가_병합된_노출량을_받아_돈다(exposure_csv, arm_csv, tmp_path):
    out = str(tmp_path / "merged")
    subprocess.run(
        [sys.executable, "-m", "joinaudit.cli", exposure_csv, arm_csv,
         "--date", "%s=첫활동일" % os.path.basename(exposure_csv),
         "--out-dir", out], capture_output=True, text=True, cwd=ROOT, check=True)
    with open(os.path.join(out, "merged.csv"), encoding="utf-8-sig", newline="") as fh:
        header = next(csv.reader(fh))
    value = next(h for h in header if h.endswith("활동일수"))
    group = next(h for h in header if h.endswith("arm"))
    proc = subprocess.run(
        [sys.executable, "-m", "statwise.cli", os.path.join(out, "merged.csv"),
         "--value", value, "--group", group], capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "기술통계" in proc.stdout


def test_우리는_다른_툴을_import_하지_않는다():
    """연계는 CSV 스키마로만 한다 — 남의 폴더에 손대지 않는다."""
    import ast
    pkg = os.path.join(ROOT, "doseaudit")
    others = {"joinaudit", "statwise", "longistat", "logflow", "medpath", "visitaudit"}
    for filename in os.listdir(pkg):
        if not filename.endswith(".py"):
            continue
        with open(os.path.join(pkg, filename), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in others, filename
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in others, filename

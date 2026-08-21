"""테스트 공용 픽스처. 네트워크·외부 라이브러리를 일절 쓰지 않는다."""

import csv
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robustcheck.analyze import analyse           # noqa: E402
from robustcheck.dataio import read_table         # noqa: E402
from robustcheck.spec import Spec, build_dataset  # noqa: E402

EXAMPLES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")

HEADER = ["subject_id", "arm", "isi_baseline", "isi_week4", "rmssd_ms",
          "sleep_eff_pct"]


def write_csv(path, rows, header=None, encoding="utf-8", newline="\n"):
    """행 목록을 CSV 로 쓴다. 인코딩·개행을 바꿔 가며 시험할 수 있게 해 둔다."""
    text_rows = [header or HEADER] + [list(map(_cell, row)) for row in rows]
    lines = [",".join(_quote(c) for c in row) for row in text_rows]
    with open(path, "w", encoding=encoding, newline="") as fh:
        fh.write(newline.join(lines) + newline)
    return str(path)


def _cell(value):
    return "" if value is None else str(value)


def _quote(cell):
    if any(ch in cell for ch in (",", '"', "\n")):
        return '"%s"' % cell.replace('"', '""')
    return cell


def make_rows(n=20, effect=4.0, seed=3):
    """군 효과가 `effect` 인 합성 2군 자료를 결정론적으로 만든다."""
    import random
    rng = random.Random(seed)
    rows = []
    for i in range(1, n + 1):
        active = i % 2 == 1
        base = round(rng.gauss(19.0, 2.5), 1)
        week4 = round(max(0.1, base - (effect if active else 0.0)
                          - rng.gauss(2.0, 2.0)), 1)
        rows.append(["S%03d" % i, "active" if active else "sham", base, week4,
                     round(rng.gauss(30.0, 6.0), 1), round(rng.gauss(82.0, 4.0), 1)])
    return rows


def analyse_path(path, **kwargs):
    """CSV 경로 + Spec 인자 → Analysis."""
    spec_kwargs = {k: v for k, v in kwargs.items() if k != "equal_var"}
    spec = Spec(**spec_kwargs)
    dataset = build_dataset(read_table(path), spec)
    return analyse(dataset, equal_var=kwargs.get("equal_var", False))


@pytest.fixture
def robust_csv():
    return os.path.join(EXAMPLES, "견고_예제.csv")


@pytest.fixture
def fragile_csv():
    return os.path.join(EXAMPLES, "취약_예제.csv")


@pytest.fixture
def undecidable_csv():
    return os.path.join(EXAMPLES, "판정불가_예제.csv")


@pytest.fixture
def merged_csv():
    return os.path.join(EXAMPLES, "joinaudit_merged.csv")


@pytest.fixture
def two_group_analysis(robust_csv):
    return analyse_path(robust_csv, design="two-group", group="arm",
                        value="isi_week4")


@pytest.fixture
def fragile_analysis(fragile_csv):
    return analyse_path(fragile_csv, design="two-group", group="arm",
                        value="isi_week4")

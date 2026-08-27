"""테스트 공통 픽스처 — 전부 오프라인, 전부 합성 데이터."""

from __future__ import annotations

import csv
import datetime as _dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EXAMPLES = ROOT / "examples"


@pytest.fixture
def examples_dir() -> Path:
    return EXAMPLES


def write_csv_file(path: Path, header, rows, encoding: str = "utf-8-sig") -> Path:
    """테스트용 CSV 를 만듭니다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding=encoding, newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    return path


@pytest.fixture
def make_csv(tmp_path):
    def _make(name: str, header, rows, encoding: str = "utf-8-sig") -> Path:
        return write_csv_file(tmp_path / name, header, rows, encoding=encoding)

    return _make


@pytest.fixture
def dirty_csv(tmp_path) -> Path:
    """직접식별자가 든 작은 파일."""
    rows = []
    base = _dt.date(2026, 3, 14)
    people = [
        ("S01", "김현중", "010-2345-6789", "1988-04-02", "M"),
        ("S02", "이서연", "010-9876-5432", "1991-11-27", "F"),
        ("S03", "박준호", "010-3141-5926", "1979-06-15", "M"),
    ]
    for i, (sid, name, phone, birth, sex) in enumerate(people):
        for visit in range(2):
            rows.append(
                [sid, name, phone, birth, sex, (base + _dt.timedelta(days=i * 7 + visit)).isoformat(),
                 400 + i, "특이사항 없음" if visit == 0 else "새벽에 깨서 ○○○ 간호사한테 얘기함"]
            )
    return write_csv_file(
        tmp_path / "dirty.csv",
        ["subject_id", "name", "phone", "birth", "sex", "visit_date", "TST_min", "비고"],
        rows,
    )


@pytest.fixture
def clean_csv(tmp_path) -> Path:
    """치명 0 · 경고 0 이 나와야 하는 파일."""
    rows = []
    n = 0
    for age_group in ("30대", "40대"):
        for sex in ("M", "F"):
            for _ in range(5):
                n += 1
                for week in (0, 4):
                    rows.append([f"S{n:02d}", age_group, sex, week, 18 - week // 2, 400 + n, "특이사항 없음"])
    return write_csv_file(
        tmp_path / "clean.csv",
        ["subject_id", "age_group", "sex", "week", "isi_total", "TST_min", "비고"],
        rows,
    )

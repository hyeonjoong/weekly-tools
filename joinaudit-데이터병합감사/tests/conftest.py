"""테스트 공용 도구. 전부 오프라인이고 임시 폴더에만 쓴다."""

from __future__ import annotations

import csv
import importlib.util
import io
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples")

if ROOT not in sys.path:                      # 설치 없이도 테스트가 돌게
    sys.path.insert(0, ROOT)


def _load_generator():
    """`examples/_예제생성.py` 를 모듈로 불러온다.

    예제 생성기를 테스트에서 그대로 쓰면 (1) .xlsx 작성기를 두 벌 유지하지
    않아도 되고 (2) 번들 예제가 실제로 재생성 가능한지도 함께 검증된다.
    """
    path = os.path.join(EXAMPLES, "_예제생성.py")
    spec = importlib.util.spec_from_file_location("joinaudit_examples_gen", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


gen = _load_generator()
write_xlsx = gen.write_xlsx


def write_bytes(path: str, data: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def write_text(path: str, text: str, encoding: str = "utf-8") -> str:
    return write_bytes(path, text.encode(encoding))


def write_rows(path: str, rows, encoding: str = "utf-8", delimiter: str = ",",
               newline: str = "\n") -> str:
    """행 목록을 표 파일로. 구분자를 품은 셀은 표준 CSV 규칙대로 따옴표로 감싼다."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, lineterminator=newline)
    for row in rows:
        writer.writerow([str(c) for c in row])
    return write_text(path, buf.getvalue(), encoding=encoding)


@pytest.fixture
def tmpdir_path(tmp_path):
    return str(tmp_path)


@pytest.fixture
def examples_dir():
    return EXAMPLES


@pytest.fixture
def clean_set():
    """깨끗한 예제 3종의 경로 (watch, diary, isi)."""
    return [os.path.join(EXAMPLES, "clean", name)
            for name in ("watch_hrv.csv", "diary.xlsx", "isi.csv")]


@pytest.fixture
def flawed_set():
    return [os.path.join(EXAMPLES, "flawed", name)
            for name in ("watch_hrv.csv", "diary.xlsx", "isi.csv")]

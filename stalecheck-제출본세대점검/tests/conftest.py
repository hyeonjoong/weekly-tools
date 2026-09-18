# -*- coding: utf-8 -*-
"""공통 픽스처. 네트워크를 쓰지 않고, 임시 폴더 밖에 아무것도 쓰지 않는다."""

import importlib.util
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


def _load_example_module():
    path = os.path.join(ROOT, "examples", "예제_만들기.py")
    spec = importlib.util.spec_from_file_location("stalecheck_예제", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


예제 = _load_example_module()


@pytest.fixture(scope="session")
def example_builder():
    return 예제


@pytest.fixture
def example_tree(tmp_path, example_builder):
    """번들된 합성 예제(낡은 봉투 + 깨끗한 봉투)를 임시 폴더에 만든다."""
    return example_builder.build_example(str(tmp_path / "예제"))


@pytest.fixture
def stale_pair(example_tree):
    return example_tree["stale"], example_tree["stale_package"]


@pytest.fixture
def clean_pair(example_tree):
    return example_tree["clean"], example_tree["clean_package"]


def write(path, data, mtime=None):
    """테스트용 파일 쓰기(임시 폴더 전용)."""
    directory = os.path.dirname(str(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    mode = "wb" if isinstance(data, bytes) else "w"
    kwargs = {} if isinstance(data, bytes) else {"encoding": "utf-8"}
    with open(str(path), mode, **kwargs) as fh:
        fh.write(data)
    if mtime is not None:
        os.utime(str(path), (mtime, mtime))
    return str(path)


T0 = time.mktime((2026, 7, 30, 11, 10, 0, 0, 0, -1))
T1 = time.mktime((2026, 7, 31, 10, 39, 0, 0, 0, -1))


@pytest.fixture
def mini(tmp_path):
    """작업 폴더/봉투 뼈대를 만들어 주는 헬퍼."""
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))

    def add(rel, data, mtime=T0, where="work"):
        base = work if where == "work" else pkg
        return write(os.path.join(str(base), rel), data, mtime)

    return {"work": str(work), "package": str(pkg), "add": add}


#: 사용자의 실제 논문 트리 — 있으면 실측 회귀를 돌리고, 없으면 건너뛴다.
REAL_TREE = os.path.expanduser("~/Downloads/02_프로젝트/논문_투고")
real_tree_only = pytest.mark.skipif(
    not os.path.isdir(REAL_TREE),
    reason="실측 회귀는 사용자의 논문_투고/ 트리가 있을 때만 돕니다 (저장소에는 실데이터가 없습니다)",
)

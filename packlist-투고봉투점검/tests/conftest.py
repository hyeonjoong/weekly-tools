"""테스트 공용 픽스처. 합성 docx 빌더를 examples/ 에서 가져다 쓴다."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "examples"))

from _docxbuild import build_corrupt_docx, build_docx, fake_png  # noqa: E402

TITLE = "A synthetic manuscript about respiratory entrainment and sleep"
AUTHORS = "Hyeon-Joong Kim, Jiyeon Ha, Tuomas Eerola"


@pytest.fixture
def builder():
    return build_docx


@pytest.fixture
def corrupt_builder():
    return build_corrupt_docx


@pytest.fixture
def png():
    return fake_png


@pytest.fixture
def envelope(tmp_path):
    """빈 봉투 폴더를 만들어 주는 헬퍼."""
    folder = tmp_path / "봉투"
    folder.mkdir()
    return folder


def make_manuscript(folder, name="원고.docx", **kwargs):
    """기본형 원고: 그림 2개가 모두 앵커된 정상 문서."""
    images = kwargs.pop("images", None)
    if images is None:
        images = {"image1.png": fake_png("m1", 2000), "image2.png": fake_png("m2", 2200)}
    anchored = kwargs.pop("anchored", list(images))
    paragraphs = kwargs.pop("paragraphs", [
        TITLE, AUTHORS,
        "Body text cites Fig. 1 and Fig. 2 and also S1 Table.",
        ("Fig. 1. First caption. (a) left. (b) right.", "image1.png"),
        ("Fig. 2. Second caption. (a) top.", "image2.png"),
    ])
    kwargs.setdefault("title", TITLE)
    return build_docx(folder / name, paragraphs=paragraphs, images=images,
                      anchored=anchored, **kwargs)


@pytest.fixture
def manuscript_factory():
    return make_manuscript


def put(folder, name, data=b"%PDF-1.4\nsynthetic\n"):
    """봉투에 부속 파일 하나를 놓는다."""
    path = folder / name
    path.write_bytes(data)
    return path


@pytest.fixture
def put_file():
    return put

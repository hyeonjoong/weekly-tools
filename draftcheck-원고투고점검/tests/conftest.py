"""테스트 공용 픽스처. 전부 오프라인이며 번들 예제만 사용한다."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from draftcheck.checks import run_checks  # noqa: E402
from draftcheck.docio import detect_sections, read_manuscript  # noqa: E402

EXAMPLES = ROOT / "examples"
LIMITS = EXAMPLES / "journals" / "sleepmed.json"


def analyse(path, **kwargs):
    """원고 하나를 끝까지 돌려 Result 를 돌려주는 지름길."""
    ms = read_manuscript(path)
    sec = detect_sections(ms)
    return run_checks(ms, sec, **kwargs)


@pytest.fixture(scope="session")
def flawed():
    return analyse(EXAMPLES / "manuscript_flawed.md")


@pytest.fixture(scope="session")
def clean():
    return analyse(EXAMPLES / "manuscript_clean.md")


@pytest.fixture(scope="session")
def flawed_docx():
    return analyse(EXAMPLES / "manuscript_flawed.docx")


@pytest.fixture(scope="session")
def clean_docx():
    return analyse(EXAMPLES / "manuscript_clean.docx")


def kinds(result, severity=None):
    return [f.kind for f in result.findings if severity is None or f.severity == severity]


def messages(result, severity=None):
    return [f.message for f in result.findings if severity is None or f.severity == severity]


def find(result, kind, needle=""):
    """유형이 kind 이고 메시지에 needle 이 든 findings."""
    return [f for f in result.findings if f.kind == kind and needle in f.message]

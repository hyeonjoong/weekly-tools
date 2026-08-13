"""테스트 공용 도우미. 완전 오프라인 — 네트워크를 쓰는 테스트는 하나도 없다."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import pytest

from numcheck.docio import manuscript_from_text
from numcheck.engine import analyze_manuscript
from numcheck.model import Report
from numcheck.options import Options
from numcheck.scales import ScaleRegistry

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def analyze_text(text: str, fmt: str = "md", **kwargs) -> Report:
    """문자열 원고를 검사한다."""
    opts = Options(registry=kwargs.pop("registry", None) or ScaleRegistry(), **kwargs)
    return analyze_manuscript(manuscript_from_text(text, fmt), opts)


def findings(report: Report, level: Optional[str] = None, item: Optional[str] = None) -> List:
    out = report.findings
    if level is not None:
        out = [f for f in out if f.level == level]
    if item is not None:
        out = [f for f in out if item in f.item]
    return out


def items(report: Report, level: Optional[str] = None) -> List[str]:
    return [f.item for f in findings(report, level)]


@pytest.fixture
def examples_dir() -> Path:
    return EXAMPLES

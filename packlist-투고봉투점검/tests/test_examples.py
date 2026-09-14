"""동봉된 합성 예제 4종이 약속한 종료코드를 정확히 낸다.

`실행.command` 가 이 순서대로 돌려 0 · 1 · 3 · 2 를 보여 준다.
"""

import importlib.util
import os
import sys

import pytest

from packlist import EXIT_CRITICAL, EXIT_OK, EXIT_REFUSED, EXIT_UNDECIDABLE
from packlist.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "examples"))
import _make_examples as maker    # noqa: E402


@pytest.fixture(scope="module")
def examples(tmp_path_factory):
    root = tmp_path_factory.mktemp("examples")
    maker.make_normal(str(root))
    maker.make_orphan(str(root))
    maker.make_undecidable(str(root))
    maker.make_refused(str(root))
    maker.make_specs(str(root))
    return root


def test_normal_envelope_exits_zero(examples):
    assert main([str(examples / "01_정상봉투"),
                 "--manuscript", "원고_synthetic_v3.docx"]) == EXIT_OK


def test_orphan_envelope_exits_one(examples):
    assert main([str(examples / "02_고아있음")]) == EXIT_CRITICAL


def test_undecidable_envelope_exits_three(examples):
    assert main([str(examples / "03_판정불가")]) == EXIT_UNDECIDABLE


def test_refused_envelope_exits_two(examples):
    assert main([str(examples / "04_거절_원고만")]) == EXIT_REFUSED


def test_orphan_example_mirrors_the_real_incident_shape(examples):
    """미디어 6 · 중복 3쌍 · 본문 앵커 4 · 고아 2 — 실제 사고와 같은 모양."""
    from packlist.docxpkg import read_docx
    info = read_docx(examples / "02_고아있음" / "원고_synthetic_v10.docx")
    assert len(info.media) == 6
    assert len(info.duplicate_groups()) == 3
    assert len(info.body_anchored) == 4
    assert len(info.orphans) == 2


def test_normal_example_promises_are_all_found(examples):
    from packlist.analysis import analyse
    from packlist.docxpkg import read_docx
    from packlist.envelope import choose_manuscript, scan_envelope
    env = scan_envelope(str(examples / "01_정상봉투"))
    chosen = choose_manuscript(env, "원고_synthetic_v3.docx")
    info = read_docx(env.root / chosen.name, chosen.name)
    others = [read_docx(env.root / f.name, f.name)
              for f in env.docx_files if f.name != chosen.name]
    report = analyse(env, chosen, info, others)
    assert {row.verdict for row in report.promises} == {"있음"}
    assert report.critical_count == 0


def test_normal_example_cover_letter_title_matches(examples):
    from packlist.analysis import analyse
    from packlist.docxpkg import read_docx
    from packlist.envelope import choose_manuscript, scan_envelope
    env = scan_envelope(str(examples / "01_정상봉투"))
    chosen = choose_manuscript(env, "원고_synthetic_v3.docx")
    info = read_docx(env.root / chosen.name, chosen.name)
    others = [read_docx(env.root / f.name, f.name)
              for f in env.docx_files if f.name != chosen.name]
    report = analyse(env, chosen, info, others)
    assert report.frontmatter.mismatches() == []


def test_example_specs_load(examples):
    from packlist.spec import load_expectations, load_limits
    assert load_expectations(str(examples / "제출목록_예시.json")).required
    assert load_limits(str(examples / "저널규정_예시.json")).max_total_mb


def test_examples_contain_no_real_manuscript_text(examples):
    """예제에 실제 원고 내용이 섞여 들어가지 않았는지 확인한다."""
    from packlist.docxpkg import read_docx
    info = read_docx(examples / "01_정상봉투" / "원고_synthetic_v3.docx")
    assert "synthetic" in info.text.lower()
    assert "BELL-001" not in info.text


def test_make_examples_is_idempotent(examples):
    maker.make_orphan(str(examples))
    assert main([str(examples / "02_고아있음")]) == EXIT_CRITICAL

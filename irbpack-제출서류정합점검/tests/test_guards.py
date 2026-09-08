"""강제 장치 1~5 — 코드로 고정."""
import ast
import os
import re

import pytest

from irbpack import guards
from irbpack.model import Doc, Para
from tests.conftest import full_packet, md_protocol, run, write_packet

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "irbpack")


def _sources():
    for fn in sorted(os.listdir(PKG)):
        if fn.endswith(".py"):
            with open(os.path.join(PKG, fn), encoding="utf-8") as fh:
                yield fn, fh.read()


# ------------------------------------------------------------ 1. 원고 감지 → exit 2

def _doc(lines, ext=".md"):
    d = Doc(path="m" + ext, name="m" + ext, ext=ext)
    d.paras = [Para(idx=i, text=t) for i, t in enumerate(lines)]
    return d


def test_manuscript_by_sections():
    why = guards.manuscript_reason(_doc(["Abstract", "본문", "Introduction", "Methods", "Results", "Discussion", "References"]))
    assert why and "원고" in why


def test_korean_manuscript():
    assert guards.manuscript_reason(_doc(["초록", "서론", "고찰", "참고문헌"]))


def test_tex_is_manuscript():
    assert guards.manuscript_reason(_doc([], ext=".tex"))


def test_latex_commands():
    assert guards.manuscript_reason(_doc(["\\documentclass{article}", "\\begin{document}"]))


def test_protocol_with_references_is_not_manuscript():
    assert guards.manuscript_reason(_doc(["연구 배경", "연구 방법", "참고문헌", "결과 분석"])) is None


def test_manuscript_in_packet_exits_2_without_verdict(tmp_path):
    ms = "\n".join(["Abstract", "We studied.", "Introduction", "x", "Methods", "y", "Results", "z", "Discussion", "w", "References", "1."])
    res, fatal = run(full_packet(tmp_path, extra_files={"manuscript.md": ms}))
    assert fatal and any("draftcheck" in f for f in fatal)
    assert res.issues == [] and res.extractions == []


# ------------------------------------------------------------ 2. 표본수·검정력 재계산 없음 (AST)

_BANNED_NAMES = re.compile(r"power|effect_?size|sample_?size|cohen|ncp|noncentral|t_?ppf|norm_?ppf|z_?alpha|z_?beta", re.I)


def test_no_power_or_sample_size_functions():
    for fn, src in _sources():
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                assert not _BANNED_NAMES.search(node.name), (fn, node.name)
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert a.name.split(".")[0] not in ("statistics", "scipy", "numpy", "math"), (fn, a.name)
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in ("statistics", "scipy", "numpy", "math"), (fn, node.module)


# ------------------------------------------------------------ 3. 문서를 고치지 않음

def test_no_document_writing_code_paths():
    allowed_writers = {"safeio.py"}
    for fn, src in _sources():
        if fn in allowed_writers:
            continue
        assert not re.search(r"open\([^)]*['\"]w", src), fn
        assert "zipfile.ZipFile(" not in src or "\"w\"" not in src.split("zipfile.ZipFile(")[1][:40], fn
    for fn, src in _sources():
        for bad in ("정정본", "수정본", "보완답변서", "변경대비표"):
            assert bad not in src.replace("정정본·", "").replace("수정본·", "") or fn in ("guards.py", "__init__.py", "cli.py"), (fn, bad)


def test_output_names_are_only_reports():
    from irbpack import report
    src = dict(_sources())["report.py"]
    names = re.findall(r'"([^"]+\.(?:md|csv|docx|txt))"', src)
    assert set(names) == {"정합점검.md", "불일치목록.csv", "항목추출표.csv", "대조불가.csv"}


def test_inputs_never_modified(tmp_path):
    p = full_packet(tmp_path)
    before = {f: (os.path.getmtime(os.path.join(p, f)), open(os.path.join(p, f), "rb").read()) for f in os.listdir(p)}
    run(p)
    after = {f: (os.path.getmtime(os.path.join(p, f)), open(os.path.join(p, f), "rb").read()) for f in os.listdir(p)}
    assert before == after


# ------------------------------------------------------------ 4. 규정 준수 판정 문구 없음

@pytest.mark.parametrize("phrase", ["규정 위반", "GCP 미준수", "승인 불가", "승인될", "심의 통과", "부적합 판정", "위반입니다"])
def test_forbidden_phrases_absent(phrase):
    for fn, src in _sources():
        assert phrase not in src, (fn, phrase)


# ------------------------------------------------------------ 5. 어느 값이 맞다는 판정 없음

@pytest.mark.parametrize("phrase", ["이 맞습니다", "가 맞습니다", "이 옳", "가 옳", "틀렸습니다", "잘못된 값", "정정하십시오", "고치십시오"])
def test_no_correctness_verdict_phrases(phrase):
    for fn, src in _sources():
        assert phrase not in src, (fn, phrase)


def test_severities_are_closed_list():
    from irbpack.model import SEVERITIES, Issue
    assert set(SEVERITIES) == {"치명", "경고", "대조불가", "일치"}
    with pytest.raises(ValueError):
        Issue(severity="규정위반", item="n", title="x")

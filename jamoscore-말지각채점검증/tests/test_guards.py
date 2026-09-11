# -*- coding: utf-8 -*-
"""강제장치 — 이 툴이 **하지 않겠다고 한 일**을 코드가 실제로 못 하는지 검사한다.

세 가지를 코드 수준에서 막는다.
  ① 원본·수정본 쓰기 경로 부재 (AST)
  ② 판정하는 말투(금지어) 부재 (리포트·문서 전부)
  ③ 군간·시점간 검정 함수 부재 (AST) — 그건 statwise·longistat 의 일이다
"""

import ast
import os
import re
import subprocess
import sys

import pytest

from jamoscore.findings import FORBIDDEN_PHRASES

#: 산출물 쓰기만 담당하는 모듈. 쓰기 호출이 허용되는 유일한 곳.
WRITE_MODULE = "safeio.py"
#: 통계 검정 함수 이름 조각 — 하나라도 정의돼 있으면 경계를 넘은 것이다.
TEST_FUNCTION_MARKERS = (
    "ttest", "t_test", "wilcoxon", "mannwhitney", "mann_whitney", "anova",
    "chisquare", "chi_square", "chisq", "kruskal", "friedman", "mcnemar",
    "levene", "shapiro", "fisher_exact", "pvalue", "p_value", "regression",
    "mixed_model", "ancova", "effect_size", "cohens_d", "hedges",
)


def python_files(package_dir):
    for name in sorted(os.listdir(package_dir)):
        if name.endswith(".py"):
            yield os.path.join(package_dir, name)


def parse(path):
    with open(path, encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=path)


# ---------------------------------------------- ① 쓰기 경로가 없는가
def test_no_open_for_writing_outside_safeio(package_dir):
    """``open(..., 'w')`` 는 safeio 밖에 존재하면 안 된다."""
    offenders = []
    for path in python_files(package_dir):
        if os.path.basename(path) == WRITE_MODULE:
            continue
        for node in ast.walk(parse(path)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name not in ("open", "fdopen"):
                continue
            modes = [a.value for a in node.args[1:2]
                     if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            modes += [kw.value.value for kw in node.keywords
                      if kw.arg == "mode" and isinstance(kw.value, ast.Constant)]
            if any(m and m[0] in "wax" or "+" in (m or "") for m in modes):
                offenders.append("%s:%d" % (os.path.basename(path), node.lineno))
    assert offenders == [], offenders


def test_no_destructive_filesystem_calls(package_dir):
    """원본을 지우거나 옮기는 호출이 패키지 어디에도 없어야 한다."""
    banned = {"remove", "unlink", "rmdir", "rmtree", "rename", "replace",
              "truncate", "chmod", "chown"}
    offenders = []
    for path in python_files(package_dir):
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Call):
                attr = getattr(node.func, "attr", None)
                if attr in banned:
                    owner = getattr(getattr(node.func, "value", None), "id", "")
                    if owner in ("os", "shutil", "Path", "pathlib"):
                        offenders.append(
                            "%s:%d" % (os.path.basename(path), node.lineno))
    assert offenders == [], offenders


def test_xlsx_module_has_no_write_api(package_dir):
    """XLSX 리더에는 쓰기 함수 자체가 없어야 한다."""
    tree = parse(os.path.join(package_dir, "xlsx.py"))
    names = [n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert not [n for n in names if "write" in n.lower() or "save" in n.lower()]


def test_zipfile_is_never_opened_for_writing(package_dir):
    for path in python_files(package_dir):
        text = open(path, encoding="utf-8").read()
        assert 'ZipFile(' not in text or '"w"' not in text, path


# --------------------------------------- ② 판정하는 말투가 없는가
DOC_FILES = ("README.md", "사용법.md", "실행.command", "HARDENING.md")


@pytest.mark.parametrize("phrase", FORBIDDEN_PHRASES)
def test_forbidden_phrase_absent_from_package(package_dir, phrase):
    """금지어 목록이 정의된 findings.py 만 예외 — 거기 말고는 어디에도 없어야 한다."""
    for path in python_files(package_dir):
        if os.path.basename(path) == "findings.py":
            continue
        text = open(path, encoding="utf-8").read()
        assert phrase not in text, "%s 에 %r" % (os.path.basename(path), phrase)


def test_findings_module_only_mentions_phrases_in_the_list(package_dir):
    """정의 파일 안에서도 금지어는 목록 리터럴 밖에 나오면 안 된다."""
    tree = parse(os.path.join(package_dir, "findings.py"))
    allowed = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "FORBIDDEN_PHRASES" in targets:
                allowed = {e.value for e in ast.walk(node)
                           if isinstance(e, ast.Constant)
                           and isinstance(e.value, str)}
    assert set(FORBIDDEN_PHRASES) <= allowed
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in FORBIDDEN_PHRASES:
                continue
            for phrase in FORBIDDEN_PHRASES:
                assert phrase not in node.value, (node.lineno, phrase)


@pytest.mark.parametrize("phrase", FORBIDDEN_PHRASES)
@pytest.mark.parametrize("doc", DOC_FILES)
def test_forbidden_phrase_absent_from_docs(repo_root, doc, phrase):
    path = os.path.join(repo_root, doc)
    if not os.path.exists(path):
        pytest.skip("%s 없음" % doc)
    text = open(path, encoding="utf-8").read()
    assert phrase not in text, "%s 에 %r" % (doc, phrase)


@pytest.mark.parametrize("phrase", FORBIDDEN_PHRASES)
def test_forbidden_phrase_absent_from_generated_report(examples_dir, tmp_path,
                                                       repo_root, phrase):
    from tests.conftest import SPEC_ARGS
    from jamoscore.cli import main
    out_dir = tmp_path / "결과"
    main([os.path.join(examples_dir, "문제있음_채점.xlsx")] + SPEC_ARGS
         + ["--out-dir", str(out_dir)])
    for root, _, files in os.walk(str(out_dir)):
        for name in files:
            text = open(os.path.join(root, name), encoding="utf-8-sig",
                        errors="replace").read()
            assert phrase not in text, "%s 에 %r" % (name, phrase)


def test_report_vocabulary_is_limited_to_review_language(examples_dir, capsys):
    from tests.conftest import SPEC_ARGS
    from jamoscore.cli import main
    main([os.path.join(examples_dir, "문제있음_채점.xlsx")] + SPEC_ARGS)
    out = capsys.readouterr().out
    assert "확인 필요" in out
    assert "판정하지 않습니다" in out


# ------------------------------- ③ 군간·시점간 검정 함수가 없는가
def test_no_statistical_test_functions_defined(package_dir):
    offenders = []
    for path in python_files(package_dir):
        for node in ast.walk(parse(path)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                low = node.name.lower().replace("-", "_")
                for marker in TEST_FUNCTION_MARKERS:
                    if marker in low:
                        offenders.append("%s:%s" % (os.path.basename(path),
                                                    node.name))
    assert offenders == [], offenders


def test_no_group_comparison_imports(package_dir):
    """scipy·statsmodels 를 끌어오면 군간 검정으로 미끄러진다."""
    banned = {"scipy", "statsmodels", "numpy", "pandas", "sklearn"}
    for path in python_files(package_dir):
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in banned, path
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in banned, path


def test_package_has_zero_third_party_dependencies(repo_root):
    text = open(os.path.join(repo_root, "pyproject.toml"), encoding="utf-8").read()
    assert re.search(r"dependencies\s*=\s*\[\s*\]", text)


def test_no_network_access_anywhere(package_dir):
    banned = {"socket", "http", "urllib", "requests", "ftplib", "smtplib",
              "telnetlib", "asyncio", "httpx", "ssl", "xmlrpc"}
    for path in python_files(package_dir):
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in banned, path
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in banned, path


def test_no_subprocess_or_eval(package_dir):
    for path in python_files(package_dir):
        text = open(path, encoding="utf-8").read()
        for banned in ("subprocess", "eval(", "exec(", "os.system", "pickle"):
            assert banned not in text, "%s 에 %r" % (os.path.basename(path), banned)


# ------------------------------------------------- 리포트 무결성 강제
def test_report_cannot_be_built_without_coverage_block():
    from jamoscore import report
    class Hollow(report.Coverage):
        def render(self):
            return ["[없는블록]"]
    with pytest.raises(report.ReportIntegrityError):
        report.build([], [], [], Hollow(), "판정: 없음", 0)


def test_coverage_block_tail_sentence_is_mandatory():
    from jamoscore import report
    class Partial(report.Coverage):
        def render(self):
            return [report.COVERAGE_HEADER, "   내용만 있고 꼬리말이 없음"]
    with pytest.raises(report.ReportIntegrityError):
        report.build([], [], [], Partial(), "판정: 없음", 0)


def test_gitignore_blocks_real_data_dropped_into_the_folder(repo_root):
    """사용자가 실제 채점 엑셀을 이 폴더에 떨어뜨려도 커밋되지 않아야 한다."""
    candidates = [
        "본실험_결과.xlsx", "환자데이터.xlsx", "채점검증_0911/채점검증.md",
        "결과/문제목록.csv", "개인정보.csv", "내자료.xls",
    ]
    missed = []
    for name in candidates:
        proc = subprocess.run(["git", "check-ignore", "-q", name],
                              cwd=repo_root)
        if proc.returncode != 0:
            missed.append(name)
    assert missed == [], missed


def test_gitignore_does_not_block_bundled_examples(repo_root):
    for name in ["examples/정상_채점.xlsx", "examples/요약만_있음.csv"]:
        proc = subprocess.run(["git", "check-ignore", "-q", name], cwd=repo_root)
        assert proc.returncode != 0, name

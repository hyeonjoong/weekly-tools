"""강제 장치 1~5 — 이 툴이 **하지 않기로 한 것**을 코드가 지키는지 정적으로 검사한다.

1. 원고를 감지하면 판정하지 않고 exit 2      (test_cli.py / test_roles.py 에서도 확인)
2. 표본수·검정력을 재계산하지 않는다          (AST 검사)
3. 문서를 고치지 않는다                       (원본 쓰기 경로 부재)
4. 규정 준수 여부를 판정하지 않는다            (금지어 grep)
5. 어느 값이 옳은지 판정하지 않는다            (금지어 grep)
"""

import ast
import os
import re

import pytest

SOURCE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "irbpack")
SOURCE_FILES = sorted(
    os.path.join(SOURCE_DIR, name) for name in os.listdir(SOURCE_DIR) if name.endswith(".py")
)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


ALL_SOURCE = "\n".join(read(path) for path in SOURCE_FILES)


def test_source_files_exist():
    assert len(SOURCE_FILES) >= 8


# ------------------------------------------------------------------ 강제 장치 2

@pytest.mark.parametrize("name", [
    "power", "sample_size", "samplesize", "effect_size", "effectsize",
    "cohen", "ttest", "t_test", "chisq", "zscore", "norm_ppf", "sample_n",
])
def test_no_power_calculation_functions(name):
    """검정력·표본수 계산 함수가 아예 없어야 한다 — '90명이 적정한가'는 powerplan 의 일이다."""
    for path in SOURCE_FILES:
        tree = ast.parse(read(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert name not in node.name.lower(), "%s 에 %s 계산 함수가 있습니다" % (path, name)


def test_no_statistics_imports():
    for path in SOURCE_FILES:
        tree = ast.parse(read(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            assert not ({"scipy", "numpy", "statsmodels", "pandas", "math"} & set(names)), \
                "%s 가 통계 라이브러리를 import 합니다" % path


def test_no_dependencies_declared():
    root = os.path.dirname(SOURCE_DIR)
    text = read(os.path.join(root, "pyproject.toml"))
    assert "dependencies = []" in text


# ------------------------------------------------------------------ 강제 장치 3

def test_no_write_mode_file_opens_outside_safeio():
    """산출물 쓰기는 safeio 한 곳에만 있어야 한다 (원본 수정 경로가 생기지 않게)."""
    for path in SOURCE_FILES:
        if os.path.basename(path) == "safeio.py":
            continue
        source = read(path)
        assert not re.search(r"open\([^)]*['\"][wa]", source), "%s 에 쓰기 모드 open 이 있습니다" % path


def test_no_document_writer_functions():
    for word in ("save_docx", "write_docx", "make_docx", "정정본", "수정본", "패치"):
        assert word not in ALL_SOURCE, "%s 를 만드는 코드 경로가 있으면 안 됩니다" % word


def test_no_shutil_or_os_remove():
    for word in ("shutil.", "os.remove", "os.unlink", "os.rename", "os.replace"):
        assert word not in ALL_SOURCE


# ------------------------------------------------------------------ 강제 장치 4

@pytest.mark.parametrize("phrase", [
    "규정 위반", "규정위반", "GCP 미준수", "GCP미준수", "승인 불가", "승인불가",
    "심의 통과", "반려될", "법 위반", "위법",
])
def test_no_regulatory_judgement_phrases(phrase):
    assert phrase not in ALL_SOURCE, "'%s' 는 이 툴이 할 수 있는 판정이 아닙니다" % phrase


# ------------------------------------------------------------------ 강제 장치 5

@pytest.mark.parametrize("phrase", [
    "올바른 값", "정답은", "맞는 값은", "수정하세요", "고치세요", "로 바꾸세요",
])
def test_no_which_value_is_right_phrases(phrase):
    assert phrase not in ALL_SOURCE, "'%s' 는 어느 값이 옳은지 판정하는 문구입니다" % phrase


def test_report_states_it_does_not_judge():
    """리포트가 '어느 값이 옳은지는 판정하지 않는다' 를 실제 출력에 싣는다."""
    source = read(os.path.join(SOURCE_DIR, "report.py"))
    assert "어느 값이 옳은지" in source


# ------------------------------------------------------------------ 네트워크·PII

@pytest.mark.parametrize("module", ["socket", "urllib", "http", "requests", "ftplib", "smtplib"])
def test_no_network_imports(module):
    for path in SOURCE_FILES:
        tree = ast.parse(read(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            assert module not in names, "%s 가 %s 를 import 합니다" % (path, module)


def test_no_subprocess_or_eval():
    for word in ("subprocess", "eval(", "exec(", "pickle", "os.system"):
        assert word not in ALL_SOURCE, "%s 가 소스에 있습니다" % word


def test_masking_is_applied_in_report_layer():
    source = read(os.path.join(SOURCE_DIR, "report.py"))
    assert "safe_line" in source and "safe_cell" in source or "write_csv" in source


# ------------------------------------------------------------------ 경계 표시

def test_readme_distinguishes_from_revcheck():
    root = os.path.dirname(SOURCE_DIR)
    readme = read(os.path.join(root, "README.md"))
    assert "revcheck" in readme
    assert "다른 버전" in readme and "다른 문서" in readme


def test_readme_has_korean_and_english_purpose():
    root = os.path.dirname(SOURCE_DIR)
    readme = read(os.path.join(root, "README.md"))
    assert "## 목적" in readme or "목적 / Why this exists" in readme
    assert "Why this exists" in readme


def test_readme_states_no_irb_prediction():
    root = os.path.dirname(SOURCE_DIR)
    readme = read(os.path.join(root, "README.md"))
    assert "IRB 심의 결과를 예측하지 않습니다" in readme


def test_items_list_is_closed_at_twelve():
    from irbpack.items import ITEMS
    assert len(ITEMS) == 12
    source = read(os.path.join(SOURCE_DIR, "items.py"))
    assert "닫힌 목록" in source


def test_every_module_has_docstring():
    for path in SOURCE_FILES:
        tree = ast.parse(read(path))
        assert ast.get_docstring(tree), "%s 에 모듈 docstring 이 없습니다" % path


def test_no_todo_or_fixme_left():
    for marker in ("TODO", "FIXME", "XXX:", "pass  # 나중에"):
        assert marker not in ALL_SOURCE


# ------------------------------------------------------------------ 패키징·데이터 위생

def test_gitignore_blocks_real_submission_documents():
    """이 폴더는 실제 IRB 패킷을 끌어다 놓기 좋은 자리라 문서 확장자를 통째로 막는다."""
    root = os.path.dirname(SOURCE_DIR)
    text = read(os.path.join(root, ".gitignore"))
    for pattern in ("*.docx", "*.pdf", "*.hwp", "*.xlsx", "*.csv"):
        assert pattern in text, "%s 가 .gitignore 에 없습니다" % pattern


def test_gitignore_blocks_report_artifacts():
    root = os.path.dirname(SOURCE_DIR)
    text = read(os.path.join(root, ".gitignore"))
    for name in ("정합점검.md", "불일치목록.csv", "항목추출표.csv", "대조불가.csv", "정합점검결과/"):
        assert name in text


def test_gitignore_reallows_bundled_examples():
    """번들 예제(합성)는 다시 허용해야 저장소에 남는다."""
    root = os.path.dirname(SOURCE_DIR)
    text = read(os.path.join(root, ".gitignore"))
    assert "!examples/**/*.docx" in text
    assert "!examples/**/*.hwp" in text


def test_console_script_declared():
    root = os.path.dirname(SOURCE_DIR)
    text = read(os.path.join(root, "pyproject.toml"))
    assert 'irbpack = "irbpack.cli:main"' in text


def test_launcher_is_executable():
    root = os.path.dirname(SOURCE_DIR)
    launcher = os.path.join(root, "실행.command")
    assert os.path.exists(launcher)
    assert os.access(launcher, os.X_OK)


def test_launcher_runs_examples_and_waits():
    root = os.path.dirname(SOURCE_DIR)
    text = read(os.path.join(root, "실행.command"))
    assert 'cd "$(dirname "$0")"' in text
    assert "엔터를 누르면 창이 닫힙니다" in text
    assert "examples/" in text


def test_korean_quickstart_exists():
    root = os.path.dirname(SOURCE_DIR)
    text = read(os.path.join(root, "사용법.md"))
    assert "실행.command" in text and "--baseline" in text and "커버리지 자백" in text


def test_license_is_mit_2026():
    root = os.path.dirname(SOURCE_DIR)
    text = read(os.path.join(root, "LICENSE"))
    assert "MIT License" in text and "Copyright (c) 2026 hyeonjoong" in text


# ------------------------------------------------------------------ 변이 테스트로 드러난 빈틈 (라운드1 B2·B3·B4)

def test_role_margin_is_enforced(tmp_path):
    """1등과 2등 점수 차가 임계 미만이면 추측하지 않고 멈춘다.

    라운드1 변이 테스트에서 `MIN_MARGIN = 0` 이 449개 테스트를 전부 통과했다 —
    즉 '애매하면 멈춘다'는 이 툴의 핵심 안전장치가 고정되어 있지 않았다.
    """
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from conftest import write_md
    from irbpack.docread import read_document
    from irbpack.roles import MIN_MARGIN, detect_role

    # 파일명에 '동의서'(3점)와 '모집'(3점)이 함께 든 문서 — 동점이라 확정할 수 없다.
    path = write_md(tmp_path / "동의서_모집안내_v1.0.md", ["안내문", "본문"])
    role, reason = detect_role(read_document(path))
    assert role == "", "동점인데도 역할을 추측했습니다"
    assert "비슷" in reason or "약" in reason
    assert MIN_MARGIN >= 1


def test_blank_version_form_guard_is_needed_and_present(tmp_path):
    """'버전 v 1______' 처럼 숫자 뒤가 빈칸인 서식은 값이 아니다.

    라운드1 변이 테스트에서 이 가드를 지워도 전부 통과했다(정규식이 우연히 막고 있었다).
    """
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from conftest import write_md
    from irbpack.docread import read_document
    from irbpack.items import extract_all

    path = write_md(tmp_path / "CRF_통합_v1.0.md", ["증례기록서", "동의서 버전 v 1______"])
    versions = [mention.value for mention in extract_all(read_document(path))
                if mention.item == "version" and mention.facet == "버전"]
    assert versions == []


def test_safe_line_strips_control_characters():
    """summary 에는 원본 파일명이 그대로 들어가므로 safe_line 이 마지막 방어선이다 (라운드1 B4)."""
    from irbpack.safeio import safe_line
    assert "\n" not in safe_line("가\n[치명] 위조")
    assert "‮" not in safe_line("가‮나")

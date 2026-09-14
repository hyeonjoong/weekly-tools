"""강제장치 — 이 툴이 **하지 않기로 한 일**을 코드가 못 하게 막는다.

기획 단계에서 일부러 뺀 기능들이 나중에 슬그머니 들어오는 것을 막는
회귀 테스트다. 여기서 실패하면 기능이 늘어난 게 아니라 약속이 깨진 것이다.
"""

import ast
import os
import re
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "packlist")
SOURCES = sorted(os.path.join(PKG, n) for n in os.listdir(PKG) if n.endswith(".py"))
DOCS = ["README.md", "사용법.md", "실행.command"]


def parsed(path):
    with open(path, encoding="utf-8") as handle:
        return ast.parse(handle.read(), filename=path)


def imported_names(tree, full=False):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name if full else a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module if full else node.module.split(".")[0])
    return names


# ── 강제장치 1: 고치지 않는다 (감사 전용) ────────────────────────────
@pytest.mark.parametrize("path", SOURCES, ids=os.path.basename)
def test_no_shutil_import(path):
    assert "shutil" not in imported_names(parsed(path))


@pytest.mark.parametrize("path", SOURCES, ids=os.path.basename)
def test_zipfile_is_never_opened_for_writing(path):
    tree = parsed(path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = getattr(target, "attr", getattr(target, "id", ""))
        if name != "ZipFile":
            continue
        assert len(node.args) < 2, f"{path}: ZipFile 에 모드를 넘겼습니다"
        for keyword in node.keywords:
            assert keyword.arg != "mode", f"{path}: ZipFile(mode=...) 금지"


#: 이름만으로도 파괴적인 호출 (문자열 메서드와 겹치지 않는 것들)
_ALWAYS_BANNED = {"rmtree", "rmdir", "removedirs", "unlink"}
#: `os.`/`shutil.` 아래에서만 파괴적인 호출
_MODULE_BANNED = {"remove", "rename", "replace", "chmod", "truncate", "system", "link"}


@pytest.mark.parametrize("path", SOURCES, ids=os.path.basename)
def test_no_destructive_filesystem_calls(path):
    for node in ast.walk(parsed(path)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        assert attr not in _ALWAYS_BANNED, f"{path}: 파괴적 호출 {attr}"
        base = getattr(node.func.value, "id", "")
        if base in ("os", "shutil", "pathlib"):
            assert attr not in _MODULE_BANNED, f"{path}: 파괴적 호출 {base}.{attr}"


# ── 강제장치 2: 저널 규정을 코드에 박지 않는다 ──────────────────────
JOURNAL_TOKENS = ("npj", "elsevier", "springer", "wiley", "plos", "lancet",
                  "jama", "bmj", "frontiers", "mdpi", "editorial manager",
                  "scholarone", "nature portfolio", "ieee", "sage")


@pytest.mark.parametrize("token", JOURNAL_TOKENS)
def test_no_journal_names_in_source(token):
    pattern = re.compile(r"(?<![a-z])" + re.escape(token) + r"(?![a-z])", re.IGNORECASE)
    for path in SOURCES:
        with open(path, encoding="utf-8") as handle:
            assert not pattern.search(handle.read()), f"{path}: 저널명 '{token}'"


def test_no_hardcoded_size_limit_constants():
    """`MAX_TOTAL_MB = 50` 같은 상한이 코드에 생기면 즉시 실패한다."""
    pattern = re.compile(r"^[A-Z_]*(?:MAX|LIMIT)[A-Z_]*_?MB[A-Z_]*\s*=", re.MULTILINE)
    for path in SOURCES:
        with open(path, encoding="utf-8") as handle:
            assert not pattern.search(handle.read()), f"{path}: 용량 상한 리터럴"


# ── 강제장치 3: 이미지를 디코딩하지 않는다 (OCR 없음) ───────────────
IMAGE_LIBS = ("PIL", "Pillow", "cv2", "pytesseract", "tesserocr",
              "imageio", "skimage", "numpy", "matplotlib")


@pytest.mark.parametrize("path", SOURCES, ids=os.path.basename)
def test_no_image_or_ocr_imports(path):
    assert not (imported_names(parsed(path)) & set(IMAGE_LIBS))


# ── 네트워크 없음 ──────────────────────────────────────────────────
NETWORK_LIBS = ("socket", "http", "requests", "ftplib", "smtplib",
                "telnetlib", "asyncio", "ssl", "webbrowser")
#: `urllib` 전체가 아니라 네트워크를 여는 하위 모듈만 금지한다.
#: `urllib.parse.unquote` 는 순수 문자열 처리이고, 퍼센트 인코딩된
#: 관계 Target 을 풀려면 필요하다.
NETWORK_SUBMODULES = ("urllib.request", "urllib.error", "urllib.robotparser")


@pytest.mark.parametrize("path", SOURCES, ids=os.path.basename)
def test_no_network_imports(path):
    assert not (imported_names(parsed(path)) & set(NETWORK_LIBS))


@pytest.mark.parametrize("path", SOURCES, ids=os.path.basename)
def test_only_pure_urllib_parse_is_imported(path):
    full = imported_names(parsed(path), full=True)
    assert not (full & set(NETWORK_SUBMODULES))
    assert all(not m.startswith("urllib") or m == "urllib.parse" for m in full)


@pytest.mark.parametrize("path", SOURCES, ids=os.path.basename)
def test_no_subprocess_or_eval(path):
    names = imported_names(parsed(path))
    assert "subprocess" not in names and "os.system" not in names
    for node in ast.walk(parsed(path)):
        if isinstance(node, ast.Call):
            called = getattr(node.func, "id", "")
            assert called not in ("eval", "exec", "compile")


# ── 강제장치 4: 금지어 ─────────────────────────────────────────────
BANNED_WORDS = ("투고 가능", "규정 준수", "승인", "제출해도 됩니다", "완벽")


@pytest.mark.parametrize("word", BANNED_WORDS)
def test_banned_words_absent_from_sources(word):
    for path in SOURCES:
        with open(path, encoding="utf-8") as handle:
            assert word not in handle.read(), f"{path}: 금지어 '{word}'"


@pytest.mark.parametrize("word", BANNED_WORDS)
def test_banned_words_absent_from_docs(word):
    for name in DOCS:
        path = os.path.join(ROOT, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            assert word not in handle.read(), f"{name}: 금지어 '{word}'"


# ── 강제장치 5: 표제 비교 항목은 정확히 3개 ────────────────────────
def test_frontmatter_fields_are_exactly_three():
    from packlist.frontmatter import FRONTMATTER_FIELDS
    assert len(FRONTMATTER_FIELDS) == 3


def test_frontmatter_compare_returns_three_checks_per_document(envelope, builder):
    from packlist.docxpkg import read_docx
    from packlist.frontmatter import compare_frontmatter
    builder(envelope / "a.docx", paragraphs=["A long enough title for this test case", "본문"])
    builder(envelope / "b.docx", paragraphs=["다른 문서입니다 충분히 긴 첫 문단", "본문"])
    result = compare_frontmatter(read_docx(envelope / "a.docx", "a.docx"),
                                 [read_docx(envelope / "b.docx", "b.docx")])
    assert all(len(checks) == 3 for checks in result.checks.values())


# ── .gitignore 가 실제로 막는지 ────────────────────────────────────
GIT = subprocess.run(["which", "git"], capture_output=True).returncode == 0


@pytest.mark.skipif(not GIT, reason="git 이 없습니다")
@pytest.mark.parametrize("name", [
    "환자원고.docx", "IRB_동의서.pdf", "분석결과.xlsx", "발표.pptx",
    ".DS_Store", "봉투점검.md", "자산목록.csv", "그림1.png",
    "봉투점검_0911/봉투점검.md",
])
def test_gitignore_actually_blocks_real_data(tmp_path, name):
    import shutil as _shutil   # 테스트 전용 (패키지 소스에는 없다)
    repo = tmp_path / "scratch"
    repo.mkdir()
    _shutil.copy(os.path.join(ROOT, ".gitignore"), repo / ".gitignore")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x")
    result = subprocess.run(["git", "check-ignore", name], cwd=repo, capture_output=True)
    assert result.returncode == 0, f"{name} 이 .gitignore 로 막히지 않습니다"


@pytest.mark.skipif(not GIT, reason="git 이 없습니다")
@pytest.mark.parametrize("name", ["README.md", "packlist/cli.py", "tests/test_cli.py"])
def test_gitignore_does_not_block_source(tmp_path, name):
    import shutil as _shutil
    repo = tmp_path / "scratch"
    repo.mkdir()
    _shutil.copy(os.path.join(ROOT, ".gitignore"), repo / ".gitignore")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x")
    result = subprocess.run(["git", "check-ignore", name], cwd=repo, capture_output=True)
    assert result.returncode != 0, f"{name} 이 실수로 무시되고 있습니다"

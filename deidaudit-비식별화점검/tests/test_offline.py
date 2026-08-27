"""완전 오프라인·의존성 0 을 코드로 강제합니다."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "deidaudit"
PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

_FORBIDDEN_MODULES = {
    "requests", "urllib", "urllib3", "http", "httplib", "socket", "ftplib",
    "smtplib", "telnetlib", "asyncio", "aiohttp", "httpx",
    "pandas", "numpy", "scipy", "openpyxl", "xlrd", "matplotlib",
}

_STDLIB_ONLY_ALLOWED = {
    "argparse", "ast", "calendar", "collections", "csv", "dataclasses", "datetime",
    "decimal", "hashlib", "hmac", "io", "itertools", "json", "os", "pathlib", "re",
    "shutil", "stat", "string", "sys", "typing", "unicodedata", "zipfile", "xml",
    "__future__",
}


def _iter_imports():
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    yield path, alias.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # 상대 임포트
                    continue
                if node.module:
                    yield path, node.module.split(".")[0]


def test_no_network_or_heavy_dependency_imports():
    for path, module in _iter_imports():
        assert module not in _FORBIDDEN_MODULES, f"{path.name} 이 {module} 을 임포트합니다"


def test_only_standard_library_is_imported():
    for path, module in _iter_imports():
        assert module in _STDLIB_ONLY_ALLOWED, f"{path.name} 이 예상 밖 모듈 {module} 을 임포트합니다"


def test_pyproject_declares_no_dependencies():
    text = PYPROJECT.read_text(encoding="utf-8")
    assert "dependencies = []" in text


def test_no_source_file_opens_a_url():
    for path in sorted(PACKAGE.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "http://" not in text.replace("http://schemas.openxmlformats.org", "").replace(
            "http://purl.org", ""
        ).replace("http://www.w3.org", ""), path.name
        assert "urlopen" not in text

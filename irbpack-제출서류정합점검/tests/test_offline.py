"""외부 의존성 0 · 네트워크 0 — 정적으로 확인."""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "irbpack")
STDLIB_OK = {"__future__", "argparse", "collections", "csv", "dataclasses", "fnmatch", "io", "os", "re", "sys", "typing", "unicodedata", "zipfile", "zlib", "xml"}
NET = {"socket", "urllib", "http", "requests", "ssl", "ftplib", "smtplib", "subprocess"}


def _imports():
    for fn in os.listdir(PKG):
        if fn.endswith(".py"):
            tree = ast.parse(open(os.path.join(PKG, fn), encoding="utf-8").read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        yield fn, a.name.split(".")[0]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    yield fn, (node.module or "").split(".")[0]


def test_only_stdlib_imports():
    for fn, mod in _imports():
        assert mod in STDLIB_OK, (fn, mod)


def test_no_network_modules():
    for fn, mod in _imports():
        assert mod not in NET, (fn, mod)


def test_pyproject_has_no_dependencies():
    text = open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8").read()
    assert "dependencies = []" in text


def test_python_version_floor():
    assert sys.version_info >= (3, 9)

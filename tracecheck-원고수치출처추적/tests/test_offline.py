"""이 툴이 네트워크를 쓰지 않는다는 것을 **정적으로 증명**합니다.

임상 원고와 분석 산출물은 미공개 자료입니다. '안 보냅니다'라는 문장은 증거가
아니므로, 소스 전체를 AST 로 파싱해 네트워크 모듈이 import 되지 않는다는 것을
확인합니다. 새 모듈이 추가돼도 자동으로 검사됩니다.
"""

import ast
import os

import pytest

PACKAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "tracecheck")

FORBIDDEN = {
    "socket", "ssl", "http", "http.client", "urllib", "urllib.request",
    "urllib.parse", "ftplib", "smtplib", "telnetlib", "requests", "httpx",
    "asyncio", "xmlrpc", "webbrowser", "subprocess", "multiprocessing",
    "ctypes", "pickle", "shelve", "marshal",
}


def source_files():
    for name in sorted(os.listdir(PACKAGE)):
        if name.endswith(".py"):
            yield os.path.join(PACKAGE, name)


@pytest.mark.parametrize("path", list(source_files()),
                         ids=lambda p: os.path.basename(p))
def test_no_network_or_process_imports(path):
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module)
            imported.add(node.module.split(".")[0])
    banned = imported & FORBIDDEN
    assert not banned, "%s 가 금지된 모듈을 import 합니다: %s" % (path, banned)


def test_no_dynamic_import_or_eval():
    """`__import__`/`eval`/`exec` 로 우회하는 경로도 없어야 합니다."""
    for path in source_files():
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ("eval", "exec", "__import__", "compile"), \
                    "%s 에 동적 실행 호출이 있습니다" % path


def test_package_has_no_external_dependencies():
    """pyproject 의 dependencies 가 비어 있어야 합니다."""
    root = os.path.dirname(PACKAGE)
    with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as handle:
        text = handle.read()
    assert "dependencies = []" in text

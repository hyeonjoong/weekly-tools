# -*- coding: utf-8 -*-
"""수정 금지 강제 — 소스를 AST로 읽어 '고칠 수 있는 코드' 자체가 없음을 확인한다.

문서로 약속하는 대신 **구조로 막는다.** 이 툴은 어느 경로로도 입력을 고칠 수 없어야 한다.
"""

import ast
import os
import re

import pytest

PKG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "stalecheck")
MODULES = sorted(f for f in os.listdir(PKG_DIR) if f.endswith(".py"))
#: 유일하게 쓰기가 허용된 모듈
WRITER = "writer.py"

#: 파일을 지우거나 옮기거나 덮어쓰는 os 함수들
MUTATING_OS_CALLS = {
    "remove", "unlink", "rmdir", "removedirs", "rename", "renames", "replace",
    "truncate", "chmod", "chown", "utime", "link", "symlink", "mkfifo", "mknod",
    # 아래는 "우회로" — 이 중 하나만 있어도 위의 금지가 무의미해진다.
    "system", "popen", "execv", "execve", "execl", "execlp", "execvp",
    "spawnv", "spawnve", "spawnl", "fork", "forkpty", "posix_spawn",
}

#: 간접 호출로 import 금지를 빠져나가는 길. `__import__("shutil").rmtree(...)` 한 줄이면
#: 위의 모든 규칙이 무력해진다.
INDIRECT_IMPORT_NAMES = {"__import__", "importlib", "globals", "vars"}

FORBIDDEN_MODULES = {"shutil", "tempfile", "pathlib", "subprocess",
                     "socket", "urllib", "http", "ftplib", "smtplib",
                     "requests", "distutils"}


def parse(name):
    with open(os.path.join(PKG_DIR, name), encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=name), fh


def tree(name):
    with open(os.path.join(PKG_DIR, name), encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=name)


def source(name):
    with open(os.path.join(PKG_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def test_module_set_is_exactly_what_we_audit():
    """감사 대상 모듈이 몰래 늘거나 줄면 규칙이 적용되는 범위가 달라진다."""
    assert set(MODULES) == {
        "__init__.py", "__main__.py", "cli.py", "engine.py", "errors.py",
        "normalize.py", "pairing.py", "report.py", "scanning.py",
        "siblings.py", "textdiff.py", "verdicts.py", "writer.py",
    }
    assert "shutil" in FORBIDDEN_MODULES



@pytest.mark.parametrize("name", MODULES)
def test_no_forbidden_module_imports(name):
    for node in ast.walk(tree(name)):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module.split(".")[0]]
        for mod in names:
            assert mod not in FORBIDDEN_MODULES, "%s 가 %s 를 import 한다" % (name, mod)


def _os_aliases(t):
    """`import os as o` / `from os import remove` 같은 별칭을 찾아 둔다."""
    aliases = {"os"}
    direct = set()
    for node in ast.walk(t):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "os":
                    aliases.add(a.asname or "os")
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for a in node.names:
                direct.add(a.asname or a.name)
    return aliases, direct


@pytest.mark.parametrize("name", MODULES)
def test_no_mutating_os_calls(name):
    t = tree(name)
    aliases, direct = _os_aliases(t)
    for node in ast.walk(t):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id in aliases:
                assert func.attr not in MUTATING_OS_CALLS, \
                    "%s 가 %s.%s 를 호출한다" % (name, func.value.id, func.attr)
        elif isinstance(func, ast.Name):
            assert func.id not in (MUTATING_OS_CALLS & direct), \
                "%s 가 os.%s 를 직접 import 해 호출한다" % (name, func.id)


@pytest.mark.parametrize("name", MODULES)
def test_no_indirect_import(name):
    """`__import__("shutil")` 한 줄이면 import 금지 규칙 전체가 무의미해진다."""
    for node in ast.walk(tree(name)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in INDIRECT_IMPORT_NAMES, \
                "%s 가 %s 를 호출한다" % (name, node.func.id)


def test_no_getattr_on_module_objects():
    """`getattr(os, "remove")(p)` 로 호출 검사를 우회하지 못하게 한다.

    모듈별로 쪼개면 대부분의 모듈에서 루프가 비어 **늘 통과**한다. 한 번에 훑고,
    검사할 대상이 실제로 있었는지까지 확인해 규칙이 죽지 않게 한다.
    """
    inspected = 0
    for name in MODULES:
        t = tree(name)
        aliases, _ = _os_aliases(t)
        for node in ast.walk(t):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "getattr" and node.args
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id in aliases):
                inspected += 1
                assert (len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
                        and str(node.args[1].value).startswith("O_")), \
                    "%s 가 getattr 로 os 속성을 동적으로 가져온다" % name
    assert inspected, "검사할 getattr(os, ...) 호출이 없습니다 — 규칙이 죽었습니다"


def test_no_builtin_open_outside_writer():
    """writer.py 밖에서는 내장 `open()` 을 아예 쓰지 않는다.

    파일 읽기는 전부 `normalize._open_regular` 을 거친다 — 그래야 FIFO 로
    바꿔치기된 경로에서 멈추지 않는다. 그러므로 `open(` 이 **하나라도** 나오면
    누군가 그 관문을 우회한 것이다(모드가 읽기여도 마찬가지).
    """
    offenders = []
    for name in MODULES:
        if name == WRITER:
            continue
        for node in ast.walk(tree(name)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "open"):
                offenders.append(name)
    assert offenders == [], (
        "%s 가 내장 open() 을 직접 쓴다 — normalize._open_regular 을 쓰세요" % offenders)


def test_file_reads_go_through_open_regular():
    """관문이 실제로 존재하고 쓰이는지 확인한다 — 위 테스트가 빈 통과가 되지 않게."""
    users = [name for name in MODULES
             if "_open_regular" in source(name) and name != "normalize.py"]
    assert "textdiff.py" in users
    assert "_open_regular" in source("normalize.py")


#: `os.open` 을 써도 되는 모듈과, 거기서 허용되는 플래그.
#: normalize 는 FIFO 로 바꿔치기된 경로에 멈추지 않으려고 O_RDONLY 로만 연다.
#: cli.py 는 **os.devnull 에만** 쓰기로 연다 — 끊긴 fd 를 무해하게 만들어
#: 종료코드가 120 으로 덮어써지지 않게 하려는 것이고, 대상 경로는 아래에서 확인한다.
_OS_OPEN_ALLOWED = {WRITER: None,
                    "normalize.py": {"O_RDONLY", "O_NONBLOCK"},
                    "cli.py": {"O_WRONLY"}}


def test_os_open_flags_are_read_only_outside_writer():
    """os.open 의 **플래그를 실제로 본다** — 이름만 검사하면 쓰기를 놓친다."""
    inspected = 0
    for name in MODULES:
        allowed = _OS_OPEN_ALLOWED.get(name, set())
        for node in ast.walk(tree(name)):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "open"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"):
                continue
            inspected += 1
            if allowed is None:
                continue  # writer.py 는 쓰기가 본업이다
            flags = {n.attr for n in ast.walk(node.args[1])
                     if isinstance(n, ast.Attribute)} if len(node.args) > 1 else set()
            assert flags, "%s: os.open 플래그를 정적으로 확인할 수 없습니다" % name
            assert flags <= allowed, "%s: 허용되지 않은 os.open 플래그 %s" % (name, flags)
            if name == "cli.py":
                target = node.args[0]
                assert (isinstance(target, ast.Attribute)
                        and target.attr == "devnull"), \
                    "cli.py 는 os.devnull 에만 쓰기로 열 수 있습니다"
    assert inspected >= 3, "검사할 os.open 호출이 없습니다 — 규칙이 죽었습니다"


@pytest.mark.parametrize("name", [m for m in MODULES if m != WRITER])
def test_no_makedirs_outside_writer(name):
    assert "makedirs" not in source(name), name


def test_writer_only_opens_under_out_dir():
    """writer.py 의 모든 os.open 은 _open_artifact 를 거친다."""
    t = tree(WRITER)
    opens = []
    for fn in ast.walk(t):
        if isinstance(fn, ast.FunctionDef):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and node.func.attr == "open" \
                        and isinstance(node.func.value, ast.Name) \
                        and node.func.value.id == "os":
                    opens.append(fn.name)
    assert opens == ["_open_verified_dir", "_open_artifact"], opens


def test_writer_public_functions_take_out_dir_first():
    t = tree(WRITER)
    for node in t.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("write_"):
            assert node.args.args[0].arg == "out_dir", node.name


def test_open_artifact_checks_symlink_and_nlink():
    src = source(WRITER)
    assert "os.path.islink(target)" in src
    assert "st_nlink > 1" in src
    assert "O_NOFOLLOW" in src
    # FIFO 에서 멈추지 않으려면 O_NONBLOCK 이 필요하고,
    # 검증한 폴더에 쓰려면 dir_fd 가 필요하다.
    assert "O_NONBLOCK" in src
    assert "dir_fd=dir_fd" in src


@pytest.mark.parametrize("name", MODULES)
def test_no_network_usage(name):
    src = source(name)
    for needle in ("socket", "urlopen", "http://", "https://", "requests.",
                   "urllib"):
        assert needle not in src, "%s 에 네트워크 흔적(%s)" % (name, needle)


@pytest.mark.parametrize("name", MODULES)
def test_no_eval_or_exec(name):
    for node in ast.walk(tree(name)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ("eval", "exec", "compile"), name


@pytest.mark.parametrize("name", MODULES)
def test_every_module_has_a_docstring(name):
    assert ast.get_docstring(tree(name)), name


@pytest.mark.parametrize("name", MODULES)
def test_public_functions_documented(name):
    t = tree(name)
    for node in t.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            assert ast.get_docstring(node) or len(node.body) <= 2, \
                "%s.%s 에 docstring 이 없다" % (name, node.name)


def test_walk_never_follows_symlinks():
    for name in MODULES:
        for node in ast.walk(tree(name)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "walk":
                kwargs = {kw.arg: kw.value for kw in node.keywords}
                assert "followlinks" in kwargs, "%s 의 os.walk 에 followlinks 가 없다" % name
                assert kwargs["followlinks"].value is False, name


# ---------------------------------------------------------------- 금지어
#: 이 툴은 봉투가 최신이라고 보증하지 않는다 — 안심시키는 말을 쓰지 않는다.
FORBIDDEN_PHRASES = ["최신입니다", "제출 가능", "동기화 완료", "문제 없음",
                     "safe to submit", "이상 없음", "모두 일치합니다"]

TEXT_FILES = ["README.md", "사용법.md", "실행.command", "HARDENING.md"]


def _repo(name):
    path = os.path.join(os.path.dirname(PKG_DIR), name)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


@pytest.mark.parametrize("phrase", FORBIDDEN_PHRASES)
def test_forbidden_phrase_absent_from_source(phrase):
    for name in MODULES:
        assert phrase not in source(name), "%s 에 '%s'" % (name, phrase)


@pytest.mark.parametrize("phrase", FORBIDDEN_PHRASES)
def test_forbidden_phrase_only_in_negation_in_docs(phrase):
    """문서에 나와도 좋지만, '…라고 말하지 않습니다' 같은 부정문 안이어야 한다."""
    negations = ("않습니다", "않는다", "않음", "아닙니다", "말하지", "보증하지",
                 "never", "not ", "금지")
    for name in TEXT_FILES:
        text = _repo(name)   # 파일이 없으면 조용히 넘기지 않고 실패한다
        for line in text.splitlines():
            if phrase in line:
                assert any(n in line for n in negations), \
                    "%s: '%s' 가 부정문 밖에 있다 — %s" % (name, phrase, line.strip())


def test_report_never_emits_a_reassurance():
    from stalecheck import report
    src = source("report.py")
    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in src
    assert "보증하지 않습니다" in src


def test_no_todo_or_fixme_left():
    for name in MODULES:
        src = source(name)
        assert "TODO" not in src and "FIXME" not in src, name


def test_no_bare_except():
    for name in MODULES:
        for node in ast.walk(tree(name)):
            if isinstance(node, ast.ExceptHandler):
                assert node.type is not None, "%s 에 bare except" % name


def test_no_pass_only_function_bodies():
    for name in MODULES:
        for node in ast.walk(tree(name)):
            if isinstance(node, ast.FunctionDef):
                body = [n for n in node.body if not isinstance(n, ast.Expr)]
                assert not (len(body) == 1 and isinstance(body[0], ast.Pass)), \
                    "%s.%s 가 비어 있다" % (name, node.name)

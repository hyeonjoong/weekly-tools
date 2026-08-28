"""경계를 지키는 강제 장치 — 소스 자체를 훑어 확인합니다.

기획서의 다섯 가지 강제 장치 중 코드로 못 박을 수 있는 것들입니다.
사람이 나중에 "잠깐만 추가"하는 것을 막는 것이 목적이라, 런타임 동작이 아니라
**소스 텍스트**를 검사합니다.
"""
from __future__ import annotations

import ast
import os
import re

import pytest

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stimaudit")
ROOT = os.path.dirname(PKG)


def _sources():
    for name in sorted(os.listdir(PKG)):
        if name.endswith(".py"):
            with open(os.path.join(PKG, name), encoding="utf-8") as fh:
                yield name, fh.read()


def _code_only(text):
    """문자열·주석·독스트링을 뺀 '실행되는 코드'만 남깁니다.

    설명문에는 asper/acum 이 당연히 나옵니다(무엇을 안 하는지 적어야 하니까).
    금지해야 하는 것은 **계산하는 코드**입니다.
    """
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            node.value = ""
    return ast.unparse(tree)


# ------------------------------------- 강제 장치 ② 심리음향량 자체 계산 금지

FORBIDDEN_IDENTIFIERS = ("asper", "acum", "iso532", "iso_532", "roughness_dw",
                         "sharpness_din", "zwicker", "bark_", "loudness_iso")


@pytest.mark.parametrize("name,text", list(_sources()))
def test_no_psychoacoustic_computation_in_code(name, text):
    """러프니스·샤프니스·ISO 532 라우드니스를 계산하는 코드가 없어야 합니다.

    프록시로 흉내 내는 순간 이 툴은 DEBUSSY 의 열등한 사본이 됩니다.
    (`manifest.py` 는 DEBUSSY 가 뽑은 값을 **받아 쓰기만** 하므로,
     열 이름이 데이터로 흘러갈 뿐 식별자로 등장하지 않습니다.)
    """
    code = _code_only(text).lower()
    for bad in FORBIDDEN_IDENTIFIERS:
        assert bad not in code, "{}: {}".format(name, bad)


def test_no_function_named_after_a_psychoacoustic_quantity():
    for name, text in _sources():
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                low = node.name.lower()
                for bad in FORBIDDEN_IDENTIFIERS:
                    assert bad not in low, "{}: {}".format(name, node.name)


# ------------------------------- 강제 장치 ③⑤ 파일별 티어 준수 판정을 하지 않음

@pytest.mark.parametrize("name,text", list(_sources()))
def test_no_tier_compliance_flag(name, text):
    # 코드만 봅니다 — `refs.py` 의 설명문은 사내 기존 스크립트가 무엇을 하는지
    # **인용**하면서 그 이름을 적어야 하기 때문입니다.
    low = _code_only(text).lower()
    for bad in ("tier1_compliant", "tier2_compliant", "tier_compliant", "is_compliant"):
        assert bad not in low, "{}: {}".format(name, bad)


def test_severity_constants_are_only_three():
    from stimaudit import findings
    assert {findings.CRITICAL, findings.WARNING, findings.INFO} == {"치명", "경고", "정보"}


def test_critical_kinds_are_only_the_four_methodological_ones():
    """치명이 붙는 종류를 넷으로 못 박습니다 — 논문 수치는 여기 없습니다."""
    import inspect

    from stimaudit import setcheck
    from stimaudit import findings as F
    src = inspect.getsource(setcheck)
    criticals = set(re.findall(r"severity=F\.CRITICAL,\s*kind=(F\.\w+)", src))
    assert criticals == {"F." + n for n in
                         ("KIND_DEAD", "KIND_CLIPPING", "KIND_CLAIM_MISMATCH")}
    # 음량 불일치는 문턱에 따라 치명/경고가 갈리므로 별도 경로입니다.
    assert "sev = F.CRITICAL if d > crit else F.WARNING" in src


# ------------------------------------------------- 기존 툴 폴더를 건드리지 않음

SIBLING_TOOLS = ("eegband", "hrvkit", "calmbark", "statwise", "circadia",
                 "visitaudit", "debussy", "bell_acoustic_qc", "acoustic_params")


@pytest.mark.parametrize("name,text", list(_sources()))
def test_no_import_of_other_tools(name, text):
    tree = ast.parse(text)
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        for mod in mods:
            for sib in SIBLING_TOOLS:
                assert sib not in mod.lower(), "{}: {}".format(name, mod)


# ------------------------------------------------------ 표준 라이브러리 전용

STDLIB_ONLY = {
    "argparse", "array", "ast", "cmath", "csv", "dataclasses", "errno", "io",
    "json", "math", "os", "re", "shutil", "struct", "subprocess", "sys", "tempfile",
    "time", "typing", "unicodedata", "wave", "__future__",
}


@pytest.mark.parametrize("name,text", list(_sources()))
def test_only_stdlib_and_own_package_imported(name, text):
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                assert top in STDLIB_ONLY, "{}: import {}".format(name, a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue                      # 패키지 내부 상대 import
            top = (node.module or "").split(".")[0]
            assert top in STDLIB_ONLY, "{}: from {}".format(name, node.module)


@pytest.mark.parametrize("name,text", list(_sources()))
def test_no_network_access(name, text):
    """임상 자료를 다루므로 절대 밖으로 나가지 않습니다."""
    code = _code_only(text).lower()
    for bad in ("socket", "urllib", "requests", "http.client", "smtplib", "ftplib"):
        assert bad not in code, "{}: {}".format(name, bad)


def test_pyproject_declares_no_dependencies():
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as fh:
        text = fh.read()
    assert re.search(r"^dependencies = \[\]\s*$", text, re.M)


# -------------------------------------------------- ffmpeg 은 디코드에만 쓰임

def test_ffmpeg_only_used_for_decoding():
    """분석은 한 줄도 ffmpeg 에 맡기지 않습니다 — 컨테이너를 여는 데만 씁니다."""
    for name, text in _sources():
        if "ffmpeg" in text.lower() and name != "decode.py":
            code = _code_only(text).lower()
            assert "ffmpeg" not in code, name
    from stimaudit import decode
    import inspect
    src = inspect.getsource(decode)
    assert "pcm_s24le" in src              # 디코드만, 필터 없음
    for filt in ("-af", "ebur128", "volumedetect", "loudnorm", "astats"):
        assert filt not in src, filt


def test_analysis_modules_never_open_a_file_for_writing():
    """원본은 읽기 전용입니다 — 분석 경로에 쓰기 모드 open 이 없어야 합니다.

    이전 판은 `'"rb"' in code` 를 escape hatch 로 썼는데, `_code_only` 가 모든
    문자열 상수를 비우므로 그 조건은 **영원히 거짓**이었습니다. 즉 검사가
    "open( 이 아예 없어야 한다"로 동작했고, 정당한 `open(p, "rb")` 를 추가하면
    엉뚱한 메시지로 실패했습니다. 이제 AST 로 open 의 **모드 인자**를 봅니다.
    """
    for name in ("analyze.py", "levels.py", "claims.py", "setcheck.py",
                 "manifest.py", "design.py", "wavread.py"):
        with open(os.path.join(PKG, name), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "open"):
                continue
            mode = "r"
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = str(kw.value.value)
            assert set(mode) & {"r"} and not (set(mode) & {"w", "a", "x", "+"}), \
                "{}: open(mode={!r})".format(name, mode)


def test_only_safeio_and_the_example_generator_create_files():
    """파일을 만드는 곳은 `safeio` 와 `wavread.write_wav` 뿐이어야 합니다."""
    writers = []
    for name, text in _sources():
        if name == "safeio.py":
            continue
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("mkdir", "makedirs", "mkstemp", "mktemp"):
                    writers.append("{}: {}".format(name, node.func.attr))
    assert writers == [], writers


def test_write_wav_is_not_called_by_the_cli():
    """`write_wav` 는 예제 생성 전용이고 CLI 경로에서 불리지 않습니다."""
    for name, text in _sources():
        if name in ("wavread.py",):
            continue
        assert "write_wav" not in _code_only(text), name

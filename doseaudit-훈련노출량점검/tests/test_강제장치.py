"""강제장치 4종 — "안 한다"고 적은 것을 **코드가 할 수 없게** 못 박는다.

README 의 약속은 문장이라 언제든 어겨질 수 있다. 여기 있는 네 가지는 소스를
AST 로 읽고 산출물을 grep 해서, 약속이 깨지면 테스트가 먼저 실패하게 만든다.
"""

import ast
import os
import re

import pytest

from doseaudit.artifacts import write_all
from doseaudit.audit import Config, run_audit
from doseaudit.report import render_console
from doseaudit.rules import ActiveDayRule, Window

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "doseaudit")

#: 산출물을 쓰는 것이 **직업인** 모듈. 여기 밖에서는 쓰기가 일어나면 안 된다.
WRITER_MODULES = {"paths.py", "csvout.py", "artifacts.py", "cli.py"}


def package_files():
    return sorted(f for f in os.listdir(PKG) if f.endswith(".py"))


def parse(filename):
    with open(os.path.join(PKG, filename), encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename)


def source(filename):
    with open(os.path.join(PKG, filename), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture
def result(flawed_logs, flawed_tracker):
    return run_audit(Config(
        logs=flawed_logs, tracker_path=flawed_tracker,
        rule=ActiveDayRule.parse("any-row"),
        window=Window.parse("enroll-to-cut", "2026-04-27"),
        enroll_source=None, cap=None, target_per_week=3.0,
        pool_types=False, criterion=None, out_dir=None))


@pytest.fixture
def artifact_texts(result, tmp_path):
    out = str(tmp_path / "out")
    os.makedirs(out)
    names = write_all(result, out, render_console(result))
    texts = {}
    for name in names:
        with open(os.path.join(out, name), encoding="utf-8-sig") as fh:
            texts[name] = fh.read()
    texts["<콘솔>"] = "\n".join(render_console(result))
    return texts


# ── 강제장치 ① 입력을 고치지 않는다 ─────────────────────────────────────────
@pytest.mark.parametrize("filename", [f for f in os.listdir(PKG) if f.endswith(".py")])
def test_읽기전용_모듈에_쓰기_호출이_없다(filename):
    """`open(..., 'w')` 는 산출물 모듈에만 있어야 한다."""
    if filename in WRITER_MODULES:
        pytest.skip("산출물을 쓰는 것이 직업인 모듈")
    tree = parse(filename)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # `open(...)` 만 보면 안 된다. `io.open(p,"w")` 와 `pathlib.Path(p).write_text()`
        # 는 이름이 달라 그대로 빠져나가고, 실제로 입력 워크북을 0바이트로 만들 수 있다.
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        else:
            continue
        if name in ("write_text", "write_bytes"):
            raise AssertionError("%s: pathlib 쓰기 호출 %s()" % (filename, name))
        if name == "open":
            mode = node.args[1].value if len(node.args) > 1 and isinstance(
                node.args[1], ast.Constant) else None
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = kw.value.value
            assert mode in (None, "r", "rb", "rt"), "%s: open(mode=%r)" % (filename, mode)


#: `os.open` 의 쓰기 플래그. AST 에서 이름으로 잡는다.
_OS_WRITE_FLAGS = {"O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND"}


@pytest.mark.parametrize("filename", [f for f in os.listdir(PKG) if f.endswith(".py")])
def test_저수준_쓰기_경로도_산출물_모듈에만_있다(filename):
    """`os.open(..., O_TRUNC)` · `os.link` · `zipfile.ZipFile(p,"w")` 도 쓰기다.

    고수준 `open()` 만 막으면, 같은 일을 하는 다른 이름들이 전부 통과한다 —
    적대 리뷰가 실제로 이 구멍으로 입력 워크북을 truncate 해 보였다.
    """
    if filename in WRITER_MODULES:
        pytest.skip("산출물을 쓰는 것이 직업인 모듈")
    tree = parse(filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _OS_WRITE_FLAGS:
            raise AssertionError("%s: os.%s 사용" % (filename, node.attr))
        if isinstance(node, ast.Attribute) and node.attr in ("link", "symlink", "utime", "mkfifo"):
            if isinstance(node.value, ast.Name) and node.value.id == "os":
                raise AssertionError("%s: os.%s 사용" % (filename, node.attr))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "ZipFile":
            mode = node.args[1].value if len(node.args) > 1 and isinstance(
                node.args[1], ast.Constant) else None
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = kw.value.value
            assert mode in (None, "r"), "%s: ZipFile(mode=%r)" % (filename, mode)


@pytest.mark.parametrize("filename", [f for f in os.listdir(PKG) if f.endswith(".py")])
def test_패키지_어디에도_삭제_이동_함수가_없다(filename):
    """중복 제거·결측 대체·트래커 수정본 생성 — 전부 하지 않는다."""
    banned = {"remove", "unlink", "rmdir", "removedirs", "rename", "replace",
              "truncate", "rmtree", "move", "copyfile", "copy2", "chmod", "chown"}
    tree = parse(filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in banned:
            # `str.replace` 는 문자열 메서드라 허용 — 모듈 호출만 잡는다.
            if node.attr == "replace" and not _is_os_or_shutil(node.value):
                continue
            assert not _is_os_or_shutil(node.value), "%s: %s" % (filename, node.attr)


def _is_os_or_shutil(node):
    return isinstance(node, ast.Name) and node.id in {"os", "shutil"}


@pytest.mark.parametrize("filename", [f for f in os.listdir(PKG) if f.endswith(".py")])
def test_shutil_을_import_하지_않는다(filename):
    for node in ast.walk(parse(filename)):
        if isinstance(node, ast.Import):
            assert all(a.name != "shutil" for a in node.names), filename
        if isinstance(node, ast.ImportFrom):
            assert node.module != "shutil", filename


def test_입력_파일이_실행_후에도_그대로다(flawed_logs, flawed_tracker, tmp_path):
    def snapshot(folder):
        out = {}
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                with open(path, "rb") as fh:
                    out[name] = (os.path.getmtime(path), fh.read())
        return out

    before_logs = snapshot(flawed_logs)
    with open(flawed_tracker, "rb") as fh:
        before_tracker = fh.read()
    from doseaudit.cli import main
    main(["--logs", flawed_logs, "--tracker", flawed_tracker, "--active-day", "any-row",
          "--window", "enroll-to-cut", "--cut", "2026-04-27", "--target-per-week", "3",
          "--out-dir", str(tmp_path / "out")])
    assert snapshot(flawed_logs) == before_logs
    with open(flawed_tracker, "rb") as fh:
        assert fh.read() == before_tracker


# ── 강제장치 ② 순응/비순응을 판정하지 않는다 ────────────────────────────────
FORBIDDEN_WORDS = ("순응함", "비순응", "adherent", "non-adherent", "충분히 사용",
                   "충분히 사용했", "탈락함", "탈락했", "불량 순응", "compliant",
                   "non-compliant", "poor adherence", "good adherence")


#: "안 한다"를 적으려면 그 단어를 써야 한다. 그래서 금지어 검사는 **부정문을
#: 예외로 두되**, 예외가 되려면 부정 표지가 금지어에 **붙어 있어야** 한다.
NEGATION_MARKERS = ("않", "말하지", "아니", "없", "안 ", "not ", "never", "no ")

#: 부정 표지를 찾을 범위(금지어 뒤 글자 수). 줄 전체를 보면 안 된다 —
#: "…비순응으로 분류하였으며 예외는 없다." 처럼 **판정을 내리는 문장**이 줄 끝의
#: '없'  하나로 면제된다. 적대 리뷰가 실제로 이 문장을 Methods 초안에 통과시켰다.
NEGATION_WINDOW = 12


def _offending_lines(text, word):
    """금지어가 **부정 표지 없이** 등장하는 줄만 돌려준다.

    부정 표지는 금지어 바로 뒤(`NEGATION_WINDOW` 글자 안) 또는 바로 앞에 있어야
    면제된다. 같은 줄 아무 데나 있으면 되는 규칙은 면제가 아니라 구멍이다.
    """
    out = []
    lowered_word = word.lower()
    for line in text.splitlines():
        low = line.lower()
        start = 0
        while True:
            pos = low.find(lowered_word, start)
            if pos < 0:
                break
            near = low[max(0, pos - 6):pos + len(lowered_word) + NEGATION_WINDOW]
            if not any(m in near for m in NEGATION_MARKERS):
                out.append(line.strip())
                break
            start = pos + len(lowered_word)
    return out


@pytest.mark.parametrize("word", FORBIDDEN_WORDS)
@pytest.mark.parametrize("filename", [f for f in os.listdir(PKG) if f.endswith(".py")])
def test_소스에_판정_어휘가_부정문_밖에_없다(filename, word):
    assert _offending_lines(source(filename), word) == [], "%s: %s" % (filename, word)


@pytest.mark.parametrize("word", FORBIDDEN_WORDS)
def test_리포트에_판정_어휘가_부정문_밖에_없다(artifact_texts, word):
    for name, text in artifact_texts.items():
        assert _offending_lines(text, word) == [], "%s: %s" % (name, word)


@pytest.mark.parametrize("word", FORBIDDEN_WORDS + ("권장", "추천", "바람직",
                                                    "recommended", "should use"))
def test_CSV_에는_판정_어휘가_단_한_번도_없다(artifact_texts, word):
    """CSV 는 피험자 한 명이 한 행인 **판정의 표면**이다 — 여기엔 예외가 없다."""
    for name, text in artifact_texts.items():
        if not name.endswith(".csv"):
            continue
        assert word.lower() not in text.lower(), "%s: %s" % (name, word)


@pytest.mark.parametrize("word", FORBIDDEN_WORDS)
def test_판정_문장_자체에_판정_어휘가_없다(result, word):
    """`[치명]`/`[경고]` 제목과 본문 — 사람이 결론으로 읽는 줄."""
    for finding in result.findings:
        assert word.lower() not in finding.title.lower(), finding.title
        for line in finding.lines:
            assert _offending_lines(line, word) == [], line


@pytest.mark.parametrize("word", ("권장", "추천", "바람직", "올바른 값", "정답은",
                                  "recommended", "should use"))
def test_리포트가_어느_정의도_권장하지_않는다(artifact_texts, word):
    for name, text in artifact_texts.items():
        assert _offending_lines(text, word) == [], "%s: %s" % (name, word)


def test_순응도_임계값이_코드에_박혀_있지_않다():
    """프로토콜 기준은 `--criterion` 으로 **받을 때만** 판정한다."""
    for filename in package_files():
        tree = parse(filename)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    name = getattr(target, "id", "")
                    assert not re.search(r"(?i)(threshold|criterion|cutoff|기준값)", name), \
                        "%s: %s" % (filename, name)


# ── 강제장치 ③ 효과를 보지 않는다 ───────────────────────────────────────────
STATS_NAMES = ("ttest", "t_test", "anova", "mannwhitney", "wilcoxon", "spearman",
               "pearson", "correlation", "corrcoef", "linregress", "regress",
               "pvalue", "p_value", "chisquare", "kruskal", "logrank", "cox")


@pytest.mark.parametrize("filename", [f for f in os.listdir(PKG) if f.endswith(".py")])
def test_검정_상관_함수가_정의돼_있지_않다(filename):
    for node in ast.walk(parse(filename)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lowered = node.name.lower().replace("-", "_")
            for banned in STATS_NAMES:
                assert banned not in lowered, "%s: def %s" % (filename, node.name)


@pytest.mark.parametrize("filename", [f for f in os.listdir(PKG) if f.endswith(".py")])
def test_검정_상관_함수를_호출하지도_않는다(filename):
    for node in ast.walk(parse(filename)):
        name = None
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                name = node.func.attr
            elif isinstance(node.func, ast.Name):
                name = node.func.id
        if name:
            lowered = name.lower()
            for banned in STATS_NAMES:
                assert banned not in lowered, "%s: %s()" % (filename, name)


@pytest.mark.parametrize("filename", [f for f in os.listdir(PKG) if f.endswith(".py")])
def test_통계_라이브러리를_import_하지_않는다(filename):
    """노출량 ↔ 결과의 관계는 `statwise`·`longistat`·`medpath` 의 일이다."""
    banned = {"scipy", "numpy", "pandas", "sklearn", "statsmodels", "matplotlib"}
    for node in ast.walk(parse(filename)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in banned, "%s: %s" % (filename, alias.name)
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in banned, "%s: %s" % (filename, node.module)


def test_statistics_는_기술통계에만_쓴다():
    """표준 라이브러리 `statistics` 에도 상관·회귀가 있다 — 그쪽은 쓰지 않는다.

    허용 목록은 **기술통계만** 담는다. `quantiles` 는 2026-09-17 적대 패널에서
    "범위만으로는 분포를 말할 수 없다(리뷰어가 묻는 자리)"는 지적을 받아
    의도적으로 추가했다 — `correlation`·`covariance`·`linear_regression` 은
    여전히 금지이고, 그것이 이 테스트가 지키는 선이다.
    """
    allowed = {"median", "fmean", "mean", "quantiles"}
    banned = {"correlation", "covariance", "linear_regression"}
    for filename in package_files():
        for node in ast.walk(parse(filename)):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id == "statistics"):
                assert node.attr not in banned, "%s: statistics.%s" % (filename, node.attr)
                assert node.attr in allowed, "%s: statistics.%s" % (filename, node.attr)
            # `statistics.<attr>` 만 보면 **별칭 import 가 통째로 빠져나간다**:
            # `from statistics import correlation as _assoc` 를 쓰면 호출부가
            # `_assoc(a, b)` 라 어느 금지 목록에도 걸리지 않는다. 적대 리뷰가
            # 이 구멍으로 상관계수 0.965 를 내는 공개 함수를 심어 보였다.
            if isinstance(node, ast.ImportFrom) and node.module == "statistics":
                for alias in node.names:
                    assert alias.name not in banned, \
                        "%s: from statistics import %s" % (filename, alias.name)
                    assert alias.name in allowed, \
                        "%s: from statistics import %s" % (filename, alias.name)


# ── 강제장치 ④ 이름과 Login ID 가 산출물에 0건 ──────────────────────────────
KOREAN_NAME_RE = re.compile(r"[가-힣]{2,4}\d*")
FAKE_NAMES = ("홍길동", "김철수", "이영희", "박민수", "최지훈", "정수아", "강하늘")
FAKE_LOGINS = ("echo", "foxtrot", "golf", "hotel", "india", "juliet", "kilo",
               "alpha", "bravo", "charlie", "delta")


@pytest.mark.parametrize("name", FAKE_NAMES)
def test_트래커의_실명이_산출물에_없다(artifact_texts, name):
    for artifact, text in artifact_texts.items():
        assert name not in text, "%s: %s" % (artifact, name)


@pytest.mark.parametrize("login", FAKE_LOGINS)
def test_파일명의_LoginID_가_산출물에_없다(artifact_texts, login):
    for artifact, text in artifact_texts.items():
        assert "_%s_" % login not in text, "%s: %s" % (artifact, login)
        assert "_%s." % login not in text, "%s: %s" % (artifact, login)


def test_트래커의_이름_열을_읽는_코드가_없다():
    """`환자명` 열은 이름으로 언급될 뿐, 값으로 읽히지 않는다."""
    text = source("tracker.py")
    assert "FORBIDDEN_PATTERNS" in text
    # 금지 열은 index 에 들어가기 전에 continue 로 빠진다
    assert "dropped.append(name)" in text
    assert "continue" in text


def test_D열_격자를_읽지_않는다():
    assert "_GRID_RE" in source("tracker.py")
    assert "grid += 1" in source("tracker.py")


def test_모든_평균은_Mean3_를_거친다():
    """단일 평균 하나만 내보내는 코드 경로가 없어야 한다."""
    text = source("dose.py")
    assert "class Mean3" in text
    for node in ast.walk(parse("dose.py")):
        if isinstance(node, (ast.FunctionDef,)):
            assert "mean" not in node.name.lower() or node.name.startswith("_"), node.name


def test_Mean3_밖에서_fmean_을_직접_쓰지_않는다():
    for filename in package_files():
        if filename == "dose.py":
            continue
        assert "fmean" not in source(filename), filename

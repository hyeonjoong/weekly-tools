"""문서에 적힌 명령과 숫자가 **실제 출력과 같은지** 자동으로 검사한다.

문서 정직성은 한 번 고치면 끝나는 문제가 아니다. 코드가 바뀌면 README의 예시 숫자가
조용히 거짓이 된다. 그래서 여기서

1. README.md · 사용법.md · 실행.command 안의 모든 `powerplan ...` 명령을 **긁어내서 실행**하고
2. 문서가 특정 숫자를 약속한 자리는 그 숫자가 **정말 출력에 나오는지** 확인한다.

새 예시를 문서에 넣으면 자동으로 1번의 검사 대상이 된다.
"""

from __future__ import annotations

import io
import re
import shlex
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from powerplan.cli import main

ROOT = Path(__file__).resolve().parent.parent
DOC_FILES = ("README.md", "사용법.md", "실행.command", "examples/README.md")

#: 문서에서 긁어낸 명령 중 실행하지 않을 것 — **자리표시자 파일명과 도움말뿐**이다.
#: 예전에는 "pilot.csv"가 들어 있어 `examples/wowfit_pilot.csv`까지 걸러졌고,
#: 그 바람에 문서의 pilot 예시 6개가 통째로 검증되지 않았다. 토큰은 정확히 일치하는
#: 인자로만 판정한다.
_SKIP_EXACT = frozenset({"내파일.csv", "pilot.csv", "data.csv", "표본수.md"})
_SKIP_FLAGS = ("--help", "-o")


def _subcommands() -> frozenset[str]:
    import argparse

    from powerplan.cli import _build_parser

    for action in _build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return frozenset(action.choices)
    raise AssertionError("하위 명령을 찾을 수 없습니다")  # pragma: no cover


SUBCOMMANDS = _subcommands()


def _extract_commands(text: str) -> list[str]:
    """`powerplan <설계> ...` 형태의 한 줄 명령을 모은다 (프롬프트·이스케이프 제거).

    코드 블록 안의 출력 예시(`powerplan — 두 독립군 …`)나 셸 함수 정의는 걸러야 하므로,
    두 번째 토큰이 **실제 하위 명령**인 줄만 인정한다.
    """
    found = []
    for raw in text.splitlines():
        line = raw.strip()
        # echo "…" 안에 들여쓰기된 명령도 잡는다 (실행.command의 안내 배너)
        echoed = re.match(r'^echo\s+"(.*)"\s*$', line)
        if echoed:
            line = echoed.group(1).strip()
        line = line.replace("\\$", "$")
        if line.startswith("$ "):
            line = line[2:].strip()
        if not line.startswith("powerplan "):
            continue
        line = line.split("#")[0].strip()
        try:
            parts = shlex.split(line)
        except ValueError:
            continue
        if len(parts) < 2 or parts[1] not in SUBCOMMANDS:
            continue
        if any(token in _SKIP_EXACT for token in parts):
            continue
        if any(token in _SKIP_FLAGS for token in parts):
            continue
        found.append(line)
    return found


def _all_documented_commands() -> list[tuple[str, str]]:
    out = []
    for name in DOC_FILES:
        path = ROOT / name
        if not path.exists():  # pragma: no cover - 파일이 사라지면 아래 테스트가 잡는다
            continue
        for cmd in _extract_commands(path.read_text(encoding="utf-8")):
            out.append((name, cmd))
    return out


DOCUMENTED = _all_documented_commands()


def _run(argv: list[str]) -> tuple[int, str]:
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        code = main(argv)
    return code, buf.getvalue() + err.getvalue()


def test_documentation_contains_commands():
    """문서에서 명령을 긁어내는 규칙이 여전히 작동하는지 (0개면 이 파일이 무의미하다)."""
    assert len(DOCUMENTED) >= 40
    files = {name for name, _ in DOCUMENTED}
    assert {"README.md", "사용법.md", "실행.command"} <= files
    # 예제 CSV를 쓰는 pilot 명령이 반드시 포함되어야 한다 (예전에 통째로 빠졌다)
    pilots = [c for _n, c in DOCUMENTED if c.split()[1] == "pilot"]
    assert len(pilots) >= 4, pilots
    # 하위 명령이 골고루 검증되는지
    covered = {c.split()[1] for _n, c in DOCUMENTED}
    assert len(covered) >= 12, sorted(covered)


@pytest.mark.parametrize("source, command", DOCUMENTED,
                         ids=[f"{n}:{c[:60]}" for n, c in DOCUMENTED])
def test_documented_command_runs(source, command):
    argv = shlex.split(command)[1:]        # 'powerplan' 제거
    code, out = _run(argv)
    assert code == 0, f"{source}의 예시가 실패했습니다: {command}\n{out}"
    assert out.strip()


#: 문서가 명시적으로 약속한 숫자 — 바뀌면 문서를 고쳐야 한다
PROMISED_NUMBERS = [
    ("ttest2 --d 0.5 --power 0.8 --dropout 0.15", ["군당 64명", "군당 76명", "80.1%"]),
    ("ttest2 --d 0.309 --power 0.8 --analysis ancova --baseline-r 0.711", ["군당 83명"]),
    ("ttest2 --d 0.309 --power 0.8", ["군당 166명"]),
    ("ttest2 --d 0.5 --n 30 --power 0.8", ["47.8%", "군당 64명"]),
    ("prop2 --p1 0.30 --p2 0.50 --power 0.8", ["군당 93명"]),
    ("corr --r 0.35 --power 0.8", ["62명"]),
    ("icc --icc 0.8 --width 0.15 --raters 2", ["90명"]),
    ("loa --sd-diff 2.0 --half-width 0.5", ["183명"]),
    ("ttest2 --d 0.4 --power 0.8", ["군당 100명"]),
    # 새로 추가한 설계들의 문서 약속
    ("repeated --d 0.4 --post 3 --baseline-n 1 --rho 0.6 --power 0.8",
     ["군당 65명", "설계배율 0.640", "520회", "마지막 방문 1회"]),
    ("repeated --d 0.4 --post 3 --rho 0.6 --power 0.8 --estimand average",
     ["군당 39명", "설계배율 0.373", "사후 3회 방문의 평균"]),
    ("repeated --d 0.4 --post 1 --baseline-n 0 --analysis post --rho 0 --power 0.8",
     ["군당 100명"]),
    ("repeated --d 0.4 --post 1 --baseline-n 1 --rho 0.6 --power 0.8", ["군당 65명"]),
    ("survival --hr 0.7 --median1 12 --accrual 18 --followup 12 --power 0.8",
     ["군당 198명", "247.9건", "68.9%", "56.3%"]),
    ("mcnemar --p01 0.05 --p10 0.15 --power 0.8", ["155명", "31.0쌍", "20.0%"]),
    ("noninf --margin 0.10 --p1 0.70 --p2 0.70 --power 0.8", ["군당 330명"]),
    ("ttest2 --d 0.5 --power 0.9 --interim 1 --spending pocock",
     ["군당 95명", "2.1570", "2.2010", "0.03101", "60.2%", "1.1110", "172명"]),
    # 무익성(futility) 경계 — README·사용법.md가 인용한 숫자를 전부 못 박는다
    ("ttest2 --d 0.5 --power 0.9 --interim 1 --spending pocock --futility obf",
     ["군당 97명", "0.3849", "65.0%", "63.1%", "1.1317", "0.0246",
      "39% (추세대로면 1%)", "90.0% → 89.5%", "0.5%p", "133명", "129명"]),
    ("ttest2 --d 0.5 --power 0.9 --interim 1 --spending pocock --futility pocock",
     ["군당 107명"]),
    ("pilot examples/wowfit_pilot.csv --pre 훈련전_단어인지도 --post 훈련후_단어인지도 "
     "--filter 군=중재 --power 0.8",
     ["쌍 n=11", "변화량 평균=10.3", "Cohen's dz = 1.5743", "Hedges g = 1.4527",
      "사전-사후 상관 r = 0.9395", "[0.6571, 2.4589]", "제외된 행 11개"]),
    ("pilot examples/serene_pilot.csv --value isi_week8 --group arm "
     "--baseline isi_baseline --power 0.8",
     ["Cohen's d = -0.3086", "Hedges g = -0.3008", "평균차 -1.616", "합동 SD 5.235",
      "r = 0.7106", "5.9%", "[-1.0047, 0.3926]", "신뢰구간이 0을 포함"]),
    ("ttest2 --d 0.4 --power 0.8 --cluster-size 10 --cluster-icc 0.05 --dropout 0.1",
     ["군당 145명", "군당 170명", "1군 17개, 2군 17개", "탈락 10%, 군집 단위 올림"]),
    # 반복사건 계수(count)·순서형(ordinal) — 문서가 인용한 숫자
    ("count --rate1 1.2 --rr 0.75 --dispersion 0.7 --power 0.9",
     ["군당 425명", "892.5건", "대조 510.0", "중재 382.5", "1.84배"]),
    ("count --rate1 1.2 --rr 0.75 --power 0.9", ["군당 247명"]),
    ("count --rate1 0.6 --rr 0.75 --dispersion 0.7 --exposure 2 --power 0.9",
     ["군당 425명"]),
    ("ordinal --probs 0.1,0.2,0.4,0.2,0.1 --or 1.8 --power 0.9",
     ["군당 198명", "0.9220", "0.4108", "0.167 / 0.269 / 0.372 / 0.134 / 0.058"]),
    # README가 인용한 네 절단점 — 반올림한 값이 아니라 정확한 누적확률로 계산했다
    ("prop2 --p1 0.1 --p2 0.16666666666666669 --power 0.9", ["군당 545명"]),
    ("prop2 --p1 0.3 --p2 0.43548387096774194 --power 0.9", ["군당 265명"]),
    ("prop2 --p1 0.7 --p2 0.80769230769230771 --power 0.9", ["군당 335명"]),
    ("prop2 --p1 0.9 --p2 0.94186046511627908 --power 0.9", ["군당 872명"]),
    ("count --rate1 1.2 --rr 0.75 --dispersion 0.7 --power 0.9 --ratio 2 --variance null",
     ["총 957명", "커집니다"]),
    ("ordinal --probs 0.1,0.2,0.4,0.2,0.1 --probs2 0.16,0.27,0.37,0.14,0.06 --power 0.9",
     ["절단점별 오즈비", "기하평균"]),
]


@pytest.mark.parametrize("command, expected", PROMISED_NUMBERS,
                         ids=[c[:55] for c, _ in PROMISED_NUMBERS])
def test_documented_numbers_are_still_true(command, expected):
    code, out = _run(shlex.split(command))
    assert code == 0, out
    normalised = out.replace("\n", " ")
    normalised = re.sub(r"\s+", " ", normalised)
    for token in expected:
        assert token in normalised, (
            f"문서가 약속한 '{token}'이 실제 출력에 없습니다: powerplan {command}\n{out}")


def test_readme_test_count_matches_reality():
    """README가 밝힌 테스트 개수가 실제 수집 개수와 맞는지 (±5% 허용)."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"(\d+)개 테스트", readme)
    assert match, "README에 테스트 개수 표기가 사라졌습니다"
    claimed = int(match.group(1))
    collected = _collect_count()
    if collected is None:  # pragma: no cover - pytest를 못 띄우는 환경
        pytest.skip("테스트 수집을 실행할 수 없는 환경입니다")
    assert abs(collected - claimed) <= max(5, claimed * 0.05), (
        f"README는 {claimed}개라 하는데 실제 수집은 {collected}개입니다 — README를 고치세요")


def _collect_count() -> int | None:
    """`pytest --collect-only`를 별도 프로세스로 돌려 실제 수집 개수를 센다."""
    import subprocess
    import sys

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q",
             "-p", "no:cacheprovider"],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    match = re.search(r"(\d+) tests? collected", proc.stdout)
    return int(match.group(1)) if match else None


def test_design_table_lists_every_subcommand():
    """README 표에 없는 하위 명령이 있으면 사용자가 그 기능을 발견할 수 없다."""
    from powerplan.cli import _build_parser

    parser = _build_parser()
    subcommands = set()
    for action in parser._actions:
        if isinstance(action, __import__("argparse")._SubParsersAction):
            subcommands = set(action.choices)
    assert subcommands
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    table = readme.split("## 지원하는 설계", 1)[1].split("## 사용법", 1)[0]
    missing = [key for key in subcommands if f"`{key}`" not in table]
    assert not missing, f"README 설계 표에 없는 하위 명령: {missing}"


def test_usage_doc_lists_every_subcommand():
    doc = (ROOT / "사용법.md").read_text(encoding="utf-8")
    from powerplan.cli import _build_parser

    parser = _build_parser()
    for action in parser._actions:
        if isinstance(action, __import__("argparse")._SubParsersAction):
            for key in action.choices:
                assert f"`{key}`" in doc, f"사용법.md에 {key} 설명이 없습니다"

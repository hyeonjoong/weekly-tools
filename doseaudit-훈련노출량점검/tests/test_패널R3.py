"""적대 패널 3라운드가 찾은 결함들의 회귀 테스트 (2026-09-17).

3라운드는 네 명(정확성·가장자리·문서정직성·안전/테스트품질)을 병렬로 붙였고,
**1,037개가 전부 초록인 상태에서** 아래 결함들이 숨어 있었다. 경위는
`HARDENING.md` 3라운드 절에 있다.

여기 있는 테스트는 전부 한 번 실제로 깨졌던 것이다.
"""

import datetime
import os
import subprocess
import sys

import pytest

from doseaudit.audit import LEVEL_CRITICAL, LEVEL_WARN, Config, run_audit
from doseaudit.dose import (REASON_NO_ACTIVITY, TYPE_BLANK, Mean3,
                            check_vendor_summaries, longest_gap_days)
from doseaudit.errors import EXIT_REFUSE
from doseaudit.logs import load_bundle
from doseaudit.rules import MAX_WEEKS, ActiveDayRule, Window
from doseaudit.tracker import load_tracker
from xlsxwrite import MODULES, module_sheet, write_xlsx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HEADER = ["날짜", "레벨", "훈련유형", "반복횟수", "소요시간(초)"]


def sheet(rows, mean_sec="5초", mean_rep="1회", summary=True):
    """`[요약]` 블록 + 헤더 + 데이터행 — 벤더 워크북과 같은 모양."""
    out = []
    if summary:
        out += [["[요약]"], ["평균 소요시간", mean_sec], ["평균 반복횟수", mean_rep], None]
    out.append(list(HEADER))
    out += [list(r) for r in rows]
    return out


def workbook(path, sheets):
    """지정한 시트 + 나머지 모듈은 빈 시트로 채운 워크북."""
    named = dict(sheets)
    pages = []
    for module in MODULES:
        pages.append((module, named.pop(module) if module in named else module_sheet([], 0, 0)))
    pages.extend(named.items())
    write_xlsx(path, pages)


def run_cli(args, timeout=30):
    return subprocess.run([sys.executable, "-m", "doseaudit"] + args,
                          cwd=ROOT, capture_output=True, text=True, timeout=timeout)


def audit(logs, tracker=None, rule="any-row", window="fixed-weeks=8", cut=None,
          target=3.0, cap=None, criterion=None, enroll=None):
    return run_audit(Config(
        logs=logs, tracker_path=tracker, rule=ActiveDayRule.parse(rule),
        window=Window.parse(window, cut), enroll_source=enroll, cap=cap,
        target_per_week=target, pool_types=False, criterion=criterion, out_dir=None))


# ══ 가장자리 감사 R3-1 · 시트 이름 중복 해소가 **영원히 돌던** 문제 ═════════
def test_잘린_시트이름이_겹쳐도_멈춘다(tmp_path):
    """`while name in modules: name = "%s~%d" % (name[:57], len(modules))`.

    `len(modules)` 가 루프 안에서 변하지 않아, 만들어낸 후보가 이미 키이면
    같은 문자열을 영원히 다시 만들었다. **출력도 종료코드도 없이 100% CPU.**
    멈추지 않는 툴은 틀린 답보다 나쁘다 — 빈 답조차 주지 않는다.
    """
    folder = tmp_path / "logs"
    folder.mkdir()
    rows = [["2026.03.02", "1", "REGULAR", "1회", "5초"]]
    names = ["A" * 59 + "…", "A" * 57 + "~2", "A" * 59 + "B" * 16]
    write_xlsx(str(folder / "AB12CD34_x_1.xlsx"), [(n, sheet(rows)) for n in names])
    bundle = load_bundle(str(folder))          # 예전엔 여기서 영원히 돌았다
    assert len(bundle.subjects) == 1
    # 세 시트가 **서로 다른 키**로 들어가야 한다 — 덮어쓰면 행이 조용히 사라진다.
    assert len(bundle.subjects[0].modules) == 3


def test_잘린_시트이름_충돌이_inspect_도_멈추지_않는다(tmp_path):
    """`--inspect` 는 이 툴이 **가장 먼저 실행하라고 안내하는** 화면이다."""
    folder = tmp_path / "logs"
    folder.mkdir()
    rows = [["2026.03.02", "1", "REGULAR", "1회", "5초"]]
    names = ["B" * 59 + "…", "B" * 57 + "~2", "B" * 59 + "C" * 16]
    write_xlsx(str(folder / "AB12CD34_x_1.xlsx"), [(n, sheet(rows)) for n in names])
    done = run_cli(["--inspect", "--logs", str(folder)], timeout=30)
    assert done.returncode == 0


# ══ 가장자리 감사 R3-2/3 · `date` 범위를 넘겨 OverflowError 로 죽던 문제 ════
def test_먼_미래_가입일에_고정주수를_더해도_죽지_않는다(tmp_path):
    """손으로 관리한 트래커는 '진행 중'을 `9999-12-31` 로 적는 일이 있다."""
    folder = tmp_path / "logs"
    folder.mkdir()
    workbook(str(folder / "AB12CD34_x_1.xlsx"),
             [(MODULES[0], sheet([["9999.12.31", "1", "REGULAR", "1회", "5초"]]))])
    result = audit(str(folder), window="fixed-weeks=8")
    exposure = result.exposures[0]
    # 창을 만들 수 없다 → **창이 없다**로 떨어지고, 이미 있는 정직한 경고 경로를 탄다.
    assert exposure.window_known is False
    assert exposure.adherence_pct is None


@pytest.mark.parametrize("weeks", ["999999", "1000000000"])
def test_너무_긴_고정주수는_판정하지_않고_거절한다(weeks):
    done = run_cli(["--logs", "examples/clean/logs", "--active-day", "any-row",
                    "--window", "fixed-weeks=%s" % weeks, "--no-files"])
    assert done.returncode == EXIT_REFUSE
    assert "Traceback" not in (done.stdout + done.stderr)


def test_최대_주수_경계():
    """경계 자체는 받아들인다 — 거절선이 실제 사용 범위를 자르면 안 된다."""
    assert Window.parse("fixed-weeks=%d" % int(MAX_WEEKS), None).weeks == MAX_WEEKS
    with pytest.raises(Exception):
        Window.parse("fixed-weeks=%d" % (int(MAX_WEEKS) + 1), None)


# ══ 가장자리 감사 R3-7 · 표현 불가능한 임계값이 판정을 만들어내던 문제 ══════
def test_inf_로_커지는_활동일_임계값은_거절한다():
    """`min-rows=9…9`(400자리) → `float` 로 `inf`.

    받아 주면 **모든 날이 비활동**이 되고, 트래커 대조가 전원 불일치라는
    [치명]을 만든다 — 이 툴이 스스로는 절대 내리지 않겠다고 한 판정을,
    표현할 수 없는 임계값이 대신 내리는 셈이다.
    """
    done = run_cli(["--logs", "examples/clean/logs", "--active-day", "min-rows=" + "9" * 400,
                    "--window", "enroll-to-last", "--no-files"])
    assert done.returncode == EXIT_REFUSE
    assert "Traceback" not in (done.stdout + done.stderr)


# ══ 가장자리 감사 R3-4/6 · 산술 넘침·언더플로로 죽던 문제 ═══════════════════
def test_합계가_넘치는_소요시간은_평균_대신_대조불가(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    big = "1" + "0" * 308
    workbook(str(folder / "AB12CD34_x_1.xlsx"),
             [(MODULES[0], sheet([["2026.03.02", "1", "REGULAR", "1회", big + "초"],
                                  ["2026.03.03", "1", "REGULAR", "1회", big + "초"]]))])
    result = audit(str(folder))
    # 숫자를 만들어내지 않는다 — 트레이스백도 내지 않는다.
    assert result.modules[MODULES[0]].seconds.triple()[0] == "대조불가"


def test_평균을_낼_수_없으면_None_이지_예외가_아니다():
    huge = 1e308
    assert Mean3([huge, huge]).total_mean is None
    assert Mean3([huge, huge]).csv_fields()[0] == "대조불가"


def test_아주_작은_처방일수에도_0으로_나누지_않는다():
    done = run_cli(["--logs", "examples/clean/logs", "--active-day", "any-row",
                    "--window", "enroll-to-cut", "--cut", "2026-03-03",
                    "--target-per-week", "5e-324", "--no-files"])
    assert "Traceback" not in (done.stdout + done.stderr)
    assert "ZeroDivisionError" not in (done.stdout + done.stderr)


def test_소수점_40자리_인쇄값도_죽지_않는다(tmp_path):
    """`quantize` 는 decimal 기본 컨텍스트(28자리)를 넘으면 InvalidOperation."""
    folder = tmp_path / "logs"
    folder.mkdir()
    workbook(str(folder / "AB12CD34_x_1.xlsx"),
             [(MODULES[0], sheet([["2026.03.02", "1", "REGULAR", "1회", "5초"]],
                                 mean_sec="5." + "0" * 40 + "초"))])
    check = check_vendor_summaries(load_bundle(str(folder)))   # 예전엔 여기서 죽었다
    assert check.n_checked >= 1


# ══ 가장자리 감사 R3-8 · 단일 이벤트 로그 CSV 의 종료코드가 3이던 문제 ══════
def test_단일_CSV_를_주면_판정_없이_거절한다():
    """문서가 약속한 값은 2(→`logflow` 안내)인데 3(판정 불가)이 나왔다."""
    done = run_cli(["--logs", "examples/이벤트로그csv/app_events.csv",
                    "--active-day", "any-row", "--window", "enroll-to-last", "--no-files"])
    assert done.returncode == EXIT_REFUSE
    assert "logflow" in (done.stdout + done.stderr)


# ══ 가장자리 감사 R3 · 크래시가 '치명 발견'(1)과 구분되지 않던 문제 ═════════
def test_예상못한_예외는_1_이_아니라_2_로_끝난다():
    """CPython 은 잡히지 않은 예외에 **종료코드 1** 을 준다 — 이 툴에서 1은
    '치명 발견'이다. 즉 크래시와 진짜 발견이 CI 에서 구분되지 않았다."""
    import doseaudit.cli as cli_mod
    source = open(os.path.join(ROOT, "doseaudit", "cli.py"), encoding="utf-8").read()
    assert "except Exception as exc:" in source
    assert "EXIT_REFUSE" in source.split("except Exception as exc:")[1][:600]
    assert hasattr(cli_mod, "run")


# ══ 정확성 감사 R3-1 · 최장공백이 창의 양 끝 침묵을 세지 않던 문제 ══════════
def test_최장공백은_창의_양_끝도_센다():
    """3월에 그만둔 사람이 6월까지 열린 창에서 `공백 6일` 로 인쇄되던 자리."""
    start = datetime.date(2026, 1, 29)
    end = datetime.date(2026, 6, 23)
    active = {datetime.date(2026, 2, 3), datetime.date(2026, 2, 9),
              datetime.date(2026, 3, 21)}
    # 손계산: 창시작~첫활동 5일 · 02-03~02-09 사이 5일 · 02-09~03-21 사이 39일 ·
    #         마지막활동~창끝 94일 ← 최댓값
    assert longest_gap_days(active, start, end) == 94
    # 창을 모르면 활동일 사이만 잰다(예전 동작).
    assert longest_gap_days(active) == 39


def test_창_안에_활동이_없으면_창_전체가_공백이다():
    start = datetime.date(2026, 3, 1)
    end = datetime.date(2026, 3, 10)
    assert longest_gap_days(set(), start, end) == 10      # 양 끝 포함 10일


# ══ 정확성 감사 R3-2 · 해석 못 한 행이 섞이면 벤더를 **무고**하던 문제 ══════
def test_해석_못_한_행이_섞이면_벤더를_지목하지_않는다(tmp_path):
    """`10초`·`20초`·`5분` 에 벤더 인쇄값 `110.00초`(= 산술적으로 맞다).

    `5분` 을 해석 못 했다고 남은 두 행으로 `15.0` 을 내고 "재계산되지 않습니다"
    [치명]을 찍으면, **분모가 다른 두 숫자**를 놓고 벤더를 지목하는 것이다.
    이 툴이 절대 하지 않겠다고 한 단 하나의 말.
    """
    folder = tmp_path / "logs"
    folder.mkdir()
    workbook(str(folder / "EEEE1111_x_1.xlsx"),
             [(MODULES[0], sheet([["2026.03.02", "1", "REGULAR", "1회", "10초"],
                                  ["2026.03.03", "1", "REGULAR", "1회", "20초"],
                                  ["2026.03.04", "1", "REGULAR", "1회", "5분"]],
                                 mean_sec="110.00초"))])
    check = check_vendor_summaries(load_bundle(str(folder)))
    assert check.mismatches == []
    assert any("대조불가" in row[4] for row in check.uncomparable)

    result = audit(str(folder))
    assert not any(f.level == LEVEL_CRITICAL and "벤더" in f.title for f in result.findings)
    assert any(f.level == LEVEL_WARN and "벤더" in f.title for f in result.findings)


# ══ 정확성 감사 R3-4 · 없는 항목으로 '재계산 일치'를 주장하던 문제 ══════════
def test_항목이_없는_요약블록은_확인한_것으로_세지_않는다(tmp_path):
    """`150/150` 은 이 툴이 자랑하는 숫자다 — 실제 검산한 수보다 크면 안 된다."""
    folder = tmp_path / "logs"
    folder.mkdir()
    page = [["[요약]"], ["평균 레벨", "1"], None, list(HEADER),
            ["2026.03.02", "1", "REGULAR", "1회", "5초"],
            ["2026.03.03", "1", "REGULAR", "1회", "5초"]]
    workbook(str(folder / "GGGG1111_x_1.xlsx"), [(MODULES[0], page)])
    check = check_vendor_summaries(load_bundle(str(folder)))
    # `평균 소요시간`·`평균 반복횟수` 가 둘 다 없으므로 **검산한 시트가 아니다**.
    assert check.n_checked == 0
    assert check.n_agree == 0


# ══ 정확성 감사 R3-3 · 활동 0건인 피험자가 대조에서 사라지던 문제 ═══════════
def test_활동이_0건이어도_트래커_대조에서_빼지_않는다(tmp_path):
    """`enroll-to-last` 는 활동이 없으면 창의 끝을 못 잡는다. 그렇다고 빼면
    **트래커가 `완료 5일` 이라 적어 둔 가장 크게 어긋난 사람**이 조용히 사라지고
    `치명 0건 · 종료코드 0` 이 나온다."""
    folder = tmp_path / "logs"
    folder.mkdir()
    workbook(str(folder / "NNNN2222_x_1.xlsx"), [])          # 전 모듈 0행
    workbook(str(folder / "AAAA1111_x_2.xlsx"),
             [(MODULES[0], sheet([["2026.03.0%d" % d, "1", "REGULAR", "1회", "5초"]
                                  for d in (2, 3, 4)]))])
    tracker = tmp_path / "t.csv"
    tracker.write_text("Access Code,가입일,완료일수,현재까지 일수\n"
                       "NNNN2222,2026-03-01,5,30\nAAAA1111,2026-03-01,3,30\n",
                       encoding="utf-8")
    result = audit(str(folder), str(tracker), window="enroll-to-last", enroll="tracker")
    codes = {d.code: d for d in result.comparison["diffs"]}
    assert "NNNN2222" in codes, "활동 0건인 피험자가 대조에서 사라졌다"
    assert codes["NNNN2222"].recomputed == 0
    assert result.exit_code == 1


def test_창을_못_정한_이유를_구분해_인쇄한다(tmp_path):
    """'가입일을 확인하세요'라고 안내받은 사람이 멀쩡한 가입일 칸을 들여다보던 자리."""
    folder = tmp_path / "logs"
    folder.mkdir()
    workbook(str(folder / "NNNN2222_x_1.xlsx"), [])
    tracker = tmp_path / "t.csv"
    tracker.write_text("Access Code,가입일,완료일수,현재까지 일수\nNNNN2222,2026-03-01,0,30\n",
                       encoding="utf-8")
    result = audit(str(folder), str(tracker), window="enroll-to-last", enroll="tracker")
    exposure = result.exposures[0]
    assert exposure.window_unknown_reason == REASON_NO_ACTIVITY
    finding = next(f for f in result.findings if "창을 정할 수 없어" in f.title)
    assert any(REASON_NO_ACTIVITY in line for line in finding.lines)


# ══ 정확성 감사 R3-5/6 · 산출물이 자기 숫자를 설명하지 못하던 문제 ══════════
def test_모듈요약이_0행비율의_분모를_자백한다(tmp_path):
    """`0행비율` 의 분모는 **해석된 행**, `행수` 는 **전체 행**.

    `해석실패행수` 칸이 없으면 `0초행수 ÷ 행수` 가 인쇄된 비율과 어긋나고,
    그 차이를 산출물만 보고는 설명할 길이 없다.
    """
    from doseaudit.artifacts import module_rows
    folder = tmp_path / "logs"
    folder.mkdir()
    workbook(str(folder / "AB12CD34_x_1.xlsx"),
             [(MODULES[0], sheet([["2026.03.02", "1", "REGULAR", "1회", "0초"],
                                  ["2026.03.03", "1", "REGULAR", "1회", "0초"],
                                  ["2026.03.04", "1", "REGULAR", "1회", "10초"],
                                  ["2026.03.05", "1", "REGULAR", "1회", "해석불가"]]))])
    result = audit(str(folder))
    header, rows = module_rows(result)
    row = next(r for r in rows if r[0] == MODULES[0] and r[1] == "(전체)")
    n_rows = row[header.index("행수")]
    n_zero = row[header.index("소요시간0초행수")]
    n_bad = row[header.index("소요시간_해석실패행수")]
    ratio = float(row[header.index("소요시간_0행비율")])
    assert (n_rows, n_zero, n_bad) == (4, 2, 1)
    # 비율이 CSV 안에서 재현된다: 2 / (4 − 1) = 0.6667
    assert round(n_zero / (n_rows - n_bad), 4) == round(ratio, 4)


def test_유형없는_행도_유형별_표에_남는다(tmp_path):
    from doseaudit.artifacts import module_rows
    folder = tmp_path / "logs"
    folder.mkdir()
    workbook(str(folder / "AB12CD34_x_1.xlsx"),
             [(MODULES[0], sheet([["2026.03.02", "1", "", "1회", "5초"],
                                  ["2026.03.03", "1", "REGULAR", "1회", "5초"]]))])
    result = audit(str(folder))
    header, rows = module_rows(result)
    module = [r for r in rows if r[0] == MODULES[0]]
    total = next(r for r in module if r[1] == "(전체)")[header.index("행수")]
    by_type = [r for r in module if r[1] != "(전체)"]
    assert {r[1] for r in by_type} == {TYPE_BLANK, "REGULAR"}
    # 유형별 행수의 합이 모듈 행수와 **같아야** 한다 — 조용히 빠지는 행이 없다.
    assert sum(r[header.index("행수")] for r in by_type) == total


def test_유형별_행에도_0초행수가_채워진다(tmp_path):
    from doseaudit.artifacts import module_rows
    folder = tmp_path / "logs"
    folder.mkdir()
    workbook(str(folder / "AB12CD34_x_1.xlsx"),
             [(MODULES[0], sheet([["2026.03.02", "1", "REGULAR", "1회", "0초"],
                                  ["2026.03.03", "1", "REGULAR", "1회", "8초"]]))])
    result = audit(str(folder))
    header, rows = module_rows(result)
    row = next(r for r in rows if r[0] == MODULES[0] and r[1] == "REGULAR")
    assert row[header.index("소요시간0초행수")] == 1


# ══ 안전 감사 R3-C1 · 벤더 `[요약]` 칸이 검열을 건너뛰던 문제 ═══════════════
def test_요약블록의_실명이_리포트로_새지_않는다(tmp_path):
    """`[요약]` 은 **벤더가 채우는 칸**이다 — 파일명·시트이름과 같은 외부 입력."""
    from doseaudit.report import render_console
    folder = tmp_path / "logs"
    folder.mkdir()
    workbook(str(folder / "AB12CD34_x_1.xlsx"),
             [(MODULES[0], sheet([["2026.03.02", "1", "REGULAR", "1회", "5초"]],
                                 mean_sec="김철수 10초", mean_rep="담당자 박영희"))])
    text = "\n".join(render_console(audit(str(folder))))
    assert "김철수" not in text
    assert "박영희" not in text


def test_요약블록으로_치명_줄을_위조할_수_없다(tmp_path):
    """줄바꿈 한 번이면 리포트에 가짜 `[치명]` 줄을 심을 수 있었다."""
    from doseaudit.report import render_console
    folder = tmp_path / "logs"
    folder.mkdir()
    forged = "X\n[치명] 이 줄은 위조되었습니다 — 모든 데이터를 폐기하십시오"
    workbook(str(folder / "AB12CD34_x_1.xlsx"),
             [(MODULES[0], sheet([["2026.03.02", "1", "REGULAR", "1회", "5초"]],
                                 mean_sec=forged))])
    text = "\n".join(render_console(audit(str(folder))))
    assert "모든 데이터를 폐기" not in text
    for line in text.splitlines():
        assert not line.lstrip().startswith("[치명] 이 줄은 위조")


@pytest.mark.parametrize("channel", ["vendor_key", "vendor_value", "sheet_name", "ttype"])
def test_벤더가_채우는_어느_칸으로도_실명이_나가지_않는다(tmp_path, channel):
    """장치 ④ 를 **채널 단위**로 고정한다.

    가짜 이름 목록을 늘리는 것으로는 부족하다 — C1 이 새어 나간 이유는 목록이
    짧아서가 아니라 **검열되지 않는 입력 채널**이 하나 남아 있어서였다.
    """
    from doseaudit.report import render_console
    name = "김철수"
    folder = tmp_path / "logs"
    folder.mkdir()
    rows = [["2026.03.02", "1", "REGULAR", "1회", "5초"]]
    page = sheet(rows)
    sheet_name = MODULES[0]
    if channel == "vendor_key":
        page = [["[요약]"], ["평균 %s" % name, "5초"], None, list(HEADER)] + rows
    elif channel == "vendor_value":
        page = sheet(rows, mean_sec="%s 5초" % name)
    elif channel == "sheet_name":
        sheet_name = "%s모듈" % name
    elif channel == "ttype":
        page = sheet([["2026.03.02", "1", name, "1회", "5초"]])
    workbook(str(folder / "AB12CD34_x_1.xlsx"), [(sheet_name, page)])
    text = "\n".join(render_console(audit(str(folder))))
    if channel == "ttype":
        # 자유 텍스트 `훈련유형` 은 알려진 잔여 경로다 — README 한계 절에 적혀 있다.
        pytest.xfail("훈련유형 자유 텍스트는 문서화된 잔여 경로")
    assert name not in text, "채널 %s 로 실명이 새어 나갔다" % channel


# ══ 안전 감사 R3-C2 · 하드링크 산출물에서 부분 출력이 남던 문제 ═════════════
@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_산출물이_링크면_아무것도_쓰지_않는다(tmp_path, kind):
    """심볼릭만 테스트하고 있어서, 하드링크 가드를 지워도 1,037개가 초록이었다.
    실제로는 산출물 4개 중 2개가 먼저 쓰인 뒤 거절됐다 — 반쯤 채워진 결과 폴더는
    완전한 결과로 오해된다."""
    from doseaudit.artifacts import write_all
    from doseaudit.errors import DoseAuditError
    folder = tmp_path / "logs"
    folder.mkdir()
    workbook(str(folder / "AB12CD34_x_1.xlsx"),
             [(MODULES[0], sheet([["2026.03.02", "1", "REGULAR", "1회", "5초"]]))])
    out = tmp_path / "out"
    out.mkdir()
    precious = tmp_path / "precious.txt"
    precious.write_text("소중한 원본", encoding="utf-8")
    target = out / "노출량점검.md"
    if kind == "symlink":
        os.symlink(str(precious), str(target))
    else:
        os.link(str(precious), str(target))
    result = audit(str(folder))
    with pytest.raises(DoseAuditError):
        write_all(result, str(out), ["x"])
    # 미리 있던 파일 말고는 **아무것도** 만들어지지 않았다.
    assert sorted(os.listdir(str(out))) == ["노출량점검.md"]
    assert precious.read_text(encoding="utf-8") == "소중한 원본"


# ══ 안전 감사 R3-C3/C4 · 해석 못 한 행을 '측정된 0' 으로 세던 변이 ══════════
def test_해석_못_한_소요시간은_0초_행으로_세지_않는다(tmp_path):
    """이 툴의 존재 이유가 '0' 과 '미측정'의 구분인데, 그 구분이 **0초 행 카운터**
    에서는 테스트로 고정돼 있지 않았다(변이 M25·M28 이 살아남았다)."""
    folder = tmp_path / "logs"
    folder.mkdir()
    workbook(str(folder / "AB12CD34_x_1.xlsx"),
             [(MODULES[0], sheet([["2026.03.02", "1", "REGULAR", "1회", "10초"],
                                  ["2026.03.03", "1", "REGULAR", "1회", "기록없음"],
                                  ["2026.03.04", "1", "REGULAR", "1회", "0초"]]))])
    result = audit(str(folder))
    # 피험자 단위 — 진짜 0초는 1건뿐이다.
    assert result.exposures[0].n_zero_sec == 1
    # 모듈 단위 — 같은 구분이 여기서도 지켜져야 한다.
    assert result.modules[MODULES[0]].n_zero_sec == 1
    assert result.modules[MODULES[0]].seconds.n_unparsed == 1


# ══ 안전 감사 R3-C5 · 커버리지 자백의 분모가 무너지던 변이 ══════════════════
def test_자백의_분모는_못_읽은_파일까지_센다(tmp_path):
    """자백은 이 툴의 **머리글 정직성**이다. 분모가 읽은 파일 수로 바뀌면
    `읽음: 워크북 1/1` 이라고 인쇄하면서 두 개를 못 읽은 상태가 된다."""
    from doseaudit.report import render_console
    folder = tmp_path / "logs"
    folder.mkdir()
    workbook(str(folder / "AB12CD34_x_1.xlsx"),
             [(MODULES[0], sheet([["2026.03.02", "1", "REGULAR", "1회", "5초"]]))])
    for name in ("EF56GH78_y_2.xlsx", "IJ90KL12_z_3.xlsx"):
        (folder / name).write_bytes(b"not a zip at all")
    result = audit(str(folder))
    text = "\n".join(render_console(result))
    assert "워크북 1/3" in text
    assert result.exit_code == 3        # 3 이 1 보다 우선


# ══ 안전 감사 R3-Wa · 산출물이 입력 트래커를 덮어쓰던 문제 ══════════════════
def test_트래커를_산출물_이름으로_덮어쓰지_않는다(tmp_path):
    """`--tracker out/트래커불일치.csv --out-dir out/` → 입력이 사라졌다."""
    from doseaudit.artifacts import write_all
    from doseaudit.errors import DoseAuditError
    folder = tmp_path / "logs"
    folder.mkdir()
    workbook(str(folder / "AB12CD34_x_1.xlsx"),
             [(MODULES[0], sheet([["2026.03.02", "1", "REGULAR", "1회", "5초"]]))])
    out = tmp_path / "out"
    out.mkdir()
    tracker = out / "트래커불일치.csv"
    tracker.write_text("Access Code,가입일,완료일수\nAB12CD34,2026-03-01,1\n", encoding="utf-8")
    before = tracker.read_text(encoding="utf-8")
    result = audit(str(folder), str(tracker))
    with pytest.raises(DoseAuditError):
        write_all(result, str(out), ["x"])
    assert tracker.read_text(encoding="utf-8") == before


# ══ 안전 감사 R3 · 트래커의 '읽지 않은 열' 자백이 항상 비어 있던 문제 ═══════
def test_읽지_않은_트래커_열을_실제로_인쇄한다(tmp_path):
    tracker = tmp_path / "t.csv"
    tracker.write_text(
        "Access Code,환자명,가입일,완료일수,현재까지 일수,담당자,비고\n"
        "AB12CD34,홍길동,2026-03-01,3,30,김간호,특이사항 없음\n", encoding="utf-8")
    loaded = load_tracker(str(tracker))
    assert set(loaded.unread_columns) == {"담당자", "비고"}
    assert "환자명" in loaded.dropped_columns      # 개인식별은 따로 인쇄된다

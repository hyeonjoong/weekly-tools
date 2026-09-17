"""적대 패널 1라운드가 찾은 결함들의 회귀 테스트 (2026-09-17).

여기 있는 테스트는 전부 **한 번 실제로 깨졌던 것**이다. 자세한 경위는
`HARDENING.md` 에 있고, 이 파일은 같은 결함이 되살아나지 못하게 못 박는다.
"""

import os
import subprocess
import sys

import pytest

from doseaudit.audit import LEVEL_CRITICAL, LEVEL_INFO, LEVEL_WARN, Config, run_audit
from doseaudit.csvout import escape_cell
from doseaudit.errors import EXIT_CLEAN, EXIT_REFUSE, EXIT_UNJUDGEABLE
from doseaudit.logs import load_bundle
from doseaudit.paths import prepare_out_dir
from doseaudit.report import methods_draft_en, methods_draft_kr, render_console
from doseaudit.rules import CONTRAST_WINDOWS, ActiveDayRule, Window
from doseaudit.sanitize import mask_filename
from doseaudit.errors import RefuseError
from xlsxwrite import MODULES, module_sheet, write_xlsx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_cli(args):
    return subprocess.run([sys.executable, "-m", "doseaudit"] + args,
                          cwd=ROOT, capture_output=True, text=True)


def audit(logs, tracker=None, rule="any-row", window="fixed-weeks=8", cut=None,
          target=3.0, cap=None, criterion=None):
    return run_audit(Config(
        logs=logs, tracker_path=tracker, rule=ActiveDayRule.parse(rule),
        window=Window.parse(window, cut), enroll_source=None, cap=cap,
        target_per_week=target, pool_types=False, criterion=criterion, out_dir=None))


# ── 정확성 감사 #0 · 셀 참조 하나로 임의 크기 할당 ───────────────────────────
def test_열_번호가_엑셀_한계를_넘으면_읽지_않는다(make_workbook, basic_rows, tmp_path):
    """`r="AAAAAAA3"` 하나짜리 1.5KB 파일이 3.9GB 를 먹고 9.7초를 쓰던 자리."""
    import time
    import zipfile
    path = make_workbook("AB12CD34", {"음소쌍": basic_rows})
    with zipfile.ZipFile(path) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    key = "xl/worksheets/sheet1.xml"
    members[key] = members[key].replace(
        b"</sheetData>", b'<row r="99"><c r="AAAAAAA99" t="str"><v>x</v></c></row></sheetData>')
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    started = time.time()
    bundle = load_bundle(str(tmp_path / "logs"))
    assert time.time() - started < 5.0
    assert len(bundle.unreadable) == 1
    assert "열 번호" in bundle.unreadable[0][1]


# ── 정확성 감사 #1 · 분자와 분모가 다른 구간을 보던 문제 ────────────────────
def test_노출률은_창_안에서만_센다(make_workbook, tmp_path, make_tracker):
    make_workbook("AB12CD34", {"음소쌍": [
        ("2026.02.20", 1, "REGULAR", 1, 5), ("2026.02.22", 2, "REGULAR", 1, 5),
        ("2026.03.02", 3, "REGULAR", 1, 5), ("2026.03.05", 4, "REGULAR", 1, 5),
        ("2026.03.20", 5, "REGULAR", 1, 5), ("2026.03.24", 6, "REGULAR", 1, 5)]})
    tracker = make_tracker([("AB12CD34", "2026-03-01", 2, 14)])
    exposure = audit(str(tmp_path / "logs"), tracker,
                     window="enroll-to-cut", cut="2026-03-14").exposures[0]
    assert (exposure.n_active, exposure.n_outside_window) == (2, 4)
    assert exposure.adherence_pct == pytest.approx(100 * 2 / (3 * 2.0))


def test_창밖_활동은_경고로_보고된다(make_workbook, tmp_path, make_tracker):
    make_workbook("AB12CD34", {"음소쌍": [("2026.02.20", 1, "REGULAR", 1, 5),
                                        ("2026.03.02", 2, "REGULAR", 1, 5)]})
    tracker = make_tracker([("AB12CD34", "2026-03-01", 1, 14)])
    result = audit(str(tmp_path / "logs"), tracker, window="enroll-to-cut", cut="2026-03-14")
    finding = next(f for f in result.findings if "창 밖" in f.title)
    assert finding.level == LEVEL_WARN
    assert "버린 것이 아니라" in "\n".join(finding.lines)


# ── 정확성 감사 #2 · 시작일을 모르는데 고정 주수 분모를 만들던 문제 ─────────
def test_가입일을_모르면_고정주수도_분모를_만들지_않는다(make_workbook, tmp_path, make_tracker):
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, 5)]}, seq=1)
    make_workbook("ZZ99ZZ99", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, 5)]}, seq=2)
    tracker = make_tracker([("AB12CD34", "2026-03-01", 1, 14)])
    result = audit(str(tmp_path / "logs"), tracker, window="fixed-weeks=1")
    by_code = {e.code: e for e in result.exposures}
    assert by_code["AB12CD34"].observed_days == 7.0
    assert by_code["ZZ99ZZ99"].observed_days is None
    assert by_code["ZZ99ZZ99"].adherence_pct is None


# ── 정확성 감사 #3 · Methods 초안이 방향을 무조건 주장하던 문제 ─────────────
def test_방향이_섞이면_전원_같은_방향이라고_쓰지_않는다(make_workbook, tmp_path, make_tracker):
    """원고에 붙으라고 만든 문장이다 — 거짓이면 그 자리가 리뷰어의 자리다."""
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.0%d" % d, 1, "REGULAR", 1, 5)
                                        for d in (2, 3, 4)]}, seq=1)
    make_workbook("EF56GH78", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, 5)]}, seq=2)
    tracker = make_tracker([("AB12CD34", "2026-03-01", 1, 30),
                            ("EF56GH78", "2026-03-01", 7, 30)])
    result = audit(str(tmp_path / "logs"), tracker, window="enroll-to-cut", cut="2026-03-30")
    assert result.comparison["uniform_sign"] is False
    assert "전원 같은 방향" not in methods_draft_kr(result)
    assert "방향은 섞여 있음" in methods_draft_kr(result)
    assert "all in the same direction" not in methods_draft_en(result)
    assert "in both directions" in methods_draft_en(result)


def test_한_방향이면_그렇게_쓴다(flawed_logs, flawed_tracker):
    result = audit(flawed_logs, flawed_tracker, window="enroll-to-cut", cut="2026-04-27")
    assert result.comparison["uniform_sign"] is True
    assert "전원 같은 방향" in methods_draft_kr(result)
    assert "all in the same direction" in methods_draft_en(result)


# ── 정확성 감사 #5 · 소수점 완료일수를 잘라 +0 치명을 만들던 문제 ───────────
def test_소수점_완료일수를_자르지_않는다(make_workbook, tmp_path, make_tracker):
    from doseaudit.artifacts import tracker_rows
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.0%d" % d, 1, "REGULAR", 1, 5)
                                        for d in (2, 3, 4)]})
    tracker = make_tracker([("AB12CD34", "2026-03-01", 3.5, 30)])
    result = audit(str(tmp_path / "logs"), tracker, window="enroll-to-cut", cut="2026-03-30")
    diff = result.comparison["diffs"][0]
    assert diff.fmt_tracker() == "3.5"
    assert diff.fmt_delta() == "-0.50"
    header, rows = tracker_rows(result)
    assert rows[0][header.index("트래커_완료일수")] == "3.5"
    assert rows[0][header.index("차이")] != "+0"


# ── 정확성 감사 #8 / 문서 감사 C6 · 벤더 일치를 말하지 않던 문제 ────────────
def test_벤더_요약이_맞으면_맞다고_말한다(flawed_logs):
    result = audit(flawed_logs)
    # 벤더 관련 발견이 여러 개일 수 있다(재계산 일치 [정보] + 재계산 불가 [경고]).
    # 여기서 고정하려는 것은 **일치를 일치라고 말하는가** 이므로 그 건을 집어서 본다.
    finding = next(f for f in result.findings
                   if "벤더" in f.title and "산수는 맞습니다" in "\n".join(f.lines))
    assert finding.level == LEVEL_INFO
    # 그리고 어느 경우에도 '벤더가 틀렸다'(치명)로는 올라가지 않는다.
    assert not any(f.level == LEVEL_CRITICAL and "벤더" in f.title for f in result.findings)


# ── 가장자리 감사 P5 · 없는 열로 정합성을 주장하던 문제 ─────────────────────
def test_진행률_열이_없으면_정합하다고_말하지_않는다(make_workbook, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, 5)]})
    path = str(tmp_path / "t.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("Access Code,가입일,완료일수\nAB12CD34,2026-03-01,9\n")
    result = audit(str(tmp_path / "logs"), path, window="enroll-to-cut", cut="2026-03-30")
    assert result.comparison["progress_checked"] == []
    assert not any("정합적" in f.title for f in result.findings)


def test_진행률을_실제로_확인했으면_몇_명인지_말한다(flawed_logs, flawed_tracker, tmp_path,
                                                 make_tracker, make_workbook):
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.0%d" % d, 1, "REGULAR", 1, 5)
                                        for d in (2, 3)]}, seq=1)
    tracker = make_tracker([("AB12CD34", "2026-03-01", 1, 30)])
    result = audit(str(tmp_path / "logs"), tracker, window="enroll-to-cut", cut="2026-03-30")
    finding = next(f for f in result.findings if "정합적" in f.title)
    assert "1명 전원이 재현됩니다" in "\n".join(finding.lines)


# ── 가장자리 감사 P7·P8·P9 · 조용한 행 손실 ─────────────────────────────────
def test_정규화_후_이름이_같아지는_시트가_둘이면_거절(tmp_path):
    import unicodedata
    folder = tmp_path / "logs"
    folder.mkdir()
    rows = module_sheet([["2026.03.02", "1", "REGULAR", "2회", "5초"]], 5, 2)
    write_xlsx(str(folder / "AB12CD34_x_1.xlsx"),
               [("음소쌍", rows), (unicodedata.normalize("NFD", "음소쌍"), rows)])
    with pytest.raises(RefuseError) as info:
        load_bundle(str(folder))
    assert "이름이 같은 시트가 둘" in str(info.value)


def test_규격_5열_밖에만_값이_있는_행을_세어_둔다(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    rows = [["[요약]"], None,
            ["날짜", "레벨", "훈련유형", "반복횟수", "소요시간(초)", "비고"],
            ["2026.03.02", "1", "REGULAR", "2회", "5초", "정상"],
            ["", "", "", "", "", "여기에만 값이 있음"]]
    write_xlsx(str(folder / "AB12CD34_x_1.xlsx"),
               [(MODULES[0], rows)] + [(m, module_sheet([], 0, 0)) for m in MODULES[1:]])
    bundle = load_bundle(str(folder))
    assert bundle.n_data_rows == 1
    assert any("뒤 열에만" in row[3] for row in bundle.parse_fail_rows)


def test_Access_Code_가_빈_트래커_행을_자백한다(make_workbook, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, 5)]})
    path = str(tmp_path / "t.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("Access Code,가입일,완료일수,현재까지 일수,진행률(%)\n")
        fh.write("AB12CD34,2026-03-01,1,30,3\n")
        fh.write(",,7,30,23\n")
        fh.write(",,9,30,30\n")
    result = audit(str(tmp_path / "logs"), path, window="enroll-to-cut", cut="2026-03-30")
    assert result.tracker.n_blank_rows == 2
    assert "비어 건너뛴 트래커 행 2개" in "\n".join(render_console(result))


# ── 가장자리 감사 P10·P12 · 말이 안 되는 인자 ───────────────────────────────
@pytest.mark.parametrize("flag,value", [
    ("--criterion", "nan"), ("--criterion", "inf"), ("--cap", "nan"),
    ("--cap", "1e400"), ("--target-per-week", "nan"), ("--target-per-week", "inf"),
])
def test_유한하지_않은_인자는_거절(flawed_logs, flag, value):
    """`--criterion nan` 은 전원을 '미달'로 찍는다 — 이 툴이 안 한다고 한 판정이다."""
    proc = run_cli(["--logs", flawed_logs, "--active-day", "any-row",
                    "--window", "fixed-weeks=8", "--target-per-week", "3",
                    flag, value, "--no-files"])
    assert proc.returncode == EXIT_REFUSE
    assert "유한한 숫자" in proc.stderr


def test_빈_out_dir_은_거절(flawed_logs):
    proc = run_cli(["--logs", flawed_logs, "--active-day", "any-row",
                    "--window", "fixed-weeks=8", "--out-dir", ""])
    assert proc.returncode == EXIT_REFUSE


# ── 가장자리 감사 P1 · 닫힌 stdout / 콘솔 인코딩 ────────────────────────────
@pytest.mark.parametrize("env,expected", [
    ({"PYTHONIOENCODING": "cp949"}, EXIT_CLEAN),
    ({"PYTHONIOENCODING": "ascii"}, EXIT_CLEAN),
    ({"LC_ALL": "C", "LANG": "C"}, EXIT_CLEAN),
])
def test_콘솔_인코딩이_UTF8_이_아니어도_종료코드가_바뀌지_않는다(clean_logs, env, expected):
    """cp949 콘솔에서 `—` 하나 때문에 깨끗한 자료가 '치명 발견'이 되던 자리."""
    environ = dict(os.environ)
    environ.update(env)
    proc = subprocess.run(
        [sys.executable, "-m", "doseaudit", "--logs", clean_logs, "--active-day", "any-row",
         "--window", "fixed-weeks=8", "--no-files"],
        cwd=ROOT, capture_output=True, env=environ)
    assert proc.returncode == expected


@pytest.mark.parametrize("args,expected", [
    (["--active-day", "any-row", "--window", "fixed-weeks=8", "--no-files"], EXIT_CLEAN),
    ([], EXIT_REFUSE),
])
def test_stdout_이_닫혀_있어도_종료코드가_맞는다(clean_logs, args, expected):
    """launchd·cron·`>&-` 에서 실행되면 `sys.stdout` 이 None 이다."""
    code = ("import subprocess,sys,os\n"
            "devnull=os.open(os.devnull,os.O_RDONLY)\n"
            "p=subprocess.run([sys.executable,'-m','doseaudit','--logs',%r]+%r,"
            "cwd=%r,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
            "sys.exit(p.returncode)" % (clean_logs, args, ROOT))
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True)
    assert proc.returncode == expected


# ── 안전 감사 P1·P2 · 건너뛴/못 읽은 파일 이름으로 새는 PII 와 위조 ─────────
def test_건너뛴_파일_이름이_LoginID_를_흘리지_않는다(make_workbook, basic_rows, tmp_path,
                                                    capsys):
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    folder = tmp_path / "logs"
    (folder / "QR33ST44_realloginid7788_9.csv").write_text("x", encoding="utf-8")
    (folder / "김철수.pdf").write_text("x", encoding="utf-8")
    result = audit(str(folder))
    text = "\n".join(render_console(result))
    assert "realloginid7788" not in text
    assert "김철수" not in text
    assert "건너뜀" in text


def test_건너뛴_파일_이름으로_치명_줄을_위조할_수_없다(make_workbook, basic_rows, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    folder = tmp_path / "logs"
    (folder / "a\n[치명] 트래커가 전부 일치합니다 — 조작된 줄.csv").write_text("x", encoding="utf-8")
    text = "\n".join(render_console(audit(str(folder))))
    assert not [l for l in text.splitlines() if l.lstrip().startswith("[치명]") and "조작" in l]
    assert "조작된 줄" not in text


@pytest.mark.parametrize("raw,expected", [
    ("홍길동_hong1234_1.xlsx", "?_…_1.xlsx"),
    ("김철수.pdf", "?.pdf"),
    ("3D609A5O_mystart_81.xlsx", "3D609A5O_…_81.xlsx"),
])
def test_코드_규격이_아닌_첫_토큰은_가린다(raw, expected):
    """첫 토큰이 Access Code 가 아니라고 이미 판정했다면, 그것은 이름일 수 있다."""
    assert mask_filename(raw, mask_head=True) == expected


# ── 안전 감사 P3·P4 · 트래커 쪽 PII ─────────────────────────────────────────
def test_Access_Code_자리에_이름이_있으면_값을_읽지_않는다(make_workbook, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, 5)]})
    path = str(tmp_path / "t.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("Access Code,가입일,완료일수,현재까지 일수,진행률(%)\n")
        fh.write("AB12CD34,2026-03-01,1,30,3\n")
        fh.write("이순신,2026-03-01,5,30,17\n")
    result = audit(str(tmp_path / "logs"), path, window="enroll-to-cut", cut="2026-03-30")
    text = "\n".join(render_console(result))
    assert "이순신" not in text
    assert len(result.tracker.rows) == 1


@pytest.mark.parametrize("column", [
    "환자 성명", "피험자 성명", "Patient Name", "참여자명", "수급자 성함",
    "성명(한글)", "이름(한글)", "대상자명", "보호자 연락처", "환자ID",
])
def test_이름_열_이름이_조금_달라도_열지_않는다(column):
    from doseaudit.tracker import FORBIDDEN_PATTERNS
    assert any(p.search(column) for p in FORBIDDEN_PATTERNS), column


# ── 안전 감사 P5·P6·P8 ──────────────────────────────────────────────────────
def test_상위_폴더가_심볼릭_링크면_어디에_쓰는지_밝힌다(clean_logs, tmp_path):
    """막지는 않는다(macOS 의 `/tmp` 부터가 링크다). 다만 숨기지도 않는다."""
    from doseaudit.paths import resolved_note
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    os.symlink(str(real), str(link))
    target = str(link / "안쪽")
    assert prepare_out_dir(target) == target
    assert resolved_note(target) == os.path.realpath(target)
    proc = run_cli(["--logs", clean_logs, "--active-day", "any-row",
                    "--window", "fixed-weeks=8", "--out-dir", target])
    assert proc.returncode == EXIT_CLEAN
    assert "링크를 따라 실제로 쓰인 곳" in proc.stdout
    assert os.path.isfile(os.path.join(str(real), "안쪽", "노출량점검.md"))


def test_out_dir_최종_조각이_링크면_여전히_거절(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    os.symlink(str(real), str(link))
    with pytest.raises(RefuseError):
        prepare_out_dir(str(link))


@pytest.mark.parametrize("raw", ["﻿=cmd|'/c calc'!A1", "​=1+1", "⁠+1",
                                 " =1+1", " @SUM(A1)"])
def test_보이지_않는_접두사_뒤의_수식도_막는다(raw):
    assert escape_cell(raw).startswith("'")


def test_산출물_하나가_링크면_아무것도_쓰지_않는다(flawed_logs, flawed_tracker, tmp_path):
    from doseaudit.artifacts import REPORT_MD, write_all
    result = audit(flawed_logs, flawed_tracker, window="enroll-to-cut", cut="2026-04-27")
    out = tmp_path / "out"
    out.mkdir()
    victim = tmp_path / "원본.md"
    victim.write_text("원본", encoding="utf-8")
    os.symlink(str(victim), str(out / REPORT_MD))
    with pytest.raises(RefuseError):
        write_all(result, str(out), render_console(result))
    # 앞의 CSV 세 장도 남지 않아야 한다 — 반쯤 채워진 폴더는 완전한 결과로 오해된다
    assert [n for n in os.listdir(str(out)) if not n.startswith(".")] == [REPORT_MD]
    assert victim.read_text(encoding="utf-8") == "원본"


# ── 가장자리 감사 P3 · 전부 못 읽었을 때 ────────────────────────────────────
def test_전부_못_읽으면_거절이_아니라_판정불가다(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    (folder / "AB12CD34_x_1.xlsx").write_bytes(b"not a zip")
    (folder / "EF56GH78_y_2.xlsx").write_bytes(b"also not")
    proc = run_cli(["--logs", str(folder), "--active-day", "any-row",
                    "--window", "fixed-weeks=8", "--no-files"])
    assert proc.returncode == EXIT_UNJUDGEABLE
    assert "못 읽음: 2개" in proc.stdout
    assert "판정하지 않았습니다" in proc.stdout or "아무것도 판정하지 않았습니다" in proc.stdout


# ── 문서 감사 B1 · 민감도 표가 분모의 창도 흔든다 ───────────────────────────
def test_민감도_표가_분모의_창도_흔든다(flawed_logs, flawed_tracker):
    """활동일 규칙만 흔들면 "분모가 무엇이었는지를 드러낸다"는 한 줄이 빈다."""
    result = audit(flawed_logs, flawed_tracker, window="enroll-to-cut", cut="2026-04-27")
    axes = {row["axis"] for row in result.sensitivity}
    assert "활동일 규칙" in axes and "분모의 창" in axes
    labels = {row["label"] for row in result.sensitivity}
    for spec in CONTRAST_WINDOWS:
        assert spec in labels


def test_민감도_표가_중앙값_폭을_한_줄로_말한다(flawed_logs, flawed_tracker):
    from doseaudit.report import sensitivity_lines
    result = audit(flawed_logs, flawed_tracker, window="enroll-to-cut", cut="2026-04-27")
    text = "\n".join(sensitivity_lines(result))
    assert "사이를 움직입니다" in text


# ── 문서 감사 A1 · 실데이터 상수가 코드에 박혀 있던 문제 ────────────────────
def test_inspect_가_실제로_읽은_시트_수를_인쇄한다(flawed_logs):
    proc = run_cli(["--inspect", "--logs", flawed_logs])
    assert "36장 중" in proc.stdout
    assert "180장" not in proc.stdout


# ── 문서 감사 A4·A5·C1·C2 · 계산해 놓고 인쇄하지 않던 값들 ──────────────────
def test_상한_절단_인원이_콘솔에_나온다(make_workbook, tmp_path, make_tracker):
    """절단 인원은 Methods 초안에만 있고 콘솔에는 없어, `--no-files` 로는 볼 수 없었다."""
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.0%d" % d, 1, "REGULAR", 1, 5)
                                        for d in range(2, 9)]})
    tracker = make_tracker([("AB12CD34", "2026-03-02", 7, 7)])
    result = audit(str(tmp_path / "logs"), tracker, window="fixed-weeks=1",
                   target=3.0, cap=100.0)
    assert result.exposures[0].capped is True
    finding = next(f for f in result.findings if "상한 절단으로" in f.title)
    assert finding.level == LEVEL_INFO
    assert "AB12CD34" in "\n".join(finding.lines)


def test_선언된_기준_충족_인원이_콘솔과_초안에_나온다(flawed_logs, flawed_tracker):
    result = audit(flawed_logs, flawed_tracker, window="enroll-to-cut",
                   cut="2026-04-27", criterion=20.0)
    assert any("선언된 기준" in f.title for f in result.findings)
    assert "충족한 피험자" in methods_draft_kr(result)
    assert "met the criterion" in methods_draft_en(result)


def test_초안이_IQR_을_같이_보고한다(flawed_logs, flawed_tracker):
    result = audit(flawed_logs, flawed_tracker, window="enroll-to-cut", cut="2026-04-27")
    assert "IQR" in methods_draft_kr(result)
    assert "IQR" in methods_draft_en(result)


def test_영문_초안이_한글_모듈명에_자리번호를_붙인다(flawed_logs, flawed_tracker):
    result = audit(flawed_logs, flawed_tracker, window="enroll-to-cut", cut="2026-04-27")
    assert "Module 6 (발성훈련)" in methods_draft_en(result)


# ── 문서 감사 C3 · 중복의심을 모듈별로 나눈다 ───────────────────────────────
def test_중복의심을_모듈별로_나눠_보여_준다(flawed_logs):
    result = audit(flawed_logs)
    finding = next(f for f in result.findings if "중복의심" in f.title)
    body = "\n".join(finding.lines)
    assert "모듈별로 나눠 보면" in body
    assert any(name in body for name in result.modules)

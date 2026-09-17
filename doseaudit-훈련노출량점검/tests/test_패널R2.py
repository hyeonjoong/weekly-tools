"""적대 패널 2라운드가 찾은 결함들의 회귀 테스트 (2026-09-17).

2라운드의 일은 **1라운드의 수정이 실제로 버티는지** 확인하는 것이었고, 여섯 개는
버티지 못했습니다(한 번 고친 자리가 화면 하나에만 반영돼 있던 경우 포함).
자세한 경위는 `HARDENING.md` 2라운드 절에 있습니다.
"""

import datetime
import os
import subprocess
import sys

import pytest

from doseaudit.audit import LEVEL_CRITICAL, LEVEL_WARN, Config, run_audit
from doseaudit.dose import _vendor_agrees, check_vendor_summaries
from doseaudit.errors import EXIT_CLEAN, RefuseError
from doseaudit.logs import load_bundle
from doseaudit.report import render_console
from doseaudit.rules import ActiveDayRule, Window
from doseaudit.sanitize import mask_filename
from xlsxwrite import MODULES, module_sheet, write_xlsx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def audit(logs, tracker=None, rule="any-row", window="fixed-weeks=8", cut=None,
          target=3.0, cap=None, criterion=None):
    return run_audit(Config(
        logs=logs, tracker_path=tracker, rule=ActiveDayRule.parse(rule),
        window=Window.parse(window, cut), enroll_source=None, cap=cap,
        target_per_week=target, pool_types=False, criterion=criterion, out_dir=None))


# ── R2-1 · 콘솔만 소수점을 자르고 있었다 ─────────────────────────────────────
def test_콘솔도_소수점_완료일수를_자르지_않는다(make_workbook, tmp_path, make_tracker):
    """CSV·Methods 에는 수정이 갔는데 **콘솔 경로만** `%d` 로 남아 있었다.

    그 결과 `트래커 3일 ↔ 로그 3일 (+0)` — 1라운드가 없앴다고 한 바로 그 화면.
    """
    make_workbook("AAAA1111", {"음소쌍": [("2026.03.0%d" % d, 1, "REGULAR", 1, 5)
                                        for d in (2, 3, 4)]})
    tracker = make_tracker([("AAAA1111", "2026-03-02", 3.5, 10)])
    result = audit(str(tmp_path / "logs"), tracker, window="enroll-to-cut", cut="2026-03-11")
    text = "\n".join(render_console(result))
    assert "트래커 3.5일" in text
    assert "(-0.50)" in text
    assert "트래커 3일  ↔  로그 3일 (+0)" not in text
    assert "차이 -0.50 ~ -0.50" in text


# ── R2-2 · 은행가 반올림이 거짓 치명을 만들었다 ──────────────────────────────
@pytest.mark.parametrize("recomputed,printed,decimals,expected", [
    (2.5, 3, 0, True),      # 스프레드시트는 0에서 먼 쪽으로 붙인다
    (0.5, 1, 0, True),
    (3.5, 4, 0, True),
    (2.5, 2, 0, False),
    (4.045, 4.05, 2, True),  # 이진 부동소수점으로는 4.04499…
    (4.044, 4.04, 2, True),
    (2.9, 3, 0, True),
    (2.9, 2, 0, False),      # 옛 규칙(차이<1.0)이 놓치던 자리
])
def test_벤더_대조는_십진_반올림으로_한다(recomputed, printed, decimals, expected):
    assert _vendor_agrees(recomputed, printed, decimals) is expected


def test_평균이_정확히_반올림_경계여도_치명이_아니다(make_workbook, tmp_path):
    """`2회`·`3회` 두 행 → 평균 2.5 → 벤더는 `3회` 로 인쇄한다. 이건 일치다."""
    make_workbook("BBBB2222", {"음소쌍": [("2026.03.02", 1, "REGULAR", 2, 10),
                                        ("2026.03.03", 1, "REGULAR", 3, 10)]},
                  vendor={"음소쌍": (10, 3)})
    check = check_vendor_summaries(load_bundle(str(tmp_path / "logs")))
    assert check.mismatches == []


# ── R2-3 · 0행 시트에 인쇄된 평균이 통째로 보이지 않았다 ────────────────────
def test_일부_피험자에게만_빈_모듈의_인쇄된_평균을_잡는다(tmp_path):
    """모듈 전체가 비면 모듈 단위 치명이 뜬다. **일부만** 비면 아무것도 안 떴다."""
    folder = tmp_path / "logs"
    folder.mkdir()
    empty_with_block = module_sheet([], 42, 3)      # 데이터 0행인데 평균 42초
    has_data = module_sheet([["2026.03.02", "1", "REGULAR", "3회", "42초"]], 42, 3)
    write_xlsx(str(folder / "DDDD4444_x_1.xlsx"),
               [(MODULES[0], empty_with_block)]
               + [(m, module_sheet([], 0, 0)) for m in MODULES[1:]])
    write_xlsx(str(folder / "EEEE5555_y_2.xlsx"),
               [(MODULES[0], has_data)]
               + [(m, module_sheet([], 0, 0)) for m in MODULES[1:]])
    result = audit(str(folder))
    finding = next(f for f in result.findings if "0행인데" in f.title)
    assert finding.level == LEVEL_CRITICAL       # 인쇄값이 0이 아니다
    body = "\n".join(finding.lines)
    assert "DDDD4444" in body and "42초" in body


def test_모듈_전체가_비면_중복_보고하지_않는다(flawed_logs):
    """`발성훈련` 은 이미 모듈 단위 치명이다 — 시트마다 또 울면 두 번 안 연다."""
    result = audit(flawed_logs)
    extra = [f for f in result.findings if "0행인데" in f.title]
    assert extra == []


def test_대조에서_빠진_시트를_두_가지로_나눠_센다(make_workbook, basic_rows, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    check = check_vendor_summaries(load_bundle(str(tmp_path / "logs")))
    assert check.n_empty_with_block == 5
    assert check.n_no_block == 0


# ── R2-4 · 창을 정할 수 없는 피험자를 창 없이 세고 트래커와 대조했다 ────────
def test_창을_정할_수_없는_피험자는_트래커_대조에서_뺀다(make_workbook, tmp_path):
    from doseaudit.artifacts import exposure_rows, tracker_rows
    make_workbook("GGGG7777", {"음소쌍": [("2026.01.05", 1, "REGULAR", 1, 5),
                                        ("2026.01.06", 2, "REGULAR", 1, 5),
                                        ("2026.03.02", 3, "REGULAR", 1, 5),
                                        ("2026.03.04", 4, "REGULAR", 1, 5)]})
    path = str(tmp_path / "t.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("Access Code,가입일,완료일수,현재까지 일수,진행률(%)\n")
        fh.write("GGGG7777,,4,60,7\n")
    result = audit(str(tmp_path / "logs"), path, window="enroll-to-cut", cut="2026-03-08")
    assert result.exposures[0].window_known is False
    assert result.comparison["agree"] == [] and result.comparison["diffs"] == []
    assert any(code == "GGGG7777" for code, _reason in result.comparison["uncomparable"])

    header, rows = exposure_rows(result)
    assert rows[0][header.index("창적용")] == "N"

    _h, trows = tracker_rows(result)
    assert any("창을 정할 수 없어" in r[-1] for r in trows)

    finding = next(f for f in result.findings if "창을 정할 수 없어" in f.title)
    assert finding.level == LEVEL_WARN


# ── R2-5 · 한 행에 창 기준 값과 전체 기간 값이 섞여 있었다 ──────────────────
def test_전체기간_열은_이름으로_구분된다(flawed_logs, flawed_tracker):
    from doseaudit.artifacts import exposure_rows
    result = audit(flawed_logs, flawed_tracker, window="enroll-to-cut", cut="2026-04-27")
    header, _rows = exposure_rows(result)
    windowed = {"활동일수", "관찰일수", "주당활동일", "노출률", "최장공백일",
                "첫활동일", "마지막활동일"}
    for name in header:
        if name.endswith("_전체기간"):
            assert name.replace("_전체기간", "") not in windowed
    assert any(h.endswith("_전체기간") for h in header)


def test_자백이_셈의_범위를_밝힌다(flawed_logs, flawed_tracker):
    result = audit(flawed_logs, flawed_tracker, window="enroll-to-cut", cut="2026-04-27")
    text = "\n".join(render_console(result))
    assert "셈의 범위:" in text
    assert "창 기준(선언된 창 안에서만)" in text
    assert "워크북 전체(창과 무관)" in text


# ── R2-6 · 하루로 떨어지지 않는 고정 주수를 조용히 잘랐다 ───────────────────
@pytest.mark.parametrize("spec", ["fixed-weeks=1.5", "fixed-weeks=12.5", "fixed-weeks=0.5"])
def test_하루로_안_떨어지는_주수는_거절(spec):
    with pytest.raises(RefuseError) as info:
        Window.parse(spec, None)
    assert "하루 단위로 떨어지지 않습니다" in str(info.value)


# ── F2 · 파일명의 마지막 토큰으로 이름이 새고 있었다 ────────────────────────
@pytest.mark.parametrize("raw", ["2026_동의서_김철수.pdf", "EF56GH78_1_김철수.xlsx",
                                 "A_b_홍길동.xlsx", "동의서_김철수.pdf"])
def test_마지막_토큰의_이름도_가린다(raw):
    assert "김철수" not in mask_filename(raw)
    assert "홍길동" not in mask_filename(raw)


def test_건너뛴_동의서_PDF_의_이름이_리포트에_없다(make_workbook, basic_rows, tmp_path):
    """벤더 폴더에는 동의서·메모가 섞여 들어오고, 그 이름에 사람 이름이 붙어 있다."""
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    (tmp_path / "logs" / "2026_동의서_김철수.pdf").write_text("x", encoding="utf-8")
    result = audit(str(tmp_path / "logs"))
    text = "\n".join(render_console(result))
    assert "김철수" not in text
    assert "2026_…_?.pdf" in text


# ── F3 · "그대로 복사해 쓰세요" 줄이 셸 주입 가능했다 ───────────────────────
def test_복사용_실행_줄은_셸에서_안전하다(tmp_path, make_workbook, basic_rows):
    evil = tmp_path / "x'; touch /tmp/DOSEAUDIT_PWNED; echo '"
    make_workbook("AB12CD34", {"음소쌍": basic_rows}, folder=str(evil))
    proc = subprocess.run([sys.executable, "-m", "doseaudit", "--inspect",
                           "--logs", str(evil)], cwd=ROOT, capture_output=True, text=True)
    line = next(l for l in proc.stdout.splitlines() if l.strip().startswith("doseaudit --logs"))
    marker = "/tmp/DOSEAUDIT_PWNED"
    if os.path.exists(marker):
        os.remove(marker)
    # 붙여넣기를 흉내 낸다: 셸이 토큰을 어떻게 쪼개는지 본다
    check = subprocess.run(["bash", "-c", "set -- %s; printf '%%s\\n' \"$2\"" % line.strip()],
                           capture_output=True, text=True)
    assert check.stdout.strip() == "--logs"
    assert not os.path.exists(marker), "복사용 줄이 명령을 실행시켰습니다"


def test_경로에_제어문자가_있으면_복사용_줄을_만들지_않는다(tmp_path, make_workbook, basic_rows):
    from doseaudit.inspection import render_inspect
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    bundle = load_bundle(str(tmp_path / "logs"))
    text = "\n".join(render_inspect(bundle, logs_arg="/tmp/ev\nil"))
    assert "제어문자가 들어 있어" in text
    assert not any(l.strip().startswith("doseaudit --logs") for l in text.splitlines())


# ── F8 · 콘솔 두 줄이 무해화를 우회했다 ─────────────────────────────────────
def test_out_dir_이름으로_치명_줄을_위조할_수_없다(clean_logs, tmp_path):
    evil = tmp_path / "o\n[치명] 위조된 줄"
    evil.mkdir()
    proc = subprocess.run(
        [sys.executable, "-m", "doseaudit", "--logs", clean_logs, "--active-day", "any-row",
         "--window", "fixed-weeks=8", "--out-dir", str(evil)],
        cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == EXIT_CLEAN
    assert not [l for l in proc.stdout.splitlines() if l.startswith("[치명]")]
    forged = [l for l in proc.stdout.splitlines() if l.lstrip().startswith("[치명]")]
    assert forged == []


# ── F1 · .gitignore 가 번들 예제 트래커를 커밋에서 떨어뜨렸다 ───────────────
@pytest.mark.parametrize("path", [
    "examples/clean/참여현황.xlsx",
    "examples/flawed/참여현황.xlsx",
    "examples/못읽는파일/참여현황.xlsx",
    "examples/clean/logs/AB12CD34_alpha_11.xlsx",
    "examples/이벤트로그csv/app_events.csv",
])
def test_번들_예제는_커밋에_들어간다(path):
    """`참여현황*` 이 `!examples/**` 뒤에 있으면 예제 트래커 3개가 조용히 빠진다.

    그러면 새로 클론한 사람에게는 README 예제 3개와 `실행.command` 가 전부
    깨진 채로 도착한다. git 은 **마지막에 일치한 규칙**을 쓴다.
    """
    assert os.path.isfile(os.path.join(ROOT, path)), path
    proc = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
    assert proc.returncode != 0, "%s 가 .gitignore 로 빠집니다" % path


@pytest.mark.parametrize("path", [
    "3D609A5O_mystart_81.xlsx", "참여현황_2026-06-21.xlsx",
    "결과/피험자별노출량.csv", "환자목록.csv", "노출량점검.md",
])
def test_실데이터와_산출물은_여전히_막힌다(path):
    proc = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
    assert proc.returncode == 0, "%s 가 저장소로 들어갑니다" % path


# -- 변이 생존자 · 창의 **끝 경계 포함**이 어떤 테스트에도 걸려 있지 않았다 --
# `start <= d <= end` 를 `start <= d < end` 로 바꿔도 1,009개가 전부 초록이었다.
# 데이터컷 당일의 활동이 분자에서 조용히 빠지는데 아무도 모르는 상태였다.
@pytest.mark.parametrize("cut,expected_active,expected_outside", [
    ("2026-03-04", 3, 0),   # 마지막 활동일 == 컷  -> 포함되어야 한다
    ("2026-03-03", 2, 1),   # 컷 하루 전           -> 하루가 창 밖
    ("2026-03-05", 3, 0),   # 컷 하루 뒤           -> 그대로
])
def test_데이터컷_당일의_활동은_분자에_포함된다(make_workbook, tmp_path, make_tracker,
                                              cut, expected_active, expected_outside):
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.0%d" % d, 1, "REGULAR", 1, 5)
                                        for d in (2, 3, 4)]})
    tracker = make_tracker([("AB12CD34", "2026-03-02", 3, 10)])
    exposure = audit(str(tmp_path / "logs"), tracker,
                     window="enroll-to-cut", cut=cut).exposures[0]
    assert exposure.n_active == expected_active
    assert exposure.n_outside_window == expected_outside


def test_가입일_당일의_활동도_분자에_포함된다(make_workbook, tmp_path, make_tracker):
    """시작 경계도 포함이다 - 관찰일수가 양 끝을 포함해 세므로 분자도 그래야 한다."""
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, 5),
                                        ("2026.03.05", 2, "REGULAR", 1, 5)]})
    tracker = make_tracker([("AB12CD34", "2026-03-02", 2, 10)])
    exposure = audit(str(tmp_path / "logs"), tracker,
                     window="enroll-to-cut", cut="2026-03-10").exposures[0]
    assert exposure.n_active == 2
    assert exposure.n_outside_window == 0
    assert exposure.first == datetime.date(2026, 3, 2)


def test_고정주수_창의_마지막_날도_포함된다(make_workbook, tmp_path, make_tracker):
    """`fixed-weeks=1` = 가입일 포함 7일 -> 가입일 + 6일이 마지막 날이다."""
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, 5),
                                        ("2026.03.08", 2, "REGULAR", 1, 5),
                                        ("2026.03.09", 3, "REGULAR", 1, 5)]})
    tracker = make_tracker([("AB12CD34", "2026-03-02", 2, 7)])
    exposure = audit(str(tmp_path / "logs"), tracker, window="fixed-weeks=1").exposures[0]
    assert exposure.window_end == datetime.date(2026, 3, 8)
    assert exposure.n_active == 2
    assert exposure.n_outside_window == 1
    assert exposure.observed_days == 7.0


def test_enroll_to_last_는_마지막_활동일을_포함한다(make_workbook, tmp_path, make_tracker):
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, 5),
                                        ("2026.03.09", 2, "REGULAR", 1, 5)]})
    tracker = make_tracker([("AB12CD34", "2026-03-02", 2, 8)])
    exposure = audit(str(tmp_path / "logs"), tracker, window="enroll-to-last").exposures[0]
    assert exposure.n_active == 2 and exposure.n_outside_window == 0
    assert exposure.observed_days == 8.0


# -- 2라운드 후속 · 나머지 생존 결함들 --
@pytest.mark.parametrize("printed,mean,agree", [
    ("\uff11\uff0e\uff15\ucd08", 1.5, True),   # 전각 소수점
    ("\uff11\uff0e\uff15\ucd08", 1.4, False),
    ("\uff11\uff12\ucd08", 12.0, True),
    ("1.5초", 1.5, True),
])
def test_전각으로_인쇄된_값도_자릿수를_제대로_센다(printed, mean, agree):
    """`\uff11\uff0e\uff15초` 를 자릿수 0으로 세면 맞는 값에 거짓 치명이 붙는다."""
    from doseaudit.dose import printed_decimals
    from doseaudit.values import SECONDS_UNITS, strip_unit
    assert _vendor_agrees(mean, strip_unit(printed, SECONDS_UNITS),
                          printed_decimals(printed)) is agree


def test_읽기전용_산출물이_있으면_아무것도_쓰지_않는다(flawed_logs, flawed_tracker, tmp_path):
    """링크만 보고 넘어가면 세 번째에서 멈추면서 앞의 두 개가 남는다."""
    from doseaudit.artifacts import REPORT_MD, write_all
    out = tmp_path / "out"
    out.mkdir()
    blocked = out / REPORT_MD
    blocked.write_text("", encoding="utf-8")
    os.chmod(str(blocked), 0o444)
    result = audit(flawed_logs, flawed_tracker, window="enroll-to-cut", cut="2026-04-27")
    try:
        with pytest.raises(RefuseError) as info:
            write_all(result, str(out), render_console(result))
        assert "권한" in str(info.value)
        assert sorted(os.listdir(str(out))) == [REPORT_MD]
    finally:
        os.chmod(str(blocked), 0o644)


def test_셀을_흩뿌려_메모리를_먹일_수_없다(tmp_path):
    """열 번호만 막으면 `r="XFD1"` 을 여러 행에 흩뿌려 같은 짓을 할 수 있다."""
    import time
    import zipfile
    from doseaudit.errors import ReadError
    from doseaudit.xlsxread import read_workbook
    folder = tmp_path / "logs"
    folder.mkdir()
    path = str(folder / "AB12CD34_x_1.xlsx")
    write_xlsx(path, [(m, module_sheet([["2026.03.02", "1", "REGULAR", "2회", "5초"]], 5, 2))
                      for m in MODULES])
    with zipfile.ZipFile(path) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    spray = "".join('<row r="%d"><c r="XFD%d" t="str"><v>x</v></c></row>' % (i, i)
                    for i in range(100, 2100))
    members["xl/worksheets/sheet1.xml"] = members["xl/worksheets/sheet1.xml"].replace(
        b"</sheetData>", spray.encode() + b"</sheetData>")
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    started = time.time()
    with pytest.raises(ReadError) as info:
        read_workbook(path)
    assert time.time() - started < 10.0
    assert "셀 수" in str(info.value)


@pytest.mark.parametrize("evil", ["모듈\u2028[치명] 위조된 줄", "모듈\u2029위조",
                                  "모듈\u0085위조"])
def test_줄바꿈으로_읽히는_문자도_무해화한다(evil):
    """`\\n` 은 아니지만 `splitlines()` 와 다수 렌더러가 여기서 줄을 가른다."""
    from doseaudit.sanitize import safe_text
    cleaned = safe_text(evil)
    assert len(cleaned.splitlines()) == 1
    assert "\ufffd" in cleaned


@pytest.mark.parametrize("raw", ["\u3000=cmd|'/c calc'!A1", "\u2000=1+1",
                                 "\u202f@SUM(A1)", "\u205f+1+cmd", "\u1680-1+cmd"])
def test_모든_공백류_뒤의_수식도_막는다(raw):
    from doseaudit.csvout import escape_cell
    assert escape_cell(raw).startswith("'")


@pytest.mark.parametrize("raw", ["\u205f+1", "\u3000-3", " 3.5"])
def test_공백이_붙어도_숫자는_숫자로_둔다(raw):
    """`-3`(부호 붙은 차이)에 따옴표를 붙이면 표가 통째로 문자열이 된다."""
    from doseaudit.csvout import escape_cell
    assert not escape_cell(raw).startswith("'")


def test_긴_시트_이름_둘을_같다고_거절하지_않는다(tmp_path):
    """자른 뒤에 비교하면 서로 다른 긴 이름이 같아져 멀쩡한 워크북을 거절한다."""
    folder = tmp_path / "logs"
    folder.mkdir()
    base = "아" * 70
    rows = module_sheet([["2026.03.02", "1", "REGULAR", "2회", "5초"]], 5, 2)
    write_xlsx(str(folder / "AB12CD34_x_1.xlsx"),
               [(base + "첫번째", rows), (base + "두번째", rows)])
    bundle = load_bundle(str(folder))
    assert len(bundle.subjects[0].modules) == 2
    assert bundle.n_data_rows == 2


def test_못_읽은_워크북은_없는_것과_다르게_적는다(make_workbook, basic_rows, tmp_path,
                                                  make_tracker):
    """'찾아보라'와 '다시 받아라'는 다른 지시다 - 기계가 읽는 CSV 에서도 구분한다."""
    from doseaudit.artifacts import tracker_rows
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    (tmp_path / "logs" / "EF56GH78_x_2.xlsx").write_bytes(b"not a zip")
    tracker = make_tracker([("AB12CD34", "2026-03-02", 3, 10),
                            ("EF56GH78", "2026-03-02", 5, 10),
                            ("ZZ99ZZ99", "2026-03-02", 1, 10)])
    result = audit(str(tmp_path / "logs"), tracker, window="enroll-to-cut", cut="2026-03-11")
    _h, rows = tracker_rows(result)
    reasons = {r[0]: r[-1] for r in rows}
    assert "읽지 못함" in reasons["EF56GH78"]
    assert "워크북이 없음" in reasons["ZZ99ZZ99"]
    assert result.exit_code == 3


def test_민감도_표에_같은_설정이_두_번_나오지_않는다(flawed_logs, flawed_tracker):
    """선언된 창이 대조 창 목록에도 있으면, 같은 설정이 라벨 없이 한 번 더 찍힌다."""
    result = audit(flawed_logs, flawed_tracker, window="fixed-weeks=8")
    labels = [r["label"] for r in result.sensitivity]
    assert len(labels) == len(set(labels))
    assert "fixed-weeks=8" not in labels      # 선언된 행이 이미 그 설정이다
    assert sum(1 for r in result.sensitivity if r["declared"]) == 1


def test_상한을_주면_절단_없는_값도_같이_보여_준다(flawed_logs, flawed_tracker):
    """모든 행이 상한에 눌리면 표 전체가 같은 값이 되어 표의 용도가 사라진다."""
    result = audit(flawed_logs, flawed_tracker, window="enroll-to-last", cap=25.0)
    labels = [r["label"] for r in result.sensitivity]
    assert any("상한 절단 없이" in l for l in labels)
    assert "상한 절단" in {r["axis"] for r in result.sensitivity}


def test_트래커가_거절돼도_못_읽은_워크북을_숨기지_않는다(make_workbook, basic_rows, tmp_path):
    from xlsxwrite import write_xlsx as _wx
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    (tmp_path / "logs" / "EF56GH78_x_2.xlsx").write_bytes(b"broken")
    bad_tracker = str(tmp_path / "bad.xlsx")
    _wx(bad_tracker, [("참여현황", [["엉뚱한열"], ["값"]])])
    with pytest.raises(RefuseError) as info:
        audit(str(tmp_path / "logs"), bad_tracker, window="fixed-weeks=8")
    assert "읽지 못했습니다" in str(info.value)
    assert "EF56GH78" in str(info.value)

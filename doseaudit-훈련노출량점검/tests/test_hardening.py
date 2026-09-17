"""상습 결함 하드닝 — 이 저장소가 실제로 출시한 적 있는 결함들을 못 박는다."""

import os
import subprocess
import sys
import unicodedata

import pytest

from doseaudit.artifacts import EXPOSURE_CSV, write_all
from doseaudit.audit import Config, run_audit
from doseaudit.cli import main
from doseaudit.errors import EXIT_CRITICAL, EXIT_REFUSE, RefuseError
from doseaudit.paths import open_artifact, prepare_out_dir
from doseaudit.report import render_console
from doseaudit.rules import ActiveDayRule, Window
from doseaudit.sanitize import mask_filename, nfc, safe_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def result(flawed_logs, flawed_tracker):
    return run_audit(Config(
        logs=flawed_logs, tracker_path=flawed_tracker,
        rule=ActiveDayRule.parse("any-row"),
        window=Window.parse("enroll-to-cut", "2026-04-27"),
        enroll_source=None, cap=None, target_per_week=3.0,
        pool_types=False, criterion=None, out_dir=None))


# ── ① 출력이 심볼릭 링크를 따라가 입력을 덮어쓰지 않는다 ──────────────────────
def test_산출물_자리의_심볼릭_링크를_거절한다(result, tmp_path):
    victim = tmp_path / "소중한_원본.csv"
    victim.write_text("건드리면 안 되는 데이터\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    os.symlink(str(victim), str(out / EXPOSURE_CSV))
    with pytest.raises(RefuseError) as info:
        write_all(result, str(out), render_console(result))
    assert "심볼릭 링크" in str(info.value)
    assert victim.read_text(encoding="utf-8") == "건드리면 안 되는 데이터\n"


@pytest.mark.parametrize("name", ["노출량점검.md", "모듈별요약.csv", "트래커불일치.csv",
                                  "피험자별노출량.csv"])
def test_네_산출물_모두_링크를_따라가지_않는다(result, tmp_path, name):
    victim = tmp_path / "원본.txt"
    victim.write_text("원본", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    os.symlink(str(victim), str(out / name))
    with pytest.raises(RefuseError):
        write_all(result, str(out), render_console(result))
    assert victim.read_text(encoding="utf-8") == "원본"


def test_out_dir_자체가_심볼릭_링크면_거절(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    os.symlink(str(real), str(link))
    with pytest.raises(RefuseError) as info:
        prepare_out_dir(str(link))
    assert "심볼릭 링크" in str(info.value)


# ── ② 하드링크는 O_NOFOLLOW 가 막지 못한다 ───────────────────────────────────
def test_산출물_자리의_하드링크를_거절한다(tmp_path):
    victim = tmp_path / "원본.csv"
    victim.write_text("원본 내용\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    os.link(str(victim), str(out / EXPOSURE_CSV))
    with pytest.raises(RefuseError) as info:
        open_artifact(str(out), EXPOSURE_CSV)
    assert "하드링크" in str(info.value)
    assert victim.read_text(encoding="utf-8") == "원본 내용\n"


def test_평범한_기존_파일은_덮어쓴다(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / EXPOSURE_CSV).write_text("이전 결과", encoding="utf-8")
    with open_artifact(str(out), EXPOSURE_CSV) as fh:
        fh.write("새 결과")
    assert (out / EXPOSURE_CSV).read_text(encoding="utf-8") == "새 결과"


# ── ③ --out-dir 사고는 트레이스백이 아니라 한국어 한 줄 + 종료코드 2 ─────────
def test_out_dir_이_파일이면_한국어로_거절(tmp_path):
    path = tmp_path / "이미파일"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(RefuseError) as info:
        prepare_out_dir(str(path))
    assert "폴더가 아니라 파일" in str(info.value)


def test_out_dir_이_파일이면_종료코드_2(flawed_logs, tmp_path):
    path = tmp_path / "이미파일"
    path.write_text("x", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "doseaudit", "--logs", flawed_logs,
         "--active-day", "any-row", "--window", "fixed-weeks=8", "--out-dir", str(path)],
        cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == EXIT_REFUSE
    assert "Traceback" not in proc.stderr
    assert "[거절]" in proc.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root 는 권한 거부를 겪지 않습니다")
def test_쓸_권한이_없으면_한국어로_거절(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(str(locked), 0o500)
    try:
        with pytest.raises(RefuseError) as info:
            prepare_out_dir(str(locked / "안쪽"))
        assert "권한" in str(info.value)
    finally:
        os.chmod(str(locked), 0o700)


def test_out_dir_은_없으면_만든다(tmp_path):
    target = tmp_path / "새폴더" / "안쪽"
    assert prepare_out_dir(str(target)) == str(target)
    assert os.path.isdir(str(target))


# ── ④ 파일명 제어문자가 [치명] 행을 위조하지 못한다 ──────────────────────────
@pytest.mark.parametrize("evil", [
    "AB12\n[치명] 가짜입니다",
    "AB12\r[치명] 가짜",
    "AB12\x1b[31m[치명]",
    "AB12\x00[치명]",
    "AB12‮[치명]",
    "AB12‏가짜",
])
def test_제어문자와_방향재정의를_무해화한다(evil):
    cleaned = safe_text(evil)
    assert "\n" not in cleaned and "\r" not in cleaned
    assert "\x1b" not in cleaned and "\x00" not in cleaned
    assert "‮" not in cleaned and "‏" not in cleaned
    assert "�" in cleaned


def test_파일명에_심은_치명_행이_리포트에_그대로_들어가지_않는다(tmp_path, make_workbook, capsys):
    """줄바꿈이 살아 있으면 파일명 하나로 리포트 한 줄을 위조할 수 있다."""
    folder = tmp_path / "logs"
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, 5)]}, folder=str(folder))
    evil = folder / "ZZ99\n[치명] 완전히 가짜인 줄입니다_x_2.xlsx"
    evil.write_bytes(b"not a workbook")
    main(["--logs", str(folder), "--active-day", "any-row",
          "--window", "fixed-weeks=8", "--no-files"])
    out = capsys.readouterr().out
    # 지켜야 하는 성질: 심은 문자열이 **새 줄을 시작하지 못한다**.
    forged = [l for l in out.splitlines()
              if l.lstrip().startswith("[치명]") and "가짜" in l]
    assert forged == []
    # 파일은 '못 읽음'으로만 자백에 실린다 — 판정을 만들어내지 않는다.
    assert "못 읽음: 1개" in out
    # 첫 토큰이 Access Code 규격이 아니므로 통째로 가려진다(이름일 수 있으므로).
    assert "?_…_2.xlsx" in out
    assert "가짜인 줄입니다" not in out


def test_길이_제한이_걸린다():
    assert len(safe_text("x" * 1000)) <= 120
    assert safe_text("x" * 1000).endswith("…")


# ── ⑤ `| head` 가 종료코드를 뒤집지 않는다 ───────────────────────────────────
def test_파이프로_잘려도_종료코드가_유지된다(flawed_logs, flawed_tracker):
    cmd = ("%s -m doseaudit --logs %s --tracker %s --active-day any-row "
           "--window enroll-to-cut --cut 2026-04-27 --target-per-week 3 "
           "--no-files 2>/dev/null | head -3 >/dev/null; echo ${PIPESTATUS[0]}"
           % (sys.executable, _q(flawed_logs), _q(flawed_tracker)))
    proc = subprocess.run(["bash", "-c", cmd], cwd=ROOT, capture_output=True, text=True)
    assert proc.stdout.strip() == str(EXIT_CRITICAL)


def test_파이프로_잘려도_깨끗한_자료는_0이다(clean_logs, clean_tracker):
    cmd = ("%s -m doseaudit --logs %s --tracker %s --active-day any-row "
           "--window enroll-to-cut --cut 2026-04-27 --target-per-week 3 "
           "--no-files 2>/dev/null | head -1 >/dev/null; echo ${PIPESTATUS[0]}"
           % (sys.executable, _q(clean_logs), _q(clean_tracker)))
    proc = subprocess.run(["bash", "-c", cmd], cwd=ROOT, capture_output=True, text=True)
    assert proc.stdout.strip() == "0"


def test_파이프가_끊겨도_BrokenPipe_트레이스백이_없다(flawed_logs):
    cmd = ("%s -m doseaudit --logs %s --active-day any-row --window fixed-weeks=8 "
           "--no-files 2>&1 | head -2" % (sys.executable, _q(flawed_logs)))
    proc = subprocess.run(["bash", "-c", cmd], cwd=ROOT, capture_output=True, text=True)
    assert "BrokenPipeError" not in proc.stdout
    assert "Traceback" not in proc.stdout


def _q(path):
    return "'" + path.replace("'", "'\\''") + "'"


# ── ⑥ 인코딩·정규화 ──────────────────────────────────────────────────────────
def test_NFD_파일명도_같은_코드로_읽힌다(tmp_path, make_workbook):
    """macOS 는 파일명을 NFD 로 준다 — 정규화하지 않으면 같은 코드가 다른 사람이 된다."""
    assert nfc(unicodedata.normalize("NFD", "가나다")) == "가나다"


def test_한글_모듈명이_NFD_로_와도_같은_모듈이다():
    assert nfc(unicodedata.normalize("NFD", "발성훈련")) == "발성훈련"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp949", "euc-kr"])
def test_CSV_트래커_인코딩_네_가지(tmp_path, encoding):
    from doseaudit.tracker import load_tracker
    path = tmp_path / "t.csv"
    path.write_bytes("Access Code,완료일수\nAB12CD34,6\n".encode(encoding))
    assert load_tracker(str(path)).rows[0].code == "AB12CD34"


def test_읽을_수_없는_인코딩은_조용히_비우지_않는다(tmp_path):
    from doseaudit.errors import ReadError
    from doseaudit.tracker import load_tracker
    path = tmp_path / "t.csv"
    path.write_bytes(b"\xff\xfe\x00\x00Access Code\x00")
    with pytest.raises((ReadError, RefuseError)):
        load_tracker(str(path))


# ── ⑦ 파일명 마스킹 ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("3D609A5O_mystart_81.xlsx", "3D609A5O_…_81.xlsx"),
    ("AB12_login_1.xlsx", "AB12_…_1.xlsx"),
    ("AB12_login.xlsx", "AB12_….xlsx"),
    ("NOUNDERSCORE.xlsx", "NOUNDERSCORE.xlsx"),
    # 첫 토큰이 Access Code 규격(영숫자 2자 이상)이 아니면 언제나 가린다
    ("A_b_c_d_9.xlsx", "?_…_9.xlsx"),
    # 마지막 토큰도 검사한다 — 여기로 사람 이름이 새던 자리다
    ("2026_동의서_김철수.pdf", "2026_…_?.pdf"),
    ("EF56GH78_1_김철수.xlsx", "EF56GH78_…_?.xlsx"),
])
def test_파일명의_모든_토큰을_검사한다(raw, expected):
    assert mask_filename(raw) == expected


# ── ⑧ zip 폭탄 ───────────────────────────────────────────────────────────────
def test_압축_해제_총량_상한이_있다(tmp_path):
    import zipfile
    from doseaudit.errors import ReadError
    from doseaudit.xlsxread import read_workbook
    path = tmp_path / "AB12CD34_x_1.xlsx"
    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/workbook.xml", "<x/>")
        info = zipfile.ZipInfo("xl/huge.bin")
        info.file_size = 10 ** 12          # 헤더가 주장하는 크기
        zf.writestr(info, b"0")
    with pytest.raises(ReadError):
        read_workbook(str(path))

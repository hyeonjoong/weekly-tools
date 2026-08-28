"""출력 안전장치 — 원본을 덮어쓰지 않고, 표 계산기에 코드를 흘리지 않습니다."""
from __future__ import annotations

import os
import stat

import pytest

from stimaudit import safeio


def test_prepare_creates_dir(tmp_path):
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "새폴더"))
    assert os.path.isdir(out)
    assert os.path.isabs(out)


def test_prepare_accepts_existing_dir(tmp_path):
    d = os.path.join(str(tmp_path), "d")
    os.makedirs(d)
    out = safeio.prepare_out_dir(d)
    assert os.fspath(out) == os.path.abspath(d)
    assert os.path.join(out, "x") == os.path.join(os.path.abspath(d), "x")
    out.close()


def test_prepare_returns_a_directory_handle(tmp_path):
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "h"))
    try:
        assert out.fd is not None
        assert str(out) == out.path
    finally:
        out.close()
        assert out.fd is None


def test_close_is_idempotent(tmp_path):
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "h"))
    out.close()
    out.close()


def test_o_nofollow_alone_stops_a_symlink(tmp_path, monkeypatch):
    """islink 사전검사와 O_NOFOLLOW 는 **각각** 막을 수 있어야 합니다.

    두 겹 중 하나를 지워도 다른 하나가 잡아 주기 때문에, 어느 쪽을 삭제해도
    테스트가 초록이던 상태였습니다(뮤테이션 M07/M08 생존). 사전검사를 꺼서
    실제 경합 상황을 흉내 내고 O_NOFOLLOW 만으로 막히는지 확인합니다.
    """
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "o"))
    victim = os.path.join(str(tmp_path), "원본.wav")
    open(victim, "w").write("ORIGINAL")
    os.symlink(victim, os.path.join(out.path, "자극점검.md"))
    monkeypatch.setattr(os.path, "islink", lambda p: False)
    try:
        with pytest.raises(safeio.OutputError):
            safeio.write_text(out, "자극점검.md", "덮어쓰기")
        assert open(victim).read() == "ORIGINAL"
    finally:
        out.close()


def test_writes_go_to_the_validated_directory_after_a_swap(tmp_path):
    """검사 뒤 --out-dir 를 심볼릭 링크로 바꿔치기해도 원래 폴더에 씁니다.

    분석에 수 분이 걸리므로 검사와 쓰기 사이의 창이 넓습니다. 경로 문자열만
    들고 있으면 그 사이의 바꿔치기를 알 수 없습니다.
    """
    real = os.path.join(str(tmp_path), "real")
    moved = os.path.join(str(tmp_path), "real_moved")
    evil = os.path.join(str(tmp_path), "evil")
    os.makedirs(real)
    os.makedirs(evil)
    out = safeio.prepare_out_dir(real)
    try:
        os.rename(real, moved)            # 검증된 아이노드는 살아 있음
        os.symlink(evil, real)            # 경로만 공격자 폴더로 바꿔치기
        safeio.write_text(out, "a.md", "내용")
        assert os.listdir(evil) == []                 # 공격자 폴더는 비어 있고
        assert os.listdir(moved) == ["a.md"]          # 원래 아이노드에 쓰였습니다
    finally:
        out.close()


def test_csv_header_is_sanitised(tmp_path):
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "o"))
    try:
        p = safeio.write_csv(out, "t.csv", ["=EVIL()", "정상"], [["a", "b"]])
    finally:
        out.close()
    first = open(p, encoding="utf-8-sig").readline()
    assert first.startswith("'=EVIL()")


def test_prepare_rejects_empty():
    with pytest.raises(safeio.OutputError):
        safeio.prepare_out_dir("")


def test_prepare_rejects_existing_file(tmp_path):
    """--out-dir 가 파일이면 한국어 오류 — 트레이스백이 나오면 안 됩니다."""
    f = os.path.join(str(tmp_path), "afile")
    open(f, "w").write("x")
    with pytest.raises(safeio.OutputError) as e:
        safeio.prepare_out_dir(f)
    assert "폴더가 아니라 파일" in str(e.value)


def test_prepare_rejects_symlinked_dir(tmp_path):
    real = os.path.join(str(tmp_path), "real")
    os.makedirs(real)
    link = os.path.join(str(tmp_path), "link")
    os.symlink(real, link)
    with pytest.raises(safeio.OutputError) as e:
        safeio.prepare_out_dir(link)
    assert "심볼릭 링크" in str(e.value)


def test_prepare_rejects_unwritable_dir(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root 는 권한 검사를 우회합니다")
    d = os.path.join(str(tmp_path), "ro")
    os.makedirs(d)
    os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(safeio.OutputError) as e:
            safeio.prepare_out_dir(d)
        assert "권한" in str(e.value)
    finally:
        os.chmod(d, stat.S_IRWXU)


def test_prepare_reports_creation_failure(tmp_path):
    f = os.path.join(str(tmp_path), "afile")
    open(f, "w").write("x")
    with pytest.raises(safeio.OutputError):
        safeio.prepare_out_dir(os.path.join(f, "sub", "dir"))


def test_write_text_creates_file(tmp_path):
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "o"))
    try:
        p = safeio.write_text(out, "a.md", "내용")
    finally:
        out.close()
    assert open(p, encoding="utf-8").read() == "내용"


def test_symlinked_artifact_does_not_clobber_original(tmp_path):
    """이 저장소의 최근 세 툴이 안고 배포했던 결함 — 링크를 따라가 원본을 날립니다."""
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "o"))
    victim = os.path.join(str(tmp_path), "원본.wav")
    open(victim, "w").write("ORIGINAL")
    os.symlink(victim, os.path.join(out.path, "자극점검.md"))
    with pytest.raises(safeio.OutputError) as e:
        safeio.write_text(out, "자극점검.md", "덮어쓰기")
    assert "심볼릭 링크" in str(e.value)
    assert open(victim).read() == "ORIGINAL"


def test_symlinked_csv_refused(tmp_path):
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "o"))
    victim = os.path.join(str(tmp_path), "원본.csv")
    open(victim, "w").write("KEEP")
    os.symlink(victim, os.path.join(out.path, "문제목록.csv"))
    with pytest.raises(safeio.OutputError):
        safeio.write_csv(out, "문제목록.csv", ["a"], [["b"]])
    assert open(victim).read() == "KEEP"


def test_hardlinked_artifact_refused(tmp_path):
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "o"))
    victim = os.path.join(str(tmp_path), "원본.md")
    open(victim, "w").write("KEEP")
    os.link(victim, os.path.join(out.path, "자극점검.md"))
    with pytest.raises(safeio.OutputError) as e:
        safeio.write_text(out, "자극점검.md", "덮어쓰기")
    assert "하드링크" in str(e.value)
    assert open(victim).read() == "KEEP"


def test_broken_symlink_also_refused(tmp_path):
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "o"))
    os.symlink(os.path.join(str(tmp_path), "없는파일"), os.path.join(out.path, "a.md"))
    with pytest.raises(safeio.OutputError):
        safeio.write_text(out, "a.md", "x")


def test_existing_regular_file_is_overwritten(tmp_path):
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "o"))
    safeio.write_text(out, "a.md", "old")
    safeio.write_text(out, "a.md", "new")
    assert open(os.path.join(out.path, "a.md"), encoding="utf-8").read() == "new"


@pytest.mark.parametrize("lead", ["=", "+", "-", "@"])
def test_formula_injection_prefixed(lead):
    assert safeio.sanitize_cell(lead + "cmd|'/c calc'").startswith("'" + lead)


@pytest.mark.parametrize("lead", ["=", "+", "-", "@"])
def test_formula_injection_hidden_behind_whitespace(lead):
    """Excel 은 앞 공백을 무시하고 수식으로 읽습니다."""
    assert safeio.sanitize_cell("  " + lead + "cmd").startswith("'")
    assert safeio.sanitize_cell("\t" + lead + "cmd").startswith("'")


def test_control_characters_are_neutralised():
    assert safeio.sanitize_cell("\rcmd") == " cmd"
    assert safeio.sanitize_cell("a\tb") == "a b"


def test_ordinary_cells_untouched():
    assert safeio.sanitize_cell("S1_SO-CLAS.wav") == "S1_SO-CLAS.wav"
    assert safeio.sanitize_cell("−20.4") == "−20.4"       # 유니코드 마이너스는 수식 아님
    assert safeio.sanitize_cell(3.5) == "3.5"
    assert safeio.sanitize_cell(None) == ""


def test_newlines_flattened_in_cells():
    assert "\n" not in safeio.sanitize_cell("a\nb")
    assert safeio.sanitize_cell("a\r\nb") == "a b"


def test_negative_number_is_quoted_but_readable():
    """음수는 '-' 로 시작하므로 따옴표가 붙습니다 — 값 자체는 남습니다."""
    assert safeio.sanitize_cell(-20.4) == "'-20.4"


def test_csv_has_bom_for_excel(tmp_path):
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "o"))
    p = safeio.write_csv(out, "t.csv", ["파일"], [["가.wav"]])
    assert open(p, "rb").read(3) == b"\xef\xbb\xbf"
    assert "가.wav" in open(p, encoding="utf-8-sig").read()


def test_csv_round_trips(tmp_path):
    import csv
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "o"))
    p = safeio.write_csv(out, "t.csv", ["a", "b"], [[1, 2], ["x,y", "z"]])
    rows = list(csv.reader(open(p, encoding="utf-8-sig")))
    assert rows[0] == ["a", "b"]
    assert rows[2] == ["x,y", "z"]

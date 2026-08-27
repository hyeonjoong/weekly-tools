"""인코딩 자동판별(UTF-8 → CP949)과 구분자 자동판별(--delimiter auto) 테스트.

한국 임상현장에서 받는 CSV는 엑셀(Windows)이 저장한 CP949 이거나, 지역설정 때문에
세미콜론으로 구분된 경우가 흔하다. 이전 버전은 둘 다 '읽을 수 없음'으로 끝났다.
"""
import os

import pytest

from surveyscan.cli import run
from surveyscan.dataio import load_csv, sniff_delimiter

CONTENT = "ID,군,문항1,문항2\nP1,치료군,3,4\nP2,대조군,2,1\nP3,치료군,4,4\n"


def _write(path, text, encoding, delim=","):
    text = text.replace(",", delim) if delim != "," else text
    with open(path, "wb") as fh:
        fh.write(text.encode(encoding))
    return str(path)


def test_cp949_file_is_read_automatically(tmp_path):
    p = _write(tmp_path / "cp949.csv", CONTENT, "cp949")
    data = load_csv(p, id_columns=["ID"], group_column="군")
    assert data.encoding_used == "cp949"
    assert data.columns == ["문항1", "문항2"]        # 한글 헤더가 깨지지 않아야 한다
    assert data.group_values == ["치료군", "대조군", "치료군"]


def test_utf8_still_preferred(tmp_path):
    p = _write(tmp_path / "utf8.csv", CONTENT, "utf-8")
    data = load_csv(p, id_columns=["ID"])
    assert data.encoding_used == "utf-8-sig"


def test_utf8_bom_is_stripped(tmp_path):
    p = _write(tmp_path / "bom.csv", CONTENT, "utf-8-sig")
    data = load_csv(p, id_columns=["ID"])
    assert data.id_columns == ["ID"]                 # BOM 이 붙으면 'ID' 를 못 찾는다


def test_forced_encoding_disables_fallback(tmp_path):
    p = _write(tmp_path / "cp949.csv", CONTENT, "cp949")
    with pytest.raises(UnicodeDecodeError):
        load_csv(p, id_columns=["ID"], encoding="utf-8")


def test_cli_reports_non_utf8_encoding(tmp_path, capsys):
    p = _write(tmp_path / "cp949.csv", CONTENT, "cp949")
    rc = run([p, "--id-col", "ID"])
    cap = capsys.readouterr()
    assert rc == 0
    assert "cp949" in cap.err                         # 어떤 인코딩으로 읽었는지 알린다
    assert "파일 인코딩: cp949" in cap.out


def test_cli_unknown_encoding_is_error(tmp_path, capsys):
    p = _write(tmp_path / "u.csv", CONTENT, "utf-8")
    rc = run([p, "--id-col", "ID", "--encoding", "no-such-encoding"])
    assert rc == 2
    assert "알 수 없는 인코딩" in capsys.readouterr().err


def test_cli_undecodable_file_gives_advice(tmp_path, capsys):
    p = tmp_path / "bin.csv"
    p.write_bytes(b"ID,A\n\xff\xfe\x00\x81,3\n")
    rc = run([str(p), "--id-col", "ID"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--encoding" in err


@pytest.mark.parametrize("delim,name", [(";", "semi"), ("\t", "tab"), ("|", "pipe")])
def test_sniff_delimiter_variants(tmp_path, delim, name):
    p = _write(tmp_path / f"{name}.csv", CONTENT, "utf-8", delim)
    assert sniff_delimiter(p) == delim


def test_sniff_delimiter_defaults_to_comma(tmp_path):
    p = _write(tmp_path / "one.csv", "한열\n3\n4\n", "utf-8")
    assert sniff_delimiter(p) == ","


def test_sniff_delimiter_on_cp949(tmp_path):
    p = _write(tmp_path / "k.csv", CONTENT, "cp949", ";")
    assert sniff_delimiter(p) == ";"


def test_cli_delimiter_auto(tmp_path, capsys):
    p = _write(tmp_path / "semi.csv", CONTENT, "utf-8", ";")
    rc = run([p, "--id-col", "ID", "--delimiter", "auto"])
    cap = capsys.readouterr()
    assert rc == 0
    assert "세미콜론" in cap.err
    assert "문항 수   : 2" in cap.out


def test_cli_delimiter_auto_on_cp949_tab(tmp_path, capsys):
    p = _write(tmp_path / "t.csv", CONTENT, "cp949", "\t")
    rc = run([p, "--id-col", "ID", "--delimiter", "auto", "--group-col", "군"])
    cap = capsys.readouterr()
    assert rc == 0
    assert "탭" in cap.err and "문항 수   : 2" in cap.out


def test_cli_bad_delimiter_message_mentions_auto(tmp_path, capsys):
    p = _write(tmp_path / "u.csv", CONTENT, "utf-8")
    rc = run([p, "--delimiter", ";;"])
    assert rc == 2
    assert "auto" in capsys.readouterr().err


def test_sniff_missing_file_raises(tmp_path):
    with pytest.raises(OSError):
        sniff_delimiter(str(tmp_path / "nope.csv"))


def test_pinned_encoding_failure_message_is_actionable(tmp_path, capsys):
    """이미 --encoding 을 준 사용자에게 '--encoding 으로 지정하세요'라고 하면 막다른 길이다."""
    p = tmp_path / "u.csv"
    p.write_bytes("ID,문항\nP1,3\n".encode("utf-8"))
    # UTF-8 파일을 utf-16 으로 읽으라고 하면 실패한다.
    rc = run([str(p), "--id-col", "ID", "--encoding", "utf-16"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "utf-16" in err and "--encoding 을 빼고" in err


def test_nonparam_alone_warns(tmp_path, capsys):
    p = _write(tmp_path / "u.csv", CONTENT, "utf-8")
    rc = run([p, "--id-col", "ID", "--nonparam"])
    assert rc == 0
    assert "--nonparam 은" in capsys.readouterr().err


def test_sniff_ignores_trailing_empty_fields(tmp_path):
    """엑셀이 남기는 꼬리 빈 칸 때문에 구분자를 잘못 고르면 안 된다."""
    p = tmp_path / "trail.csv"
    p.write_text("ID;A;B;;;\nP1;3;4;;;\n", encoding="utf-8")
    assert sniff_delimiter(str(p)) == ";"

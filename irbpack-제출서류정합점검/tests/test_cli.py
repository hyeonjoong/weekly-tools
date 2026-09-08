"""명령줄 — 인자 오류는 전부 exit 2, 출력 파일 4종, 콘솔 인코딩 사고 방지."""
import io
import os
import sys

import pytest

from irbpack import cli
from tests.conftest import full_packet, md_icf, md_protocol, write_packet


def test_no_args_prints_help_exit_2(capsys):
    assert cli.main([]) == 2
    assert "irbpack" in capsys.readouterr().out


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0


def test_missing_path_exit_2(tmp_path, capsys):
    assert cli.main([str(tmp_path / "없음")]) == 2
    assert "경로가 없습니다" in capsys.readouterr().out


def test_empty_dir_exit_2(tmp_path, capsys):
    assert cli.main([str(tmp_path)]) == 2
    assert "읽을 문서가 없습니다" in capsys.readouterr().out


def test_bad_role_exit_2(tmp_path, capsys):
    assert cli.main([full_packet(tmp_path), "--role", "외계인=x"]) == 2
    assert "알 수 없는 역할" in capsys.readouterr().out


def test_clean_packet_exit_0_and_outputs(tmp_path, capsys):
    p = full_packet(tmp_path)
    out = str(tmp_path / "out")
    assert cli.main([p, "--out-dir", out]) == 0
    text = capsys.readouterr().out
    assert "[커버리지 자백]" in text and "치명 0건. → exit 0" in text
    assert sorted(os.listdir(out)) == sorted(["정합점검.md", "불일치목록.csv", "항목추출표.csv", "대조불가.csv"])


def test_critical_exit_1(tmp_path):
    assert cli.main([full_packet(tmp_path, icf=md_icf(visits="총 3회 방문"))]) == 1


def test_single_doc_exit_2(tmp_path, capsys):
    p = write_packet(tmp_path, {"연구계획서.md": md_protocol()})
    assert cli.main([p]) == 2
    assert "판정하지 않음" in capsys.readouterr().out


def test_unreadable_attachment_exit_3(tmp_path):
    p = full_packet(tmp_path, icf=md_icf(visits="총 3회 방문"), extra_files={"ICF_보호자용.hwp": b"HWP"})
    assert cli.main([p]) == 3


def test_out_dir_inside_packet_refused(tmp_path, capsys):
    p = full_packet(tmp_path)
    assert cli.main([p, "--out-dir", p]) == 2
    assert "서류 폴더" in capsys.readouterr().out


def test_baseline_missing_exit_2(tmp_path, capsys):
    assert cli.main([full_packet(tmp_path), "--baseline", str(tmp_path / "없음")]) == 2


def test_files_as_args_and_dedup(tmp_path):
    p = full_packet(tmp_path)
    files = [os.path.join(p, f) for f in os.listdir(p)]
    assert cli.main(files + files[:1]) == 0


def test_hidden_and_lock_files_skipped(tmp_path):
    p = full_packet(tmp_path)
    (tmp_path / "packet" / "~$연구계획서.docx").write_bytes(b"lock")
    (tmp_path / "packet" / ".DS_Store").write_bytes(b"x")
    files, _ = cli._collect([p])
    assert not any(os.path.basename(f).startswith(("~$", ".")) for f in files)


def test_console_encoding_error_does_not_become_exit_1(tmp_path, monkeypatch):
    class Ascii(io.TextIOBase):
        def __init__(self):
            self.buf = []
        def write(self, s):
            s.encode("ascii")  # 한글이면 UnicodeEncodeError
            self.buf.append(s)
            return len(s)
        def flush(self):
            pass
        encoding = "ascii"
    stream = Ascii()
    monkeypatch.setattr(sys, "stdout", stream)
    code = cli.main([full_packet(tmp_path)])
    assert code == 0
    assert stream.buf and any("exit 0" in s for s in stream.buf)


def test_manuscript_exit_2_message(tmp_path, capsys):
    ms = "\n".join(["Abstract", "x", "Introduction", "y", "Methods", "z", "Results", "w", "Discussion", "v", "References"])
    p = full_packet(tmp_path, extra_files={"paper.md": ms})
    assert cli.main([p]) == 2
    assert "draftcheck" in capsys.readouterr().out


def test_role_forced_counts_in_coverage(tmp_path, capsys):
    p = full_packet(tmp_path)
    assert cli.main([p, "--role", "프로토콜=연구계획서_v1.2.md"]) == 0
    assert "--role 지정 1개" in capsys.readouterr().out


def test_tex_in_folder_triggers_manuscript_exit_2(tmp_path, capsys):
    p = full_packet(tmp_path)
    (tmp_path / "packet" / "paper.tex").write_text("\\documentclass{article}", encoding="utf-8")
    assert cli.main([p]) == 2
    assert "draftcheck" in capsys.readouterr().out


def test_symlink_in_folder_does_not_shadow_real_doc(tmp_path):
    p = full_packet(tmp_path)
    real = os.path.join(p, "CRF_v1.2.md")
    link = os.path.join(p, "AAA_link.md")
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 불가")
    files, _ = cli._collect([p])
    assert real in files and link not in files


def test_explicit_symlink_arg_is_confessed_unread(tmp_path, capsys):
    p = full_packet(tmp_path)
    real = os.path.join(p, "CRF_v1.2.md")
    link = str(tmp_path / "link.md")
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 불가")
    files = [os.path.join(p, f) for f in os.listdir(p) if f != "CRF_v1.2.md"] + [link]
    code = cli.main(files)
    assert code == 3 and "심볼릭" in capsys.readouterr().out


def test_fatal_path_survives_ascii_console(tmp_path, monkeypatch):
    class Ascii(io.TextIOBase):
        def __init__(self):
            self.buf = []
        def write(self, s):
            s.encode("ascii")
            self.buf.append(s)
            return len(s)
        def flush(self):
            pass
        encoding = "ascii"
    stream = Ascii()
    monkeypatch.setattr(sys, "stdout", stream)
    p = write_packet(tmp_path, {"연구계획서.md": md_protocol()})
    assert cli.main([p]) == 2 and stream.buf

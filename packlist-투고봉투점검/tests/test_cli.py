"""종료코드와 거절 경로. 3(판정 불가)이 1(치명)보다 우선한다."""

import json
import os
import subprocess
import sys

import pytest

from packlist import EXIT_CRITICAL, EXIT_OK, EXIT_REFUSED, EXIT_UNDECIDABLE
from packlist.cli import main
from conftest import make_manuscript

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def full(envelope, put_file, **kwargs):
    make_manuscript(envelope, **kwargs)
    put_file(envelope, "S1_Table.pdf")
    return str(envelope)


def test_clean_envelope_exit_zero(envelope, put_file):
    assert main([full(envelope, put_file)]) == EXIT_OK


def test_orphan_exit_one(envelope, builder, png, put_file):
    builder(envelope / "원고.docx", paragraphs=["Body"],
            images={"a.png": png("a"), "b.png": png("b")}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    assert main([str(envelope)]) == EXIT_CRITICAL


def test_corrupt_docx_exit_three(envelope, corrupt_builder, put_file):
    corrupt_builder(envelope / "원고.docx")
    put_file(envelope, "S1_Table.pdf")
    assert main([str(envelope)]) == EXIT_UNDECIDABLE


def test_undecidable_beats_critical(envelope, corrupt_builder, put_file):
    """**원고**를 못 읽으면 1 이 아니라 3 이다 — 다 못 읽고 '치명 N건'은 거짓말이다."""
    corrupt_builder(envelope / "a_원고.docx")
    put_file(envelope, "S1_Table.pdf")
    put_file(envelope, "S2_Table.pdf")
    assert main([str(envelope), "--manuscript", "a_원고.docx"]) == EXIT_UNDECIDABLE


def test_unreadable_side_document_is_confessed_not_fatal(envelope, builder,
                                                         corrupt_builder, png, put_file, capsys):
    """부속 docx 하나를 못 읽는다고 원고 점검까지 포기하지 않는다."""
    builder(envelope / "a_원고.docx", paragraphs=["Body"],
            images={"x.png": png("x"), "y.png": png("y")}, anchored=["x.png"])
    corrupt_builder(envelope / "b_부속.docx")
    put_file(envelope, "S1_Table.pdf")
    assert main([str(envelope), "--manuscript", "a_원고.docx"]) == EXIT_CRITICAL
    assert "b_부속.docx" in capsys.readouterr().out


def test_single_docx_file_argument_is_refused(envelope):
    path = make_manuscript(envelope)
    assert main([path]) == EXIT_REFUSED


def test_manuscript_only_envelope_is_refused(envelope):
    make_manuscript(envelope)
    assert main([str(envelope)]) == EXIT_REFUSED


def test_irb_packet_is_refused(envelope, put_file, capsys):
    """동의서·CRF 가 섞인 폴더는 판정하지 않고 irbpack 을 가리킨다.

    부속 파일을 .docx 로 두면 'docx 가 여럿'이라는 다른 이유로도 거절되어
    IRB 탐지가 꺼져 있어도 테스트가 통과한다 — 그래서 .pdf 로 둔다.
    """
    make_manuscript(envelope)
    put_file(envelope, "연구대상자동의서.pdf")
    put_file(envelope, "증례기록서_CRF.pdf")
    assert main([str(envelope)]) == EXIT_REFUSED
    assert "irbpack" in capsys.readouterr().err


def test_ambiguous_manuscript_is_refused(envelope, put_file):
    make_manuscript(envelope, "a.docx")
    make_manuscript(envelope, "b.docx")
    put_file(envelope, "S1_Table.pdf")
    assert main([str(envelope)]) == EXIT_REFUSED


def test_ambiguous_manuscript_resolved_by_flag(envelope, put_file):
    make_manuscript(envelope, "a.docx")
    make_manuscript(envelope, "b.docx")
    put_file(envelope, "S1_Table.pdf")
    assert main([str(envelope), "--manuscript", "a.docx"]) == EXIT_OK


def test_missing_envelope_is_refused(tmp_path):
    assert main([str(tmp_path / "없음")]) == EXIT_REFUSED


def test_empty_folder_is_refused(tmp_path):
    folder = tmp_path / "빈폴더"
    folder.mkdir()
    assert main([str(folder)]) == EXIT_REFUSED


def test_bad_out_dir_is_refused(envelope, put_file, tmp_path):
    target = full(envelope, put_file)
    blocker = tmp_path / "파일"
    blocker.write_text("x")
    assert main([target, "--out-dir", str(blocker)]) == EXIT_REFUSED


def test_out_dir_writes_four_artifacts(envelope, put_file, tmp_path):
    target = full(envelope, put_file)
    out = tmp_path / "리포트"
    assert main([target, "--out-dir", str(out)]) == EXIT_OK
    assert sorted(p.name for p in out.iterdir()) == [
        "봉투점검.md", "약속대조.csv", "자산목록.csv", "회차대장.csv"]


def test_artifacts_contain_no_absolute_paths(envelope, put_file, tmp_path):
    target = full(envelope, put_file)
    out = tmp_path / "리포트"
    main([target, "--out-dir", str(out)])
    for path in out.iterdir():
        text = path.read_text(encoding="utf-8")
        assert "/Users/" not in text
        assert str(tmp_path) not in text


def test_inspect_never_judges(envelope, builder, png, put_file):
    builder(envelope / "원고.docx", paragraphs=["Body"],
            images={"a.png": png("a"), "b.png": png("b")}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    assert main([str(envelope), "--inspect"]) == EXIT_OK


def test_inspect_on_ambiguous_envelope_works(envelope, put_file):
    make_manuscript(envelope, "a.docx")
    make_manuscript(envelope, "b.docx")
    put_file(envelope, "S1_Table.pdf")
    assert main([str(envelope), "--inspect"]) == EXIT_OK


def test_inspect_reports_unreadable_docx_without_judging(envelope, corrupt_builder,
                                                         put_file, capsys):
    """--inspect 는 판정하지 않는다 — 못 읽은 파일도 '못 읽음'이라고 적고 끝낸다."""
    corrupt_builder(envelope / "원고.docx")
    put_file(envelope, "S1_Table.pdf")
    assert main([str(envelope), "--inspect"]) == EXIT_OK
    assert "못 읽음" in capsys.readouterr().out


def test_baseline_anchor_regression_exits_one(envelope, builder, png, tmp_path):
    blob_a, blob_b = png("a", 3000), png("b", 3000)
    base = tmp_path / "기준봉투"
    base.mkdir()
    builder(base / "원고.docx", paragraphs=["Body"],
            images={"a.png": blob_a, "b.png": blob_b}, anchored=["a.png", "b.png"])
    (base / "S1_Table.pdf").write_bytes(b"%PDF")
    builder(envelope / "원고.docx", paragraphs=["Body"],
            images={"a.png": blob_a, "b.png": blob_b}, anchored=["a.png"])
    (envelope / "S1_Table.pdf").write_bytes(b"%PDF")
    assert main([str(envelope), "--baseline", str(base)]) == EXIT_CRITICAL


def test_baseline_missing_folder_is_refused(envelope, put_file, tmp_path):
    target = full(envelope, put_file)
    assert main([target, "--baseline", str(tmp_path / "없음")]) == EXIT_REFUSED


def test_expect_missing_attachment_exits_one(envelope, put_file, tmp_path):
    make_manuscript(envelope, paragraphs=["Body", "See S9 Table."])
    put_file(envelope, "Cover_Letter.pdf")
    spec = tmp_path / "expect.json"
    spec.write_text(json.dumps({"required": ["S9 Table"]}), encoding="utf-8")
    assert main([str(envelope), "--expect", str(spec)]) == EXIT_CRITICAL


def test_bad_expect_json_is_refused(envelope, put_file, tmp_path):
    target = full(envelope, put_file)
    spec = tmp_path / "expect.json"
    spec.write_text("{broken", encoding="utf-8")
    assert main([target, "--expect", str(spec)]) == EXIT_REFUSED


def test_bad_limits_json_is_refused(envelope, put_file, tmp_path):
    target = full(envelope, put_file)
    spec = tmp_path / "limits.json"
    spec.write_text(json.dumps({"nothing": 1}), encoding="utf-8")
    assert main([target, "--limits", str(spec)]) == EXIT_REFUSED


def test_skip_frontmatter_flag(envelope, put_file):
    make_manuscript(envelope, "a.docx")
    make_manuscript(envelope, "b.docx")
    put_file(envelope, "S1_Table.pdf")
    assert main([str(envelope), "--manuscript", "a.docx", "--skip-frontmatter"]) == EXIT_OK


def test_module_entry_point_runs(envelope, put_file):
    target = full(envelope, put_file)
    result = subprocess.run([sys.executable, "-m", "packlist", target],
                            capture_output=True, cwd=ROOT)
    assert result.returncode == EXIT_OK
    assert "커버리지 자백" in result.stdout.decode()


def test_version_flag():
    result = subprocess.run([sys.executable, "-m", "packlist", "--version"],
                            capture_output=True, cwd=ROOT)
    assert result.returncode == 0 and b"packlist" in result.stdout


def test_broken_pipe_does_not_change_exit_code(envelope, builder, png, put_file, tmp_path):
    """`packlist ... | head` 가 종료코드를 0 으로 바꾸면 안 된다."""
    builder(envelope / "원고.docx", paragraphs=["Body"],
            images={"a.png": png("a"), "b.png": png("b")}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    script = f'{sys.executable} -m packlist "{envelope}" | head -2 >/dev/null; echo ${{PIPESTATUS[0]}}'
    result = subprocess.run(["bash", "-c", script], capture_output=True, cwd=ROOT)
    assert result.stdout.decode().strip() == str(EXIT_CRITICAL)


def test_error_messages_go_to_stderr(envelope, capsys):
    make_manuscript(envelope)
    main([str(envelope)])
    captured = capsys.readouterr()
    assert "draftcheck" in captured.err
    assert captured.out == ""

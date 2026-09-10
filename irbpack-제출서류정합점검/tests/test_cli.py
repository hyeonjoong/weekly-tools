"""CLI 와 종료코드 — 특히 **3이 1보다 우선**한다는 규칙."""

import os

import pytest

from conftest import CONSENT_LINES, PROTOCOL_LINES, write_md
from irbpack import cli
from irbpack.cli import EXIT_CRITICAL, EXIT_INPUT, EXIT_OK, EXIT_UNDECIDABLE, main


def run(args):
    return main(args)


@pytest.fixture
def packet(tmp_path):
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2_2026-09-01.md", PROTOCOL_LINES)
    write_md(directory / "ICF_성인용_v1.2_2026-09-01.md", CONSENT_LINES)
    return directory


def test_clean_packet_exit_zero(packet, tmp_path):
    assert run([str(packet), "--out-dir", str(tmp_path / "out"), "--quiet"]) == EXIT_OK


def test_conflict_exit_one(packet, tmp_path):
    write_md(packet / "모집공고안_v1.2.md",
             ["연구대상자 모집 광고안", "임상시험명: 가상 수면음향 연구",
              "모집 대상: 만 20~64세 성인", "참여 방법: 3회 방문", "참여문의 전화: 02-1234-5678"])
    assert run([str(packet), "--out-dir", str(tmp_path / "out"), "--quiet"]) == EXIT_CRITICAL


def test_unreadable_document_exit_three(packet, tmp_path):
    (packet / "동의서_보호자용.hwp").write_text("x")
    assert run([str(packet), "--out-dir", str(tmp_path / "out"), "--quiet"]) == EXIT_UNDECIDABLE


def test_exit_three_beats_exit_one(packet, tmp_path, capsys):
    """치명이 있어도 못 읽은 문서가 있으면 '판정 불가'다."""
    write_md(packet / "모집공고안_v1.2.md",
             ["연구대상자 모집 광고안", "임상시험명: 가상 수면음향 연구",
              "모집 대상: 만 20~64세 성인", "참여 방법: 3회 방문", "참여문의 전화: 02-1234-5678"])
    (packet / "동의서_보호자용.hwp").write_text("x")
    code = run([str(packet), "--out-dir", str(tmp_path / "out")])
    output = capsys.readouterr().out
    assert code == EXIT_UNDECIDABLE
    assert "판정 불가" in output


def test_too_few_compared_items_exit_three(tmp_path):
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.0.md", ["연구계획서", "선정기준", "제외기준", "대상자는 만 20~65세이다."])
    write_md(directory / "ICF_성인용_v1.0.md",
             ["연구대상자 설명문 및 동의서", "귀하는 본 연구에 참여할 것을 권유 받았습니다.", "자발적인 결정입니다."])
    assert run([str(directory), "--out-dir", str(tmp_path / "out"), "--quiet"]) == EXIT_UNDECIDABLE


def test_single_document_exit_two(tmp_path, capsys):
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2.md", PROTOCOL_LINES)
    code = run([str(directory), "--out-dir", str(tmp_path / "out")])
    assert code == EXIT_INPUT
    assert "서류 '사이'" in capsys.readouterr().err


def test_no_protocol_exit_two(tmp_path, capsys):
    directory = tmp_path / "packet"
    write_md(directory / "ICF_성인용_v1.2.md", CONSENT_LINES)
    write_md(directory / "모집공고안_v1.2.md",
             ["연구대상자 모집 광고안", "임상시험명: 가상 연구", "참여문의 전화: 02-1234-5678"])
    code = run([str(directory), "--out-dir", str(tmp_path / "out")])
    assert code == EXIT_INPUT
    assert "프로토콜" in capsys.readouterr().err


def test_two_protocols_exit_two(tmp_path, capsys):
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_A_v1.2.md", PROTOCOL_LINES)
    write_md(directory / "연구계획서_B_v1.2.md", PROTOCOL_LINES)
    code = run([str(directory), "--out-dir", str(tmp_path / "out")])
    assert code == EXIT_INPUT
    assert "2개" in capsys.readouterr().err


def test_two_protocols_resolved_by_role_option(tmp_path):
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_A_v1.2.md", PROTOCOL_LINES)
    write_md(directory / "연구계획서_B_v1.2.md", CONSENT_LINES)
    code = run([str(directory), "--role", "동의서=연구계획서_B_v1.2.md",
                "--out-dir", str(tmp_path / "out"), "--quiet"])
    assert code == EXIT_OK          # 두 문서가 같은 값을 말하므로 치명 0건


def test_role_detection_failure_exit_two(tmp_path, capsys):
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2.md", PROTOCOL_LINES)
    write_md(directory / "메모.md", ["아무 내용", "값 없음"])
    code = run([str(directory), "--out-dir", str(tmp_path / "out")])
    assert code == EXIT_INPUT
    assert "--role" in capsys.readouterr().err


def test_manuscript_exit_two(tmp_path, capsys):
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2.md", PROTOCOL_LINES)
    write_md(directory / "원고.md", ["Abstract", "본문", "Introduction", "본문", "Discussion", "본문"])
    code = run([str(directory), "--out-dir", str(tmp_path / "out")])
    error = capsys.readouterr().err
    assert code == EXIT_INPUT
    assert "원고" in error
    assert "draftcheck" in error and "revcheck" in error


def test_empty_input_exit_two(tmp_path, capsys):
    directory = tmp_path / "empty"
    directory.mkdir()
    assert run([str(directory), "--out-dir", str(tmp_path / "out")]) == EXIT_INPUT
    assert "찾지 못했습니다" in capsys.readouterr().err


def test_missing_path_exit_two(tmp_path):
    assert run([str(tmp_path / "없는폴더"), "--out-dir", str(tmp_path / "out")]) == EXIT_INPUT


def test_no_arguments_prints_help(capsys):
    assert run([]) == EXIT_INPUT
    assert "irbpack" in capsys.readouterr().out


def test_out_dir_is_a_file_exit_two(packet, tmp_path, capsys):
    target = tmp_path / "결과"
    target.write_text("x")
    code = run([str(packet), "--out-dir", str(target)])
    assert code == EXIT_INPUT
    assert "출력 오류" in capsys.readouterr().err


def test_out_dir_permission_denied_exit_two(packet, tmp_path, capsys):
    parent = tmp_path / "잠긴"
    parent.mkdir()
    os.chmod(str(parent), 0o500)
    try:
        code = run([str(packet), "--out-dir", str(parent / "안쪽")])
        assert code == EXIT_INPUT
        assert "출력 오류" in capsys.readouterr().err
    finally:
        os.chmod(str(parent), 0o700)


def test_artifacts_written(packet, tmp_path):
    out_dir = tmp_path / "out"
    run([str(packet), "--out-dir", str(out_dir), "--quiet"])
    names = sorted(os.listdir(str(out_dir)))
    assert names == sorted(["정합점검.md", "불일치목록.csv", "항목추출표.csv", "대조불가.csv"])


def test_console_prints_roles_and_coverage(packet, tmp_path, capsys):
    run([str(packet), "--out-dir", str(tmp_path / "out")])
    output = capsys.readouterr().out
    assert "[문서 역할]" in output
    assert "[커버리지 자백]" in output
    assert "프로토콜" in output


def test_quiet_prints_nothing(packet, tmp_path, capsys):
    run([str(packet), "--out-dir", str(tmp_path / "out"), "--quiet"])
    assert capsys.readouterr().out == ""


def test_inputs_can_be_files(packet, tmp_path):
    files = [os.path.join(str(packet), name) for name in sorted(os.listdir(str(packet)))]
    assert run(files + ["--out-dir", str(tmp_path / "out"), "--quiet"]) == EXIT_OK


def test_originals_not_modified(packet, tmp_path):
    before = {}
    for name in os.listdir(str(packet)):
        path = packet / name
        before[name] = (path.read_bytes(), os.stat(str(path)).st_mtime)
    run([str(packet), "--out-dir", str(tmp_path / "out"), "--quiet"])
    for name, (data, mtime) in before.items():
        path = packet / name
        assert path.read_bytes() == data
        assert os.stat(str(path)).st_mtime == mtime


def test_out_dir_inside_packet_does_not_confuse_next_run(packet, tmp_path):
    out_dir = packet / "결과"
    first = run([str(packet), "--out-dir", str(out_dir), "--quiet"])
    second = run([str(packet), "--out-dir", str(out_dir), "--quiet"])
    assert first == second == EXIT_OK


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exit_info:
        run(["--version"])
    assert exit_info.value.code == 0
    assert "irbpack" in capsys.readouterr().out


def test_exclude_role_reduces_packet(packet, tmp_path, capsys):
    write_md(packet / "모집공고안_v1.2.md",
             ["연구대상자 모집 광고안", "임상시험명: 가상 수면음향 연구", "참여문의 전화: 02-1234-5678"])
    run([str(packet), "--role", "제외=모집공고안_v1.2.md", "--out-dir", str(tmp_path / "out")])
    assert "--role 제외" in capsys.readouterr().out


def test_exclude_leaving_one_document_exit_two(packet, tmp_path, capsys):
    code = run([str(packet), "--role", "제외=ICF_성인용_v1.2_2026-09-01.md",
                "--out-dir", str(tmp_path / "out")])
    assert code == EXIT_INPUT
    assert "2개 미만" in capsys.readouterr().err


def test_bad_role_option_exit_two(packet, tmp_path, capsys):
    code = run([str(packet), "--role", "설계도=a.md", "--out-dir", str(tmp_path / "out")])
    assert code == EXIT_INPUT
    assert "모르는 역할" in capsys.readouterr().err


def test_verdict_line_wording(packet, tmp_path, capsys):
    run([str(packet), "--out-dir", str(tmp_path / "out")])
    assert "→ exit 0" in capsys.readouterr().out


def test_stdout_encoding_failure_does_not_change_exit_code(packet, tmp_path, monkeypatch):
    """콘솔 인코딩 문제가 종료코드 1로 둔갑하면 안 된다 (stimaudit 사고)."""
    monkeypatch.setattr(cli, "_configure_stdout", lambda: None)
    code = run([str(packet), "--out-dir", str(tmp_path / "out"), "--quiet"])
    assert code == EXIT_OK


def test_report_integrity_error_is_reported(packet, tmp_path, monkeypatch, capsys):
    from irbpack import report as report_module

    def broken(self, exit_code):
        raise report_module.ReportIntegrityError("커버리지 자백 없음")

    monkeypatch.setattr(report_module.Report, "markdown", broken)
    code = run([str(packet), "--out-dir", str(tmp_path / "out")])
    assert code == EXIT_INPUT
    assert "리포트 무결성" in capsys.readouterr().err


def test_packet_of_only_hwp_files(tmp_path, capsys):
    """전부 .hwp 인 패킷 — 프로토콜을 못 찾으므로 추측하지 않고 멈춘다."""
    directory = tmp_path / "packet"
    directory.mkdir()
    for name in ("연구계획서_v1.0.hwp", "동의서_v1.0.hwp", "CRF_v1.0.hwp"):
        (directory / name).write_text("한글 문서")
    code = run([str(directory), "--out-dir", str(tmp_path / "out")])
    error = capsys.readouterr().err
    assert code == EXIT_INPUT
    assert "프로토콜" in error and "읽지 못한 문서" in error


def test_four_documents_of_the_same_role(tmp_path):
    """같은 역할 문서가 여럿이어도 각각 따로 대조된다 (동의서 4종은 흔하다)."""
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2.md", PROTOCOL_LINES)
    for index, name in enumerate(["성인용", "보호자용", "대리인용", "요약본"]):
        lines = list(CONSENT_LINES)
        if index == 3:
            lines = [line.replace("만 20~65세", "만 20~64세") for line in lines]
        write_md(directory / ("ICF_%s_v1.2.md" % name), lines)
    code = run([str(directory), "--out-dir", str(tmp_path / "out"), "--quiet"])
    assert code == EXIT_CRITICAL          # 요약본만 연령이 다르다


def test_packet_where_nothing_is_extractable(tmp_path, capsys):
    """항목이 하나도 안 잡히는 패킷은 '치명 0건'이 아니라 '판정 불가'다."""
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.0.md",
             ["연구계획서", "선정기준", "제외기준", "본 문서는 서술만 있고 값이 없습니다."])
    write_md(directory / "ICF_성인용_v1.0.md",
             ["연구대상자 설명문 및 동의서", "귀하는 본 연구에 참여할 것을 권유 받았습니다.",
              "자발적인 결정입니다."])
    code = run([str(directory), "--out-dir", str(tmp_path / "out")])
    assert code == EXIT_UNDECIDABLE
    assert "판정 불가" in capsys.readouterr().out

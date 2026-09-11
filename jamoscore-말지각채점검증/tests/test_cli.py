# -*- coding: utf-8 -*-
"""CLI 종료코드·거절 경로·산출물."""

import csv
import os
import re
import subprocess
import sys

import pytest

from jamoscore.cli import main
from tests.conftest import SPEC_ARGS


def run(argv, capsys):
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def example(examples_dir, name):
    return os.path.join(examples_dir, name)


# ------------------------------------------------------------- 종료코드
def test_clean_example_exits_zero(examples_dir, capsys):
    code, out, _ = run([example(examples_dir, "정상_채점.xlsx")] + SPEC_ARGS, capsys)
    assert code == 0
    assert "치명 소견 없음" in out
    assert "종료코드 0" in out


def test_problem_example_exits_one(examples_dir, capsys):
    code, out, _ = run([example(examples_dir, "문제있음_채점.xlsx")] + SPEC_ARGS,
                       capsys)
    assert code == 1
    assert "[치명]" in out
    assert "종료코드 1" in out


def test_undetermined_example_exits_three(examples_dir, capsys):
    code, out, _ = run([example(examples_dir, "판정불가_채점.xlsx")] + SPEC_ARGS,
                       capsys)
    assert code == 3
    assert "판정 불가" in out


def test_undetermined_beats_critical(examples_dir, capsys):
    """다 못 읽었는데 '치명 N건'이라고 단정하면 거짓말이다 — 3이 1보다 우선."""
    code, out, _ = run([example(examples_dir, "판정불가_채점.xlsx")] + SPEC_ARGS,
                       capsys)
    assert code == 3
    assert "[치명]" in out            # 치명 소견은 있는데
    assert "라고 말할 수 없습니다" in out   # 그래도 판정하지 않는다


def test_summary_only_csv_is_refused(examples_dir, capsys):
    code, _, err = run([example(examples_dir, "요약만_있음.csv")] + SPEC_ARGS[:6],
                       capsys)
    assert code == 2
    assert "statwise" in err and "longistat" in err


def test_missing_subtest_is_refused(examples_dir, capsys):
    code, _, err = run([example(examples_dir, "정상_채점.xlsx")], capsys)
    assert code == 2
    assert "--subtest" in err
    assert "추론하지 않습니다" in err


def test_missing_input_is_refused(tmp_path, capsys):
    code, _, err = run([str(tmp_path / "없음.xlsx"), "--subtest", "자음:목표=전체"],
                       capsys)
    assert code == 2
    assert "찾을 수 없습니다" in err


def test_directory_input_is_refused(tmp_path, capsys):
    code, _, err = run([str(tmp_path), "--subtest", "자음:목표=전체"], capsys)
    assert code == 2
    assert "폴더" in err


def test_bad_subtest_is_refused(examples_dir, capsys):
    code, _, err = run([example(examples_dir, "정상_채점.xlsx"),
                        "--subtest", "자음:목표=이상한규칙"], capsys)
    assert code == 2
    assert "목표 규칙" in err


def test_unknown_answer_key_sheet_is_refused(examples_dir, capsys):
    code, _, err = run([example(examples_dir, "정상_채점.xlsx"),
                        "--subtest", "자음:목표=2음절초성,문항=18",
                        "--answer-key", "없는시트"], capsys)
    assert code == 2
    assert "없는시트" in err


@pytest.mark.parametrize("flag,value", [
    ("--min-count", "0"), ("--alpha", "0"), ("--alpha", "1"),
    ("--min-coverage", "-1"), ("--min-coverage", "101"),
])
def test_out_of_range_options_refused(examples_dir, capsys, flag, value):
    code, _, err = run([example(examples_dir, "정상_채점.xlsx"),
                        "--subtest", "자음:목표=2음절초성", flag, value], capsys)
    assert code == 2
    assert err.strip()


def test_duplicate_timepoints_refused(examples_dir, capsys):
    code, _, err = run([example(examples_dir, "정상_채점.xlsx"),
                        "--subtest", "자음:목표=2음절초성",
                        "--timepoints", "사전,사전"], capsys)
    assert code == 2


def test_layout_error_is_refused_not_guessed(tmp_path, capsys, examples_dir):
    """헤더를 못 찾으면 추측하지 않고 멈춘다."""
    code, _, err = run([example(examples_dir, "정상_채점.xlsx"),
                        "--subtest", "자음:목표=2음절초성,문항=18",
                        "--summary", "없는요약"], capsys)
    assert code == 2


# ---------------------------------------------------------------- inspect
def test_inspect_prints_what_it_saw(examples_dir, capsys):
    code, out, _ = run([example(examples_dir, "정상_채점.xlsx"), "--inspect"]
                       + SPEC_ARGS, capsys)
    assert code == 0
    assert "[검사 명세]" in out
    assert "[블록별 문항수·분모]" in out
    assert "S01" in out


def test_inspect_reports_pii_sheet_as_unopened(examples_dir, capsys):
    code, out, _ = run([example(examples_dir, "문제있음_채점.xlsx"), "--inspect"]
                       + SPEC_ARGS, capsys)
    assert "열지 않은" in out and "개인정보" in out


def test_inspect_warns_about_layout_before_running(examples_dir, capsys):
    code, out, _ = run([example(examples_dir, "문제있음_채점.xlsx"), "--inspect"]
                       + SPEC_ARGS, capsys)
    assert "[레이아웃 경고]" in out


# ------------------------------------------------------------- 리포트 내용
def test_report_always_has_coverage_confession(examples_dir, capsys):
    for name in ("정상_채점.xlsx", "문제있음_채점.xlsx", "판정불가_채점.xlsx"):
        _, out, _ = run([example(examples_dir, name)] + SPEC_ARGS, capsys)
        assert "[커버리지 자백]" in out
        assert '"이상 없음"이라는 뜻이 아닙니다' in out


def test_report_states_denominators(examples_dir, capsys):
    _, out, _ = run([example(examples_dir, "문제있음_채점.xlsx")] + SPEC_ARGS, capsys)
    assert "분모" in out


def test_report_mentions_deidaudit_for_pii(examples_dir, capsys):
    _, out, _ = run([example(examples_dir, "문제있음_채점.xlsx")] + SPEC_ARGS, capsys)
    assert "deidaudit" in out


def test_report_confesses_no_feature_grouping(examples_dir, capsys):
    _, out, _ = run([example(examples_dir, "정상_채점.xlsx")] + SPEC_ARGS, capsys)
    assert "--features 미지정" in out


# ------------------------------------------------------------- 산출물
@pytest.fixture
def artifacts(examples_dir, tmp_path, capsys):
    out_dir = tmp_path / "결과"
    code = main([example(examples_dir, "문제있음_채점.xlsx")] + SPEC_ARGS
                + ["--out-dir", str(out_dir)])
    capsys.readouterr()
    return code, out_dir


def test_artifacts_are_written(artifacts):
    _, out_dir = artifacts
    names = set(os.listdir(str(out_dir)))
    for expected in ("채점검증.md", "문제목록.csv", "문항점수.csv", "규칙대조.csv",
                     "동일쌍_다른채점.csv", "임계차.csv", "Methods_초안.md",
                     "statwise_longistat_입력"):
        assert expected in names, expected


def test_item_score_csv_schema(artifacts):
    _, out_dir = artifacts
    with open(str(out_dir / "문항점수.csv"), encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["ID", "시점", "검사", "하위블록", "정답수", "문항수", "분모",
                       "채점된문항수", "단어%", "음소%", "비고"]
    assert len(rows) > 1


def test_ready_to_run_csvs_have_three_columns(artifacts):
    _, out_dir = artifacts
    ready = out_dir / "statwise_longistat_입력"
    files = sorted(os.listdir(str(ready)))
    assert files
    with open(str(ready / files[0]), encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["ID", "시점", "점수"]


def test_artifacts_contain_no_absolute_paths(artifacts):
    """집 디렉터리 이름이 새어 나가면 산출물을 공유할 수 없다."""
    _, out_dir = artifacts
    home = os.path.expanduser("~")
    for root, _, files in os.walk(str(out_dir)):
        for name in files:
            text = open(os.path.join(root, name), encoding="utf-8-sig",
                        errors="replace").read()
            assert home not in text, name
            assert not re.search(r"(?<![\w가-힣])/(Users|home)/", text), name


def test_artifacts_contain_no_identifying_patterns(artifacts):
    """산출물에 이름·전화·생년월일 패턴이 한 건도 없어야 한다."""
    _, out_dir = artifacts
    phone = re.compile(r"01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}")
    rrn = re.compile(r"\b\d{6}[-–—]\d{7}\b")
    birth = re.compile(r"\b(19|20)\d{2}[-./]\d{1,2}[-./]\d{1,2}\b")
    for root, _, files in os.walk(str(out_dir)):
        for name in files:
            text = open(os.path.join(root, name), encoding="utf-8-sig",
                        errors="replace").read()
            assert not phone.search(text), name
            assert not rrn.search(text), name
            assert not birth.search(text), name


def test_out_dir_that_is_a_file_gives_korean_error(examples_dir, tmp_path, capsys):
    blocker = tmp_path / "결과"
    blocker.write_text("파일", encoding="utf-8")
    code, _, err = run([example(examples_dir, "정상_채점.xlsx")] + SPEC_ARGS
                       + ["--out-dir", str(blocker)], capsys)
    assert code == 2
    assert "파일" in err
    assert "Traceback" not in err


def test_out_dir_symlink_is_refused(examples_dir, tmp_path, capsys):
    real = tmp_path / "진짜"
    real.mkdir()
    link = tmp_path / "링크"
    os.symlink(str(real), str(link))
    code, _, err = run([example(examples_dir, "정상_채점.xlsx")] + SPEC_ARGS
                       + ["--out-dir", str(link)], capsys)
    assert code == 2
    assert "심볼릭" in err


def test_artifact_symlink_does_not_overwrite_input(examples_dir, tmp_path, capsys):
    out_dir = tmp_path / "결과"
    out_dir.mkdir()
    victim = tmp_path / "소중한원본.md"
    victim.write_text("ORIGINAL", encoding="utf-8")
    os.symlink(str(victim), str(out_dir / "채점검증.md"))
    code, _, err = run([example(examples_dir, "정상_채점.xlsx")] + SPEC_ARGS
                       + ["--out-dir", str(out_dir)], capsys)
    assert code == 2
    assert victim.read_text(encoding="utf-8") == "ORIGINAL"


# ------------------------------------------------------------- 원본 불변
def test_input_workbook_is_never_modified(examples_dir, tmp_path, capsys):
    src = example(examples_dir, "문제있음_채점.xlsx")
    before = open(src, "rb").read()
    run([src] + SPEC_ARGS + ["--out-dir", str(tmp_path / "결과")], capsys)
    assert open(src, "rb").read() == before


# ------------------------------------------------------------- 파이프
def test_exit_code_survives_head_pipe(examples_dir, repo_root):
    """`jamoscore ... | head` 에서 종료코드가 뒤집히면 안 된다."""
    cmd = [sys.executable, "-m", "jamoscore",
           example(examples_dir, "문제있음_채점.xlsx")] + SPEC_ARGS
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            cwd=repo_root)
    head = subprocess.Popen(["head", "-3"], stdin=proc.stdout,
                            stdout=subprocess.DEVNULL)
    proc.stdout.close()
    head.wait()
    proc.wait()
    assert proc.returncode in (1, -13), proc.returncode


def test_module_entry_point_runs(examples_dir, repo_root):
    proc = subprocess.run(
        [sys.executable, "-m", "jamoscore", example(examples_dir, "정상_채점.xlsx")]
        + SPEC_ARGS, capture_output=True, cwd=repo_root)
    assert proc.returncode == 0
    assert "[커버리지 자백]".encode() in proc.stdout


def test_version_flag(repo_root):
    proc = subprocess.run([sys.executable, "-m", "jamoscore", "--version"],
                          capture_output=True, cwd=repo_root)
    assert proc.returncode == 0
    assert b"jamoscore" in proc.stdout


def test_help_mentions_no_score_editing(repo_root):
    proc = subprocess.run([sys.executable, "-m", "jamoscore", "--help"],
                          capture_output=True, cwd=repo_root)
    text = proc.stdout.decode()
    assert "점수를 고치지 않고" in text

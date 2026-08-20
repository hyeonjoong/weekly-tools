"""CLI — 종료 코드와 예제 인수 기준.

종료 코드는 이 툴의 계약입니다(스크립트에서 이걸 보고 분기함). 그래서
0/1/2/3 전부와 우선순위(3 > 1)를 못 박습니다.
"""

import os

import pytest
from conftest import make_bundle, write

from tracecheck.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --------------------------------------------------------------------------- #
# 예제 인수 기준 — 기획서 완료 기준 그대로
# --------------------------------------------------------------------------- #

def test_clean_example_has_no_findings(capsys, examples_dir):
    """크라잉울프 테스트: 원고와 번들이 일치하면 치명 0 · 경고 0."""
    code, out, _err = run(capsys,
                          os.path.join(examples_dir, "clean", "원고.md"),
                          "--outputs",
                          os.path.join(examples_dir, "clean", "분석출력_2026-08-18"),
                          "--no-files")
    assert code == 0
    assert "[치명] 0건" in out and "[경고] 0건" in out
    assert "미매칭율 0.0%" in out


def test_flawed_example_finds_exactly_the_three_planted_defects(capsys,
                                                               examples_dir):
    code, out, _err = run(capsys,
                          os.path.join(examples_dir, "flawed", "원고.md"),
                          "--outputs",
                          os.path.join(examples_dir, "flawed", "분석출력_2026-08-18"),
                          "--previous",
                          os.path.join(examples_dir, "flawed", "분석출력_2026-08-03"),
                          "--no-files")
    assert code == 1
    assert "[치명] 2건" in out and "[경고] 1건" in out
    assert "11.7" in out and "9.82" in out          # 구버전 잔존 + 현재 값
    assert "91.2" in out                            # 출처 없음
    assert "6.25" in out and "백분율" in out         # 단위 혼동


def test_flawed_example_without_previous_still_finds_two(capsys, examples_dir):
    """`--previous` 없이는 구버전 잔존이 '출처 없음'으로만 잡힙니다."""
    code, out, _err = run(capsys,
                          os.path.join(examples_dir, "flawed", "원고.md"),
                          "--outputs",
                          os.path.join(examples_dir, "flawed", "분석출력_2026-08-18"),
                          "--no-files")
    assert code == 1
    assert "[치명] 2건" in out
    assert "구버전 잔존 검사는 수행되지 않았습니다" in out


# --------------------------------------------------------------------------- #
# 종료 코드
# --------------------------------------------------------------------------- #

def test_exit_0_when_everything_traced(capsys, simple_case):
    manuscript, bundle = simple_case
    code, _out, _err = run(capsys, manuscript, "--outputs", bundle, "--no-files")
    assert code == 0


def test_exit_2_without_outputs(capsys, simple_case):
    """번들 없이 도는 순간 이 툴은 numcheck 의 열등한 재탕이 됩니다."""
    manuscript, _bundle = simple_case
    code, _out, err = run(capsys, manuscript, "--no-files")
    assert code == 2
    assert "--outputs" in err and "numcheck" in err


def test_exit_2_on_warning_only(capsys, tmp_path):
    manuscript = write(str(tmp_path / "m.md"),
                       "## Results\n비율은 6.25%, 12.44, 15.91, -3.47, 0.0021 이었다.\n")
    bundle = make_bundle(tmp_path / "out",
                         {"a.csv": "rate,mean,mean2,diff,p\n"
                                   "0.0625,12.44,15.91,-3.47,0.0021\n"})
    code, out, _err = run(capsys, manuscript, "--outputs", bundle, "--no-files")
    assert code == 2
    assert "[치명] 0건" in out and "[경고] 1건" in out


def test_exit_2_on_missing_manuscript(capsys, tmp_path):
    code, _out, err = run(capsys, str(tmp_path / "없음.md"), "--outputs",
                          str(tmp_path), "--no-files")
    assert code == 2 and "찾을 수 없습니다" in err


def test_exit_3_beats_exit_1(capsys, tmp_path):
    """판정불가는 치명보다 우선 — 대조율이 낮으면 치명을 쏟아내지 않습니다."""
    manuscript = write(str(tmp_path / "m.md"),
                       "## Results\n값은 11.1, 22.2, 33.3, 44.4, 55.5, 66.6.\n")
    bundle = make_bundle(tmp_path / "out", {"a.csv": "m\n11.1\n"})
    code, out, _err = run(capsys, manuscript, "--outputs", bundle, "--no-files")
    assert code == 3
    assert "판정불가" in out
    assert "[치명]" not in out            # 목록을 쏟아내지 않습니다


def test_exit_3_when_bundle_has_no_numbers(capsys, tmp_path, simple_case):
    manuscript, _ = simple_case
    empty = make_bundle(tmp_path / "empty", {"readme.md": "숫자 없는 파일\n"})
    code, out, _err = run(capsys, manuscript, "--outputs", empty, "--no-files")
    assert code == 3 and "수치 셀을 하나도" in out


def test_max_unmatched_can_be_relaxed(capsys, tmp_path):
    manuscript = write(str(tmp_path / "m.md"),
                       "## Results\n값은 11.1, 22.2, 33.3, 44.4, 55.5, 66.6.\n")
    bundle = make_bundle(tmp_path / "out", {"a.csv": "m\n11.1\n"})
    code, out, _err = run(capsys, manuscript, "--outputs", bundle,
                          "--max-unmatched", "90", "--no-files")
    assert code == 1 and "[치명] 5건" in out


# --------------------------------------------------------------------------- #
# 산출물
# --------------------------------------------------------------------------- #

def test_outputs_are_written_into_out_dir_only(capsys, simple_case, tmp_path):
    manuscript, bundle = simple_case
    out_dir = tmp_path / "결과"
    code, _out, _err = run(capsys, manuscript, "--outputs", bundle,
                           "--out-dir", str(out_dir))
    assert code == 0
    assert sorted(os.listdir(str(out_dir))) == \
        sorted(["출처대조.md", "문제목록.csv", "대조표.csv", "요약.txt"])


def test_out_dir_inside_bundle_is_refused(capsys, simple_case):
    """산출물이 번들 안에 떨어지면 다음 실행 때 자기 리포트를 대조하게 됩니다."""
    manuscript, bundle = simple_case
    code, _out, err = run(capsys, manuscript, "--outputs", bundle,
                          "--out-dir", os.path.join(bundle, "결과"))
    assert code == 2 and "번들 폴더 안" in err


def test_manuscript_inside_bundle_is_refused(capsys, tmp_path):
    bundle = make_bundle(tmp_path / "out", {"a.csv": "m\n1.5\n"})
    manuscript = write(os.path.join(bundle, "원고.md"), "## Results\n1.5\n")
    code, _out, err = run(capsys, manuscript, "--outputs", bundle, "--no-files")
    assert code == 2 and "원고가 출력 번들" in err


def test_dump_text_needs_no_bundle(capsys, examples_dir):
    code, out, _err = run(capsys, os.path.join(examples_dir, "clean", "원고.md"),
                          "--dump-text")
    assert code == 0
    assert "대조 대상" in out and "건너뜀" in out
    assert "진단 모드" in out


def test_sections_flag_expands_scope(capsys, examples_dir):
    """Methods 를 포함시키면 등록번호·유의수준이 아니라 다른 숫자가 늘어납니다."""
    args = [os.path.join(examples_dir, "clean", "원고.md"), "--outputs",
            os.path.join(examples_dir, "clean", "분석출력_2026-08-18"), "--no-files"]
    _code, base, _err = run(capsys, *args)
    _code2, wide, _err2 = run(capsys, *(args + ["--sections",
                                                "abstract,results,tables,captions,discussion"]))
    assert "대조 대상" in base and "대조 대상" in wide
    assert base != wide


def test_quiet_prints_only_verdict(capsys, simple_case):
    manuscript, bundle = simple_case
    code, out, _err = run(capsys, manuscript, "--outputs", bundle, "--no-files",
                          "--quiet")
    assert code == 0 and out.strip().startswith("판정:")


@pytest.mark.parametrize("flag,value", [("--max-files", "0"),
                                        ("--max-cells", "-1"),
                                        ("--max-unmatched", "150")])
def test_invalid_limits_are_rejected(capsys, simple_case, flag, value):
    manuscript, bundle = simple_case
    code, _out, err = run(capsys, manuscript, "--outputs", bundle, flag, value,
                          "--no-files")
    assert code == 2 and err


def test_version_and_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0

# -*- coding: utf-8 -*-
"""1라운드 적대 검토에서 나온 결함들의 회귀 테스트.

전부 "조용히 틀린다"는 한 가지 실패 양식이다: 툴이 보지 않은 것을 자백하지 않으면
사람은 그것을 '확인했다'로 읽는다.
"""

import io
import os
import re

import pytest

from conftest import T0, T1, write
from stalecheck import cli, engine, report
from stalecheck.errors import EXIT_OK, EXIT_REFUSED, EXIT_UNDECIDABLE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def call(args):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(args, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def mini(tmp_path, work_files, pkg_files):
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    for rel, data, mt in work_files:
        write(work / rel, data, mt)
    for rel, data, mt in pkg_files:
        write(pkg / rel, data, mt)
    return str(work), str(pkg)


# ---------------------------------------------------- 확장자 밖 파일을 자백한다
def test_extension_filtered_files_are_counted(tmp_path):
    work, pkg = mini(tmp_path,
                     [("Figure1.tif", b"new", T1), ("표S1.xlsx", b"a", T1)],
                     [("Figure1.tif", b"old", T0)])
    a = engine.analyze(work, [pkg])
    work_ext, pkg_ext = a.skipped_ext
    assert work_ext[".tif"] == 1 and work_ext[".xlsx"] == 1
    assert pkg_ext[".tif"] == 1


def test_extension_filtered_files_appear_in_confession(tmp_path):
    work, pkg = mini(tmp_path,
                     [("a.md", "x", T1), ("Figure1.tif", b"new", T1)],
                     [("a.md", "x", T0), ("Figure1.tif", b"old", T0)])
    text = report.render_console(engine.analyze(work, [pkg]))
    assert "확장자가 대상 밖이라 보지 않은 파일" in text
    assert "tif 2" in text


def test_confession_offers_the_exact_ext_flags(tmp_path):
    work, pkg = mini(tmp_path,
                     [("a.md", "x", T1), ("F.tif", b"n", T1)],
                     [("a.md", "x", T0), ("F.tif", b"o", T0)])
    text = report.render_console(engine.analyze(work, [pkg]))
    assert "--ext tif" in text


def test_extensionless_files_counted_under_a_label(tmp_path):
    work, pkg = mini(tmp_path, [("a.md", "x", T1), ("Makefile", "x", T1)],
                     [("a.md", "x", T0)])
    work_ext, _ = engine.analyze(work, [pkg]).skipped_ext
    assert work_ext["(확장자없음)"] == 1


def test_all_tif_envelope_exits_three_not_zero(tmp_path):
    """봉투가 전부 .tif 면 '치명 0건 exit 0' 이 아니라 판정불가 exit 3 이어야 한다."""
    work, pkg = mini(tmp_path, [("Figure1.tif", b"new", T1)],
                     [("Figure1.tif", b"old", T0)])
    code, _, err = call(["--work", work, "--package", pkg, "--quiet"])
    assert code == EXIT_UNDECIDABLE
    assert "--ext tif" in err


def test_all_tif_envelope_exit_three_names_the_count(tmp_path):
    work, pkg = mini(tmp_path, [("a.tif", b"n", T1), ("b.eps", b"n", T1)],
                     [("a.tif", b"o", T0), ("b.eps", b"o", T0)])
    _, _, err = call(["--work", work, "--package", pkg, "--quiet"])
    assert "봉투 파일 2개가 대상 확장자 밖" in err


def test_ext_flag_makes_the_tif_pair_visible(tmp_path):
    work, pkg = mini(tmp_path, [("Figure1.tif", b"new", T1)],
                     [("Figure1.tif", b"old", T0)])
    code, out, _ = call(["--work", work, "--package", pkg, "--ext", "tif"])
    assert code == 1
    assert "[치명] 봉투가 구세대 — 1쌍" in out


def test_empty_envelope_still_exits_zero(tmp_path):
    """봉투에 파일이 아예 없으면 exit 3 이 아니다 — 거짓 경보를 만들지 않는다."""
    work, pkg = mini(tmp_path, [("a.md", "x", T1)], [])
    code, _, _ = call(["--work", work, "--package", pkg, "--quiet"])
    assert code == EXIT_OK


# ---------------------------------------------------- --inspect 가 --package 없이 동작
def test_inspect_without_package_still_prints_the_scan(tmp_path):
    """--package 가 없어도 --inspect 는 '무엇을 읽었는지'를 인쇄한다.
    판정은 하지 않으므로 종료코드는 2다."""
    work, _ = mini(tmp_path, [("a.md", "x", T1)], [("a.md", "x", T0)])
    code, out, _ = call(["--work", work, "--inspect"])
    assert code == EXIT_REFUSED
    assert "[읽은 것]" in out


def test_inspect_without_package_prints_counts(tmp_path):
    work, _ = mini(tmp_path,
                   [("a.md", "x", T1), ("b.py", "x", T1), ("c.tif", b"x", T1)],
                   [("a.md", "x", T0)])
    _, out, _ = call(["--work", work, "--inspect"])
    assert "작업 폴더 파일 2개" in out
    assert "md 1" in out and "py 1" in out
    assert "tif 1" in out


def test_inspect_without_package_lists_candidates(tmp_path):
    work, _ = mini(tmp_path, [("a.md", "x", T1)], [("a.md", "x", T0)])
    _, out, _ = call(["--work", work, "--inspect"])
    assert "[봉투 후보]" in out and "submission" in out


def test_inspect_without_package_says_it_did_not_judge(tmp_path):
    work, _ = mini(tmp_path, [("a.md", "x", T1)], [("a.md", "x", T0)])
    _, out, _ = call(["--work", work, "--inspect"])
    assert "판정하지 않았습니다" in out
    assert "[치명]" not in out


def test_without_inspect_missing_package_still_refuses(tmp_path):
    work, _ = mini(tmp_path, [("a.md", "x", T1)], [("a.md", "x", T0)])
    code, _, err = call(["--work", work])
    assert code == EXIT_REFUSED and "--package" in err


def test_inspect_without_package_prints_excluded_dirs(tmp_path):
    work, _ = mini(tmp_path, [("a.md", "x", T1), ("_이전/old.md", "x", T0)],
                   [("a.md", "x", T0)])
    _, out, _ = call(["--work", work, "--inspect"])
    assert "_이전" in out


# ---------------------------------------------------- 봉투 여러 벌일 때 짝없음
def test_work_only_is_summed_across_packages(tmp_path):
    """A 봉투에 들어간 파일이 B 봉투 기준으로 '작업폴더에만 있음' 이 되면 안 된다."""
    work = tmp_path / "논문"
    for name in ("SUBMISSION_A", "SUBMISSION_B"):
        os.makedirs(str(work / name))
    write(work / "src" / "a.md", "x", T1)
    write(work / "src" / "b.md", "y", T1)
    write(work / "SUBMISSION_A" / "a.md", "x", T0)
    write(work / "SUBMISSION_B" / "b.md", "y", T0)
    a = engine.analyze(str(work), [str(work / "SUBMISSION_A"),
                                   str(work / "SUBMISSION_B")])
    assert a.work_only == []
    assert "작업폴더에만 0개" in report.render_console(a)


def test_work_only_still_reports_genuinely_unpaired(tmp_path):
    work = tmp_path / "논문"
    os.makedirs(str(work / "submission"))
    write(work / "src" / "a.md", "x", T1)
    write(work / "src" / "메모.md", "y", T1)
    write(work / "submission" / "a.md", "x", T0)
    a = engine.analyze(str(work), [str(work / "submission")])
    assert [r.rel for r in a.work_only] == [os.path.join("src", "메모.md")]


def test_unpaired_csv_uses_summed_work_only(tmp_path):
    work = tmp_path / "논문"
    for name in ("SUBMISSION_A", "SUBMISSION_B"):
        os.makedirs(str(work / name))
    write(work / "src" / "a.md", "x", T1)
    write(work / "SUBMISSION_A" / "a.md", "x", T0)
    write(work / "SUBMISSION_B" / "혼자.md", "z", T0)
    a = engine.analyze(str(work), [str(work / "SUBMISSION_A"),
                                   str(work / "SUBMISSION_B")])
    labels = [r[0] for r in report.unpaired_rows(a)]
    assert labels.count("작업폴더에만 있음") == 0
    assert labels.count("봉투에만 있음") == 1


# ---------------------------------------------------- 봉투 쪽 제외 폴더 자백
def test_excluded_dirs_inside_the_envelope_are_confessed(tmp_path):
    work, pkg = mini(tmp_path, [("a.md", "x", T1)],
                     [("a.md", "x", T0), ("03_archive/보충표.md", "old", T0)])
    text = report.render_console(engine.analyze(work, [pkg]))
    assert os.path.join("submission", "03_archive") in text


def test_excluded_dirs_property_labels_the_package(tmp_path):
    work, pkg = mini(tmp_path, [("a.md", "x", T1)],
                     [("a.md", "x", T0), ("_superseded/x.md", "o", T0)])
    a = engine.analyze(work, [pkg])
    assert os.path.join("submission", "_superseded") in a.excluded_dirs


def test_inspect_prints_envelope_excluded_dirs(tmp_path):
    work, pkg = mini(tmp_path, [("a.md", "x", T1)],
                     [("a.md", "x", T0), ("03_archive/x.md", "o", T0)])
    _, out, _ = call(["--work", work, "--package", pkg, "--inspect"])
    assert "03_archive" in out


# ---------------------------------------------------- 가장 결정적인 줄이 위로
def test_summary_line_precedes_the_item_list(tmp_path):
    work, pkg = mini(tmp_path,
                     [("a.md", "새A", T1), ("b.md", "새B", T1)],
                     [("a.md", "옛A", T0), ("b.md", "옛B", T0)])
    lines = report.render_console(engine.analyze(work, [pkg])).splitlines()
    summary = next(i for i, l in enumerate(lines) if "갱신되지 않았습니다" in l)
    first_item = next(i for i, l in enumerate(lines) if l.strip() == "a.md")
    assert summary < first_item


# ---------------------------------------------------- 실행.command 이식성
def test_launcher_has_no_non_ascii_shell_variables():
    """bash 는 비ASCII 식별자를 거부한다 — 더블클릭 첫 화면에 에러가 뜨면 안 된다."""
    path = os.path.join(ROOT, "실행.command")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assignments = re.findall(r"^\s*([^\s=#]+)=", text, re.MULTILINE)
    for name in assignments:
        assert name.isascii(), "쉘 변수 이름이 ASCII 가 아닙니다: %r" % name


def test_launcher_variable_references_are_ascii():
    path = os.path.join(ROOT, "실행.command")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    for ref in re.findall(r"\$\{?([A-Za-z_\W][^\s\"'}/]*)", text):
        if ref and not ref[0].isascii():
            pytest.fail("비ASCII 쉘 변수 참조: %r" % ref)


def test_launcher_demonstrates_all_four_exit_codes():
    path = os.path.join(ROOT, "실행.command")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    order = [int(m) for m in re.findall(r"종료코드 (\d)\b", text)]
    assert order[:4] == [0, 1, 3, 2]

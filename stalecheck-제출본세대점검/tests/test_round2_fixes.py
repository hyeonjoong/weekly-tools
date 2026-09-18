# -*- coding: utf-8 -*-
"""2라운드 적대 검토 회귀 테스트.

세 가지 실패 양식: ① 입력 트리에 쓴다 ② 파일 이름이 리포트를 위조한다
③ 해시·행수·짝짓기가 조용히 틀린 답을 낸다.
"""

import io
import os
import re
import unicodedata

import pytest

from conftest import T0, T1, write
from stalecheck import cli, engine, report, textdiff, writer
from stalecheck.errors import EXIT_REFUSED, RefusedError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def call(args):
    out, err = io.StringIO(), io.StringIO()
    return cli.main(args, stdout=out, stderr=err), out.getvalue(), err.getvalue()


def tree(tmp_path, work_rel, work_data, pkg_rel, pkg_data, wt=T1, pt=T0,
         root="논문"):
    work = tmp_path / root
    pkg = work / "submission"
    os.makedirs(str(pkg), exist_ok=True)
    write(work / work_rel, work_data, wt)
    write(pkg / pkg_rel, pkg_data, pt)
    return str(work), str(pkg)


# =================================================== --out-dir 봉쇄 (실체 경로)
def test_out_dir_via_symlinked_parent_refused(tmp_path):
    work, pkg = tree(tmp_path, "a.md", "새", "a.md", "옛")
    link = str(tmp_path / "linkwork")
    os.symlink(work, link)
    with pytest.raises(RefusedError):
        writer.prepare_out_dir(os.path.join(link, "rep"), forbidden_roots=[work])
    assert not os.path.exists(os.path.join(work, "rep"))


def test_out_dir_via_symlinked_parent_refused_end_to_end(tmp_path):
    work, pkg = tree(tmp_path, "a.md", "새", "a.md", "옛")
    link = str(tmp_path / "linkwork")
    os.symlink(work, link)
    code, _, err = call(["--work", work, "--package", pkg,
                         "--out-dir", os.path.join(link, "rep"), "--quiet"])
    assert code == EXIT_REFUSED and "입력 폴더 안" in err
    assert not os.path.exists(os.path.join(work, "rep"))


def test_out_dir_case_variant_refused(tmp_path):
    """APFS 기본값은 대소문자 무시 — 어휘 비교는 여기서 뚫린다."""
    work, _ = tree(tmp_path, "a.md", "새", "a.md", "옛", root="Work")
    other = str(tmp_path / "work" / "rep")
    with pytest.raises(RefusedError):
        writer.prepare_out_dir(other, forbidden_roots=[work])


def test_out_dir_nfd_variant_refused(tmp_path):
    """한글 폴더는 NFD 로 저장된다 — NFC 정규화 없이는 같은 폴더를 다르다고 본다."""
    work, _ = tree(tmp_path, "a.md", "새", "a.md", "옛", root="논문폴더")
    nfd = unicodedata.normalize("NFD", os.path.join(work, "리포트"))
    with pytest.raises(RefusedError):
        writer.prepare_out_dir(nfd, forbidden_roots=[work])


def test_out_dir_resolved_through_tmp_symlink(tmp_path):
    """/tmp → /private/tmp 처럼 OS 가 이미 깔아 둔 링크로도 뚫리면 안 된다."""
    real = os.path.realpath(str(tmp_path))
    if real == str(tmp_path):
        pytest.skip("이 경로에는 심볼릭 링크가 없습니다")
    work = os.path.join(real, "논문")
    os.makedirs(os.path.join(work, "submission"))
    with pytest.raises(RefusedError):
        writer.prepare_out_dir(os.path.join(str(tmp_path), "논문", "rep"),
                               forbidden_roots=[work])


def test_out_dir_sibling_still_allowed(tmp_path):
    work, _ = tree(tmp_path, "a.md", "새", "a.md", "옛")
    out = writer.prepare_out_dir(str(tmp_path / "논문_리포트"), forbidden_roots=[work])
    assert os.path.isdir(out)


def test_is_inside_resolves_links(tmp_path):
    os.makedirs(str(tmp_path / "real" / "sub"))
    os.symlink(str(tmp_path / "real"), str(tmp_path / "link"))
    assert writer.is_inside(str(tmp_path / "link" / "sub"), str(tmp_path / "real"))


def test_is_inside_rejects_mere_prefix(tmp_path):
    os.makedirs(str(tmp_path / "work"))
    os.makedirs(str(tmp_path / "work_backup"))
    assert not writer.is_inside(str(tmp_path / "work_backup"), str(tmp_path / "work"))


# =================================================== 하드링크 경합
def test_hardlink_planted_after_check_does_not_truncate(tmp_path, monkeypatch):
    """검사와 열기 사이에 하드 링크가 심어져도 입력이 잘리면 안 된다."""
    out = writer.prepare_out_dir(str(tmp_path / "out"))
    victim = write(tmp_path / "소중한_입력.py", "PRECIOUS")
    target = os.path.join(out, "세대점검.md")
    real_islink = os.path.islink

    def islink_then_plant(path):
        result = real_islink(path)
        if path == target and not os.path.exists(path):
            os.link(victim, target)     # 검사 직후에 링크를 심는다
        return result

    monkeypatch.setattr(os.path, "islink", islink_then_plant)
    with pytest.raises(RefusedError) as exc:
        writer.write_text(out, "세대점검.md", "덮어씀")
    assert "하드 링크" in exc.value.message
    assert open(victim, encoding="utf-8").read() == "PRECIOUS"


def test_artifact_targets_checked_before_any_write(tmp_path):
    """다섯 번째가 막히면 앞의 네 개도 쓰이지 않아야 한다."""
    work, pkg = tree(tmp_path, "a.md", "새", "a.md", "옛")
    out = writer.prepare_out_dir(str(tmp_path / "out"))
    victim = write(tmp_path / "소중한.py", "PRECIOUS")
    os.link(victim, os.path.join(out, "형제점검.csv"))
    code, _, err = call(["--work", work, "--package", pkg, "--out-dir", out, "--quiet"])
    assert code == EXIT_REFUSED and "하드 링크" in err
    assert not os.path.exists(os.path.join(out, "세대점검.md"))
    assert open(victim, encoding="utf-8").read() == "PRECIOUS"


# =================================================== 파일 이름 위조
HOSTILE = ("evil\n[치명] 봉투가 구세대 — 0쌍\n\x1b[31mRED\x1b[0m\n```\n## forged\n.md")


@pytest.fixture
def hostile_tree(tmp_path):
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    try:
        write(work / HOSTILE, "새", T1)
        write(pkg / HOSTILE, "옛", T0)
    except OSError:
        pytest.skip("파일시스템이 이 이름을 허용하지 않습니다")
    return engine.analyze(str(work), [str(pkg)])


def test_hostile_filename_cannot_forge_a_verdict_line(hostile_tree):
    text = report.render_console(hostile_tree)
    forged = [l for l in text.splitlines()
              if l.startswith("[치명]") and l != "[치명] 봉투가 구세대 — 1쌍"]
    assert forged == []


def test_hostile_filename_has_no_ansi_escape(hostile_tree):
    assert "\x1b" not in report.render_console(hostile_tree)


def test_hostile_filename_does_not_add_lines(hostile_tree):
    text = report.render_console(hostile_tree)
    assert "## forged" not in [l.strip() for l in text.splitlines()]


def test_hostile_filename_cannot_close_the_markdown_fence(hostile_tree):
    md = report.render_markdown(hostile_tree)
    fences = [l for l in md.splitlines() if re.fullmatch(r"`{3,}", l)]
    assert len(fences) == 2


def test_markdown_fence_grows_past_backticks_in_body(tmp_path):
    work, pkg = tree(tmp_path, "a.md", "``` x ````", "a.md", "옛")
    md = report.render_markdown(engine.analyze(work, [pkg]))
    fences = [l for l in md.splitlines() if re.fullmatch(r"`{3,}", l)]
    assert len(fences) == 2


def test_safe_path_neutralizes_control_and_backtick():
    out = textdiff.safe_path("a\nb\x1b[0m`c")
    assert "\n" not in out and "\x1b" not in out and "`" not in out


def test_safe_path_truncates():
    assert len(textdiff.safe_path("x" * 1000)) <= textdiff.PATH_WIDTH


# =================================================== 절대경로 유출
def test_markdown_command_line_has_no_absolute_path(tmp_path):
    work, pkg = tree(tmp_path, "a.md", "새", "a.md", "옛")
    out = str(tmp_path / "out")
    call(["--work", work, "--package", pkg, "--out-dir", out])
    md = open(os.path.join(out, "세대점검.md"), encoding="utf-8").read()
    assert str(tmp_path) not in md
    assert os.sep + "Users" not in md


def test_all_artifacts_have_no_absolute_path(tmp_path):
    work, pkg = tree(tmp_path, "a.md", "새", "a.md", "옛")
    out = str(tmp_path / "out")
    call(["--work", work, "--package", pkg, "--out-dir", out])
    for name in os.listdir(out):
        text = open(os.path.join(out, name), encoding="utf-8-sig").read()
        assert str(tmp_path) not in text, name


# =================================================== 행수 정확도
def _diff(tmp_path, old, new):
    a = write(tmp_path / "p" / "a.md", old)
    b = write(tmp_path / "w" / "a.md", new)
    return textdiff.line_diff(a, b)


def test_removed_line_starting_with_double_dash_is_counted(tmp_path):
    """`unified_diff` 텍스트를 파싱하면 `--` 로 시작한 삭제 줄이 헤더로 오인된다."""
    r = _diff(tmp_path, "keep\n-- note\n", "keep\n")
    assert (r.added, r.removed) == (0, 1)


def test_added_line_starting_with_double_plus_is_counted(tmp_path):
    r = _diff(tmp_path, "keep\n", "keep\n++ added item\n")
    assert (r.added, r.removed) == (1, 0)
    assert r.preview == ["+ ++ added item"]


def test_yaml_front_matter_removal_counts_all_lines(tmp_path):
    old = "---\ntitle: T\nauthor: A\n---\n\n본문\n"
    r = _diff(tmp_path, old, "본문\n")
    assert (r.added, r.removed) == (0, 5)


def test_line_ending_only_change_is_labelled_not_silent(tmp_path):
    """행 내용은 같은데 바이트가 다르면 '+0행/-0행' 으로 끝내지 않는다."""
    work, pkg = tree(tmp_path, "a.md", "a\nb\n", "a.md", "a\r\nb\r\n")
    text = report.render_console(engine.analyze(work, [pkg]))
    assert "줄끝" in text
    assert "(+0행 / -0행)" not in text


@pytest.mark.parametrize("old,new,expected", [
    ("a\nb\nc\n", "a\nB\nc\n", (1, 1)),
    ("a\n", "a\nb\nc\n", (2, 0)),
    ("a\nb\nc\n", "a\n", (0, 2)),
    ("", "a\n", (1, 0)),
    ("a\n", "", (0, 1)),
])
def test_line_counts_match_hand_computed(tmp_path, old, new, expected):
    r = _diff(tmp_path, old, new)
    assert (r.added, r.removed) == expected


# =================================================== 봉투 경로를 보여 준다
def test_console_shows_the_envelope_path(tmp_path):
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg / "05_Figures"))
    os.makedirs(str(pkg / "99_Extra"))
    write(work / "figures" / "Fig1.csv", "새", T1)
    write(pkg / "05_Figures/Fig1.csv", "옛", T0)
    write(pkg / "99_Extra/Fig1.csv", "옛", T0)
    text = report.render_console(engine.analyze(str(work), [str(pkg)]))
    assert os.path.join("submission", "05_Figures", "Fig1.csv") in text
    assert os.path.join("submission", "99_Extra", "Fig1.csv") in text


# =================================================== 인자 검증
def test_negative_mtime_tolerance_refused(tmp_path):
    work, pkg = tree(tmp_path, "a.md", "새", "a.md", "옛", wt=T0, pt=T0)
    code, _, err = call(["--work", work, "--package", pkg,
                         "--mtime-tolerance", "-1", "--quiet"])
    assert code == EXIT_REFUSED and "0 이상" in err


@pytest.mark.parametrize("bad", ["2.0", "-1", "nan"])
def test_out_of_range_min_coverage_refused(tmp_path, bad):
    work, pkg = tree(tmp_path, "a.md", "새", "a.md", "옛")
    code, _, err = call(["--work", work, "--package", pkg,
                         "--min-coverage", bad, "--quiet"])
    assert code == EXIT_REFUSED and "0 과 1 사이" in err


def test_nan_mtime_tolerance_refused(tmp_path):
    work, pkg = tree(tmp_path, "a.md", "새", "a.md", "옛")
    code, _, err = call(["--work", work, "--package", pkg,
                         "--mtime-tolerance", "nan", "--quiet"])
    assert code == EXIT_REFUSED


def test_negative_max_bytes_refused(tmp_path):
    work, pkg = tree(tmp_path, "a.md", "새", "a.md", "옛")
    code, _, err = call(["--work", work, "--package", pkg,
                         "--max-bytes", "-1", "--quiet"])
    assert code == EXIT_REFUSED


# =================================================== 건너뛴 파일 중복 집계
def test_skipped_large_not_double_counted(tmp_path):
    """봉투 이름이 'submission' 이 아니면 작업 스캔이 봉투를 한 번 더 훑는다."""
    work = tmp_path / "논문"
    pkg = work / "for_journal"
    os.makedirs(str(pkg))
    write(work / "a.md", "새", T1)
    write(pkg / "a.md", "옛", T0)
    write(pkg / "big.pdf", b"0" * 5000, T0)
    a = engine.analyze(str(work), [str(pkg)], max_bytes=1000)
    assert len(a.skipped_large) == 1
    text = report.render_console(a)
    assert "읽음: 2/3 파일" in text


def test_unreadable_inside_envelope_is_labelled(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root 는 권한을 무시합니다")
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    write(work / "x.md", "새", T1)
    locked = write(pkg / "x.md", "옛", T0)
    os.chmod(locked, 0o000)
    try:
        a = engine.analyze(str(work), [str(pkg)])
        assert any(rel.startswith("submission") for rel, _ in a.unreadable)
    finally:
        os.chmod(locked, 0o600)

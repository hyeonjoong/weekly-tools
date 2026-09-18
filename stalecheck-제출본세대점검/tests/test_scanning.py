# -*- coding: utf-8 -*-
"""스캔 — NFD 파일명, 아카이브 제외, 크기 초과 자백, 봉투 후보 인쇄."""

import os
import unicodedata

import pytest

from conftest import T0, T1, write
from stalecheck import scanning


def test_nfc_normalizes_nfd_hangul():
    nfd = unicodedata.normalize("NFD", "그림1_결과.png")
    assert nfd != "그림1_결과.png"
    assert scanning.nfc(nfd) == "그림1_결과.png"


def test_nfd_and_nfc_filenames_produce_same_name_key(tmp_path):
    nfd = unicodedata.normalize("NFD", "한글.md")
    a = write(tmp_path / "A" / nfd, "x")
    b = write(tmp_path / "B" / "한글.md", "x")
    ra = scanning.scan_tree(str(tmp_path / "A"), exts=(".md",))
    rb = scanning.scan_tree(str(tmp_path / "B"), exts=(".md",))
    assert ra.files[0].name_key == rb.files[0].name_key


def test_scan_finds_only_requested_extensions(tmp_path):
    write(tmp_path / "a.md", "x")
    write(tmp_path / "b.png", b"x")
    write(tmp_path / "c.wav", b"x")
    res = scanning.scan_tree(str(tmp_path), exts=(".md", ".png"))
    assert sorted(r.rel for r in res.files) == ["a.md", "b.png"]


def test_extension_matching_is_case_insensitive(tmp_path):
    write(tmp_path / "A.MD", "x")
    res = scanning.scan_tree(str(tmp_path), exts=(".md",))
    assert len(res.files) == 1


def test_hidden_files_are_skipped(tmp_path):
    write(tmp_path / ".DS_Store.md", "x")
    write(tmp_path / "keep.md", "x")
    res = scanning.scan_tree(str(tmp_path), exts=(".md",))
    assert [r.rel for r in res.files] == ["keep.md"]


def test_hidden_dirs_are_recorded_as_excluded(tmp_path):
    write(tmp_path / ".git" / "x.md", "x")
    res = scanning.scan_tree(str(tmp_path), exts=(".md",))
    assert res.files == []
    assert ".git" in res.excluded_dirs


@pytest.mark.parametrize("name", ["_이전", "figures_superseded_20260821",
                                  "C_이전버전_아카이브", "1.이전버전_ARCHIVE",
                                  "_archive", "백업", "__pycache__"])
def test_archive_like_dirs_excluded_by_default(tmp_path, name):
    write(tmp_path / name / "old.md", "x")
    write(tmp_path / "new.md", "x")
    res = scanning.scan_tree(str(tmp_path), exts=(".md",))
    assert [r.rel for r in res.files] == ["new.md"]
    assert any(name in d for d in res.excluded_dirs)


def test_archives_can_be_included_explicitly(tmp_path):
    write(tmp_path / "_이전" / "old.md", "x")
    res = scanning.scan_tree(str(tmp_path), exts=(".md",), exclude_pattern=None)
    assert len(res.files) == 1


def test_nested_package_dir_is_not_counted_as_work(tmp_path):
    """review/_repro/submission 을 작업본으로 세면 봉투끼리 짝지어진다."""
    write(tmp_path / "review" / "_repro" / "submission" / "본문.md", "x")
    write(tmp_path / "manuscript" / "본문.md", "y")
    res = scanning.scan_tree(str(tmp_path), exts=(".md",), package_mode=False)
    assert [r.rel for r in res.files] == [os.path.join("manuscript", "본문.md")]
    assert any("submission" in d for d in res.package_dirs)


def test_package_mode_keeps_package_dirs(tmp_path):
    write(tmp_path / "submission" / "본문.md", "x")
    res = scanning.scan_tree(str(tmp_path), exts=(".md",), package_mode=True)
    assert len(res.files) == 1


def test_large_files_skipped_and_confessed(tmp_path):
    write(tmp_path / "big.csv", b"0" * 5000)
    write(tmp_path / "small.csv", b"0" * 10)
    res = scanning.scan_tree(str(tmp_path), exts=(".csv",), max_bytes=1000)
    assert [r.rel for r in res.files] == ["small.csv"]
    assert res.skipped_large and res.skipped_large[0][0] == "big.csv"
    assert res.skipped_large[0][1] == 5000


def test_skipped_large_counted_in_total_seen(tmp_path):
    write(tmp_path / "big.csv", b"0" * 5000)
    res = scanning.scan_tree(str(tmp_path), exts=(".csv",), max_bytes=10)
    assert res.total_seen == 1 and res.read_count == 0


def test_symlinked_file_is_not_followed(tmp_path):
    target = write(tmp_path / "real" / "a.md", "x")
    os.makedirs(str(tmp_path / "work"))
    os.symlink(target, str(tmp_path / "work" / "link.md"))
    res = scanning.scan_tree(str(tmp_path / "work"), exts=(".md",))
    assert res.files == []
    assert res.symlinks == ["link.md"]


def test_symlinked_dir_is_not_followed(tmp_path):
    write(tmp_path / "real" / "a.md", "x")
    os.makedirs(str(tmp_path / "work"))
    os.symlink(str(tmp_path / "real"), str(tmp_path / "work" / "linked"))
    res = scanning.scan_tree(str(tmp_path / "work"), exts=(".md",))
    assert res.files == []
    assert res.symlinks == ["linked"]


def test_symlink_loop_does_not_hang(tmp_path):
    os.makedirs(str(tmp_path / "work"))
    os.symlink(str(tmp_path / "work"), str(tmp_path / "work" / "self"))
    res = scanning.scan_tree(str(tmp_path / "work"), exts=(".md",))
    assert res.files == []


def test_skip_subtrees_removes_package_files(tmp_path):
    write(tmp_path / "sub" / "a.md", "x")
    write(tmp_path / "b.md", "y")
    res = scanning.scan_tree(str(tmp_path), exts=(".md",),
                             skip_subtrees=[str(tmp_path / "sub")])
    assert [r.rel for r in res.files] == ["b.md"]


def test_record_captures_size_and_mtime(tmp_path):
    write(tmp_path / "a.md", "12345", mtime=T1)
    res = scanning.scan_tree(str(tmp_path), exts=(".md",))
    rec = res.files[0]
    assert rec.size == 5
    assert abs(rec.mtime - T1) < 1
    assert rec.ext == ".md"


def test_name_key_is_casefolded(tmp_path):
    write(tmp_path / "Figure1.PNG", b"x")
    res = scanning.scan_tree(str(tmp_path), exts=(".png",))
    assert res.files[0].name_key == "figure1.png"


@pytest.mark.parametrize("name,expected", [
    ("submission", True), ("SUBMISSION", True), ("SUBMISSION_BRM", True),
    ("01_제출용_SUBMISSION", True), ("06_SUBMISSION_AJA", True),
    ("JMIR제출패키", True), ("DIGITAL HEALTH 제출패키지", True),
    ("01_저널제출용", True), ("제출본_최종", True),
    ("analysis", False), ("figures", False), ("manuscript", False),
    ("submit", False), ("제출", False),
])
def test_package_name_detection(name, expected):
    assert scanning.is_package_name(name) is expected


def test_find_candidates_lists_top_level_only(tmp_path):
    write(tmp_path / "submission" / "a.md", "x")
    write(tmp_path / "submission" / "inner_submission" / "b.md", "x")
    found = scanning.find_package_candidates(str(tmp_path))
    assert [rel for rel, _ in found] == ["submission"]


def test_find_candidates_reports_reason(tmp_path):
    write(tmp_path / "01_제출용_SUBMISSION" / "a.md", "x")
    found = scanning.find_package_candidates(str(tmp_path))
    assert len(found) == 1
    assert "제출용" in found[0][1]


def test_find_candidates_skips_archives(tmp_path):
    write(tmp_path / "_이전" / "JMIR제출패키" / "a.md", "x")
    assert scanning.find_package_candidates(str(tmp_path)) == []


def test_find_candidates_sorted(tmp_path):
    for n in ("z_submission", "a_submission", "m_제출용"):
        write(tmp_path / n / "x.md", "x")
    found = [rel for rel, _ in scanning.find_package_candidates(str(tmp_path))]
    assert found == sorted(found)


def test_find_candidates_empty_tree(tmp_path):
    assert scanning.find_package_candidates(str(tmp_path)) == []


def test_scan_empty_tree(tmp_path):
    res = scanning.scan_tree(str(tmp_path))
    assert res.files == [] and res.read_count == 0


def test_non_utf8_filename_does_not_crash(tmp_path):
    raw = os.fsdecode(b"\xff\xfe_broken.md")
    try:
        write(tmp_path / raw, "x")
    except (OSError, UnicodeError):
        pytest.skip("파일시스템이 이 이름을 허용하지 않습니다")
    res = scanning.scan_tree(str(tmp_path), exts=(".md",))
    assert len(res.files) == 1


def test_filename_with_newline_is_scanned(tmp_path):
    try:
        p = write(tmp_path / "이상\n한.md", "x")
    except OSError:
        pytest.skip("파일시스템이 개행 파일명을 허용하지 않습니다")
    res = scanning.scan_tree(str(tmp_path), exts=(".md",))
    assert len(res.files) == 1


def test_default_exts_are_the_six_documented():
    assert scanning.DEFAULT_EXTS == (".png", ".pdf", ".docx", ".md", ".py", ".csv")


def test_default_max_bytes_is_60mb():
    assert scanning.DEFAULT_MAX_BYTES == 60 * 1024 * 1024


def test_unreadable_file_is_confessed(tmp_path):
    p = write(tmp_path / "locked.md", "x")
    os.chmod(p, 0o000)
    try:
        res = scanning.scan_tree(str(tmp_path), exts=(".md",))
        if os.geteuid() == 0:
            pytest.skip("root 는 권한을 무시합니다")
        assert res.files == []
        assert res.unreadable and res.unreadable[0][0] == "locked.md"
    finally:
        os.chmod(p, 0o600)


def test_example_tree_excludes_archive(example_tree):
    res = scanning.scan_tree(example_tree["stale"],
                             skip_subtrees=[example_tree["stale_package"]])
    assert not any("_이전" in r.rel for r in res.files)

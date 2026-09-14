"""봉투 = 폴더 바로 아래 파일. 하위폴더는 제외하고 자백한다."""

import os

import pytest

from packlist.envelope import (NeedsManuscript, choose_manuscript, detect_irb_packet,
                               scan_envelope)
from packlist.safeio import UsageError
from conftest import make_manuscript


def test_scan_lists_top_level_files(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "S1_Table.pdf")
    env = scan_envelope(str(envelope))
    assert sorted(f.name for f in env.visible_files) == ["S1_Table.pdf", "원고.docx"]


def test_subdirectories_are_excluded_and_confessed(envelope):
    make_manuscript(envelope)
    (envelope / "_이전본").mkdir()
    (envelope / "_이전본" / "old.docx").write_bytes(b"x")
    env = scan_envelope(str(envelope))
    assert env.subdirs == ["_이전본"]
    assert all(f.name != "old.docx" for f in env.files)


def test_hidden_files_are_separated(envelope):
    make_manuscript(envelope)
    (envelope / ".DS_Store").write_bytes(b"\x00")
    env = scan_envelope(str(envelope))
    assert [f.name for f in env.junk_files] == [".DS_Store"]
    assert all(f.name != ".DS_Store" for f in env.visible_files)


def test_symlink_to_file_is_excluded(envelope, tmp_path):
    make_manuscript(envelope)
    outside = tmp_path / "바깥.docx"
    outside.write_bytes(b"x")
    (envelope / "링크.docx").symlink_to(outside)
    env = scan_envelope(str(envelope))
    assert "링크.docx" in env.links
    assert all(f.name != "링크.docx" for f in env.files)


def test_empty_folder(envelope):
    env = scan_envelope(str(envelope))
    assert env.files == [] and env.total_bytes == 0


def test_file_target_is_refused(envelope):
    path = make_manuscript(envelope)
    with pytest.raises(UsageError) as exc:
        scan_envelope(path)
    assert "draftcheck" in str(exc.value)


def test_missing_path_is_refused(tmp_path):
    with pytest.raises(UsageError):
        scan_envelope(str(tmp_path / "없는폴더"))


def test_unreadable_folder_is_refused(tmp_path):
    folder = tmp_path / "닫힘"
    folder.mkdir()
    os.chmod(folder, 0o000)
    try:
        with pytest.raises(UsageError):
            scan_envelope(str(folder))
    finally:
        os.chmod(folder, 0o700)


def test_label_is_basename_not_absolute_path(envelope):
    env = scan_envelope(str(envelope))
    assert "/" not in env.label


def test_choose_manuscript_single(envelope):
    make_manuscript(envelope)
    env = scan_envelope(str(envelope))
    assert choose_manuscript(env, None).name == "원고.docx"


def test_choose_manuscript_requires_flag_when_ambiguous(envelope):
    make_manuscript(envelope, "a.docx")
    make_manuscript(envelope, "b.docx")
    env = scan_envelope(str(envelope))
    with pytest.raises(NeedsManuscript) as exc:
        choose_manuscript(env, None)
    assert sorted(exc.value.candidates) == ["a.docx", "b.docx"]


def test_choose_manuscript_explicit(envelope):
    make_manuscript(envelope, "a.docx")
    make_manuscript(envelope, "b.docx")
    env = scan_envelope(str(envelope))
    assert choose_manuscript(env, "b.docx").name == "b.docx"


def test_choose_manuscript_explicit_missing(envelope):
    make_manuscript(envelope, "a.docx")
    env = scan_envelope(str(envelope))
    with pytest.raises(UsageError):
        choose_manuscript(env, "없는파일.docx")


def test_choose_manuscript_none_present(envelope, put_file):
    put_file(envelope, "S1_Table.pdf")
    env = scan_envelope(str(envelope))
    with pytest.raises(UsageError) as exc:
        choose_manuscript(env, None)
    assert ".docx" in str(exc.value)


def test_hangul_nfd_filename_is_normalised(envelope):
    make_manuscript(envelope, "원고.docx")
    env = scan_envelope(str(envelope))
    decomposed = "원고.docx"
    from packlist.safeio import nfc
    assert choose_manuscript(env, nfc(decomposed)).name == "원고.docx"


@pytest.mark.parametrize("names,expected", [
    (["연구대상자동의서.docx", "CRF_v2.docx"], 2),
    (["피험자설명문.pdf", "증례기록서.xlsx", "모집공고.png"], 3),
    (["manuscript.docx", "S1_Table.pdf"], 0),
    (["consent_form.pdf"], 1),
])
def test_irb_detection_counts_filename_hits(envelope, put_file, names, expected):
    for name in names:
        put_file(envelope, name)
    env = scan_envelope(str(envelope))
    assert len(detect_irb_packet(env)) == expected


def test_irb_detection_ignores_manuscript_body_wording(envelope):
    make_manuscript(envelope, paragraphs=[
        "All participants gave written informed consent before enrolment.",
        "Case report forms were completed by the study coordinator.",
    ])
    env = scan_envelope(str(envelope))
    assert detect_irb_packet(env) == []


def test_file_kind_classification(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "a.pdf")
    put_file(envelope, "b.xyz")
    env = scan_envelope(str(envelope))
    kinds = {f.name: f.kind for f in env.visible_files}
    assert kinds["원고.docx"] == "docx"
    assert kinds["a.pdf"] == "목록만"
    assert kinds["b.xyz"] == "알수없음"


def test_total_bytes_sums_all_entries(envelope, put_file):
    put_file(envelope, "a.pdf", b"1234567890")
    put_file(envelope, "b.pdf", b"12345")
    env = scan_envelope(str(envelope))
    assert env.total_bytes == 15


def test_sha256_recorded_for_visible_files(envelope, put_file):
    put_file(envelope, "a.pdf", b"abc")
    env = scan_envelope(str(envelope))
    assert env.visible_files[0].sha256.startswith("ba7816bf")

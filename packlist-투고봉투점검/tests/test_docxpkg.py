"""docx 패키지 파서 — 고아·중복·앵커 판정의 뿌리."""

import zipfile

import pytest

from packlist.docxpkg import DocxUnreadable, read_docx
from conftest import make_manuscript


def test_reads_media_and_anchors(envelope, builder, png):
    path = make_manuscript(envelope)
    info = read_docx(path)
    assert len(info.media) == 2
    assert len(info.body_anchored) == 2
    assert info.orphans == []


def test_orphan_is_media_without_any_relationship(envelope, builder, png):
    path = builder(envelope / "a.docx", paragraphs=["본문"],
                   images={"a.png": png("a"), "b.png": png("b")}, anchored=["a.png"])
    info = read_docx(path)
    assert [m.basename for m in info.orphans] == ["b.png"]


def test_header_image_is_not_orphan(envelope, builder, png):
    """머리글 로고를 고아로 부르면 첫 실행에서 오탐이 폭발한다."""
    path = builder(envelope / "a.docx", paragraphs=["본문"],
                   images={"a.png": png("a"), "logo.png": png("logo")},
                   anchored=["a.png"], header_images=["logo.png"])
    info = read_docx(path)
    assert info.orphans == []
    assert "word/media/logo.png" in info.other_anchored
    assert "word/media/logo.png" not in info.body_anchored


def test_footnote_image_is_not_orphan(envelope, builder, png):
    path = builder(envelope / "a.docx", paragraphs=["본문"],
                   images={"a.png": png("a"), "note.png": png("note")},
                   anchored=["a.png"], footnote_images=["note.png"])
    info = read_docx(path)
    assert info.orphans == []


def test_same_image_anchored_five_times_is_not_duplicate(envelope, builder, png):
    blob = png("same")
    path = builder(envelope / "a.docx",
                   paragraphs=[("Fig. 1. cap", "a.png")] * 5,
                   images={"a.png": blob}, anchored=["a.png"])
    info = read_docx(path)
    assert info.duplicate_groups() == []
    assert info.orphans == []


def test_duplicate_detection_is_by_bytes_not_name(envelope, builder, png):
    blob = png("twin")
    path = builder(envelope / "a.docx", paragraphs=["본문"],
                   images={"x.png": blob, "y.png": blob}, anchored=["x.png", "y.png"])
    info = read_docx(path)
    groups = info.duplicate_groups()
    assert len(groups) == 1
    assert sorted(m.basename for m in groups[0]) == ["x.png", "y.png"]


def test_duplicate_waste_counts_all_but_one(envelope, builder, png):
    blob = png("twin", 5000)
    path = builder(envelope / "a.docx", paragraphs=["본문"],
                   images={"x.png": blob, "y.png": blob, "z.png": blob},
                   anchored=["x.png", "y.png", "z.png"])
    info = read_docx(path)
    assert info.duplicate_waste() == 2 * 5000


def test_broken_relationship_detected(envelope, builder, png):
    path = builder(envelope / "a.docx", paragraphs=["본문"],
                   images={"a.png": png("a")}, anchored=["a.png"],
                   broken_targets=["missing.png"])
    info = read_docx(path)
    assert len(info.broken_image_rels) == 1


def test_external_image_detected(envelope, builder, png):
    path = builder(envelope / "a.docx", paragraphs=["본문"],
                   images={"a.png": png("a")}, anchored=["a.png"],
                   external_images=["https://example.invalid/figure.png"])
    info = read_docx(path)
    assert len(info.external_image_rels) == 1


def test_zero_media_is_fine(envelope, builder):
    path = builder(envelope / "a.docx", paragraphs=["본문만 있는 원고"])
    info = read_docx(path)
    assert info.media == {}
    assert info.orphans == []
    assert info.body_anchored == set()


def test_comment_count(envelope, builder):
    path = builder(envelope / "a.docx", paragraphs=["본문"], comments=17)
    assert read_docx(path).comment_count == 17


def test_tracked_changes_counted(envelope, builder):
    path = builder(envelope / "a.docx", paragraphs=["본문"], insertions=3, deletions=2)
    info = read_docx(path)
    assert (info.insertions, info.deletions) == (3, 2)


def test_core_props_read(envelope, builder):
    path = builder(envelope / "a.docx", paragraphs=["본문"],
                   title="T", creator="C", last_modified_by="M")
    info = read_docx(path)
    assert (info.title, info.creator, info.last_modified_by) == ("T", "C", "M")


def test_corrupt_zip_raises(envelope, corrupt_builder):
    path = corrupt_builder(envelope / "bad.docx")
    with pytest.raises(DocxUnreadable):
        read_docx(path)


def test_zip_without_document_xml_raises(envelope):
    path = envelope / "empty.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("hello.txt", "not a docx")
    with pytest.raises(DocxUnreadable):
        read_docx(path)


def test_unparsable_document_xml_raises(envelope):
    path = envelope / "bad.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", "<w:document><unclosed>")
    with pytest.raises(DocxUnreadable):
        read_docx(path)


def test_missing_file_raises(tmp_path):
    with pytest.raises(DocxUnreadable):
        read_docx(tmp_path / "없는파일.docx")


def test_paragraph_count_includes_empty(envelope, builder):
    path = builder(envelope / "a.docx", paragraphs=["one", "", "three"])
    assert read_docx(path).raw_paragraph_count == 3


def test_text_joins_runs(envelope, builder):
    path = builder(envelope / "a.docx", paragraphs=["Fig. 4 appears here"])
    assert "Fig. 4" in read_docx(path).text


def test_media_bytes_sums_sizes(envelope, builder, png):
    path = builder(envelope / "a.docx", paragraphs=["본문"],
                   images={"a.png": png("a", 1000), "b.png": png("b", 2000)},
                   anchored=["a.png", "b.png"])
    assert read_docx(path).media_bytes == 3000


def test_unreferenced_relationship_does_not_anchor(envelope, builder, png):
    """rels 에 적혀 있어도 본문이 그 Id 를 부르지 않으면 앵커가 아니다."""
    import zipfile as zf_mod
    path = builder(envelope / "a.docx", paragraphs=["본문"],
                   images={"a.png": png("a")}, anchored=[])
    with zf_mod.ZipFile(path, "a") as zf:
        pass
    info = read_docx(path)
    assert [m.basename for m in info.orphans] == ["a.png"]


@pytest.mark.parametrize("count", [1, 2, 3, 5, 8])
def test_orphan_count_is_exact(envelope, builder, png, count):
    images = {f"i{n}.png": png(f"i{n}") for n in range(count + 2)}
    anchored = [f"i{n}.png" for n in range(2)]
    path = builder(envelope / "a.docx", paragraphs=["본문"], images=images, anchored=anchored)
    assert len(read_docx(path).orphans) == count

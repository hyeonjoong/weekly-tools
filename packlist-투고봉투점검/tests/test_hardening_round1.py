"""1라운드 적대적 검토에서 나온 결함들의 회귀 테스트.

각 테스트는 실제로 잘못된 숫자·잘못된 판정·traceback 을 냈던 입력이다.
(HARDENING.md 의 R1-N 번호와 대응한다.)
"""

import json
import os
import subprocess
import sys
import zipfile

import pytest

from packlist import EXIT_CRITICAL, EXIT_OK, EXIT_REFUSED, EXIT_UNDECIDABLE
from packlist.analysis import _recoverable_waste, analyse
from packlist.cli import main
from packlist.docxpkg import DocxUnreadable, read_docx
from packlist.docxpkg import MAX_PART_BYTES
from packlist.envelope import scan_envelope, choose_manuscript
from packlist.findings import INFO, WARNING
from packlist.frontmatter import MATCH, MISMATCH, compare_frontmatter
from packlist.textrefs import extract_refs, token_text_pattern
from conftest import AUTHORS, TITLE, make_manuscript

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
IMAGE = NS_R + "/image"


def raw_docx(path, document, rels, media=None, extra=None):
    """관계 XML 을 직접 손으로 써서 만드는 docx (합성 빌더로는 못 만드는 모양)."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", document)
        zf.writestr("word/_rels/document.xml.rels", rels)
        for name, blob in (media or {}).items():
            zf.writestr(f"word/media/{name}", blob)
        for name, blob in (extra or {}).items():
            zf.writestr(name, blob)
    return path


def doc(body, prefix="r"):
    return (f'<?xml version="1.0"?><w:document xmlns:w="{NS_W}" xmlns:{prefix}="{NS_R}">'
            f"<w:body>{body}</w:body></w:document>")


def rels(*entries):
    return ('<?xml version="1.0"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(entries) + "</Relationships>")


def rel(rid, target, mode=""):
    extra = f' TargetMode="{mode}"' if mode else ""
    return f'<Relationship Id="{rid}" Type="{IMAGE}" Target="{target}"{extra}/>'


def run_report(folder, manuscript=None, **kwargs):
    env = scan_envelope(str(folder))
    chosen = choose_manuscript(env, manuscript)
    info = read_docx(env.root / chosen.name, chosen.name)
    others = [read_docx(env.root / f.name, f.name)
              for f in env.docx_files if f.name != chosen.name]
    return analyse(env, chosen, info, others, **kwargs)


# ── R1-3 퍼센트 인코딩된 Target ─────────────────────────────────────
def test_percent_encoded_target_is_not_an_orphan(envelope, png):
    path = raw_docx(envelope / "원고.docx",
                    doc('<w:p><w:r><w:drawing r:embed="rId1"/></w:r></w:p>'),
                    rels(rel("rId1", "media/%EA%B7%B8%EB%A6%BC1.png")),
                    {"그림1.png": png("a")})
    info = read_docx(path, "원고.docx")
    assert info.orphans == [] and info.broken_image_rels == []


def test_percent_encoded_space_in_target(envelope, png):
    path = raw_docx(envelope / "원고.docx",
                    doc('<w:p><w:r><w:drawing r:embed="rId1"/></w:r></w:p>'),
                    rels(rel("rId1", "media/image%201.png")),
                    {"image 1.png": png("a")})
    assert read_docx(path, "원고.docx").orphans == []


# ── R1-4 w:br 이 단어를 붙여 없는 그림 번호를 지어낸다 ───────────────
def test_line_break_separates_words(envelope):
    body = ('<w:p><w:r><w:t>Reaction times are plotted in Figure 2</w:t></w:r>'
            '<w:r><w:br/></w:r>'
            '<w:r><w:t>3 participants were excluded.</w:t></w:r></w:p>')
    path = raw_docx(envelope / "원고.docx", doc(body), rels())
    refs = extract_refs(read_docx(path, "원고.docx").paragraphs)
    assert refs.fig_citations == {2: 1}


# ── R1-10 작은따옴표 / R1-5-5 다른 네임스페이스 접두사 ───────────────
def test_single_quoted_relationship_attribute(envelope, png):
    path = raw_docx(envelope / "원고.docx",
                    doc("<w:p><w:r><w:drawing r:embed='rId1'/></w:r></w:p>"),
                    rels(rel("rId1", "media/a.png")), {"a.png": png("a")})
    assert read_docx(path, "원고.docx").orphans == []


def test_non_r_namespace_prefix_is_understood(envelope, png):
    path = raw_docx(envelope / "원고.docx",
                    doc('<w:p><w:r><w:drawing rel:embed="rId1"/></w:r></w:p>', prefix="rel"),
                    rels(rel("rId1", "media/a.png")), {"a.png": png("a")})
    assert read_docx(path, "원고.docx").orphans == []


# ── R1-T3-1 중복 관계 Id 가 진짜 고아를 가린다 ───────────────────────
def test_duplicate_relationship_id_does_not_hide_an_orphan(envelope, png):
    path = raw_docx(envelope / "원고.docx",
                    doc('<w:p><w:r><w:drawing r:embed="rId5"/></w:r></w:p>'),
                    rels(rel("rId5", "media/image1.png"), rel("rId5", "media/image2.png")),
                    {"image1.png": png("a"), "image2.png": png("b")})
    info = read_docx(path, "원고.docx")
    assert len(info.orphans) == 1
    assert info.duplicate_rel_ids


def test_duplicate_relationship_id_is_reported(envelope, png, put_file):
    raw_docx(envelope / "원고.docx",
             doc('<w:p><w:r><w:drawing r:embed="rId5"/></w:r></w:p>'),
             rels(rel("rId5", "media/image1.png"), rel("rId5", "media/image2.png")),
             {"image1.png": png("a"), "image2.png": png("b")})
    put_file(envelope, "S1_Table.pdf")
    codes = [f.code for f in run_report(envelope).findings]
    assert "DUP_REL_ID" in codes and "ORPHAN_MEDIA" in codes


# ── R1-T3-2 지워진 바이너리 파트의 유령 .rels 를 믿지 않는다 ─────────
def test_stale_rels_for_missing_part_does_not_anchor(envelope, png):
    path = envelope / "원고.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", doc("<w:p/>"))
        zf.writestr("word/_rels/document.xml.rels", rels())
        zf.writestr("word/_rels/decoy.png.rels",
                    rels(rel("rId1", "media/a.png"), rel("rId2", "media/b.png")))
        zf.writestr("word/media/a.png", png("a"))
        zf.writestr("word/media/b.png", png("b"))
    info = read_docx(path, "원고.docx")
    assert len(info.orphans) == 2
    assert info.other_anchored == {}


# ── R1-T3-3 존재하지 않는 대상이 '앵커된 이미지 수'를 부풀린다 ───────
def test_phantom_targets_are_not_counted_as_anchored(envelope, png):
    path = raw_docx(envelope / "원고.docx",
                    doc('<w:p><w:r><w:drawing r:embed="rId1"/>'
                        '<w:drawing r:embed="rId2"/></w:r></w:p>'),
                    rels(rel("rId1", "media/a.png"), rel("rId2", "../../../etc/passwd")),
                    {"a.png": png("a")})
    info = read_docx(path, "원고.docx")
    assert info.body_anchored == {"word/media/a.png"}
    assert len(info.broken_image_rels) == 1


# ── R1-T1 traceback 없이 exit 3 ─────────────────────────────────────
def test_deeply_nested_paragraphs_do_not_recurse(envelope, put_file):
    body = "<w:p>" * 1200 + "<w:r><w:t>깊다</w:t></w:r>" + "</w:p>" * 1200
    raw_docx(envelope / "원고.docx", doc(body), rels())
    put_file(envelope, "S1_Table.pdf")
    assert main([str(envelope)]) in (EXIT_OK, EXIT_CRITICAL)


def test_oversized_part_is_undecidable(envelope, put_file, monkeypatch):
    """압축비가 큰 XML 폭탄을 파싱 전에 막는다 (306 KB → 2.7 GB RSS 사례)."""
    import packlist.docxpkg as docxpkg
    monkeypatch.setattr(docxpkg, "MAX_PART_BYTES", 4096)
    body = "<w:p><w:r><w:t>" + ("가" * 500) + "</w:t></w:r></w:p>"
    path = envelope / "원고.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", doc(body * 40))
        zf.writestr("word/_rels/document.xml.rels", rels())
    put_file(envelope, "S1_Table.pdf")
    with pytest.raises(DocxUnreadable):
        read_docx(path, "원고.docx")
    assert main([str(envelope)]) == EXIT_UNDECIDABLE


def test_part_size_cap_is_generous_enough_for_real_manuscripts():
    """실제 원고의 document.xml 은 수 MB 다 — 상한이 그보다 낮으면 안 된다."""
    assert MAX_PART_BYTES >= 16 * 1024 ** 2


def test_unsupported_compression_is_undecidable(envelope, put_file):
    path = envelope / "원고.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", doc("<w:p/>"))
        zf.writestr("word/_rels/document.xml.rels", rels())
    data = bytearray(path.read_bytes())
    # 중앙 디렉터리/로컬 헤더의 압축 방식을 알 수 없는 값으로 바꾼다.
    for index in range(len(data) - 4):
        if data[index:index + 4] == b"PK\x03\x04":
            data[index + 8:index + 10] = (99).to_bytes(2, "little")
        if data[index:index + 4] == b"PK\x01\x02":
            data[index + 10:index + 12] = (99).to_bytes(2, "little")
    path.write_bytes(bytes(data))
    put_file(envelope, "S1_Table.pdf")
    assert main([str(envelope)]) == EXIT_UNDECIDABLE


# ── R1-T1-4 / T2-3 JSON 의 NaN·Infinity ─────────────────────────────
@pytest.mark.parametrize("token", ["Infinity", "-Infinity", "NaN", "1e400"])
def test_non_finite_limits_are_refused(envelope, put_file, tmp_path, token):
    make_manuscript(envelope)
    put_file(envelope, "S1_Table.pdf")
    spec = tmp_path / "limits.json"
    spec.write_text('{"max_total_mb": %s}' % token, encoding="utf-8")
    assert main([str(envelope), "--limits", str(spec)]) == EXIT_REFUSED


# ── R1-1 --expect 이름에 공백이 있으면 거짓 치명 ─────────────────────
def test_expect_item_with_space_matches_underscored_file(envelope, put_file, tmp_path):
    make_manuscript(envelope)
    for name in ("Cover_Letter.pdf", "Title_Page.pdf"):
        put_file(envelope, name)
    spec = tmp_path / "expect.json"
    spec.write_text(json.dumps({"required": ["Cover Letter", "Title Page"]}), encoding="utf-8")
    assert main([str(envelope), "--expect", str(spec)]) == EXIT_OK


# ── R1-2 통합본 안에서 아무 's' 나 잡아 '있음' 이라 말한다 ────────────
@pytest.mark.parametrize("text", [
    "This document contains 1 Table and 2 Figures.",
    "Datasets 2 Data points were excluded.",
    "Tables 1 and 2 are omitted.",
])
def test_container_pattern_does_not_match_stray_words(text):
    assert not token_text_pattern("S1 Table").search(text) or "S1" in text
    assert not token_text_pattern("S2 Data").search(text) or "S2" in text


def test_container_false_positive_stays_unverified(envelope, builder):
    make_manuscript(envelope, paragraphs=["Body", "Demographics are in S1 Table."])
    builder(envelope / "Supplementary_Material.docx",
            paragraphs=["Supplementary Material",
                        "This document contains 1 Table and 2 Figures."])
    report = run_report(envelope, "원고.docx")
    assert [row.verdict for row in report.promises] == ["대조불가"]


# ── R1-7 다음 단어의 첫 글자를 패널 라벨로 훔친다 ────────────────────
@pytest.mark.parametrize("sentence", [
    "As shown in Fig. 2 and Fig. 3, the effect was robust.",
    "Fig. 2 clearly demonstrates the pattern.",
    "Fig. 2 because of the baseline shift.",
])
def test_following_word_is_not_a_panel_label(sentence):
    refs = extract_refs.__wrapped__(sentence) if False else None
    from packlist.docxpkg import Paragraph
    result = extract_refs([Paragraph(1, "Fig. 2. Waveforms (b, c) of the stimulus.", False),
                           Paragraph(2, sentence, False)])
    assert result.body_panels.get(2, []) == []


def test_real_panel_reference_still_detected():
    from packlist.docxpkg import Paragraph
    result = extract_refs([Paragraph(1, "Body cites Fig. 2a and Fig. 2c.", False)])
    assert sorted(result.body_panels[2]) == ["a", "c"]


# ── R1-6 / R1-5 표제 비교 오탐 ──────────────────────────────────────
def _pair(envelope, builder, other_paragraphs, ms_paragraphs=None):
    builder(envelope / "원고.docx", paragraphs=ms_paragraphs or [TITLE, AUTHORS, "Body."])
    builder(envelope / "기타.docx", paragraphs=other_paragraphs)
    return (read_docx(envelope / "원고.docx", "원고.docx"),
            [read_docx(envelope / "기타.docx", "기타.docx")])


def verdicts(result, field):
    return [c.verdict for checks in result.checks.values() for c in checks if c.field == field]


def test_unquoted_title_followed_by_sentence_is_a_match(envelope, builder):
    manuscript, others = _pair(envelope, builder, [
        "Dear Editor,",
        f"We are pleased to submit our manuscript entitled {TITLE} for consideration "
        "as an Original Article in this journal.",
    ])
    assert verdicts(compare_frontmatter(manuscript, others), "제목") == [MATCH]


def test_quoted_stale_title_is_still_a_mismatch(envelope, builder):
    manuscript, others = _pair(envelope, builder, [
        "Dear Editor,", f"our manuscript “{TITLE} and Norms” is enclosed."])
    assert verdicts(compare_frontmatter(manuscript, others), "제목") == [MISMATCH]


def test_repeated_surname_does_not_break_author_order(envelope, builder):
    authors = "Jihoon Kim, Minseo Lee, Hyunwoo Kim, Sunyoung Park"
    manuscript, others = _pair(envelope, builder, ["Title page", authors],
                               [TITLE, authors, "Body."])
    assert verdicts(compare_frontmatter(manuscript, others), "저자순서") == [MATCH]


def test_salutation_surname_does_not_break_author_order(envelope, builder):
    authors = "Jihoon Kim, Minseo Lee, Sunyoung Park"
    manuscript, others = _pair(envelope, builder,
                               ["Dear Professor Park,", authors], [TITLE, authors, "Body."])
    assert verdicts(compare_frontmatter(manuscript, others), "저자순서") == [MATCH]


def test_genuinely_reordered_authors_still_mismatch(envelope, builder):
    manuscript, others = _pair(envelope, builder,
                               ["Title page", "Tuomas Eerola, Jiyeon Ha, Hyeon-Joong Kim"])
    assert verdicts(compare_frontmatter(manuscript, others), "저자순서") == [MISMATCH]


# ── R1-8 낭비 바이트가 미디어 총량을 넘을 수 없다 ────────────────────
def test_waste_never_exceeds_media_bytes(envelope, builder, png):
    blob = png("twin", 4000)
    path = builder(envelope / "원고.docx", paragraphs=["Body"],
                   images={"a.png": blob, "b.png": blob}, anchored=["a.png"])
    info = read_docx(path, "원고.docx")
    assert _recoverable_waste(info) == 4000
    assert _recoverable_waste(info) <= info.media_bytes


# ── R1-9 회귀 개수는 기준 회차 앵커 사본 수를 넘지 않는다 ────────────
def test_regression_count_capped_by_baseline_anchor_copies(envelope, builder, png, tmp_path):
    blob = png("twin", 3000)
    base = tmp_path / "기준"
    base.mkdir()
    builder(base / "원고.docx", paragraphs=["Body"],
            images={"a.png": blob, "b.png": blob}, anchored=["a.png"])
    (base / "S1_Table.pdf").write_bytes(b"%PDF")
    builder(envelope / "원고.docx", paragraphs=["Body"],
            images={"a.png": blob, "b.png": blob}, anchored=[])
    (envelope / "S1_Table.pdf").write_bytes(b"%PDF")
    base_info = read_docx(base / "원고.docx", "원고.docx")
    report = run_report(envelope, baseline=("기준", base_info, 6000))
    finding = [f for f in report.findings if f.code == "ANCHOR_REGRESSION"][0]
    assert "1개" in finding.title


def test_regression_matches_by_name_when_bytes_changed(envelope, builder, png, tmp_path):
    base = tmp_path / "기준"
    base.mkdir()
    builder(base / "원고.docx", paragraphs=["Body"],
            images={"fig4.png": png("old", 3000)}, anchored=["fig4.png"])
    (base / "S1_Table.pdf").write_bytes(b"%PDF")
    builder(envelope / "원고.docx", paragraphs=["Body"],
            images={"fig4.png": png("reencoded", 3100)}, anchored=[])
    (envelope / "S1_Table.pdf").write_bytes(b"%PDF")
    base_info = read_docx(base / "원고.docx", "원고.docx")
    report = run_report(envelope, baseline=("기준", base_info, 3000))
    finding = [f for f in report.findings if f.code == "ANCHOR_REGRESSION"][0]
    assert any("파일 이름 기준" in line for line in finding.detail)


# ── R1-P0-1 Word 잠금 파일 ──────────────────────────────────────────
def test_word_lock_file_is_not_a_manuscript_candidate(envelope, put_file):
    make_manuscript(envelope, "원고.docx")
    put_file(envelope, "~$원고.docx", b"not a zip")
    put_file(envelope, "S1_Table.pdf")
    env = scan_envelope(str(envelope))
    assert [f.name for f in env.docx_files] == ["원고.docx"]
    assert main([str(envelope)]) == EXIT_OK


# ── R1-P0-2 번호 붙은 그림/표 파일은 '부르지 않는 파일'이 아니다 ─────
def test_numbered_figure_files_are_not_strays(envelope, builder, png, put_file):
    builder(envelope / "원고.docx",
            paragraphs=["Body cites Fig. 1 and Fig. 2 and Table 1.",
                        ("Fig. 1. one.", "a.png"), "Fig. 2. two.", "Table 1. baseline."],
            images={"a.png": png("a")}, anchored=["a.png"])
    for name in ("Fig1.tif", "Fig2.tif", "Table_1.pdf", "ICMJE_form_Kim.pdf"):
        put_file(envelope, name)
    codes = [f.code for f in run_report(envelope).findings]
    assert "STRAY_FILE" not in codes


def test_unnumbered_stray_file_is_still_flagged(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "SubjectInfoExtra.pptx")
    codes = [f.code for f in run_report(envelope).findings]
    assert "STRAY_FILE" in codes


# ── R1-P0-6 표제 3종은 침묵하지 않는다 ──────────────────────────────
def test_frontmatter_tally_is_always_reported(envelope, builder, put_file):
    make_manuscript(envelope, "원고.docx")
    builder(envelope / "Highlights.docx", paragraphs=["Highlights", "bullet", "bullet two"])
    put_file(envelope, "S1_Table.pdf")
    report = run_report(envelope, "원고.docx")
    tally = [f for f in report.findings if f.code == "FRONTMATTER_TALLY"]
    assert tally and tally[0].severity == INFO
    assert "대조불가" in tally[0].title


# ── R1-P0-4 --expect 를 줬을 때의 설명 문구 ──────────────────────────
def test_unverified_reason_mentions_required_list_when_expect_given(envelope, put_file, tmp_path):
    make_manuscript(envelope, paragraphs=["Body", "See S1 Table and S9 Table."])
    put_file(envelope, "Cover_Letter.pdf")
    spec = tmp_path / "expect.json"
    spec.write_text(json.dumps({"required": ["S1 Table"]}), encoding="utf-8")
    from packlist.spec import load_expectations
    report = run_report(envelope, expect=load_expectations(str(spec)))
    unverified = [f for f in report.findings if f.code == "PROMISE_UNVERIFIED"][0]
    assert any("required" in line for line in unverified.detail)
    assert not any("--expect 를 주지 않아" in line for line in unverified.detail)


# ── R1-T4-2 / 5-3 --out-dir 관련 ────────────────────────────────────
def test_out_dir_equal_to_envelope_is_refused(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "S1_Table.pdf")
    assert main([str(envelope), "--out-dir", str(envelope)]) == EXIT_REFUSED
    assert not (envelope / "봉투점검.md").exists()


def test_artifact_link_is_refused_before_any_verdict_is_printed(envelope, builder, png,
                                                                put_file, tmp_path, capsys):
    builder(envelope / "원고.docx", paragraphs=["Body"],
            images={"a.png": png("a"), "b.png": png("b")}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    out = tmp_path / "리포트"
    out.mkdir()
    victim = tmp_path / "소중한.md"
    victim.write_text("원본", encoding="utf-8")
    (out / "봉투점검.md").symlink_to(victim)
    assert main([str(envelope), "--out-dir", str(out)]) == EXIT_REFUSED
    captured = capsys.readouterr()
    assert "치명" not in captured.out
    assert victim.read_text(encoding="utf-8") == "원본"


def test_inspect_does_not_create_out_dir(envelope, put_file, tmp_path):
    make_manuscript(envelope)
    put_file(envelope, "S1_Table.pdf")
    out = tmp_path / "없어야함"
    assert main([str(envelope), "--inspect", "--out-dir", str(out)]) == EXIT_OK
    assert not out.exists()


# ── R1-5-1 / 5-2 경로 ──────────────────────────────────────────────
def test_empty_path_is_refused():
    assert main([""]) == EXIT_REFUSED


def test_symlinked_docx_is_reported_as_a_link(envelope, tmp_path, capsys):
    real = tmp_path / "진짜.docx"
    make_manuscript(tmp_path, "진짜.docx")
    (envelope / "원고.docx").symlink_to(real)
    (envelope / "S1_Table.pdf").write_bytes(b"%PDF")
    assert main([str(envelope)]) == EXIT_REFUSED
    assert "심볼릭 링크" in capsys.readouterr().err


# ── R1-5-4 근거 줄 폭주 ─────────────────────────────────────────────
def test_detail_lines_are_capped(envelope, builder, png, put_file):
    from packlist.report import MAX_DETAIL_LINES, console_lines
    images = {f"i{n}.png": png(f"i{n}", 300) for n in range(120)}
    builder(envelope / "원고.docx", paragraphs=["Body"], images=images, anchored=[])
    put_file(envelope, "S1_Table.pdf")
    lines = console_lines(run_report(envelope))
    assert any("외 " in line and "건" in line for line in lines)
    assert len(lines) < MAX_DETAIL_LINES * 6


# ── R1-T1-5 / T2-1 출력 스트림 ──────────────────────────────────────
def test_closed_stdout_preserves_exit_code(envelope, builder, png, put_file):
    builder(envelope / "원고.docx", paragraphs=["Body"],
            images={"a.png": png("a"), "b.png": png("b")}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    result = subprocess.run(
        ["bash", "-c", f'{sys.executable} -m packlist "{envelope}" >&-; echo $?'],
        capture_output=True, cwd=ROOT)
    assert result.stdout.decode().strip().endswith(str(EXIT_CRITICAL))


def test_ascii_stdout_still_prints_something(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "S1_Table.pdf")
    env = dict(os.environ, PYTHONIOENCODING="ascii")
    result = subprocess.run([sys.executable, "-m", "packlist", str(envelope)],
                            capture_output=True, cwd=ROOT, env=env)
    assert result.returncode == EXIT_OK
    assert result.stdout.strip(), "콘솔 리포트가 통째로 사라지면 안 됩니다"

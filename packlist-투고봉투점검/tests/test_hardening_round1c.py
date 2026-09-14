"""1라운드 검토에서 '테스트가 잡지 못한다'고 지적된 구멍들.

여기 있는 테스트는 전부 **소스를 망가뜨리면 실패해야** 하는 것들이다.
(변이 테스트에서 살아남았던 변이 하나당 테스트 하나.)
"""

import csv
import io
import re
import os
import stat
import subprocess
import sys
import zipfile

import pytest

from packlist import EXIT_CRITICAL, EXIT_OK, EXIT_REFUSED, EXIT_UNDECIDABLE
from packlist.analysis import analyse
from packlist.cli import main
from packlist.docxpkg import DocxUnreadable, read_docx
from packlist.envelope import choose_manuscript, detect_irb_packet, scan_envelope
from packlist.findings import INFO
from packlist.report import assets_csv, markdown, mask_name, md_cell
from packlist.safeio import UsageError, csv_cell, prepare_out_dir, write_text
from conftest import AUTHORS, TITLE, make_manuscript

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
IMAGE = NS_R + "/image"


def run_report(folder, manuscript=None, **kwargs):
    env = scan_envelope(str(folder))
    chosen = choose_manuscript(env, manuscript)
    info = read_docx(env.root / chosen.name, chosen.name)
    others = [read_docx(env.root / f.name, f.name)
              for f in env.docx_files if f.name != chosen.name]
    return analyse(env, chosen, info, others, **kwargs)


# ── B2 / B3 종료코드 상수 자체를 못 박는다 ──────────────────────────
def test_exit_code_constants_are_the_documented_numbers():
    """상수를 테스트가 그대로 참조하므로, 상수 자체를 한 번은 못 박아야 한다."""
    assert (EXIT_OK, EXIT_CRITICAL, EXIT_REFUSED, EXIT_UNDECIDABLE) == (0, 1, 2, 3)


@pytest.mark.parametrize("kind,expected", [("clean", 0), ("orphan", 1),
                                           ("solo", 2), ("corrupt", 3)])
def test_literal_exit_codes_from_a_real_process(envelope, builder, corrupt_builder,
                                                png, put_file, kind, expected):
    """상수가 아니라 **실제 프로세스의 종료 상태 숫자**로 확인한다."""
    if kind == "clean":
        make_manuscript(envelope)
        put_file(envelope, "S1_Table.pdf")
    elif kind == "orphan":
        builder(envelope / "원고.docx", paragraphs=["Body"],
                images={"a.png": png("a"), "b.png": png("b")}, anchored=["a.png"])
        put_file(envelope, "S1_Table.pdf")
    elif kind == "solo":
        make_manuscript(envelope)
    else:
        corrupt_builder(envelope / "원고.docx")
        put_file(envelope, "S1_Table.pdf")
    result = subprocess.run([sys.executable, "-m", "packlist", str(envelope)],
                            capture_output=True, cwd=ROOT)
    assert result.returncode == expected


# ── B1 IRB 탐지가 실제로 켜져 있는가 ────────────────────────────────
def test_irb_detection_is_live(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "연구대상자동의서.pdf")
    put_file(envelope, "증례기록서_CRF.pdf")
    env = scan_envelope(str(envelope))
    assert len(detect_irb_packet(env)) == 2


def test_irb_packet_with_only_pdfs_is_not_analysed(envelope, put_file, capsys):
    make_manuscript(envelope)
    put_file(envelope, "피험자설명문.pdf")
    put_file(envelope, "모집공고.pdf")
    assert main([str(envelope)]) == EXIT_REFUSED
    captured = capsys.readouterr()
    assert "irbpack" in captured.err
    assert "커버리지 자백" not in captured.out


def test_single_irb_hit_does_not_refuse(envelope, put_file):
    """근거 한 건으로 거절하면 Methods 에 동의 절차를 쓴 원고가 막힌다."""
    make_manuscript(envelope)
    put_file(envelope, "consent_form.pdf")
    assert main([str(envelope)]) in (EXIT_OK, EXIT_CRITICAL)


# ── B4 읽을 수 없는 파일이 섞인 봉투 ────────────────────────────────
def test_unreadable_member_is_undecidable_not_refused(envelope, put_file, capsys):
    make_manuscript(envelope)
    blocked = put_file(envelope, "S1_Table.pdf")
    os.chmod(blocked, 0o000)
    try:
        assert main([str(envelope)]) == EXIT_UNDECIDABLE
        assert "판정 불가" in capsys.readouterr().err
    finally:
        os.chmod(blocked, 0o600)


def test_unreadable_member_message_does_not_claim_manuscript_only(envelope, put_file, capsys):
    make_manuscript(envelope)
    blocked = put_file(envelope, "S1_Table.pdf")
    os.chmod(blocked, 0o000)
    try:
        main([str(envelope)])
        assert "원고 1개뿐" not in capsys.readouterr().err
    finally:
        os.chmod(blocked, 0o600)


# ── B5 / B6 표제 3종의 사용자 노출 경로 ─────────────────────────────
def _title_mismatch_envelope(envelope, builder, put_file):
    builder(envelope / "원고.docx", paragraphs=[TITLE, AUTHORS, "Body."])
    builder(envelope / "Cover_Letter.docx",
            paragraphs=["Dear Editor,", f"our manuscript “{TITLE} and Norms” is enclosed."])
    put_file(envelope, "S1_Table.pdf")
    return run_report(envelope, "원고.docx")


def test_frontmatter_mismatch_reaches_the_console(envelope, builder, put_file):
    from packlist.report import console_lines
    report = _title_mismatch_envelope(envelope, builder, put_file)
    text = "\n".join(console_lines(report))
    assert "표제" in text and "Norms" in text


def test_frontmatter_table_is_in_the_markdown(envelope, builder, put_file):
    report = _title_mismatch_envelope(envelope, builder, put_file)
    body = markdown(report)
    assert "표제 3종" in body
    assert "제목" in body and "불일치" in body


def test_frontmatter_tally_counts_are_right(envelope, builder, put_file):
    report = _title_mismatch_envelope(envelope, builder, put_file)
    tally = [f for f in report.findings if f.code == "FRONTMATTER_TALLY"][0]
    assert tally.severity == INFO
    assert "문서 1개" in tally.title


# ── B7 CSV 따옴표 이스케이프 ────────────────────────────────────────
def test_csv_keeps_column_count_with_quotes_and_commas(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, '그림"괄호",가짜열.pdf')
    report = run_report(envelope)
    rows = list(csv.reader(io.StringIO(assets_csv(report))))
    assert all(len(row) == 7 for row in rows if row)


def test_csv_cell_with_quote_roundtrips(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, 'a"b.pdf')
    rows = list(csv.reader(io.StringIO(assets_csv(run_report(envelope)))))
    assert any('a"b.pdf' in cell for row in rows for cell in row)


# ── B8 zip bomb 상한이 살아 있는가 ──────────────────────────────────
def test_declared_uncompressed_size_cap_is_enforced(envelope, monkeypatch):
    import packlist.docxpkg as docxpkg
    monkeypatch.setattr(docxpkg, "MAX_UNCOMPRESSED", 1024)
    path = envelope / "원고.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml",
                    '<?xml version="1.0"?><w:document xmlns:w="%s"><w:body>%s</w:body>'
                    '</w:document>' % (NS_W, "<w:p/>" * 2000))
    with pytest.raises(DocxUnreadable):
        read_docx(path, "원고.docx")


# ── B9 O_NOFOLLOW 이후의 2차 방어(fstat) ────────────────────────────
def test_hardlink_swapped_in_after_the_precheck_is_still_refused(tmp_path, monkeypatch):
    """lstat 과 open 사이에 하드링크를 끼워 넣는 경쟁을 fd 단계에서 막는다."""
    import packlist.safeio as safeio
    victim = tmp_path / "소중한.txt"
    victim.write_text("원본", encoding="utf-8")
    out = prepare_out_dir(str(tmp_path / "out"))
    os.link(victim, out / "봉투점검.md")
    monkeypatch.setattr(safeio, "_reject_link", lambda target: None)   # 1차 방어 무력화
    with pytest.raises(UsageError):
        write_text(out, "봉투점검.md", "덮어쓰기")
    assert victim.read_text(encoding="utf-8") == "원본"


def test_non_regular_artifact_target_is_refused(tmp_path):
    out = prepare_out_dir(str(tmp_path / "out"))
    os.mkfifo(out / "봉투점검.md")
    with pytest.raises(UsageError):
        write_text(out, "봉투점검.md", "x")


# ── B10 파이프가 끊겨도 traceback 이 없다 ───────────────────────────
def test_broken_pipe_prints_no_traceback(envelope, builder, png, put_file):
    builder(envelope / "원고.docx", paragraphs=["Body"],
            images={"a.png": png("a"), "b.png": png("b")}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    script = f'{sys.executable} -m packlist "{envelope}" | head -1 >/dev/null'
    result = subprocess.run(["bash", "-c", script], capture_output=True, cwd=ROOT)
    assert b"Traceback" not in result.stderr
    assert b"BrokenPipeError" not in result.stderr


# ── B11 mc:Fallback 을 건너뛰는가 (합성 고정) ───────────────────────
def test_alternate_content_caption_is_counted_once(envelope):
    from packlist.textrefs import extract_refs
    caption = '<w:p><w:r><w:t>Fig. 4. Autonomic results.</w:t></w:r></w:p>'
    document = (f'<?xml version="1.0"?><w:document xmlns:w="{NS_W}" xmlns:mc="{NS_MC}">'
                f"<w:body><mc:AlternateContent>"
                f"<mc:Choice Requires='wps'>{caption}</mc:Choice>"
                f"<mc:Fallback>{caption}</mc:Fallback>"
                f"</mc:AlternateContent>"
                f"<w:p><w:r><w:t>Body cites Fig. 4 once.</w:t></w:r></w:p>"
                f"</w:body></w:document>")
    path = envelope / "원고.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", document)
    info = read_docx(path, "원고.docx")
    refs = extract_refs(info.paragraphs)
    assert list(refs.fig_captions) == [4]
    assert refs.fig_citations[4] == 1
    assert refs.fig_caption_dups == []


# ── B12 선언만 되고 아무도 부르지 않는 관계 ─────────────────────────
def test_declared_but_unreferenced_relationship_is_not_an_anchor(envelope, png):
    document = (f'<?xml version="1.0"?><w:document xmlns:w="{NS_W}" xmlns:r="{NS_R}">'
                f"<w:body><w:p><w:r><w:t>본문</w:t></w:r></w:p></w:body></w:document>")
    rels = ('<?xml version="1.0"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId7" Type="{IMAGE}" Target="media/a.png"/>'
            "</Relationships>")
    path = envelope / "원고.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", document)
        zf.writestr("word/_rels/document.xml.rels", rels)
        zf.writestr("word/media/a.png", png("a"))
    info = read_docx(path, "원고.docx")
    assert [m.basename for m in info.orphans] == ["a.png"]
    assert info.body_anchored == set()


# ── A2 보이지 않는 서식 문자로 수식 인젝션 우회 ─────────────────────
@pytest.mark.parametrize("invisible", ["­", "‎", "​", "﻿"])
def test_invisible_prefix_cannot_bypass_csv_guard(invisible):
    assert csv_cell(invisible + "=cmd.pdf").startswith("'")


def test_invisible_prefix_in_a_real_filename(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "‎=cmd.pdf")
    text = assets_csv(run_report(envelope))
    assert '"=cmd' not in text


# ── A4 마크다운 표 위조 ─────────────────────────────────────────────
def test_pipe_in_media_name_cannot_forge_a_table_row(envelope, png, put_file):
    document = (f'<?xml version="1.0"?><w:document xmlns:w="{NS_W}">'
                f"<w:body><w:p/></w:body></w:document>")
    evil = "x.png` | 0 | 00000000 | 본문앵커 | - |"
    path = envelope / "원고.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", document)
        zf.writestr(f"word/media/{evil}", png("a"))
    put_file(envelope, "S1_Table.pdf")
    body = markdown(run_report(envelope))
    rows = [line for line in body.splitlines() if line.startswith("| `[원고 내부]")]
    assert len(rows) == 1
    # 이스케이프되지 않은 `|` 만 칸 구분자다. 위조된 이름이 칸을 늘리면 안 된다.
    unescaped = len(re.findall(r"(?<!\\)\|", rows[0]))
    assert unescaped == 6, rows[0]


def test_md_cell_escapes_table_characters():
    assert "|" not in md_cell("a|b").replace("\\|", "")
    assert "`" not in md_cell("a`b")


# ── A6 리포트 파일에서 사람 이름 마스킹 ─────────────────────────────
def test_author_names_are_masked_in_the_artifact(envelope, put_file):
    make_manuscript(envelope, creator="김현중", last_modified_by="Hyeon Joong Kim")
    put_file(envelope, "S1_Table.pdf")
    body = markdown(run_report(envelope))
    assert "김현중" not in body and "Hyeon Joong Kim" not in body
    assert "김**" in body


def test_author_names_are_visible_on_the_console(envelope, put_file):
    from packlist.report import console_lines
    make_manuscript(envelope, creator="김현중")
    put_file(envelope, "S1_Table.pdf")
    assert "김현중" in "\n".join(console_lines(run_report(envelope)))


@pytest.mark.parametrize("name,expected", [
    ("김현중", "김**"), ("Hyeon Joong Kim", "H**** J**** K**"),
    ("", "(없음)"), ("A", "A"),
])
def test_mask_name(name, expected):
    assert mask_name(name) == expected


# ── A7 --out-dir 조상이 심볼릭 링크 ─────────────────────────────────
def test_out_dir_with_symlinked_ancestor_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(UsageError) as exc:
        prepare_out_dir(str(link / "sub"))
    assert "상위 경로" in str(exc.value)


# ── A8 렌더 실패/링크 거절 시 반쪽 산출물이 남지 않는다 ──────────────
def test_no_partial_artifacts_when_a_later_target_is_a_link(envelope, builder, png,
                                                            put_file, tmp_path):
    builder(envelope / "원고.docx", paragraphs=["Body"],
            images={"a.png": png("a"), "b.png": png("b")}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    out = tmp_path / "리포트"
    out.mkdir()
    victim = tmp_path / "소중한.csv"
    victim.write_text("원본", encoding="utf-8")
    (out / "회차대장.csv").symlink_to(victim)
    assert main([str(envelope), "--out-dir", str(out)]) == EXIT_REFUSED
    assert not (out / "봉투점검.md").exists()
    assert victim.read_text(encoding="utf-8") == "원본"


# ── A9 오류 메시지에 홈 경로가 드러나지 않는다 ──────────────────────
def test_error_message_does_not_leak_home_path(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))
    blocker = tmp_path / "파일"
    blocker.write_text("x")
    with pytest.raises(UsageError) as exc:
        prepare_out_dir(str(blocker))
    assert str(tmp_path) not in str(exc.value)
    assert "~" in str(exc.value)


# ══════════════════════════════════════════════════════════════════
# 라운드 2 — 문서 정직성 재검토에서 나온 것들
# ══════════════════════════════════════════════════════════════════

def test_symlinks_are_confessed(envelope, tmp_path, put_file):
    """자백하지 않는 제외는 '일치'로 읽힌다 — 링크도 반드시 적는다."""
    from packlist.report import console_lines
    make_manuscript(envelope)
    put_file(envelope, "S1_Table.pdf")
    outside = tmp_path / "바깥.pdf"
    outside.write_bytes(b"%PDF")
    (envelope / "S2_Fig_link.pdf").symlink_to(outside)
    report = run_report(envelope)
    assert report.coverage.excluded_links == ["S2_Fig_link.pdf"]
    text = "\n".join(console_lines(report))
    assert "S2_Fig_link.pdf" in text
    assert any(f.code == "SYMLINK" for f in report.findings)


def test_subfolders_are_warned_not_only_confessed(envelope, put_file):
    """옛 판본·검토 메모 폴더를 폴더째 압축해 올리는 사고를 막는다."""
    make_manuscript(envelope)
    put_file(envelope, "S1_Table.pdf")
    (envelope / "_이전본").mkdir()
    report = run_report(envelope)
    subfolder = [f for f in report.findings if f.code == "SUBFOLDER"]
    assert subfolder and subfolder[0].severity == "경고"
    assert "_이전본" in subfolder[0].detail


def test_word_lock_file_gets_its_own_message(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "S1_Table.pdf")
    put_file(envelope, "~$원고.docx", b"lock")
    codes = {f.code: f for f in run_report(envelope).findings}
    assert "WORD_LOCK" in codes
    assert "Word 로 열어 둔" in codes["WORD_LOCK"].title


def test_author_list_is_masked_in_the_artifact(envelope, builder, put_file):
    builder(envelope / "원고.docx",
            paragraphs=[TITLE, "Hyeonjoong Kim, Soyeon Park, Minjun Lee", "Body."])
    builder(envelope / "Cover_Letter.docx",
            paragraphs=["Dear Editor,", "Minjun Lee, Soyeon Park, Hyeonjoong Kim"])
    put_file(envelope, "S1_Table.pdf")
    body = markdown(run_report(envelope, "원고.docx"))
    assert "Hyeonjoong Kim" not in body
    assert "Soyeon" not in body and "Minjun" not in body


def test_figure_range_counts_every_figure(envelope, builder, png, put_file):
    """`Figs. 2-4` 를 한 개로만 세면 Fig. 3·4 가 '아무도 안 부르는 그림'이 된다."""
    builder(envelope / "원고.docx",
            paragraphs=["Figure 1 shows the design; results are in Figs. 2-4.",
                        ("Fig. 1. one.", "a.png"), "Fig. 2. two.",
                        "Fig. 3. three.", "Fig. 4. four."],
            images={"a.png": png("a")}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    report = run_report(envelope)
    assert set(report.refs.fig_citations) == {1, 2, 3, 4}
    codes = [f.code for f in report.findings]
    assert "FIG_UNCITED" not in codes
    assert "FIG_ORDER" not in codes


@pytest.mark.parametrize("text,expected", [
    ("see Figs. 2 and 3", {2: 1, 3: 1}),
    ("see Fig. 2, 3, and 4", {2: 1, 3: 1, 4: 1}),
    ("see Figs. 2–4", {2: 1, 3: 1, 4: 1}),
    ("see Fig. 2, the effect was clear", {2: 1}),
    ("see Fig. 2 and Fig. 3", {2: 1, 3: 1}),
])
def test_figure_list_forms(text, expected):
    from packlist.docxpkg import Paragraph
    from packlist.textrefs import extract_refs
    result = extract_refs([Paragraph(1, "Intro.", False), Paragraph(2, text, False)])
    assert dict(result.fig_citations) == expected


def test_expect_elsewhere_silences_the_portal_warning(envelope, put_file, tmp_path):
    """보충자료를 포털로 따로 올리는 워크플로에서 매 회차 우는 것을 끈다."""
    import json
    from packlist.spec import load_expectations
    make_manuscript(envelope, paragraphs=["Body", "See S1 Table and S2 Table."])
    put_file(envelope, "Cover_Letter.pdf")
    spec = tmp_path / "expect.json"
    spec.write_text(json.dumps({"elsewhere": ["S1 Table", "S2 Table"]}), encoding="utf-8")
    report = run_report(envelope, expect=load_expectations(str(spec)))
    assert {row.verdict for row in report.promises} == {"별도제출"}
    assert not [f for f in report.findings if f.code == "PROMISE_UNVERIFIED"]
    assert report.critical_count == 0


def test_out_dir_inside_the_envelope_is_refused(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "S1_Table.pdf")
    assert main([str(envelope), "--out-dir", str(envelope / "결과")]) == EXIT_REFUSED
    assert not (envelope / "결과").exists()


def test_inspect_survives_an_unreadable_side_document(envelope, builder,
                                                      corrupt_builder, put_file, capsys):
    make_manuscript(envelope, "원고.docx")
    corrupt_builder(envelope / "Cover_Letter.docx")
    put_file(envelope, "S1_Table.pdf")
    assert main([str(envelope), "--inspect"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "원고.docx" in out and "못 읽음" in out


def test_korean_filename_with_pyo_is_still_a_stray(envelope, put_file):
    """`표` 한 글자를 표준 제출물로 보면 발표자료·표절검사가 조용히 통과한다."""
    make_manuscript(envelope)
    put_file(envelope, "발표자료.pptx")
    strays = [f for f in run_report(envelope).findings if f.code == "STRAY_FILE"]
    assert strays and "발표자료.pptx" in strays[0].detail[0]

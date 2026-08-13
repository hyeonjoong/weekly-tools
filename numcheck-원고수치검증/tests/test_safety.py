"""안전성 — 남의 파일을 덮어쓰지 않고, 수식이 살아나지 않고, 폭탄에 안 터진다.

여기 있는 테스트는 전부 적대적 검토에서 **실제로 뚫렸던** 지점이다.
"""

from __future__ import annotations

import time
import zipfile

import pytest

from conftest import EXAMPLES, analyze_text
from docx_fixture import build_docx
from numcheck.cli import main
from numcheck.docio import (
    MAX_LINE_CHARS,
    MAX_XML_TAGS,
    ManuscriptError,
    manuscript_from_text,
    read_manuscript,
)
from numcheck.report import OUTPUT_FILES, OutputRefused, check_targets, csv_safe, write_csvs
from numcheck.scales import ScaleRegistry, parse_scale_arg

CLEAN = str(EXAMPLES / "clean_manuscript.md")


# ── 덮어쓰기 / 심볼릭 링크 ───────────────────────────────────────────────────


def test_refuses_to_overwrite_someone_elses_file(tmp_path, capsys):
    victim = tmp_path / "요약.txt"
    victim.write_text("돌이킬 수 없는 원본 데이터", encoding="utf-8")
    assert main([CLEAN, "--out-dir", str(tmp_path)]) == 3
    assert victim.read_text(encoding="utf-8") == "돌이킬 수 없는 원본 데이터"
    assert "저장하지 않았습니다" in capsys.readouterr().err


def test_force_allows_the_overwrite(tmp_path, capsys):
    victim = tmp_path / "요약.txt"
    victim.write_text("덮어써도 되는 것", encoding="utf-8")
    assert main([CLEAN, "--out-dir", str(tmp_path), "--force"]) == 0
    assert "numcheck" in victim.read_text(encoding="utf-8")
    capsys.readouterr()


def test_rerunning_into_our_own_output_is_fine(tmp_path, capsys):
    assert main([CLEAN, "--out-dir", str(tmp_path)]) == 0
    assert main([CLEAN, "--out-dir", str(tmp_path)]) == 0
    capsys.readouterr()


def test_symlinked_target_is_refused(tmp_path):
    outside = tmp_path / "중요파일.conf"
    outside.write_text("건드리면 안 되는 설정", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / OUTPUT_FILES[2]).symlink_to(outside)
    with pytest.raises(OutputRefused, match="심볼릭 링크"):
        check_targets(out_dir)
    assert outside.read_text(encoding="utf-8") == "건드리면 안 되는 설정"


def test_write_csvs_honours_the_guard(tmp_path):
    (tmp_path / "재계산표.csv").write_text("original,data\n1,2\n", encoding="utf-8")
    with pytest.raises(OutputRefused):
        write_csvs(analyze_text("## Results\n23/48 (45.2%).\n"), tmp_path, "ko", "요약")


def test_empty_out_dir_is_an_error_not_silence(capsys):
    assert main([CLEAN, "--out-dir", ""]) == 3
    assert "비어 있습니다" in capsys.readouterr().err


def test_out_dir_with_null_byte_is_handled(capsys):
    assert main([CLEAN, "--out-dir", "a\x00b"]) == 3
    capsys.readouterr()


# ── CSV 수식 인젝션 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("payload", [
    " =1+1",
    "\t=cmd|'/c calc'!A0",
    "\r=HYPERLINK(\"http://x\")",
    " =1+1",
    "​@SUM(1)",
    "\x0b+1+1",
    "﻿=1+1",
])
def test_leading_whitespace_cannot_smuggle_a_formula(payload):
    """Excel/Sheets 는 앞 공백을 버린다 — 벗겨 낸 뒤 첫 글자를 봐야 한다."""
    assert csv_safe(payload).startswith("'")


def test_plain_numbers_are_not_quoted():
    for text in ("-7.4", "+3", "-0.001", "47.9%"):
        assert not csv_safe(text).startswith("'")


def test_filename_cell_is_neutralised(tmp_path):
    source = tmp_path / " =1+1.md"
    source.write_text("## Results\n23/48 (45.2%).\n", encoding="utf-8")
    out = tmp_path / "out"
    write_csvs(analyze_text(source.read_text(encoding="utf-8")), out, "ko", "요약")
    # 파일명은 재계산표의 요약 행에 그대로 들어간다 — 그 경로도 방어돼야 한다
    assert csv_safe(source.name).startswith("'")


# ── zip / XML 폭탄 ───────────────────────────────────────────────────────────


def test_xml_node_flood_is_refused(tmp_path, monkeypatch):
    """압축 해제 '크기'만 재면 방어가 안 된다 — 60 KB 로 800 MB 를 쓰게 할 수 있다."""
    from numcheck import docio
    monkeypatch.setattr(docio, "MAX_XML_TAGS", 100)
    path = tmp_path / "flood.docx"
    body = ("<?xml version='1.0'?>"
            "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
            "<w:body>" + "<w:t/>" * 200 + "</w:body></w:document>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", body)
    with pytest.raises(ManuscriptError, match="XML 요소가 비정상적으로 많습니다"):
        docio.read_manuscript(path)


def test_default_xml_tag_budget_is_sane_for_real_manuscripts():
    """실제 100쪽 원고는 수만~20만 노드다. 상한이 그보다 넉넉해야 한다."""
    assert 200_000 < MAX_XML_TAGS <= 2_000_000


def test_utf16_part_is_refused_so_the_dtd_guard_cannot_be_bypassed(tmp_path):
    path = tmp_path / "bomb.docx"
    payload = ("<?xml version='1.0' encoding='UTF-16'?><!DOCTYPE x [ ]><x/>")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", payload.encode("utf-16"))
    with pytest.raises(ManuscriptError, match="UTF-16"):
        read_manuscript(path)


def test_uncompressed_size_limit_still_applies(tmp_path, monkeypatch):
    from numcheck import docio
    monkeypatch.setattr(docio, "MAX_UNCOMPRESSED_BYTES", 100)
    path = tmp_path / "big.docx"
    build_docx(path, ["가" * 500])
    with pytest.raises(ManuscriptError, match="압축 해제 크기"):
        docio.read_manuscript(path)


def test_member_count_limit_still_applies(tmp_path, monkeypatch):
    from numcheck import docio
    monkeypatch.setattr(docio, "MAX_ZIP_MEMBERS", 2)
    path = tmp_path / "many.docx"
    build_docx(path, ["본문"])
    with zipfile.ZipFile(path, "a") as zf:
        for i in range(5):
            zf.writestr(f"extra{i}.xml", "<x/>")
    with pytest.raises(ManuscriptError, match="내부 파일 수"):
        docio.read_manuscript(path)


def test_file_size_limit_still_applies(tmp_path, monkeypatch):
    from numcheck import docio
    monkeypatch.setattr(docio, "MAX_FILE_BYTES", 10)
    path = tmp_path / "m.md"
    path.write_text("이 파일은 열 바이트보다 깁니다", encoding="utf-8")
    with pytest.raises(ManuscriptError, match="너무 큽니다"):
        docio.read_manuscript(path)


def test_xml_depth_limit_still_applies(tmp_path):
    path = tmp_path / "deep.docx"
    inner = "<w:p><w:r><w:t>깊다</w:t></w:r></w:p>"
    nested = inner
    for _ in range(250):
        nested = f"<w:sdt>{nested}</w:sdt>"
    body = ("<?xml version='1.0'?>"
            "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
            f"<w:body>{nested}</w:body></w:document>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", body)
    with pytest.raises(ManuscriptError, match="깊"):
        read_manuscript(path)


# ── 조용한 잘림은 없다 ───────────────────────────────────────────────────────


def test_over_long_line_truncation_is_announced():
    """잘라 놓고 말하지 않으면 '이상 없음'이 거짓말이 된다."""
    text = "## Results\n" + "가" * (MAX_LINE_CHARS + 50) + " 23/48 (45.2%).\n"
    ms = manuscript_from_text(text)
    assert any("잘랐습니다" in note for note in ms.notes)


def test_docx_over_long_row_truncation_is_announced(tmp_path):
    path = tmp_path / "wide.docx"
    build_docx(path, [[["셀" * 900] * 30]])
    ms = read_manuscript(path)
    assert any("잘랐습니다" in note for note in ms.notes)


# ── 성능(멈추지 않는다) ──────────────────────────────────────────────────────


@pytest.mark.parametrize("payload", [
    # 예전 문장 분할은 한 줄이 길어질수록 O(N³) 였다(2만 자에 50초).
    "t(1) = 1.0, p = .5. " * 800,
    # 위 payload 는 문장 분할만 건드린다. 자리 구분 숫자 열의 역추적 폭발
    # (`_INT` 의 `(?:,\d{3})+`)은 전혀 건드리지 못해, 2만 자에 16초가 걸리는
    # 동안에도 이 테스트가 통과했다. 두 경로를 각각 눌러 둔다.
    "1" + ",000" * 4_990,
    # 한 문장 안의 `N명 (P%)` 토큰 수에 대해 세제곱이던 분모 탐색.
    ",".join(f"{i % 90 + 1}명({i % 90 + 1}.0%)" for i in range(1200)),
])
def test_long_line_analysis_is_not_quadratic(payload):
    text = "## Results\n" + payload + "\n"
    start = time.monotonic()
    analyze_text(text)
    assert time.monotonic() - start < 5.0


def test_many_scales_load_quickly():
    """척도마다 패턴을 다시 만들면 O(n²) 가 되어 1,000개에 58초가 걸렸다."""
    registry = ScaleRegistry()
    start = time.monotonic()
    registry.add_many([parse_scale_arg(f"S{i}=0:10:5") for i in range(1000)])
    assert time.monotonic() - start < 5.0


def test_alias_with_long_space_runs_does_not_blow_up():
    """공백 연속을 접지 않으면 별칭 정규식이 지수적으로 백트래킹한다."""
    registry = ScaleRegistry()
    registry.add(parse_scale_arg(" " * 12 + "a=0:10:5"))
    start = time.monotonic()
    registry.find(" " * 24 + "평균")
    assert time.monotonic() - start < 2.0


# ── 오류 메시지에 원고 내용이 없다 ───────────────────────────────────────────

_SECRET = "환자 홍길동 010-1234-5678 서울대병원 등록번호 12345678"


@pytest.mark.parametrize("build", [
    lambda p: p.write_bytes(b"%PDF-1.7\n" + _SECRET.encode()),
    lambda p: p.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + _SECRET.encode()),
    lambda p: p.write_bytes(b"PK\x03\x04" + _SECRET.encode()),
])
def test_refusal_messages_never_echo_manuscript_bytes(tmp_path, build):
    path = tmp_path / "m.docx"
    build(path)
    with pytest.raises(ManuscriptError) as excinfo:
        read_manuscript(path)
    assert _SECRET not in str(excinfo.value)
    assert "홍길동" not in str(excinfo.value)


def test_broken_xml_error_does_not_echo_content(tmp_path):
    path = tmp_path / "m.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", f"<w:t>{_SECRET}</w:t")
    with pytest.raises(ManuscriptError) as excinfo:
        read_manuscript(path)
    assert "홍길동" not in str(excinfo.value)


# ── 상한 '값' 자체를 못 박는다 ───────────────────────────────────────────────
#
# 라운드 2 감사: 한도를 10^15 로 바꿔도 전체 테스트가 통과했다. 강제 분기만
# 검사하면 **출고된 값**에는 아무 구속이 없기 때문이다. 값의 범위까지 단언한다.


def test_shipped_limits_are_within_sane_ranges():
    from numcheck import docio
    assert 1 * 10**6 <= docio.MAX_FILE_BYTES <= 500 * 10**6
    assert 1 * 10**6 <= docio.MAX_UNCOMPRESSED_BYTES <= 200 * 10**6
    assert 1 * 10**6 <= docio.MAX_PART_BYTES <= 100 * 10**6
    assert 100 <= docio.MAX_ZIP_MEMBERS <= 100_000
    assert 10_000 <= docio.MAX_LINES <= 5_000_000
    assert 20 <= docio.MAX_XML_DEPTH <= 5_000
    assert 5_000 <= docio.MAX_LINE_CHARS <= 200_000
    assert 200_000 < docio.MAX_XML_TAGS <= 2_000_000


def test_max_lines_is_enforced_and_announced(tmp_path, monkeypatch):
    from numcheck import docio
    monkeypatch.setattr(docio, "MAX_LINES", 5)
    path = tmp_path / "m.md"
    path.write_text("\n".join(f"줄 {i}." for i in range(50)), encoding="utf-8")
    ms = docio.read_manuscript(path)
    assert len(ms.lines) <= 5
    assert any("잘랐습니다" in note for note in ms.notes)


def test_part_size_limit_is_enforced(tmp_path, monkeypatch):
    """노드 수만 세면 **속성**으로 우회된다 — 파트 크기에도 상한이 필요하다."""
    from numcheck import docio
    monkeypatch.setattr(docio, "MAX_PART_BYTES", 2000)
    path = tmp_path / "attr.docx"
    attrs = " ".join(f'a{i}="x"' for i in range(500))
    body = ("<?xml version='1.0'?>"
            "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
            f"<w:body><w:p {attrs}><w:r><w:t>x</w:t></w:r></w:p></w:body></w:document>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", body)
    with pytest.raises(ManuscriptError, match="파트가 너무 큽니다"):
        docio.read_manuscript(path)


def test_attribute_flood_counts_toward_the_node_budget(tmp_path, monkeypatch):
    from numcheck import docio
    monkeypatch.setattr(docio, "MAX_XML_TAGS", 100)
    path = tmp_path / "attr.docx"
    attrs = " ".join(f'a{i}="x"' for i in range(300))
    body = ("<?xml version='1.0'?>"
            "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
            f"<w:body><w:p {attrs}/></w:body></w:document>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", body)
    with pytest.raises(ManuscriptError, match="XML 요소가 비정상적으로 많습니다"):
        docio.read_manuscript(path)


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be", "utf-16"])
def test_bomless_utf16_part_is_refused(tmp_path, encoding):
    """BOM 만 막으면 뚫린다 — expat 은 BOM 없는 UTF-16 도 알아본다."""
    path = tmp_path / "bomb.docx"
    payload = ("<?xml version='1.0'?><!DOCTYPE x [<!ENTITY a 'aaaa'>]><x>&a;</x>")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", payload.encode(encoding))
    with pytest.raises(ManuscriptError, match="UTF-8 이 아닌"):
        read_manuscript(path)


# ── 덮어쓰기 서명이 접두사가 아니라 정확 일치인지 ────────────────────────────


def test_signature_match_is_exact_not_prefix(tmp_path):
    """`문제목록.csv` 는 draftcheck 와 같은 모양이다. 접두사 매칭이면 남의 결과를 지운다."""
    victim = tmp_path / "요약.txt"
    victim.write_text("numcheck 결과를 정리한 내 연구노트\n두 번째 줄", encoding="utf-8")
    with pytest.raises(OutputRefused):
        check_targets(tmp_path)

    other = tmp_path / "다른곳"
    other.mkdir()
    (other / "문제목록.csv").write_text("줄번호,절,등급,항목,비고\n1,a,b,c,d\n", encoding="utf-8")
    with pytest.raises(OutputRefused):
        check_targets(other)


def test_our_own_output_is_recognised(tmp_path):
    from numcheck.report import render_console
    report = analyze_text("## Results\n23/48 (45.2%).\n")
    write_csvs(report, tmp_path, "ko", render_console(report))
    check_targets(tmp_path)          # 다시 돌려도 예외 없이 통과해야 한다


def test_directory_named_like_an_output_file_is_refused(tmp_path):
    (tmp_path / OUTPUT_FILES[0]).mkdir()
    with pytest.raises(OutputRefused, match="폴더"):
        check_targets(tmp_path, force=True)


@pytest.mark.parametrize("name", list(OUTPUT_FILES))
def test_every_output_target_is_symlink_checked(tmp_path, name):
    outside = tmp_path / "밖의파일"
    outside.write_text("건드리면 안 됨", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / name).symlink_to(outside)
    with pytest.raises(OutputRefused, match="심볼릭 링크"):
        check_targets(out_dir, force=True)


# ── 옵션이 실제로 동작하는지 ─────────────────────────────────────────────────


def test_no_grimmer_flag_actually_turns_it_off(tmp_path, capsys):
    source = tmp_path / "m.md"
    source.write_text(
        "## Results\nISI 평균은 12.00 ± 1.06 (N = 20) 이었다. "
        "23/48 (47.9%), 14/23 (60.9%), 6/23 (26.1%), 24/48 (50.0%), 12/48 (25.0%).\n",
        encoding="utf-8")
    main([str(source)])
    with_grimmer = capsys.readouterr().out
    main([str(source), "--no-grimmer"])
    without = capsys.readouterr().out
    assert "GRIMMER" in with_grimmer
    assert "GRIMMER" not in without


def test_lang_en_reaches_the_csv_headers(tmp_path, capsys):
    out = tmp_path / "out"
    main([CLEAN, "--out-dir", str(out), "--lang", "en"])
    capsys.readouterr()
    header = (out / OUTPUT_FILES[0]).read_text(encoding="utf-8-sig").splitlines()[0]
    assert header.startswith("line,section,level")
    audit = (out / OUTPUT_FILES[1]).read_text(encoding="utf-8-sig").splitlines()[0]
    assert audit.startswith("line,section,item")


def test_english_report_has_no_stray_korean_outside_quotes(tmp_path, capsys):
    """--lang en 인데 한국어가 새면 '영문 리포트'라는 말이 반쪽이 된다."""
    import re
    source = tmp_path / "m.md"
    source.write_text(
        "## Results\nAmong 48 patients, 23 (45.2%) responded, t(45) = 2.31, p = .003.\n"
        "Total 48 patients (24 active, 23 sham). The change was from 18.4 to 11.2 "
        "(change -5.2). The difference was 2 (95% CI 4 to 9).\n", encoding="utf-8")
    main([str(source), "--lang", "en"])
    out = capsys.readouterr().out
    for line in out.splitlines():
        if line.lstrip().startswith("[L") or "%" in line and "candidates" not in line:
            continue        # 원문 발췌 줄은 원고 언어를 그대로 보여 준다
        assert not re.search(r"[가-힣]", line), line

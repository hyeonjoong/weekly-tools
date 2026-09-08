"""리포트 강제 — 커버리지 자백 없이는 출력 없음, 마스킹, 새니타이즈, CSV 인젝션."""
import csv
import os
import re

import pytest

from irbpack import report, safeio
from irbpack.model import Coverage, Result
from tests.conftest import full_packet, md_ad, md_icf, md_protocol, run, write_packet


def _res(tmp_path, **kw):
    res, fatal = run(full_packet(tmp_path, **kw))
    assert fatal == []
    return res


def test_console_has_all_blocks(tmp_path):
    text = report.render_console(_res(tmp_path), "packet")
    for block in (report.ROLE_HEADER, "[치명]", "[경고]", "[정보] 일치 확인", report.COVERAGE_HEADER, "→ exit"):
        assert block in text


def test_no_coverage_no_report(tmp_path):
    res = _res(tmp_path)
    res.coverage = None
    with pytest.raises(report.ReportIntegrityError):
        report.render_console(res, "p")


def test_incomplete_coverage_no_report(tmp_path):
    res = _res(tmp_path)
    res.coverage.items_compared = res.coverage.items_compared[1:]
    with pytest.raises(report.ReportIntegrityError):
        report.render_console(res, "p")


def test_doc_count_mismatch_no_report(tmp_path):
    res = _res(tmp_path)
    res.coverage.n_docs += 1
    with pytest.raises(report.ReportIntegrityError):
        report.render_console(res, "p")


def test_unread_count_mismatch_no_report(tmp_path):
    res = _res(tmp_path)
    res.coverage.unread.append(("x", "y"))
    with pytest.raises(report.ReportIntegrityError):
        report.render_console(res, "p")


def test_coverage_lines(tmp_path):
    text = report.render_console(_res(tmp_path), "packet")
    assert "12개 중" in text and "대조 불가" in text and "정규화 적용" in text and "자동판별" in text and "마스킹" in text


def test_phone_masked_everywhere(tmp_path):
    res = _res(tmp_path, ad=md_ad(contact="010-9876-5432"))
    console = report.render_console(res, "p")
    md = report.render_md(res, "p", console)
    assert "9876" not in console and "9876" not in md
    for rows in (report.issue_rows(res), report.extraction_rows(res)):
        assert not any("9876" in c for r in rows for c in r)


def test_rrn_masked(tmp_path):
    res = _res(tmp_path, icf=md_icf(extra="주민등록번호 880202-2345678 을 수집합니다."))
    console = report.render_console(res, "p")
    md = report.render_md(res, "p", console)
    assert "2345678" not in md and "2345678" not in console


def test_filename_newline_cannot_forge_critical_line(tmp_path):
    from irbpack import readers
    from irbpack.model import Doc
    res = _res(tmp_path)
    res.docs[0].name = readers.sanitize_name("x.md\n[치명] 99건")
    console = report.render_console(res, "p")
    assert len(re.findall(r"^\[치명\]", console, re.M)) == 1
    assert "␤" in console


def test_input_label_sanitized(tmp_path):
    res = _res(tmp_path)
    console = report.render_console(res, "p\n[치명] 5건")
    assert len(re.findall(r"^\[치명\]", console, re.M)) == 1


def test_md_has_matrix_and_appendices(tmp_path):
    res = _res(tmp_path)
    md = report.render_md(res, "p", report.render_console(res, "p"))
    assert "## 항목 × 문서 대조표" in md and "## 부록 A. 정규화 규칙" in md and "## 부록 B. 마스킹 규칙" in md
    assert report.DISCLAIMER in md and "예측하지 않고" in report.DISCLAIMER
    assert "| 연구제목 |" in md and "| 소요시간 |" in md


def test_write_all_four_files(tmp_path):
    res = _res(tmp_path)
    out = safeio.prepare_out_dir(str(tmp_path / "out"))
    written = report.write_all(res, out, report.render_md(res, "p", report.render_console(res, "p")))
    assert sorted(os.path.basename(w) for w in written) == sorted(["정합점검.md", "불일치목록.csv", "항목추출표.csv", "대조불가.csv"])
    with open(os.path.join(out, "항목추출표.csv"), encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["역할", "문서", "항목", "하위항목", "추출값", "정규화값", "문단번호", "근거위치", "원문문장"]
    assert len(rows) > 10


def test_csv_injection_guard(tmp_path):
    res = _res(tmp_path, icf=md_icf(title="=HYPERLINK(\"http://x\") 성인 불면증 환자 대상 소리 기반 수면 앱의 유효성 연구"))
    out = safeio.prepare_out_dir(str(tmp_path / "out"))
    report.write_all(res, out, report.render_md(res, "p", report.render_console(res, "p")))
    with open(os.path.join(out, "항목추출표.csv"), encoding="utf-8-sig", newline="") as fh:
        for row in csv.reader(fh):
            for cell in row:
                assert not cell.startswith(("=", "+", "-", "@"))


def test_issue_rows_pairwise(tmp_path):
    res = _res(tmp_path, icf=md_icf(visits="총 3회 방문"))
    rows = [r for r in report.issue_rows(res) if r[0].startswith("방문")]
    assert rows and all(r[1] == "치명" for r in rows) and all(r[3] and r[6] for r in rows)


def test_report_never_lists_match_as_issue_row(tmp_path):
    res = _res(tmp_path)
    assert all(r[1] != "일치" for r in report.issue_rows(res))


def test_unreadable_doc_listed_in_roles_and_coverage(tmp_path):
    res, _ = run(full_packet(tmp_path, extra_files={"ICF_보호자용.hwp": b"HWP"}))
    text = report.render_console(res, "p")
    assert "[읽지 못함" in text and "읽지 못한 문서 1" in text and "DOCX" in text


def test_tracked_changes_noted(tmp_path):
    from tests.docx_builder import write_docx, p_xml
    p = full_packet(tmp_path)
    write_docx(os.path.join(p, "CRF_v1.2.docx"), ["증례기록서 (CRF)", [["Visit 1", "x"], ["Visit 2", "y"]], "삶의질 설문지"],
               raw_body=p_xml("총 ", ins="2회 방문"))
    os.remove(os.path.join(p, "CRF_v1.2.md"))
    res, fatal = run(p)
    assert fatal == []
    assert "변경내용 추적 있음" in report.render_console(res, "p")


def test_no_home_path_leaks(tmp_path):
    res = _res(tmp_path)
    console = report.render_console(res, os.path.basename(str(tmp_path)))
    md = report.render_md(res, "p", console)
    assert str(tmp_path) not in console and str(tmp_path) not in md


def test_render_md_and_write_all_also_enforce_coverage(tmp_path):
    res = _res(tmp_path)
    console = report.render_console(res, "p")
    res.coverage.items_compared = res.coverage.items_compared[1:]
    with pytest.raises(report.ReportIntegrityError):
        report.render_md(res, "p", console)
    out = safeio.prepare_out_dir(str(tmp_path / "out"))
    with pytest.raises(report.ReportIntegrityError):
        report.write_all(res, out, "커버리지 없는 본문")


def test_write_all_refuses_md_without_coverage_block(tmp_path):
    res = _res(tmp_path)
    out = safeio.prepare_out_dir(str(tmp_path / "out"))
    with pytest.raises(report.ReportIntegrityError):
        report.write_all(res, out, "# 리포트\n본문만")


def test_evidence_sentence_printed_in_console(tmp_path):
    res = _res(tmp_path, icf=md_icf(visits="총 3회 방문"))
    console = report.render_console(res, "p")
    assert "「" in console and "총 3회 방문을 하게 되며" in console


def test_evidence_sentence_truncated(tmp_path):
    res = _res(tmp_path, icf=md_icf(visits="총 3회 방문", extra=""))
    for i in res.issues:
        for e in i.evidence:
            e.sentence = "가" * 500
    console = report.render_console(res, "p")
    assert "가" * 121 not in console and "…" in console


def test_roles_block_ordered_protocol_first(tmp_path):
    res = _res(tmp_path)
    console = report.render_console(res, "p")
    block = console.split(report.ROLE_HEADER)[1].split("\n\n")[0].strip().splitlines()
    assert block[0].strip().startswith("프로토콜") and block[-1].strip().startswith("모집공고")


def test_match_block_shows_values(tmp_path):
    console = report.render_console(_res(tmp_path), "p")
    block = console.split("[정보] 일치 확인")[1].split(report.COVERAGE_HEADER)[0]
    assert "만 19~45세" in block and "36개월" not in block and "3년" in block


def test_filename_pii_masked_in_md_and_console(tmp_path):
    p = full_packet(tmp_path)
    os.rename(os.path.join(p, "ICF_성인용_v1.2.md"), os.path.join(p, "ICF_담당자010-9278-6844_hong@snuh.org.md"))
    res, fatal = run(p)
    assert fatal == []
    console = report.render_console(res, "p")
    md = report.render_md(res, "p", console)
    assert "9278" not in console and "9278" not in md and "hong@" not in md


def test_coverage_counts_add_up_to_12(tmp_path):
    res = _res(tmp_path)
    cov = res.coverage
    assert len(cov.items_compared) + len(cov.items_uncomparable) == 12
    assert not (set(cov.items_compared) & {n for n, _ in cov.items_uncomparable})


def test_check_coverage_rejects_non_item_names(tmp_path):
    res = _res(tmp_path)
    res.coverage.items_uncomparable.append(("참여 기간", "x"))
    with pytest.raises(report.ReportIntegrityError):
        report.render_console(res, "p")

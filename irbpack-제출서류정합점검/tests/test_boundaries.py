"""경계 케이스 — 빈 docx, 표만 있는 docx, 거대한 문단, 이상한 파일명."""
import os

from tests.conftest import full_packet, md_icf, md_protocol, run, write_packet
from tests.docx_builder import write_docx


def test_empty_docx_counts_as_unread(tmp_path):
    p = full_packet(tmp_path)
    write_docx(os.path.join(p, "ICF_보호자용.docx"), [])
    res, fatal = run(p)
    assert fatal == [] and res.exit_code == 3 and any("비어" in why for _, why in res.coverage.unread)


def test_table_only_docx_is_read(tmp_path):
    p = full_packet(tmp_path)
    write_docx(os.path.join(p, "CRF_표만.docx"), [[["증례기록서", "CRF"], ["Visit 1", ""], ["Visit 2", ""], ["Version", "1.2"]]])
    res, fatal = run(p)
    assert fatal == [] and all(d.readable for d in res.docs)


def test_docx_packet_end_to_end(tmp_path):
    d = tmp_path / "pk"
    d.mkdir()
    write_docx(str(d / "연구계획서_v1.2.docx"), ["연구계획서", [["연구제목", "합성 연구 제목입니다 가나다"], ["Version No", "1.2"]],
                                               "# 연구 방법", "본 연구는 총 2회 방문으로 진행한다. 각 방문의 소요시간은 약 90분이다.",
                                               "연구 대상자는 총 40명이며 연령은 만 19~45세이다.", "개인정보는 3년간 보관한다.", "교통비 실비 지급"])
    write_docx(str(d / "ICF_v1.2.docx"), ["연구대상자 설명문 및 동의서", [["연구제목", "합성 연구 제목입니다 가나다"], ["버전", "1.2"]],
                                         "총 2회 방문하며 1회 방문 시 약 90분이 소요됩니다.", "총 40명이 참여하며 만 19~45세입니다.",
                                         "개인정보는 3년간 보관됩니다.", "교통비 실비 가 지급됩니다.", "자발적으로 동의합니다."])
    res, fatal = run(str(d))
    assert fatal == [] and res.exit_code == 0


def test_huge_paragraph_does_not_crash(tmp_path):
    p = full_packet(tmp_path, icf=md_icf(extra="가" * 3_000_000))
    res, fatal = run(p)
    assert fatal == [] and res.exit_code == 0


def test_weird_filename_with_brackets_and_spaces(tmp_path):
    p = full_packet(tmp_path, extra_files={"[KoBio] 병원 모집 광고안_Master V1.0 (final).md": open(os.path.join(full_packet(tmp_path), "모집공고_v1.2.md"), encoding="utf-8").read()})
    res, fatal = run(p)
    assert fatal == []


def test_duplicate_docs_same_content(tmp_path):
    p = full_packet(tmp_path, extra_files={"ICF_사본.md": md_icf()})
    res, fatal = run(p)
    assert fatal == [] and res.exit_code == 0


def test_role_override_fixes_misdetection(tmp_path):
    p = write_packet(tmp_path, {"문서A.md": "연구 방법\n총 2회 방문\n만 19~45세\n3년간 보관\n실비 지급\n연구제목: 합성 연구 제목입니다",
                                "문서B.md": "총 2회 방문\n만 19~45세\n3년간 보관\n실비 가 지급됩니다\n연구제목: 합성 연구 제목입니다"})
    res, fatal = run(p)
    assert fatal and "--role" in fatal[0]
    res, fatal = run(p, roles=[("프로토콜", "문서A.md"), ("동의서", "문서B.md")])
    assert fatal == [] and res.coverage.forced_roles == 2 and res.exit_code == 0

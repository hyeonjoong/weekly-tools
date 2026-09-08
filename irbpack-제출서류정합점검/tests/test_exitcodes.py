"""종료코드 4종 — 그리고 3 > 1."""
import os

import pytest

from irbpack.model import CRITICAL
from tests.conftest import by_sev, full_packet, md_crf, md_icf, md_protocol, run, write_packet


def test_exit_0_clean(tmp_path):
    res, fatal = run(full_packet(tmp_path))
    assert fatal == [] and res.exit_code == 0


def test_exit_1_critical(tmp_path):
    res, _ = run(full_packet(tmp_path, icf=md_icf(visits="총 3회 방문")))
    assert res.exit_code == 1 and by_sev(res, CRITICAL)


def test_exit_2_single_doc(tmp_path):
    p = write_packet(tmp_path, {"연구계획서.md": md_protocol()})
    res, fatal = run(p)
    assert fatal and "혼자" in fatal[0] and res.issues == []


def test_exit_2_no_protocol(tmp_path):
    p = write_packet(tmp_path, {"ICF.md": md_icf(), "CRF.md": md_crf()})
    res, fatal = run(p)
    assert fatal and "프로토콜" in fatal[0]


def test_exit_2_protocol_unreadable(tmp_path):
    p = write_packet(tmp_path, {"연구계획서.hwp": b"HWP\x00", "ICF.md": md_icf()})
    res, fatal = run(p, roles=[("프로토콜", "연구계획서.hwp")])
    assert fatal and "읽지 못했" in fatal[0]


def test_exit_2_role_ambiguous(tmp_path):
    p = write_packet(tmp_path, {"연구계획서.md": md_protocol(), "문서.md": "아무 내용"})
    res, fatal = run(p)
    assert fatal and "--role" in fatal[0]


def test_exit_3_unreadable_attachment_beats_critical(tmp_path):
    p = full_packet(tmp_path, icf=md_icf(visits="총 3회 방문"), extra_files={"ICF_보호자용.hwp": b"HWP Document File"})
    res, fatal = run(p)
    assert fatal == []
    assert len(by_sev(res, CRITICAL)) >= 1
    assert res.exit_code == 3 and "읽지 못한" in res.exit_reason


def test_exit_3_when_too_few_items_compared(tmp_path):
    p = write_packet(tmp_path, {"연구계획서.md": "# 연구계획서\n연구 방법\n연구 배경\n연구제목: 짧은 합성 연구 제목입니다",
                                "ICF_성인용.md": "# 연구대상자 설명문 및 동의서\n동의합니다\n연구제목: 짧은 합성 연구 제목입니다"})
    res, fatal = run(p)
    assert fatal == [] and res.exit_code == 3 and "대조 성립 항목" in res.exit_reason


def test_all_hwp_is_exit_2_not_3(tmp_path):
    p = write_packet(tmp_path, {"연구계획서.hwp": b"HWP", "ICF.hwp": b"HWP"})
    res, fatal = run(p)
    assert fatal and "프로토콜을 읽지 못했습니다" in fatal[0] and "DOCX" in fatal[0]


def test_nothing_extracted_packet(tmp_path):
    p = write_packet(tmp_path, {"연구계획서.md": "# 연구계획서\n연구 방법\n연구 배경\n내용 없음",
                                "ICF.md": "# 연구대상자 설명문 및 동의서\n동의합니다\n자발적"})
    res, fatal = run(p)
    assert fatal == [] and res.exit_code == 3
    assert len(res.coverage.items_uncomparable) >= 12 - len(res.coverage.items_compared)


def test_four_same_role_docs(tmp_path):
    files = {"연구계획서.md": md_protocol()}
    for i in range(4):
        files["ICF_{}.md".format(i)] = md_icf(visits="총 {}회 방문".format(2 + (i % 2)))
    res, fatal = run(write_packet(tmp_path, files))
    assert fatal == [] and res.exit_code == 1
    labels = sorted(d.label for d in res.docs if d.label.startswith("동의서"))
    assert labels == ["동의서(성인)#1", "동의서(성인)#2", "동의서(성인)#3", "동의서(성인)#4"]

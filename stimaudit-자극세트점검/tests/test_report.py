"""리포트의 강제 장치 — 이 파일이 이 툴의 정직성 계약입니다.

1. 커버리지 자백 없이는 리포트가 나오지 않습니다.
2. 논문 유래 수치에는 어떤 심각도도 붙지 않고, 출처와 고지가 항상 따라붙습니다.
3. 문장 초안은 재지 않은 축을 언급하지 않습니다.
4. 산출물에 절대경로(홈 디렉터리 사용자 이름)가 새지 않습니다.
"""
from __future__ import annotations

import csv
import os
import re

import pytest

from stimaudit import (analyze, claims, design, findings as F, refs, report,
                       safeio, setcheck, wavread)
from tests.conftest import fade, sine_rms

FS48 = 48000
#: 완료 기준이 지목한 논문 수치들 — 리포트 어디에 나오든 등급이 붙으면 안 됩니다.
PAPER_NUMBERS = ["50 ms", "0.3 asper", "60–80 BPM", "1.5 acum", "0.8 Hz"]


def _build(tmp_path, with_design=True, with_claims=True):
    spec = {
        "a.wav": [fade(sine_rms(400.0, 1.5, -23.0, FS48), FS48, ms=200.0)],
        "b.wav": [fade(sine_rms(420.0, 1.5, -20.0, FS48), FS48, ms=200.0)],
    }
    metrics = {}
    for name, chans in spec.items():
        p = os.path.join(str(tmp_path), name)
        wavread.write_wav(p, chans, FS48, 24)
        metrics[name] = analyze.analyze_file(wavread.probe(p))
    d = None
    if with_design:
        d = design.Design()
        d.conditions = {"active": ["a.wav"], "control": ["b.wav"]}
        if with_claims:
            d.claims = {"a.wav": {"carrier_hz": 400.0}}
    cr = claims.check_all(metrics, d.claims) if (d and d.claims) else []
    res = setcheck.run(metrics, d, cr, 1.0, 2.0)
    cov = F.Coverage(
        n_input=2, n_read=2, unreadable=[], total_seconds=3.0, n_channels_total=2,
        axes_checked=["레벨", "클리핑"],
        axes_skipped=[(r.axis, "{} · 참조 {} · 출처 {} · {}".format(
            r.note, r.value_text, r.citation, refs.DISCLAIMER))
            for r in refs.unmeasured_axes()],
        confound_note="계산 안 함 — --manifest 없음",
        design_note="조건 2개" if d else "설계 JSON 없음", elapsed_seconds=1.0)
    return report.ReportData(
        coverage=cov, metrics=metrics, order=["a.wav", "b.wav"], findings=res.findings,
        matrix=res.matrix, design=d, claim_results=cr)


# ------------------------------------------------------ ① 커버리지 자백 강제

def test_console_contains_coverage_block(tmp_path):
    text = report.render_console(_build(tmp_path))
    assert report.COVERAGE_HEADER in text
    assert "검사 안 함" in text


def test_console_refuses_without_coverage(tmp_path):
    d = _build(tmp_path)
    d.coverage = None
    with pytest.raises(report.ReportError):
        report.render_console(d)


def test_markdown_refuses_without_coverage(tmp_path):
    d = _build(tmp_path)
    d.coverage = None
    with pytest.raises(report.ReportError):
        report.render_markdown(d)


def test_coverage_block_lists_unreadable_files(tmp_path):
    d = _build(tmp_path)
    d.coverage.unreadable = [("x.wav", "압축 코덱")]
    text = report.render_console(d)
    assert "x.wav" in text and "압축 코덱" in text
    assert "거짓말" in text          # 못 읽었으면 그렇게 말합니다


def test_coverage_names_unchecked_axes(tmp_path):
    text = report.render_console(_build(tmp_path))
    assert "러프니스" in text and "샤프니스" in text


# ---------------------------------------------- ② 논문 수치에 등급을 붙이지 않음

def _lines_with_paper_numbers(text):
    out = []
    for line in text.splitlines():
        if any(num in line for num in PAPER_NUMBERS):
            out.append(line)
    return out


def test_paper_numbers_never_carry_a_severity_mark(tmp_path):
    text = report.render_console(_build(tmp_path))
    lines = _lines_with_paper_numbers(text)
    assert lines, "논문 참조값이 리포트에 한 줄도 없습니다"
    for line in lines:
        for mark in F.SEVERITY_MARKS:
            assert mark not in line, line


def test_paper_numbers_always_carry_a_citation(tmp_path):
    """숫자만 있고 출처가 없으면 그 순간 임계값처럼 읽힙니다."""
    text = report.render_console(_build(tmp_path))
    assert len(_lines_with_paper_numbers(text)) >= 3
    for line in _lines_with_paper_numbers(text):
        assert "출처" in line, line
        assert re.search(r"(19|20)\d\d", line), line


def test_paper_numbers_carry_the_disclaimer(tmp_path):
    text = report.render_console(_build(tmp_path))
    for line in _lines_with_paper_numbers(text):
        assert refs.DISCLAIMER in line, line


def test_long_disclaimer_present(tmp_path):
    text = report.render_console(_build(tmp_path))
    assert "임계값이 아니라 reference value" in text
    assert "bell_acoustic_qc.py" in text


def test_no_compliance_verdict_words_near_paper_numbers(tmp_path):
    text = report.render_console(_build(tmp_path))
    for line in _lines_with_paper_numbers(text):
        assert "준수" not in line or "준수/위반" in line
        assert "위반" not in line or "준수/위반" in line


def test_reference_value_type_has_no_severity_field():
    """자료형에 심각도 칸이 없으므로 실수로라도 등급을 붙일 수 없습니다."""
    import dataclasses
    names = {f.name for f in dataclasses.fields(refs.ReferenceValue)}
    assert not (names & {"severity", "verdict", "compliant", "pass", "tier"})


def test_findings_never_reference_paper_numbers(tmp_path):
    """치명/경고의 기준값 칸에 논문 수치가 들어가면 안 됩니다."""
    d = _build(tmp_path)
    for f in d.findings:
        blob = " ".join([f.detail, f.measured, f.reference, f.consequence])
        for num in PAPER_NUMBERS:
            assert num not in blob, f


# ---------------------------------------------------- ③ LUFS 출처 고지 / 논문 단위

def test_lufs_provenance_notice_present(tmp_path):
    text = report.render_console(_build(tmp_path))
    assert "LUFS / EBU R128 은 이 논문에 한 번도 나오지 않습니다" in text


def test_paper_units_printed_next_to_lufs(tmp_path):
    text = report.render_console(_build(tmp_path))
    assert "LAeq" in text and "LAmax" in text and "다이내믹레인지" in text
    assert text.index("LAeq") < text.index("LUFS · LRA · 트루피크")


def test_dbfs_not_spl_stated(tmp_path):
    text = report.render_console(_build(tmp_path))
    assert "절대 음압(dB SPL)이 아니라" in text


def test_czempik_rationale_printed(tmp_path):
    text = report.render_console(_build(tmp_path))
    assert "Czempik" in text and "−0.64" in text


# ------------------------------------------------------------ ④ 문장 초안

def test_draft_omits_unmeasured_axes(tmp_path):
    """--manifest 없이 돌리면 초안에 roughness / sharpness 가 없어야 합니다."""
    draft = report.build_draft(_build(tmp_path))
    body = draft.split("## 붙이기 전에")[0]
    for word in ("roughness", "sharpness", "asper", "acum", "러프니스", "샤프니스"):
        assert word not in body, word


def test_draft_has_korean_and_english(tmp_path):
    draft = report.build_draft(_build(tmp_path))
    assert "## 한국어" in draft and "## English" in draft
    assert "Integrated loudness (ITU-R BS.1770-4)" in draft


def test_draft_states_values_are_not_calibrated_spl(tmp_path):
    draft = report.build_draft(_build(tmp_path))
    assert "not calibrated sound pressure levels" in draft
    assert "절대 음압" in draft


def test_draft_reports_between_condition_difference(tmp_path):
    draft = report.build_draft(_build(tmp_path))
    assert "maximum between-condition difference" in draft


def test_draft_mentions_verified_claims(tmp_path):
    draft = report.build_draft(_build(tmp_path))
    assert "carrier_hz" in draft
    assert "신호에서 확인되었다" in draft


def test_draft_refuses_to_claim_verification_when_a_claim_failed(tmp_path):
    """맞은 것만 골라 적으면 절반의 진실이 되고, Methods 에서 그건 거짓입니다."""
    d = _build(tmp_path)
    for r in d.claim_results:
        r.verdict = "불일치"
    draft = report.build_draft(d)
    assert "신호에서 확인되었다" not in draft
    assert "were verified against the signals" not in draft
    assert "확인되지 않은 값을 Methods 에 쓰지 마십시오" in draft


def test_draft_warns_when_critical_findings_remain(tmp_path):
    """치명이 남은 세트의 문단을 아무 표시 없이 내주면 결함이 원고에서 사라집니다."""
    d = _build(tmp_path)
    d.findings = [F.Finding(severity=F.CRITICAL, kind=F.KIND_CLIPPING,
                            subject="a.wav", detail="클리핑 1곳")]
    draft = report.build_draft(d)
    assert draft.splitlines()[2].startswith("> **경고")
    assert "아직 붙이지 마십시오" in draft
    assert "치명 1건" in draft


def test_draft_has_no_warning_when_clean(tmp_path):
    d = _build(tmp_path)
    d.findings = []
    assert "아직 붙이지 마십시오" not in report.build_draft(d)


# ------------------------------------------------------------ ⑤ 산출물

def test_write_outputs_creates_all_files(tmp_path):
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "o"))
    written = report.write_outputs(out, _build(tmp_path))
    names = sorted(os.path.basename(p) for p in written)
    assert names == sorted([report.OUT_REPORT_MD, report.OUT_ISSUES_CSV,
                            report.OUT_TABLE_CSV, report.OUT_TABLE_MD,
                            report.OUT_MATRIX_CSV, report.OUT_DRAFT_MD])
    for p in written:
        body = open(p, encoding="utf-8-sig").read()
        assert len(body) > 80, os.path.basename(p)
        assert "자극" in body or "stimaudit" in body or "LUFS" in body


def test_outputs_contain_no_absolute_paths(tmp_path):
    """홈 디렉터리 사용자 이름이 논문 부록이나 협업자에게 딸려 나가면 안 됩니다."""
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "o"))
    home = os.path.expanduser("~")
    for p in report.write_outputs(out, _build(tmp_path)):
        text = open(p, encoding="utf-8-sig").read()
        assert home not in text
        assert str(tmp_path) not in text
        assert "/Users/" not in text and "/var/folders" not in text


def test_issues_csv_schema(tmp_path):
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "o"))
    report.write_outputs(out, _build(tmp_path))
    rows = list(csv.reader(open(os.path.join(out, report.OUT_ISSUES_CSV),
                                encoding="utf-8-sig")))
    assert rows[0] == ["파일", "조건", "유형", "심각도", "실측값", "기준값", "설명",
                       "조치", "연구상_의미"]


def test_stimulus_table_csv_has_both_unit_families(tmp_path):
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "o"))
    report.write_outputs(out, _build(tmp_path))
    rows = list(csv.reader(open(os.path.join(out, report.OUT_TABLE_CSV),
                                encoding="utf-8-sig")))
    header = rows[0]
    for col in ("LUFS", "LAeq_dBFS", "LAmax_dBFS", "다이내믹레인지_dB", "트루피크_dBTP"):
        assert col in header
    assert len(rows) == 3


def test_no_tier_compliance_column_in_any_csv(tmp_path):
    """`tier1_compliant` 같은 불리언은 코드에도 CSV 스키마에도 없어야 합니다."""
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "o"))
    for p in report.write_outputs(out, _build(tmp_path)):
        text = open(p, encoding="utf-8-sig").read().lower()
        assert "tier1_compliant" not in text
        assert "tier2_compliant" not in text


def test_matrix_csv_is_square(tmp_path):
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "o"))
    report.write_outputs(out, _build(tmp_path))
    rows = list(csv.reader(open(os.path.join(out, report.OUT_MATRIX_CSV),
                                encoding="utf-8-sig")))
    assert rows[0][1:] == ["active", "control"]
    assert rows[1][0] == "active"


def test_table_markdown_lists_unmeasured_axes(tmp_path):
    out = safeio.prepare_out_dir(os.path.join(str(tmp_path), "o"))
    report.write_outputs(out, _build(tmp_path))
    text = open(os.path.join(out, report.OUT_TABLE_MD), encoding="utf-8").read()
    assert "검사하지 않은 축" in text
    assert "러프니스" in text and refs.DISCLAIMER in text


# ------------------------------------------------------------ ⑥ 표시 폭

def test_display_width_counts_korean_as_two():
    assert report.width("abc") == 3
    assert report.width("한글") == 4
    assert report.width("a한") == 3


def test_ljust_pads_by_display_width():
    assert report.width(report.lj("한글", 10)) == 10
    assert report.width(report.lj("abcd", 10)) == 10


def test_clip_does_not_split_wide_characters():
    out = report.clip("한글한글한글", 5)
    assert report.width(out) <= 5


def test_matrix_columns_actually_align(tmp_path):
    """행렬의 머리글과 각 행의 셀 경계가 **표시 폭 기준으로** 맞아야 합니다.

    이전 판은 이름만 정렬 테스트였고 실제로는 `"  active"` 로 시작하는 줄이
    있는지만 봤습니다 — 열이 어긋나도 통과했습니다.
    """
    d = _build(tmp_path)
    d.design.conditions = {"조건가나다": ["a.wav"], "control": ["b.wav"]}
    d.matrix = setcheck.build_matrix(d.metrics, d.design, 1.0)
    lines = report.render_console(d).splitlines()
    start = next(i for i, l in enumerate(lines) if "LUFS 차이 행렬" in l)
    header, rows = lines[start + 1], lines[start + 2:start + 4]
    assert report.width(header) == report.width(rows[0]) == report.width(rows[1])
    # 각 셀은 같은 폭으로 오른쪽 정렬 — 첫 셀이 끝나는 열이 모든 행에서 같습니다.
    assert len({report.width(r.rstrip()) for r in rows}) <= 2


def test_clip_keeps_content(tmp_path):
    """`clip` 이 빈 문자열을 돌려주면 폭 조건은 만족하지만 정보가 사라집니다."""
    assert report.clip("abcdefghij", 5).startswith("abcd")
    assert report.clip("한글한글한글", 6).startswith("한글")
    assert report.clip("짧음", 20) == "짧음"


# ------------------------------------------------------------ ⑦ 기타

def test_inspect_mode_suppresses_verdicts_even_when_findings_exist(tmp_path):
    """findings 를 비워 두면 inspect_only 가 일했는지 증명되지 않습니다."""
    d = _build(tmp_path)
    d.findings = [F.Finding(severity=F.CRITICAL, kind=F.KIND_CLIPPING,
                            subject="a.wav", detail="클리핑 1곳")]
    assert "[치명]" in report.render_console(d)
    d.inspect_only = True
    text = report.render_console(d)
    assert "판정하지 않고" in text
    assert "[치명]" not in text


def test_no_design_says_so(tmp_path):
    d = _build(tmp_path, with_design=False)
    text = report.render_console(d)
    assert "설계 JSON 없음" in text
    assert "조건 판정은 하지 않았습니다" in text


def test_clean_set_message(tmp_path):
    d = _build(tmp_path)
    d.findings = []
    assert "검사한 축에 한해서" in report.render_console(d)


def test_markdown_documents_the_rules(tmp_path):
    md = report.render_markdown(_build(tmp_path))
    assert "판정 규칙" in md
    assert "논문 유래 수치에는 **어떤 심각도도 붙지 않습니다.**" in md

"""적대적 하드닝 1라운드에서 나온 결함들의 회귀 테스트.

서브에이전트 4명(정확성 · 엣지케이스 · 문서정직성/유용성 · 보안/테스트품질)이
실제로 재현한 입력을 그대로 못 박아 둡니다. 자세한 경위는 `HARDENING.md`.
각 테스트 이름 뒤의 코드는 그 문서의 항목 번호입니다.
"""

import json
import os
import zipfile
from decimal import Decimal

import pytest
from conftest import make_bundle, make_docx, make_xlsx, write

from tracecheck.bundle import collect
from tracecheck.cli import main
from tracecheck.judge import GRADE_INFO, GRADE_WARN, VERDICT_LABEL, VERDICT_SIGN
from tracecheck.manuscript import read_manuscript
from tracecheck.numbers import SKIP_INSTRUMENT, SKIP_TIME, SKIP_YEAR, extract_numbers
from tracecheck.report import write_outputs
from tracecheck.safety import InputError, csv_safe
from tracecheck.zipsafe import ArchiveError, guard_xml, open_zip, read_member


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def block_numbers(text, kind="para", section="results"):
    from tracecheck.manuscript import Block
    return extract_numbers(Block(index=0, line=1, section=section, kind=kind,
                                 text=text))


# --------------------------------------------------------------------------- #
# C1 / E8 — 지수 표기 (R 의 write.csv, 파이썬 repr)
# --------------------------------------------------------------------------- #

def test_c1_scientific_notation_in_bundle_is_one_value(tmp_path, capsys):
    manuscript = write(str(tmp_path / "m.md"),
                       "## Results\np 값은 0.000015, 0.0000023 이었고 "
                       "값은 33.3, 44.4, 55.5 였다.\n")
    bundle = make_bundle(tmp_path / "out",
                         {"p.csv": "metric,p\na,1.5e-05\nb,2.3e-06\n"
                                   "c,33.3\nd,44.4\ne,55.5\n"})
    code, out, _err = run(capsys, manuscript, "--outputs", bundle, "--no-files")
    assert code == 0, out
    assert "[치명] 0건" in out


def test_c1_scientific_notation_in_manuscript():
    numbers = [n for n in block_numbers("adjusted p was 1.2e-5 overall")
               if not n.skip]
    assert len(numbers) == 1
    assert str(numbers[0].value) == "0.000012"


# --------------------------------------------------------------------------- #
# C2 / C3 — 부등호 표기
# --------------------------------------------------------------------------- #

def test_c2_literal_less_than_cell_matches(tmp_path, capsys):
    """SPSS·jamovi 는 p 를 `<.001` 이라고 문자 그대로 내보냅니다."""
    manuscript = write(str(tmp_path / "m.md"),
                       "## Results\n유의하였다 (p<0.001). 값은 12.4, 5.5, 6.6, 7.7.\n")
    bundle = make_bundle(tmp_path / "out",
                         {"spss.csv": "test,p\nt,<.001\nb,12.4\nc,5.5\n"
                                      "d,6.6\ne,7.7\n"})
    code, out, _err = run(capsys, manuscript, "--outputs", bundle, "--no-files")
    assert code == 0, out


def test_c3_inequality_with_several_small_p_values_is_not_ambiguous(tmp_path,
                                                                    capsys):
    """`p<0.001` 은 원래 여러 값을 가리킵니다 — '자릿수 상충'이 아닙니다."""
    manuscript = write(str(tmp_path / "m.md"),
                       "## Results\n둘 다 유의하였다 (p<0.001). "
                       "값은 12.4, 5.5, 6.6, 7.7, 8.8.\n")
    bundle = make_bundle(tmp_path / "out",
                         {"p.csv": "test,p\na,0.0002\nb,0.0007\nc,12.4\n"
                                   "d,5.5\ne,6.6\nf,7.7\ng,8.8\n"})
    code, out, _err = run(capsys, manuscript, "--outputs", bundle, "--no-files")
    assert code == 0 and "[경고] 0건" in out


def test_c9_less_than_does_not_clamp_at_zero_for_non_p_values():
    from tracecheck.match import inequality_bounds
    low, _high, _il, _ih = inequality_bounds("<", Decimal("2.0"))
    assert low is None                       # 음수 출력값도 찾을 수 있어야 합니다
    low, _high, _il, _ih = inequality_bounds("<", Decimal("0.001"))
    assert low == 0                          # p 값은 0 아래로 갈 수 없습니다


# --------------------------------------------------------------------------- #
# C4 / C5 — 구버전 잔존 안내가 가리키는 '현재 값'
# --------------------------------------------------------------------------- #

def test_c4_stale_advice_uses_the_same_position_within_the_cell(tmp_path):
    """`11.68 (4.08)` 처럼 한 셀에 값이 둘이면 순번까지 맞춰야 합니다."""
    from tracecheck.analyze import analyze, parse_sections
    manuscript = write(str(tmp_path / "m.md"),
                       "## Results\n표준편차는 4.08 이었다. 값 1.1, 2.2, 3.3, 5.5.\n")
    current = make_bundle(tmp_path / "cur",
                          {"s.csv": "g,v\nA,9.82 (5.51)\nB,1.1\nC,2.2\n"
                                    "D,3.3\nE,5.5\n"})
    previous = make_bundle(tmp_path / "prev",
                           {"s.csv": "g,v\nA,11.68 (4.08)\nB,1.1\nC,2.2\n"
                                     "D,3.3\nE,5.5\n"})
    result = analyze(read_manuscript(manuscript), collect([current], "현재"),
                     collect([previous], "이전"), sections=parse_sections(""))
    stale = [j for j in result.criticals if j.number.value == Decimal("4.08")][0]
    assert stale.current_at_coord.value == Decimal("5.51")   # 평균 9.82 가 아니라 SD
    assert "5.51" in stale.advice


def test_c5_stale_advice_converts_percent_back(tmp_path):
    from tracecheck.analyze import analyze, parse_sections
    manuscript = write(str(tmp_path / "m.md"),
                       "## Results\n이상반응률은 6.25% 였다. 값 1.1, 2.2, 3.3, 5.5.\n")
    current = make_bundle(tmp_path / "cur",
                          {"a.json": '{"ae_rate": 0.07, "b": 1.1, "c": 2.2,'
                                     ' "d": 3.3, "e": 5.5}'})
    previous = make_bundle(tmp_path / "prev",
                           {"a.json": '{"ae_rate": 0.0625, "b": 1.1, "c": 2.2,'
                                      ' "d": 3.3, "e": 5.5}'})
    result = analyze(read_manuscript(manuscript), collect([current], "현재"),
                     collect([previous], "이전"), sections=parse_sections(""))
    stale = [j for j in result.criticals if j.number.value == Decimal("6.25")][0]
    assert "7.00" in stale.advice and "0.07" not in stale.advice


# --------------------------------------------------------------------------- #
# C6 — 백분율 환산이 정수에 붙는 사고
# --------------------------------------------------------------------------- #

def test_c6_percent_conversion_does_not_match_bare_integers(tmp_path, capsys):
    """원고 `0.07` 이 번들의 정수 `7`(n·순번)에 붙으면 치명이 경고로 숨습니다."""
    manuscript = write(str(tmp_path / "m.md"),
                       "## Results\n계수는 0.07 이었다. 값 1.1, 2.2, 3.3, 5.5.\n")
    bundle = make_bundle(tmp_path / "out",
                         {"a.csv": "n,b,c,d,e\n7,1.1,2.2,3.3,5.5\n"})
    _code, out, _err = run(capsys, manuscript, "--outputs", bundle, "--no-files")
    assert "[치명] 1건" in out and "0.07" in out


def test_c6_percent_conversion_reverse_direction(tmp_path, capsys):
    """원고가 비율(0.63), 출력이 퍼센트(62.5)인 경우.

    소수 2자리 미만은 **일부러** 환산하지 않습니다. `0.6` 의 반올림 구간은
    출력 단위로 ±5, `6`(정수)은 ±50 이라 아무 값에나 붙습니다.
    """
    manuscript = write(str(tmp_path / "m.md"),
                       "## Results\n비율은 0.63 이었다. 값 1.1, 2.2, 3.3, 5.5.\n")
    bundle = make_bundle(tmp_path / "out",
                         {"a.csv": "rate,b,c,d,e\n62.5,1.1,2.2,3.3,5.5\n"})
    _code, out, _err = run(capsys, manuscript, "--outputs", bundle, "--no-files")
    assert "[치명] 0건" in out and "백분율" in out


def test_c6_percent_conversion_never_fires_on_bare_integers(tmp_path, capsys):
    """`65개 병상` 이 표준오차 `0.65` 에 붙어 엉뚱한 갱신 권고가 나오던 문제."""
    manuscript = write(str(tmp_path / "m.md"),
                       "## Results\n병상은 65개였다. 값 1.1, 2.2, 3.3, 5.5.\n")
    current = make_bundle(tmp_path / "cur",
                          {"a.csv": "se,b,c,d,e\n0.64,1.1,2.2,3.3,5.5\n"})
    previous = make_bundle(tmp_path / "prev",
                           {"a.csv": "se,b,c,d,e\n0.65,1.1,2.2,3.3,5.5\n"})
    _code, out, _err = run(capsys, manuscript, "--outputs", current,
                           "--previous", previous, "--no-files")
    assert "구버전잔존" not in out and "0.64" not in out
    assert "[치명] 1건" in out


# --------------------------------------------------------------------------- #
# C7 / C8 — 자백의 산술
# --------------------------------------------------------------------------- #

def test_c7_dump_text_counts_match_the_real_run(capsys, examples_dir):
    manuscript = os.path.join(examples_dir, "flawed", "원고.md")
    _code, dump, _err = run(capsys, manuscript, "--dump-text")
    _code2, real, _err2 = run(
        capsys, manuscript, "--outputs",
        os.path.join(examples_dir, "flawed", "분석출력_2026-08-18"), "--no-files")
    extracted = int(dump.rsplit("\n추출 ", 1)[1].split("개")[0])
    assert "추출 숫자        %d개" % extracted in real


def test_c8_high_precision_numbers_are_confessed_not_dropped():
    """조용히 버리는 토큰이 하나라도 있으면 커버리지 자백이 거짓말이 됩니다."""
    numbers = block_numbers("The value was 0.123456789 in the model.")
    assert len(numbers) == 1 and numbers[0].skip == "자릿수 상한 초과"


def test_c8_absurd_digit_runs_are_confessed_as_identifiers():
    numbers = block_numbers("코드 123456789012345678901234567890 입니다")
    assert len(numbers) == 1 and numbers[0].skip == "순수 식별자"


def test_c10_negative_zero_is_printed_as_zero():
    numbers = block_numbers("변화량은 -0.00 이었다", kind="table")
    assert numbers[0].text == "0.00"


# --------------------------------------------------------------------------- #
# E1 — CSV 안의 줄바꿈
# --------------------------------------------------------------------------- #

def test_e1_quoted_newline_in_csv_does_not_fabricate_values(tmp_path, capsys):
    root = str(tmp_path / "out")
    os.makedirs(root)
    with open(os.path.join(root, "r.csv"), "w", encoding="utf-8", newline="") as fh:
        fh.write('a,b,c\n"11\n22",33.3,44.4\n55.5,66.6,77.7\n')
    manuscript = write(str(tmp_path / "m.md"),
                       "## Results\n값은 11, 22, 33.3, 44.4, 55.5, 66.6, 77.7 이었다.\n")
    code, out, _err = run(capsys, manuscript, "--outputs", root, "--no-files")
    assert code == 0, out
    assert "1122" not in out


# --------------------------------------------------------------------------- #
# E2 — 줄바꿈 없는 긴 원고 (전에는 몇 시간)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("repeats", [4000])
def test_e2_very_long_single_line_is_linear(tmp_path, repeats):
    import time
    text = "Sleep onset latency was 23.7 minutes at week 8 (Table 1) [12]. " * repeats
    path = write(str(tmp_path / "long.md"), "# Results\n\n" + text)
    started = time.time()
    manuscript = read_manuscript(path)
    numbers = extract_numbers(manuscript.blocks[-1])
    elapsed = time.time() - started
    assert len(numbers) == repeats * 4
    assert elapsed < 10, "%.1f초 — 문단 길이에 대해 2차로 커지고 있습니다" % elapsed


# --------------------------------------------------------------------------- #
# E3 / S3 — 산출물을 못 쓸 때
# --------------------------------------------------------------------------- #

def test_e3_unwritable_output_dir_is_exit_2_not_a_traceback(tmp_path, capsys,
                                                            simple_case):
    manuscript, bundle = simple_case
    out_dir = tmp_path / "잠긴폴더"
    out_dir.mkdir()
    os.chmod(str(out_dir), 0o555)
    try:
        code, out, err = run(capsys, manuscript, "--outputs", bundle,
                             "--out-dir", str(out_dir))
        assert code == 2                     # 1(치명 발견)로 오해되면 안 됩니다
        assert "쓸 권한이 없습니다" in err     # 사용자가 직접 준 경로만 되비춥니다
        assert "Traceback" not in err
        assert "종료 코드 0" not in out       # 화면과 종료 코드가 어긋나면 안 됩니다
    finally:
        os.chmod(str(out_dir), 0o755)


def test_r_partial_report_set_is_never_left_behind(tmp_path, capsys,
                                                   simple_case):
    """새 리포트 2개 + 옛 리포트 2개가 섞이면, 옛 요약의 '종료 코드 0' 을 보고
    안심하게 됩니다. 하나라도 못 쓰면 **아무것도 바꾸지 않습니다.**"""
    manuscript, bundle = simple_case
    out_dir = tmp_path / "결과"
    out_dir.mkdir()
    for name in ("출처대조.md", "문제목록.csv", "대조표.csv", "요약.txt"):
        (out_dir / name).write_text("옛 리포트: 종료 코드 0", encoding="utf-8")
    # 마지막 산출물 자리에 폴더를 놓아 교체를 실패시킵니다.
    (out_dir / "요약.txt").unlink()
    (out_dir / "요약.txt").mkdir()
    code, _out, err = run(capsys, manuscript, "--outputs", bundle,
                          "--out-dir", str(out_dir))
    assert code == 2 and "일반 파일이 아닌 것" in err
    for name in ("출처대조.md", "문제목록.csv", "대조표.csv"):
        assert (out_dir / name).read_text(encoding="utf-8") == "옛 리포트: 종료 코드 0"
    assert not any(n.endswith(".작성중") for n in os.listdir(str(out_dir)))


def test_r_a3_console_verdict_matches_the_exit_code(tmp_path, capsys,
                                                    simple_case):
    """out-dir 을 거부할 때 화면에 '종료 코드 0' 이 찍히면 안 됩니다."""
    manuscript, bundle = simple_case
    code, out, err = run(capsys, manuscript, "--outputs", bundle,
                         "--out-dir", os.path.join(bundle, "안쪽"))
    assert code == 2 and "번들 폴더 안" in err
    assert "판정:" not in out


def test_s5_no_orphan_reports_when_an_output_would_overwrite_an_input(tmp_path,
                                                                      capsys):
    """마지막 산출물이 충돌해도, 앞의 세 개가 이미 쓰여 있으면 안 됩니다."""
    folder = tmp_path / "작업"
    folder.mkdir()
    manuscript = write(str(folder / "요약.txt"),
                       "Results\n값은 11, 22, 33.3, 44.4, 55.5, 66.6 이었다.\n")
    bundle = make_bundle(tmp_path / "out",
                         {"a.csv": "a,b,c,d,e,f\n11,22,33.3,44.4,55.5,66.6\n"})
    code, _out, err = run(capsys, manuscript, "--outputs", bundle,
                          "--out-dir", str(folder))
    assert code == 2 and "덮어쓰게" in err
    assert os.listdir(str(folder)) == ["요약.txt"]


# --------------------------------------------------------------------------- #
# E4 / E5 / E6 / E9 — 조용한 누락 금지
# --------------------------------------------------------------------------- #

def test_e4_deep_json_is_confessed(tmp_path):
    root = str(tmp_path / "out")
    os.makedirs(root)
    node = {"v": 7.77}
    for i in range(45):
        node = {"lvl%d" % i: node}
    write(os.path.join(root, "deep.json"), json.dumps(node))
    bundle = collect([root], "현재")
    assert bundle.truncated
    assert any("중첩" in why for _f, why in bundle.unread)


def test_e5_missing_xlsx_sheet_is_confessed(tmp_path):
    root = str(tmp_path / "out")
    os.makedirs(root)
    path = os.path.join(root, "book.xlsx")
    workbook = ('<?xml version="1.0"?><workbook xmlns="http://schemas.'
                'openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://'
                'schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="있음" sheetId="1" r:id="rId1"/>'
                '<sheet name="없음" sheetId="2" r:id="rId2"/></sheets></workbook>')
    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxml'
            'formats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Target="worksheets/sheet2.xml"/>'
            '</Relationships>')
    sheet = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxml'
             'formats.org/spreadsheetml/2006/main"><sheetData><row r="1">'
             '<c r="A1"><v>12.44</v></c></row></sheetData></worksheet>')
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    bundle = collect([root], "현재")
    assert bundle.cell_count == 1
    assert any("없음" in why for _f, why in bundle.unread)


def test_e6_fifo_named_csv_is_skipped_not_opened(tmp_path):
    root = str(tmp_path / "out")
    os.makedirs(root)
    write(os.path.join(root, "real.csv"), "m\n1.5\n")
    os.mkfifo(os.path.join(root, "pipe.csv"))
    bundle = collect([root], "현재")           # 파이프를 열면 여기서 영원히 멈춥니다
    assert bundle.cell_count == 1
    assert any("일반 파일이 아닙니다" in why for _f, why in bundle.unread)


def test_e9_empty_out_dir_is_refused(capsys, simple_case):
    manuscript, bundle = simple_case
    code, _out, err = run(capsys, manuscript, "--outputs", bundle, "--out-dir", "")
    assert code == 2 and "--out-dir" in err


def test_s2_unknown_extension_is_confessed(tmp_path, capsys):
    """`.html` 안에 값이 있는데 '읽지 못한 파일 0개'라고 하면 그건 거짓말입니다."""
    bundle = make_bundle(tmp_path / "out", {
        "adherence.html": "<p>adherence 87.3 percent</p>",
        "a.csv": "a,b,c,d\n12.4,5.5,6.6,7.7\n"})
    manuscript = write(str(tmp_path / "m.md"),
                       "## Results\n순응도 87.3%, 값 12.4, 5.5, 6.6, 7.7.\n")
    _code, out, _err = run(capsys, manuscript, "--outputs", bundle, "--no-files")
    assert "읽지 못한 파일   1개" in out
    assert "지원하지 않는 형식(.html)" in out


# --------------------------------------------------------------------------- #
# S1 — 엔티티 폭탄 방어가 실제로 동작하는가
# --------------------------------------------------------------------------- #

def test_s1_dtd_in_utf16_is_refused():
    data = ('<?xml version="1.0" encoding="UTF-16"?>'
            '<!DOCTYPE lolz [<!ENTITY lol "lol">]><r/>').encode("utf-16")
    with pytest.raises(ArchiveError):
        guard_xml(data, "document.xml")


def test_s1_dtd_after_a_long_comment_is_refused():
    data = (b'<?xml version="1.0"?>' + b"<!--" + b"A" * 9000 + b"-->"
            + b'<!DOCTYPE a [<!ENTITY b "x">]><r/>')
    with pytest.raises(ArchiveError):
        guard_xml(data, "document.xml")


def test_s1_ordinary_xml_passes_the_guard():
    assert guard_xml(b'<?xml version="1.0"?><r><a>1</a></r>')


def test_s1_broken_xml_is_reported_as_damaged():
    with pytest.raises(ArchiveError) as exc:
        guard_xml(b"<r><a></r>", "sheet1.xml")
    assert "손상" in str(exc.value)


# --------------------------------------------------------------------------- #
# S-B3 — zip 폭탄 한도가 실제로 걸리는가 (전에는 통째로 삭제해도 CI 초록)
# --------------------------------------------------------------------------- #

def test_zip_bomb_compression_ratio_is_refused(tmp_path):
    path = str(tmp_path / "bomb.xlsx")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/workbook.xml", b"\0" * (8 * 1024 * 1024))
    with pytest.raises(ArchiveError) as exc:
        open_zip(path)
    assert "폭탄" in str(exc.value)


def test_zip_member_count_limit(tmp_path):
    path = str(tmp_path / "many.xlsx")
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(5001):
            zf.writestr("x%d.xml" % i, "<a/>")
    with pytest.raises(ArchiveError) as exc:
        open_zip(path)
    assert "압축 항목" in str(exc.value)


def test_zip_absolute_and_traversal_members_are_refused(tmp_path):
    for name in ("/etc/passwd", "../../escape.xml"):
        path = str(tmp_path / ("z%d.xlsx" % abs(hash(name))))
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(name, "<a/>")
        with pytest.raises(ArchiveError):
            open_zip(path)


def test_read_member_size_cap(tmp_path):
    path = str(tmp_path / "big.xlsx")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.xml", b"<a>" + b"x" * 4096 + b"</a>")
    zf = open_zip(path)
    try:
        with pytest.raises(ArchiveError):
            read_member(zf, "a.xml", limit=100)
    finally:
        zf.close()


# --------------------------------------------------------------------------- #
# S-B2 — 진짜 엑셀이 쓰는 sharedStrings 경로
# --------------------------------------------------------------------------- #

def test_shared_strings_xlsx_is_read(tmp_path):
    path = str(tmp_path / "out" / "book.xlsx")
    os.makedirs(os.path.dirname(path))
    make_xlsx(path, {"결과": [["metric", "mean"], ["ISI", 12.44]]}, shared=True)
    bundle = collect([str(tmp_path / "out")], "현재")
    cell = [c for c in bundle.cells if str(c.value) == "12.44"][0]
    assert (cell.sheet, cell.row, cell.col) == ("결과", 2, "B")


# --------------------------------------------------------------------------- #
# D-A1 / D-B1 / D-B2 — 부호, 라벨, 실무 원고 노이즈
# --------------------------------------------------------------------------- #

def test_d_b1_year_followed_by_punctuation_is_still_a_year():
    """"January 2026, 412 individuals" 의 2026 이 치명이 되던 버그."""
    for text in ("In January 2026, 412 individuals were screened.",
                 "모집은 2026. 이후 진행되었다.",
                 "recruited until 2027 and analysed"):
        reasons = {n.text: n.skip for n in block_numbers(text)}
        year = [v for k, v in reasons.items() if k in ("2026", "2027")]
        assert year == [SKIP_YEAR], text


def test_d_b4_instrument_names_are_skipped():
    for name in ("PHQ-9", "GAD-7", "SF-36", "DSM-5", "ICD-10", "COVID-19"):
        numbers = block_numbers("Scores on the %s improved." % name)
        assert [n.skip for n in numbers] == [SKIP_INSTRUMENT], name


def test_r_instrument_rule_does_not_eat_real_values():
    """`Change-3.5`(대시가 마이너스), `Na-138` 을 삼키면 결과값이 사라집니다."""
    for text in ("Change-3.5 points favoured the active arm",
                 "Na-138 mmol/L was recorded", "95% CI-0.8 to -0.1"):
        numbers = [n for n in block_numbers(text) if not n.skip]
        assert numbers, text


def test_r_m3_hyphenated_instrument_headers_are_label_numbers():
    """`SF-36 PCS` 열 이름의 36 이 원고의 36 에 '출처 확인' 되면 안 됩니다."""
    from tracecheck.numbers import cell_numbers
    for header in ("SF-36 PCS", "PHQ-9 total", "HAM-D-17"):
        assert all(lab for *_r, lab in cell_numbers(header)), header


def test_d_b5_english_time_expressions_are_skipped():
    for text in ("assessed at 8 weeks", "over 12 months of follow-up",
                 "an 8-week programme", "after 14 days"):
        numbers = block_numbers(text)
        assert [n.skip for n in numbers] == [SKIP_TIME], text


def test_r_m4_real_values_with_time_units_are_not_swallowed():
    """'평균 연령 43세', 'median stay 5 days' 는 분석 결과값입니다."""
    for text in ("The mean age was 43 years.",
                 "Median hospital stay was 5 days in the active arm.",
                 "Mean duration of therapy was 14 days.",
                 "재원 기간은 5일이었다."):
        numbers = [n for n in block_numbers(text) if not n.skip]
        assert numbers, text


def test_r_m4_korean_timepoints_still_skipped():
    for text in ("8주 시점 평균은", "12개월 추적 결과", "제 4주 방문에서"):
        numbers = block_numbers(text)
        assert SKIP_TIME in [n.skip for n in numbers], text


def test_d_a1_sign_only_difference_is_a_warning(tmp_path, capsys):
    manuscript = write(str(tmp_path / "m.md"),
                       "## Results\n입면잠복기는 14.6분 감소하였다. "
                       "값 1.1, 2.2, 3.3, 5.5.\n")
    bundle = make_bundle(tmp_path / "out",
                         {"a.csv": "diff,b,c,d,e\n-14.63,1.1,2.2,3.3,5.5\n"})
    code, out, _err = run(capsys, manuscript, "--outputs", bundle, "--no-files")
    assert code == 2 and "[치명] 0건" in out
    assert "부호만 다른 값" in out


def test_d_b2_stale_survives_a_sign_difference(tmp_path):
    from tracecheck.analyze import analyze, parse_sections
    manuscript = write(str(tmp_path / "m.md"),
                       "## Results\n입면잠복기는 11.4분 감소하였다. "
                       "값 1.1, 2.2, 3.3, 5.5.\n")
    current = make_bundle(tmp_path / "cur",
                          {"s.csv": "diff,b,c,d,e\n-6.31,1.1,2.2,3.3,5.5\n"})
    previous = make_bundle(tmp_path / "prev",
                           {"s.csv": "diff,b,c,d,e\n-11.42,1.1,2.2,3.3,5.5\n"})
    result = analyze(read_manuscript(manuscript), collect([current], "현재"),
                     collect([previous], "이전"), sections=parse_sections(""))
    assert [j.verdict for j in result.criticals] == ["구버전잔존"]
    assert "-6.31" in result.criticals[0].advice


def test_d_b3_label_only_match_is_not_a_confirmation(tmp_path):
    from tracecheck.analyze import analyze, parse_sections
    manuscript = write(str(tmp_path / "m.md"),
                       "## Results\n표에서 9 였다. 값 1.1, 2.2, 3.3, 5.5.\n")
    bundle = make_bundle(tmp_path / "out",
                         {"a.csv": "phq9_change,b,c,d,e\n1.1,1.1,2.2,3.3,5.5\n"})
    result = analyze(read_manuscript(manuscript), collect([bundle], "현재"),
                     None, sections=parse_sections(""))
    labelled = [j for j in result.judgements if j.number.value == 9]
    assert labelled and labelled[0].grade == GRADE_WARN
    assert labelled[0].verdict == VERDICT_LABEL


def test_label_cells_are_not_offered_as_the_nearest_value(tmp_path):
    from tracecheck.analyze import analyze, parse_sections
    manuscript = write(str(tmp_path / "m.md"),
                       "## Results\n값은 6.7, 1.1, 2.2, 3.3, 5.5 였다.\n")
    bundle = make_bundle(tmp_path / "out",
                         {"a.csv": "gad7_change,b,c,d,e\n1.1,1.1,2.2,3.3,5.5\n"})
    result = analyze(read_manuscript(manuscript), collect([bundle], "현재"),
                     None, sections=parse_sections(""))
    missing = [j for j in result.criticals if j.number.value == Decimal("6.7")][0]
    assert "gad7_change" not in missing.note


def test_real_values_glued_to_units_are_still_values():
    """`12.4mmHg` 의 12.4 는 값입니다 — 라벨로 오해하면 안 됩니다."""
    from tracecheck.numbers import cell_numbers
    assert [lab for *_r, lab in cell_numbers("12.4mmHg")] == [False]


# --------------------------------------------------------------------------- #
# S-A6 — CSV 가드 우회
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("payload", ["\t=cmd|' /c calc'!A1", " =HYPERLINK(1)",
                                     " +cmd", "﻿@SUM(1)"])
def test_leading_invisible_whitespace_cannot_bypass_the_csv_guard(payload):
    assert csv_safe(payload).startswith("'")


@pytest.mark.parametrize("payload", ["-1+cmd|' /c calc'!A1", "-cmd", "+cmd"])
def test_numeric_looking_prefixes_that_are_not_numbers_are_escaped(payload):
    assert csv_safe(payload).startswith("'")


# --------------------------------------------------------------------------- #
# S-A4 — 심볼릭 링크 --out-dir
# --------------------------------------------------------------------------- #

def test_symlinked_out_dir_is_refused(tmp_path, capsys, simple_case):
    manuscript, bundle = simple_case
    real = tmp_path / "진짜폴더"
    real.mkdir()
    link = str(tmp_path / "링크폴더")
    os.symlink(str(real), link)
    code, _out, err = run(capsys, manuscript, "--outputs", bundle,
                          "--out-dir", link)
    assert code == 2 and "심볼릭 링크" in err
    assert os.listdir(str(real)) == []


# --------------------------------------------------------------------------- #
# S-B1 — 인수 기준에 커버리지 숫자를 못 박기
# --------------------------------------------------------------------------- #

def test_acceptance_pins_coverage_not_just_finding_counts(capsys, examples_dir):
    """절 하나를 대조에서 빼도 결함 개수는 그대로여서 안 잡히던 구멍."""
    _code, out, _err = run(
        capsys, os.path.join(examples_dir, "flawed", "원고.md"), "--outputs",
        os.path.join(examples_dir, "flawed", "분석출력_2026-08-18"),
        "--previous", os.path.join(examples_dir, "flawed", "분석출력_2026-08-03"),
        "--no-files")
    assert "추출 숫자        43개" in out
    assert "대조 대상      33개  (Abstract 10 · Results 17 · 표 6)" in out
    assert "매칭 31 · 미매칭 2  (미매칭율 6.1%" in out
    assert "대조 제외 절     15개" in out


def test_clean_example_pins_coverage(capsys, examples_dir):
    _code, out, _err = run(
        capsys, os.path.join(examples_dir, "clean", "원고.md"), "--outputs",
        os.path.join(examples_dir, "clean", "분석출력_2026-08-18"), "--no-files")
    assert "대조 대상      34개  (Abstract 12 · Results 16 · 표 6)" in out
    assert "매칭 34 · 미매칭 0" in out


# --------------------------------------------------------------------------- #
# S-B4 — 조용히 살아남던 변이들
# --------------------------------------------------------------------------- #

def test_unread_file_list_is_printed_not_just_counted(tmp_path, capsys,
                                                      simple_case):
    manuscript, bundle = simple_case
    write(os.path.join(bundle, "old.xls"), "x")
    _code, out, _err = run(capsys, manuscript, "--outputs", bundle, "--no-files")
    assert "old.xls" in out and "구형" in out


def test_skipped_numbers_appear_in_the_comparison_table(analysis_of_flawed):
    from tracecheck.report import render_table_csv
    text = render_table_csv(analysis_of_flawed)
    assert "건너뜀" in text
    assert "시점·기간 표기" in text


def test_cell_coord_uses_the_bundle_relative_path(tmp_path):
    a = make_bundle(tmp_path / "번들_08-18", {"stat.csv": "m\n9.82\n"})
    b = make_bundle(tmp_path / "번들_08-03", {"stat.csv": "m\n11.68\n"})
    first = collect([a], "현재").cells[0]
    second = collect([b], "이전").cells[0]
    assert first.coord == second.coord        # 폴더 이름은 매번 다릅니다
    assert first.file != second.file


def test_symlinked_directory_inside_a_bundle_is_not_followed(tmp_path):
    root = str(tmp_path / "out")
    os.makedirs(root)
    outside = tmp_path / "밖"
    outside.mkdir()
    write(str(outside / "secret.csv"), "m\n999.9\n")
    os.symlink(str(outside), os.path.join(root, "링크"))
    write(os.path.join(root, "a.csv"), "m\n1.5\n")
    bundle = collect([root], "현재")
    assert [str(c.value) for c in bundle.cells] == ["1.5"]


def test_semicolon_delimiter_gives_real_column_labels(tmp_path):
    root = make_bundle(tmp_path / "out", {"a.csv": "group;mean\nA;12.44\n"})
    bundle = collect([root], "현재")
    assert bundle.cells[0].col == "mean"


def test_csv_field_size_limit_is_enforced(tmp_path):
    root = str(tmp_path / "out")
    os.makedirs(root)
    write(os.path.join(root, "huge.csv"), "m\n" + '"' + "x" * (1 << 21) + '"\n')
    bundle = collect([root], "현재")
    assert any("huge.csv" in f for f, _why in bundle.unread)


def test_manuscript_notes_reach_the_report(tmp_path, capsys):
    """인코딩을 판별 못 했다는 사실이 리포트까지 올라와야 합니다."""
    path = str(tmp_path / "cp949.txt")
    with open(path, "wb") as handle:
        handle.write("Results\n값은 12.44, 15.91, 4.08, 4.63, 3.47 이었다.\n"
                     .encode("cp949"))
    bundle = make_bundle(tmp_path / "out",
                         {"a.csv": "a,b,c,d,e\n12.44,15.91,4.08,4.63,3.47\n"})
    _code, out, _err = run(capsys, path, "--outputs", bundle, "--no-files")
    assert "cp949" in out


def test_docx_line_tag_uses_paragraph_numbers(tmp_path, capsys):
    path = make_docx(str(tmp_path / "m.docx"),
                     ["Results", "값은 12.44, 15.91, 4.08, 4.63, 99.9 이었다."])
    bundle = make_bundle(tmp_path / "out",
                         {"a.csv": "a,b,c,d\n12.44,15.91,4.08,4.63\n"})
    _code, out, _err = run(capsys, path, "--outputs", bundle, "--no-files")
    assert "문단2" in out


@pytest.fixture
def analysis_of_flawed(examples_dir):
    from tracecheck.analyze import analyze, parse_sections
    base = os.path.join(examples_dir, "flawed")
    return analyze(read_manuscript(os.path.join(base, "원고.md")),
                   collect([os.path.join(base, "분석출력_2026-08-18")], "현재"),
                   collect([os.path.join(base, "분석출력_2026-08-03")], "이전"),
                   sections=parse_sections(""))


def test_issue_csv_has_a_row_per_finding(analysis_of_flawed):
    from tracecheck.report import ISSUE_HEADER, render_issues_csv
    lines = render_issues_csv(analysis_of_flawed).strip().splitlines()
    assert lines[0].split(",") == ISSUE_HEADER
    assert len(lines) == 1 + len(analysis_of_flawed.criticals) + \
        len(analysis_of_flawed.warns)


def test_write_outputs_is_atomic_enough_to_not_leave_half_reports(tmp_path,
                                                                  analysis_of_flawed):
    out_dir = tmp_path / "결과"
    out_dir.mkdir()
    victim = write(str(out_dir / "요약.txt"), "입력인 척하는 파일")
    with pytest.raises(InputError):
        write_outputs(analysis_of_flawed, str(out_dir),
                      [os.path.realpath(victim)])
    assert os.listdir(str(out_dir)) == ["요약.txt"]


def test_info_grade_still_exists_for_plain_matches(analysis_of_flawed):
    assert analysis_of_flawed.infos
    assert all(j.grade == GRADE_INFO for j in analysis_of_flawed.infos)


def test_sign_verdict_constant_is_used_somewhere():
    assert VERDICT_SIGN == "부호확인요망"

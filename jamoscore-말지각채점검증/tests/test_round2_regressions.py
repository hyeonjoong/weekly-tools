# -*- coding: utf-8 -*-
"""2라운드 적대 검토가 찾은 결함의 회귀 테스트.

각 테스트 이름 뒤의 괄호는 `HARDENING.md` 의 항목 번호다. 여기 있는 것들은
전부 **코드를 되돌리면 실패**하는지 확인하며 썼다(변이 검사에서 살아남았던
동작이 절반이었다).
"""

import csv
import os
import sys

import pytest

from jamoscore import analyze, safeio, summary
from jamoscore.cli import main
from jamoscore.reader import Item, Pass, find_blocks
from jamoscore.spec import parse_subtest
from tests.conftest import SPEC_ARGS

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "examples"))
from 예제만들기 import write_workbook  # noqa: E402

TESTS = ["자음", "모음", "일음절"]
TPS = ["사전", "사후"]
CONS = ["아라", "아따", "아가", "아자", "아까", "아하", "아파", "아사", "아싸",
        "아타", "아짜", "아나", "아차", "아마", "아다", "아바", "아빠", "아카"]


def build(path, sheets):
    write_workbook(str(path), sheets)
    return str(path)


def block_rows(items, header=("No.", "자음-사전", "응답", "점수")):
    rows = [list(header)]
    for i, row in enumerate(items, start=1):
        rows.append([str(i)] + list(row))
    return rows


def artifact_text(out_dir):
    chunks = []
    for root, _, files in os.walk(str(out_dir)):
        for name in files:
            chunks.append(open(os.path.join(root, name), encoding="utf-8-sig",
                               errors="replace").read())
    return "\n".join(chunks)


# --------------------------------------------- 마스킹이 모든 산출물에 닿는가
def test_identifier_in_response_is_masked_in_rule_comparison_csv(tmp_path,
                                                                 capsys):
    """`규칙대조.csv` 는 Finding 이 아니라 원시 행에서 만들어진다 — 여기서 샜다."""
    path = build(tmp_path / "누출.xlsx", [("S01", block_rows([
        ("아라", "아바 010-1234-5678", "1"),     # 사람 1 / 규칙 0 → 규칙대조에 실림
        ("아따", "아바 900101-9234567", "1"),
        ("아가", "아가", "1"),
    ]))])
    out_dir = tmp_path / "결과"
    main([path, "--subtest", "자음:목표=2음절초성", "--out-dir", str(out_dir)])
    capsys.readouterr()
    rule_csv = open(str(out_dir / "규칙대조.csv"), encoding="utf-8-sig").read()
    assert "010-1234-5678" not in rule_csv
    assert "900101-9234567" not in rule_csv
    assert "010-****-****" in rule_csv


def test_identifier_in_sheet_name_is_masked_in_handoff_csv(tmp_path, capsys):
    """시트 이름이 곧 피험자 ID 라, 마스킹이 없으면 넘겨줄 CSV 1열로 새어 나간다."""
    path = build(tmp_path / "시트이름.xlsx",
                 [("S010-1234-5678", block_rows([("아라", "아라", "1")]))])
    out_dir = tmp_path / "결과"
    main([path, "--subtest", "자음:목표=2음절초성", "--out-dir", str(out_dir)])
    capsys.readouterr()
    assert "010-1234-5678" not in artifact_text(out_dir)


@pytest.mark.parametrize("raw,masked", [
    ("900101-9234567", "900101-*******"),      # 뒷자리 첫 숫자 9
    ("900101-0234567", "900101-*******"),      # 뒷자리 첫 숫자 0
    ("9001011234567", "900101-*******"),       # 구분자 없음
    ("02-1234-5678", "02-****-****"),          # 유선
    ("031-123-4567", "031-****-****"),
    ("+82-10-1234-5678", "+82-10-****-****"),
    ("９００１０１-1234567", "900101-*******"),   # 전각
])
def test_mask_identifiers_covers_real_shapes(raw, masked):
    assert safeio.mask_identifiers(raw) == masked


@pytest.mark.parametrize("safe", ["아라", "77.8", "-7.2", "s01", "1,234", "ID",
                                  "12/18", "0.2%"])
def test_mask_identifiers_leaves_data_alone(safe):
    assert safeio.mask_identifiers(safe) == safe


def test_formula_leader_in_sheet_name_is_neutralised_in_every_csv(tmp_path,
                                                                  capsys):
    """시트 이름은 세 CSV 의 1열로 들어간다 — 위생 처리가 빠지면 여기서 드러난다."""
    path = build(tmp_path / "수식.xlsx",
                 [("=cmd|'/C calc'!A1", block_rows([("아라", "아라", "1")]))])
    out_dir = tmp_path / "결과"
    main([path, "--subtest", "자음:목표=2음절초성", "--out-dir", str(out_dir)])
    capsys.readouterr()
    for root, _, files in os.walk(str(out_dir)):
        for name in files:
            if not name.endswith(".csv"):
                continue
            with open(os.path.join(root, name), encoding="utf-8-sig") as fh:
                for row in csv.reader(fh):
                    for cell in row:
                        assert not cell.startswith(tuple(safeio.FORMULA_LEADERS))


# ------------------------------------------------ 조용히 사라지던 문항
def test_leftover_scored_rows_are_reported(tmp_path, capsys):
    """빈 자극 칸 하나가 블록을 끊고 그 아래 문항이 통째로 사라지던 사고."""
    items = [(CONS[i], CONS[i], "1") for i in range(3)]
    rows = block_rows(items)
    rows.append(["4", "", "아바", "0"])      # 자극 칸만 비어 블록이 끊긴다
    for i in range(3, 9):
        rows.append([str(i + 2), CONS[i], CONS[i], "1"])
    path = build(tmp_path / "끊김.xlsx", [("S01", rows)])
    code = main([path, "--subtest", "자음:목표=2음절초성"])
    out = capsys.readouterr().out
    assert code == 3
    assert "더 있습니다" in out
    # 자백 블록의 분모에도 잃어버린 행이 들어가야 한다
    coverage = [ln for ln in out.splitlines() if "문항 " in ln and "대조(" in ln]
    assert coverage and "문항 9개 중 3개 대조(33.3%)" in coverage[0]


def test_leftover_detection_ignores_unscored_trailing_content(tmp_path, capsys):
    """자극 열 아래의 음소 목록·메모는 잃어버린 문항이 아니다."""
    rows = block_rows([(CONS[i], CONS[i], "1") for i in range(18)])
    rows.append(["소계", "", "", ""])
    for syllable in ["가", "나", "다", "라", "마"]:
        rows.append(["", syllable, "", ""])
    path = build(tmp_path / "목록.xlsx", [("S01", rows)])
    code = main([path, "--subtest", "자음:목표=2음절초성,문항=18"])
    out = capsys.readouterr().out
    assert code == 0
    assert "레이아웃 이상" not in out


def test_many_small_runs_below_are_still_counted(tmp_path, capsys):
    """덩어리가 몇 개든 끝까지 훑는다 — 중간에 멈추면 그 아래가 소리 없이 사라진다."""
    rows = block_rows([(CONS[i], CONS[i], "1") for i in range(18)])
    for n in range(8):
        rows.append(["소계%d" % n, "", "", ""])
        rows.append(["", "아라", "아라", "1"])
    path = build(tmp_path / "덩어리.xlsx", [("S01", rows)])
    code = main([path, "--subtest", "자음:목표=2음절초성,문항=18"])
    out = capsys.readouterr().out
    assert code in (1, 3)
    assert "더 있습니다" in out


def test_empty_row_with_huge_number_does_not_trigger_undetermined(tmp_path,
                                                                  examples_dir,
                                                                  capsys):
    """엑셀이 아래쪽에 남기는 빈 서식 행 때문에 판정 불가가 되면 안 된다."""
    import zipfile
    src = os.path.join(examples_dir, "정상_채점.xlsx")
    dst = tmp_path / "빈행.xlsx"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(str(dst), "w") as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item.endswith("sheet1.xml"):
                data = data.replace(b"<sheetData>",
                                    b'<sheetData><row r="300000"/>', 1)
            zout.writestr(item, data)
    code = main([str(dst)] + SPEC_ARGS)
    capsys.readouterr()
    assert code == 0


# -------------------------------------------- 요약표 묶음·밀림·집계행
def make_pass(subject, test, tp, spec_text, scores):
    spec = parse_subtest(spec_text)
    p = Pass(subject, test, tp, spec, subject, 1)
    for seq, score in enumerate(scores, start=1):
        p.items.append(Item(subject, test, tp, spec.unit, seq, seq + 1, 1,
                            "후드", "후드", str(score), subject))
    analyze.score_items([p])
    return p


def test_column_collapse_does_not_delete_a_different_columns_finding():
    """'음절_전' 을 묶다가 '일음절_전' 의 진짜 소견을 지우던 사고."""
    passes = []
    rows = {1: {1: "ID", 2: "음절_전", 3: "일음절_전"}}
    for i in range(1, 8):
        sid = "S%02d" % i
        passes.append(make_pass(sid, "음절", "사전", "음절:목표=전체", [1, 0]))
        passes.append(make_pass(sid, "일음절", "사전", "일음절:목표=전체", [1, 0]))
        # '음절_전' 은 전부 어긋나게, '일음절_전' 은 S03 만 어긋나게
        rows[i + 1] = {1: sid, 2: "99.0", 3: "70.0" if sid == "S03" else "50.0"}
    findings, stats, _ = summary.compare(
        rows, "요약", passes, ["음절", "일음절"], TPS,
        ["S%02d" % i for i in range(1, 8)])
    kinds = [f.kind for f in findings]
    assert "요약표 열 매핑 의심" in kinds
    # 묶인 열의 개별 소견은 사라져야 하고…
    assert not any(f.kind == "요약표 값 불일치" and "음절_전열" in f.location
                   and "일음절_전열" not in f.location for f in findings)
    # …다른 열의 진짜 소견은 살아 있어야 한다
    assert any(f.kind == "요약표 값 불일치" and "S03" in f.subject
               for f in findings)
    assert stats["불일치"] == 8


def test_row_shift_is_diagnosed_as_one_finding():
    passes = []
    rows = {1: {1: "ID", 2: "자음_전"}}
    truth = [40.0, 50.0, 60.0, 70.0, 80.0]
    for i, value in enumerate(truth, start=1):
        sid = "S%02d" % i
        correct = int(round(value / 10))
        passes.append(make_pass(sid, "자음", "사전", "자음:목표=전체",
                                [1] * correct + [0] * (10 - correct)))
        # 표의 값이 '한 행 아래'의 참값
        shifted = truth[i] if i < len(truth) else 99.0
        rows[i + 1] = {1: sid, 2: "%.1f" % shifted}
    findings, _, _ = summary.compare(rows, "요약", passes, ["자음"], TPS,
                                     ["S%02d" % i for i in range(1, 6)])
    shift = [f for f in findings if f.kind == "요약표 열이 한 행 밀림"]
    assert len(shift) == 1
    assert "붙여넣기" in shift[0].message


def test_missing_subject_in_summary_is_critical():
    passes = [make_pass("S01", "자음", "사전", "자음:목표=전체", [1, 1]),
              make_pass("S02", "자음", "사전", "자음:목표=전체", [1, 0])]
    rows = {1: {1: "ID", 2: "자음_전"}, 2: {1: "S01", 2: "100.0"}}
    findings, stats, _ = summary.compare(rows, "요약", passes, ["자음"], TPS,
                                         ["S01", "S02"])
    missing = [f for f in findings if f.kind == "요약표에 행이 없는 피험자"]
    assert missing and missing[0].severity == "치명"
    assert "S02" in missing[0].message
    assert stats["요약표없는피험자"] == 1


def test_aggregate_rows_plus_missing_subjects_is_critical():
    passes = [make_pass("S01", "자음", "사전", "자음:목표=전체", [1, 1]),
              make_pass("S02", "자음", "사전", "자음:목표=전체", [1, 0])]
    rows = {1: {1: "ID", 2: "자음_전"},
            2: {1: "S01", 2: "100.0"},
            3: {1: "Ex(m)", 2: "100.0"}}
    findings, _, _ = summary.compare(rows, "요약", passes, ["자음"], TPS,
                                     ["S01", "S02"])
    agg = [f for f in findings if f.kind == "집계행이 일부 피험자만으로 계산됨"]
    assert agg and "1명으로 계산된" in agg[0].message


def test_subject_name_whitespace_does_not_fake_a_missing_row():
    passes = [make_pass("s01 ", "자음", "사전", "자음:목표=전체", [1, 1])]
    rows = {1: {1: "ID", 2: "자음_전"}, 2: {1: "s01", 2: "100.0"}}
    findings, stats, _ = summary.compare(rows, "요약", passes, ["자음"], TPS,
                                         ["s01 "])
    assert [f for f in findings if f.kind == "요약표에 행이 없는 피험자"] == []
    assert stats["대조"] == 1


def test_difference_column_evidence_states_both_timepoints():
    passes = [make_pass("S01", "자음", "사전", "자음:목표=전체", [1, 0]),
              make_pass("S01", "자음", "사후", "자음:목표=전체", [1, 1])]
    rows = {1: {1: "ID", 2: "자음_차이"}, 2: {1: "S01", 2: "99.0"}}
    findings, _, _ = summary.compare(rows, "요약", passes, ["자음"], TPS, ["S01"])
    assert findings
    assert "사전" in findings[0].evidence and "사후" in findings[0].evidence
    assert "알 수 없" not in findings[0].evidence


# --------------------------------------------------- 열 결합·선언 의심
def test_exact_header_match_wins_over_prefix():
    """`반응유형` 이 `반응` 접두에 걸려 진짜 `응답` 열을 가로채던 사고."""
    rows = {1: {1: "No.", 2: "자음-사전", 3: "반응유형", 4: "응답", 5: "점수"}}
    blocks = find_blocks(rows, 1, TESTS, TPS, "S01")
    assert blocks == [(2, "자음", "사전", 4, 5)]


def test_absurd_rule_mismatch_rate_is_escalated(tmp_path, capsys):
    """0/1 점수 열에 만점 3 을 선언하면 규칙대조가 통째로 잡음이 된다."""
    rows = block_rows([("각", "각", "1") for _ in range(25)],
                      header=("No.", "자음-사전", "응답", "점수"))
    path = build(tmp_path / "선언오류.xlsx", [("S01", rows)])
    code = main([path, "--subtest", "자음:목표=초중종,만점=3,문항=25"])
    out = capsys.readouterr().out
    assert code == 1
    assert "채점 선언 의심" in out


def test_normal_rate_is_not_escalated(examples_dir, capsys):
    code = main([os.path.join(examples_dir, "정상_채점.xlsx")] + SPEC_ARGS)
    out = capsys.readouterr().out
    assert "채점 선언 의심" not in out
    assert code == 0


# ------------------------------------------------------------ 플래그
@pytest.mark.parametrize("flag", ["--response-alias", "--score-alias",
                                  "--pii-sheet", "--non-response"])
def test_empty_flag_value_is_refused(examples_dir, flag, capsys):
    code = main([os.path.join(examples_dir, "정상_채점.xlsx"),
                 "--subtest", "자음:목표=2음절초성", flag, ""])
    err = capsys.readouterr().err
    assert code == 2
    assert "빈 값" in err


def test_response_alias_actually_works(tmp_path, capsys):
    rows = [["No.", "자음-사전", "reply", "pt"], ["1", "아라", "아라", "1"]]
    path = build(tmp_path / "별칭.xlsx", [("S01", rows)])
    code = main([path, "--subtest", "자음:목표=2음절초성",
                 "--response-alias", "reply", "--score-alias", "pt"])
    out = capsys.readouterr().out
    assert code == 0
    assert "규칙 대조: 1문항" in out


def test_more_than_two_timepoints_is_refused(examples_dir, capsys):
    code = main([os.path.join(examples_dir, "정상_채점.xlsx"),
                 "--subtest", "자음:목표=2음절초성",
                 "--timepoints", "사전,중간,사후"])
    err = capsys.readouterr().err
    assert code == 2
    assert "longistat" in err


def test_map_pointing_at_a_missing_column_is_refused(examples_dir, tmp_path,
                                                     capsys):
    mapping = tmp_path / "m.json"
    mapping.write_text('{"요약열": {"없는열": {"검사": "자음"}}}', encoding="utf-8")
    code = main([os.path.join(examples_dir, "정상_채점.xlsx")] + SPEC_ARGS
                + ["--map", str(mapping)])
    err = capsys.readouterr().err
    assert code == 2
    assert "없는열" in err


def test_map_pointing_at_an_undeclared_test_is_refused(examples_dir, tmp_path,
                                                       capsys):
    mapping = tmp_path / "m.json"
    mapping.write_text('{"요약열": {"자음_전": {"검사": "없는검사"}}}',
                       encoding="utf-8")
    code = main([os.path.join(examples_dir, "정상_채점.xlsx")] + SPEC_ARGS
                + ["--map", str(mapping)])
    err = capsys.readouterr().err
    assert code == 2
    assert "없는검사" in err


# ---------------------------------------------------- 판정·산출물 일관성
def test_verdict_counts_include_late_findings(tmp_path, examples_dir, capsys):
    """`시트를 끝까지 읽지 못함` 이 판정 줄의 집계에서 빠지던 사고."""
    import zipfile
    src = os.path.join(examples_dir, "정상_채점.xlsx")
    dst = tmp_path / "큰행.xlsx"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(str(dst), "w") as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item.endswith("sheet1.xml"):
                data = data.replace(
                    b"<sheetData>",
                    b'<sheetData><row r="999999999"><c r="A999999999" '
                    b't="inlineStr"><is><t>x</t></is></c></row>', 1)
            zout.writestr(item, data)
    main([str(dst)] + SPEC_ARGS)
    out = capsys.readouterr().out
    printed = out.count("\n[치명]") + (1 if out.startswith("[치명]") else 0)
    tallied = int(out.split("치명 ")[-1].split("건")[0])
    assert tallied >= printed >= 1


def test_duplicate_sheet_names_are_warned(tmp_path, capsys):
    import zipfile
    path = build(tmp_path / "중복.xlsx", [("S01", block_rows(
        [(CONS[i], CONS[i], "1") for i in range(3)]))])
    dst = tmp_path / "중복2.xlsx"
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(str(dst), "w") as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item == "xl/workbook.xml":
                data = data.replace(
                    b"</sheets>",
                    b'<sheet name="S01" sheetId="2" r:id="rId1"/></sheets>')
            zout.writestr(item, data)
    main([str(dst), "--subtest", "자음:목표=2음절초성"])
    out = capsys.readouterr().out
    assert "이름이 겹치는 시트" in out


def test_blank_pass_is_excluded_from_handoff_csv(tmp_path, capsys):
    """하류 툴이 그 0 을 실제 수행으로 분석에 넣는다 — 넘기지 않아야 한다."""
    rows = [["No.", "자음-사전", "응답", "점수", "자음-사후", "응답", "점수"]]
    for i in range(4):
        rows.append([str(i + 1), CONS[i], CONS[i], "1", CONS[i], "", "0"])
    path = build(tmp_path / "탈락.xlsx", [("S01", rows)])
    out_dir = tmp_path / "결과"
    main([path, "--subtest", "자음:목표=2음절초성,문항=4", "--out-dir", str(out_dir)])
    capsys.readouterr()
    ready = out_dir / "statwise_longistat_입력" / "자음_단어.csv"
    text = open(str(ready), encoding="utf-8-sig").read()
    assert "사전" in text
    assert "사후" not in text
    scores = open(str(out_dir / "문항점수.csv"), encoding="utf-8-sig").read()
    assert "응답 공란·0점" in scores


def test_output_folder_is_validated_before_any_file_is_written(tmp_path,
                                                               examples_dir,
                                                               capsys):
    """하위 폴더를 나중에 검사하면 두 실행의 산출물이 섞인 채 남는다."""
    out_dir = tmp_path / "결과"
    out_dir.mkdir()
    victim = tmp_path / "바깥"
    victim.mkdir()
    os.symlink(str(victim), str(out_dir / "statwise_longistat_입력"))
    code = main([os.path.join(examples_dir, "정상_채점.xlsx")] + SPEC_ARGS
                + ["--out-dir", str(out_dir)])
    capsys.readouterr()
    assert code == 2
    assert not (out_dir / "채점검증.md").exists()


def test_percent_formatted_summary_cell_is_not_a_fake_critical(tmp_path, capsys):
    """엑셀 백분율 서식 셀은 0.75 로 저장된다 — 0.75% 가 아니다."""
    rows = block_rows([(CONS[i], CONS[i] if i < 3 else "아바", "1" if i < 3 else "0")
                       for i in range(4)])
    rows.append(["", "", "", "0.75"])
    path = build(tmp_path / "서식.xlsx", [("S01", rows)])
    code = main([path, "--subtest", "자음:목표=2음절초성,문항=4"])
    out = capsys.readouterr().out
    assert "불가능한 백분율" not in out
    assert code in (0, 1)


def test_excel_error_values_in_summary_are_reported(tmp_path, capsys):
    rows = block_rows([(CONS[i], CONS[i], "1") for i in range(2)])
    summary_rows = [["ID", "자음_전"], ["S01", "#DIV/0!"]]
    path = build(tmp_path / "오류.xlsx",
                 [("S01", rows), ("total-최종", summary_rows)])
    main([path, "--subtest", "자음:목표=2음절초성,문항=2", "--summary", "total-최종"])
    out = capsys.readouterr().out
    assert "엑셀 오류값" in out


def test_unexpected_exception_becomes_undetermined_not_critical(
        examples_dir, monkeypatch, capsys):
    """트레이스백이 종료코드 1 로 나가면 '치명 1건'과 구별되지 않는다."""
    from jamoscore import analyze as analyze_mod

    def boom(*args, **kwargs):
        raise RuntimeError("의도적으로 터뜨린 오류")

    monkeypatch.setattr(analyze_mod, "score_items", boom)
    code = main([os.path.join(examples_dir, "정상_채점.xlsx")] + SPEC_ARGS)
    captured = capsys.readouterr()
    assert code == 3
    assert "판정 불가" in captured.err
    assert "Traceback" not in captured.err
    assert os.path.expanduser("~") not in captured.err


def test_crash_message_does_not_leak_absolute_paths(examples_dir, monkeypatch,
                                                    capsys):
    from jamoscore import analyze as analyze_mod

    home = os.path.expanduser("~")

    def boom(*args, **kwargs):
        raise RuntimeError("%s/비밀/자료.xlsx 에서 실패" % home)

    monkeypatch.setattr(analyze_mod, "score_items", boom)
    code = main([os.path.join(examples_dir, "정상_채점.xlsx")] + SPEC_ARGS)
    err = capsys.readouterr().err
    assert code == 3
    assert home not in err


def test_non_regular_file_input_is_refused(tmp_path, capsys):
    fifo = tmp_path / "파이프.xlsx"
    os.mkfifo(str(fifo))
    code = main([str(fifo), "--subtest", "자음:목표=2음절초성"])
    err = capsys.readouterr().err
    assert code == 2
    assert "보통 파일" in err


def test_dtd_behind_large_padding_is_refused(tmp_path, examples_dir, capsys):
    """앞 N바이트만 보면 그만큼의 주석으로 밀어내 우회할 수 있다."""
    import zipfile
    src = os.path.join(examples_dir, "정상_채점.xlsx")
    dst = tmp_path / "폭탄.xlsx"
    padding = b"<!-- " + b"x" * (5 * 1024 * 1024) + b" -->"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(str(dst), "w") as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item.endswith("sheet1.xml"):
                data = (b'<?xml version="1.0"?>' + padding
                        + b'<!DOCTYPE x [<!ENTITY a "b">]>' + data)
            zout.writestr(item, data)
    code = main([str(dst)] + SPEC_ARGS)
    captured = capsys.readouterr()
    assert code in (2, 3)
    assert "DTD" in (captured.out + captured.err)


def test_broken_shared_strings_do_not_silently_empty_other_sheets(tmp_path,
                                                                  examples_dir,
                                                                  capsys):
    """빈 공유 문자열 목록을 캐시하면 그 뒤 모든 시트가 문자열 셀을 잃는다."""
    import zipfile
    src = os.path.join(examples_dir, "정상_채점.xlsx")
    dst = tmp_path / "문자열손상.xlsx"
    wrote = False
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(str(dst), "w") as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item == "xl/sharedStrings.xml":
                data = data[:len(data) // 2]
                wrote = True
            zout.writestr(item, data)
        if not wrote:
            zout.writestr("xl/sharedStrings.xml", b"<sst><si><t>x")
    code = main([str(dst)] + SPEC_ARGS)
    captured = capsys.readouterr()
    assert code in (2, 3)
    assert "Traceback" not in captured.err

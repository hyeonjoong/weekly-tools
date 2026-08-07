"""CLI 전 구간 — 종료코드, 산출물, 그리고 "조용히 통과하지 않음".

두 개의 대칭 테스트가 이 파일의 뼈대다.

* **거짓양성 테스트** — 깨끗한 세 파일에서 경고가 하나라도 뜨면 실패.
  매번 우는 체커는 두 번 다시 안 열린다.
* **조용히 통과하지 않음 테스트** — 결함을 심은 파일에서 해당 유형이
  `문제목록.csv` 에 나오고 종료코드가 0이 아니어야 한다.
"""

from __future__ import annotations

import csv
import os
import sys

import pytest

from conftest import write_bytes, write_rows, write_text, write_xlsx
from joinaudit.cli import main
from joinaudit.report import escape_cell, verify_downstream_schema


def read_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.reader(fh))


def issue_kinds(out_dir):
    rows = read_csv(os.path.join(out_dir, "문제목록.csv"))
    return {r[4] for r in rows[1:]}


def issues_by_severity(out_dir, severity):
    rows = read_csv(os.path.join(out_dir, "문제목록.csv"))
    return [r for r in rows[1:] if r[3] == severity]


# --------------------------------------------------------------------------
# 거짓양성 테스트 — 깨끗한 자료에서는 조용해야 한다
# --------------------------------------------------------------------------

def test_clean_example_set_reports_nothing_and_exits_zero(clean_set, tmp_path,
                                                          capsys):
    out = str(tmp_path / "결과")
    code = main([*clean_set, "--align", "night", "--out-dir", out])
    assert code == 0, capsys.readouterr().out
    assert issues_by_severity(out, "심각") == []
    assert issues_by_severity(out, "경고") == []
    for name in ("merged.csv", "병합감사.md", "문제목록.csv", "키매칭표.csv"):
        assert os.path.exists(os.path.join(out, name))


def test_clean_example_merges_every_night(clean_set, tmp_path):
    """16명 × 10밤 = 160행. 자정 넘김 귀속이 없으면 이 숫자가 안 나온다."""
    out = str(tmp_path / "결과")
    main([*clean_set, "--align", "night", "--out-dir", out])
    rows = read_csv(os.path.join(out, "merged.csv"))
    assert len(rows) == 161                      # 헤더 1 + 160
    header = rows[0]
    # 세 파일의 값이 실제로 같은 행에서 만났는가.
    assert "watch_hrv_rmssd_ms" in header
    assert "diary_총수면시간_min" in header
    assert "isi_isi_total" in header
    for row in rows[1:]:
        cells = dict(zip(header, row))
        assert cells["watch_hrv_rmssd_ms"]       # 워치 값이 비어 있지 않다
        assert cells["diary_총수면시간_min"]
        assert cells["isi_isi_total"]


def test_clean_example_output_passes_downstream_schema(clean_set, tmp_path):
    out = str(tmp_path / "결과")
    main([*clean_set, "--align", "night", "--out-dir", out])
    assert verify_downstream_schema(os.path.join(out, "merged.csv")) == []


def test_n_flow_arithmetic_holds_on_the_clean_set(clean_set, tmp_path, capsys):
    main([*clean_set, "--align", "night", "--out-dir", str(tmp_path / "o")])
    text = capsys.readouterr().out
    assert "입력 행 합계" in text and "336" in text
    audit = open(str(tmp_path / "o" / "병합감사.md"), encoding="utf-8").read()
    assert "입력 336 = 기여 336 + 제외 0" in audit


# --------------------------------------------------------------------------
# 조용히 통과하지 않음 테스트
# --------------------------------------------------------------------------

def test_flawed_example_set_reports_each_planted_defect(flawed_set, tmp_path):
    out = str(tmp_path / "결과")
    code = main([*flawed_set, "--align", "night",
                 "--alias", os.path.join(os.path.dirname(flawed_set[0]), "alias.csv"),
                 "--spec", os.path.join(os.path.dirname(flawed_set[0]), "spec.json"),
                 "--unify-id-heads", "--out-dir", out])
    assert code == 2
    kinds = issue_kinds(out)
    assert "중복키" in kinds                 # 재업로드로 같은 밤이 두 번
    assert "키정규화충돌" in kinds           # 전각/반각 표기가 한 키로 합쳐짐
    assert "범위이탈" in kinds               # ISI 45 (spec: 0~28)
    assert "단위의심" in kinds               # 시간 vs 분


def test_id_mismatch_is_surfaced_when_the_alias_table_is_missing(flawed_set,
                                                                 tmp_path):
    """별칭표 없이 돌리면 겹치지 않는 파일이 있다고 크게 알려야 한다."""
    out = str(tmp_path / "결과")
    code = main([*flawed_set, "--align", "night", "--out-dir", out])
    assert code != 0
    assert "키겹침없음" in issue_kinds(out)


def test_broken_dates_are_counted_not_swallowed(flawed_set, tmp_path):
    out = str(tmp_path / "결과")
    main([*flawed_set, "--align", "night", "--unify-id-heads", "--out-dir", out])
    audit = open(os.path.join(out, "병합감사.md"), encoding="utf-8").read()
    assert "날짜/시점 해석 실패" in audit


def test_mixed_timezone_blocks_the_merge_with_exit_3(examples_dir, tmp_path):
    out = str(tmp_path / "결과")
    code = main([os.path.join(examples_dir, "flawed", "watch_hrv.csv"),
                 os.path.join(examples_dir, "flawed", "respiration_tz.csv"),
                 "--align", "night", "--out-dir", out])
    assert code == 3
    assert "타임존혼재" in issue_kinds(out)
    # 병합 불가일 때는 merged.csv 를 만들지 않는다 — 반쪽 표가 더 위험하다.
    assert not os.path.exists(os.path.join(out, "merged.csv"))


# --------------------------------------------------------------------------
# 확신이 없으면 멈춘다 (종료코드 3)
# --------------------------------------------------------------------------

def test_two_key_candidates_stop_instead_of_guessing(tmp_path, capsys):
    # `subject_id` 와 `연구번호` 는 둘 다 1급 피험자 키 이름이다 — 둘 중 하나를
    # 툴이 골라 버리면 조용히 틀린 표가 나온다.
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "연구번호", "date", "v"],
                    ["S01", "R01", "2026-03-01", "1"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"], ["S01", "2026-03-01", "9"]])
    code = main([a, b, "--out-dir", str(tmp_path / "o")])
    assert code == 3
    assert "--key" in capsys.readouterr().out


def test_explicit_key_resolves_the_ambiguity(tmp_path):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "연구번호", "date", "v"],
                    ["S01", "R01", "2026-03-01", "1"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"], ["S01", "2026-03-01", "9"]])
    assert main([a, b, "--key", "subject_id", "--out-dir",
                 str(tmp_path / "o")]) == 0


def test_per_file_key_option(tmp_path):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["대상자", "date", "v"], ["S01", "2026-03-01", "1"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"], ["S01", "2026-03-01", "9"]])
    assert main([a, b, "--key", "a.csv=대상자", "--key", "b.csv=subject_id",
                 "--out-dir", str(tmp_path / "o")]) == 0


def test_unknown_file_in_per_file_option_is_an_error(tmp_path, capsys):
    a = write_rows(str(tmp_path / "a.csv"), [["id", "v"], ["S01", "1"]])
    b = write_rows(str(tmp_path / "b.csv"), [["id", "w"], ["S01", "9"]])
    assert main([a, b, "--key", "없는파일.csv=id",
                 "--out-dir", str(tmp_path / "o")]) == 1


def test_ambiguous_date_format_stops_with_exit_3(tmp_path, capsys):
    rows = [["id", "date", "v"]] + [[f"S{i:02d}", f"0{i}/01/2026", i]
                                    for i in range(1, 8)]
    a = write_rows(str(tmp_path / "a.csv"), rows)
    b = write_rows(str(tmp_path / "b.csv"),
                   [["id", "date", "w"], ["S01", "2026-01-03", "9"]])
    code = main([a, b, "--out-dir", str(tmp_path / "o")])
    assert code == 3
    assert "--date-format" in capsys.readouterr().out


def test_date_format_option_unblocks_it(tmp_path):
    rows = [["id", "date", "v"]] + [[f"S{i:02d}", f"0{i}/01/2026", i]
                                    for i in range(1, 8)]
    a = write_rows(str(tmp_path / "a.csv"), rows)
    b = write_rows(str(tmp_path / "b.csv"),
                   [["id", "date", "w"], ["S01", "2026-01-03", "9"]])
    out = str(tmp_path / "o")
    assert main([a, b, "--date-format", "dmy", "--out-dir", out]) in (0, 2)
    merged = read_csv(os.path.join(out, "merged.csv"))
    assert any(r[1] == "2026-01-03" for r in merged[1:])


# --------------------------------------------------------------------------
# --inspect
# --------------------------------------------------------------------------

def test_inspect_prints_detection_and_does_not_write(clean_set, tmp_path,
                                                     capsys):
    out = str(tmp_path / "결과")
    code = main([*clean_set, "--inspect", "--out-dir", out])
    assert code == 0
    text = capsys.readouterr().out
    assert "피험자 키: subject_id" in text
    assert "measured_at" in text
    assert not os.path.exists(os.path.join(out, "merged.csv"))


def test_inspect_works_on_a_single_file(clean_set, tmp_path, capsys):
    assert main([clean_set[0], "--inspect"]) == 0
    assert "watch_hrv.csv" in capsys.readouterr().out


# --------------------------------------------------------------------------
# 안전
# --------------------------------------------------------------------------

def test_refuses_to_overwrite_an_input_file(tmp_path, capsys):
    out = tmp_path / "o"
    out.mkdir()
    a = write_rows(str(out / "merged.csv"),
                   [["id", "date", "v"], ["S01", "2026-03-01", "1"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["id", "date", "w"], ["S01", "2026-03-01", "9"]])
    code = main([a, b, "--out-dir", str(out)])
    assert code == 1
    assert "덮어쓰지" in capsys.readouterr().err
    # 원본이 그대로다.
    assert read_csv(a)[1] == ["S01", "2026-03-01", "1"]


def test_input_files_are_never_modified(clean_set, tmp_path):
    before = {p: open(p, "rb").read() for p in clean_set}
    # 인자 파싱에서 빠져나가면 아무 일도 안 하고 통과해 버리므로 성공까지 확인한다.
    assert main([*clean_set, "--align", "night",
                 "--out-dir", str(tmp_path / "o")]) == 0
    for path, blob in before.items():
        assert open(path, "rb").read() == blob


@pytest.mark.parametrize("payload", ["=1+1", "+1+1", "-1+1", "@SUM(A1)",
                                     "=cmd|'/c calc'!A1", "\t=1+1", "-A1"])
def test_csv_formula_injection_is_escaped(payload):
    assert escape_cell(payload).startswith("'")


@pytest.mark.parametrize("number", ["-3.2", "-1", "+5", "+1", "0.5", "1e-3"])
def test_plain_numbers_are_not_escaped(number):
    """`-3.2` 를 `'-3.2` 로 만들면 하류 통계 툴이 전부 결측으로 읽는다.

    `+1` / `-1` 은 엑셀이 수식으로 보더라도 그 값 자체로 평가되므로 위험하지
    않다. 숫자가 아닌 것이 `=`/`+`/`-`/`@` 로 시작할 때만 막는다.
    """
    assert escape_cell(number) == number


def test_formula_in_a_cell_is_escaped_in_every_output(tmp_path):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["id", "date", "메모"],
                    ["=HYPERLINK(\"http://x\")", "2026-03-01", "=1+1"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["id", "date", "w"], ["S01", "2026-03-01", "9"]])
    out = str(tmp_path / "o")
    main([a, b, "--out-dir", out])
    for name in ("merged.csv", "문제목록.csv", "키매칭표.csv"):
        raw = open(os.path.join(out, name), encoding="utf-8-sig").read()
        for line in raw.splitlines():
            for cell in next(csv.reader([line])):
                assert not cell.startswith("=") and not cell.startswith("@")


def test_no_network_or_process_calls_at_runtime(clean_set, tmp_path):
    """네트워크 호출 0 — 텍스트 검사가 아니라 **런타임 감사 훅**으로 확인한다.

    소스를 grep 하는 검사는 `__import__("socket")` 하나로 통과한다. 여기서는
    실제 실행 중 발생한 이벤트를 본다.
    """
    if not hasattr(sys, "addaudithook"):
        pytest.skip("audit hook 이 없는 파이썬")
    seen = []

    def hook(event, _args):
        if event.split(".")[0] in ("socket", "urllib", "subprocess", "os") \
                and event not in ("os.listdir", "os.scandir", "os.stat",
                                  "os.chmod", "os.mkdir", "os.remove",
                                  "os.rename", "os.symlink", "os.link"):
            if event.startswith(("socket.", "urllib.", "subprocess.")):
                seen.append(event)

    sys.addaudithook(hook)
    main([*clean_set, "--align", "night", "--out-dir", str(tmp_path / "o")])
    assert seen == [], seen


def test_source_declares_no_third_party_dependencies():
    """`dependencies = []` 이 실제로 지켜지는지 import 문으로 확인한다."""
    import ast
    import joinaudit
    root = os.path.dirname(os.path.abspath(joinaudit.__file__))
    stdlib = {"csv", "io", "math", "os", "re", "stat", "json", "datetime",
              "unicodedata", "zipfile", "xml", "argparse", "sys", "typing",
              "dataclasses", "errno", "collections", "itertools", "functools",
              "__future__"}
    for name in sorted(os.listdir(root)):
        if not name.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(root, name), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top in stdlib, (name, alias.name)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                top = (node.module or "").split(".")[0]
                assert top in stdlib, (name, node.module)


def test_free_text_values_are_not_echoed_to_the_screen(tmp_path, capsys):
    """리포트에 이름·연락처 같은 자유기재 값이 흘러나오지 않아야 한다."""
    rows = [["id", "date", "담당자", "v"]]
    for i in range(1, 9):
        rows.append([f"S{i:02d}", f"2026-03-0{(i % 3) + 1}",
                     f"홍길동{i} 010-1234-56{i:02d}", i])
    a = write_rows(str(tmp_path / "a.csv"), rows)
    b = write_rows(str(tmp_path / "b.csv"),
                   [["id", "date", "w"], ["S01", "2026-03-01", "9"]])
    out = str(tmp_path / "o")
    main([a, b, "--out-dir", out])
    text = capsys.readouterr().out
    assert "홍길동" not in text and "010-1234" not in text
    problems = open(os.path.join(out, "문제목록.csv"), encoding="utf-8-sig").read()
    assert "홍길동" not in problems


def test_long_cell_values_are_truncated_in_issue_messages(tmp_path):
    """원본 셀을 통째로 리포트에 옮기지 않는다(길이 상한)."""
    long_value = "환자메모" * 200
    rows = [["id", "date", "메모"]]
    for i in range(1, 6):
        rows.append([f"S{i:02d}", "2026-03-01", long_value])
    a = write_rows(str(tmp_path / "a.csv"), rows)
    b = write_rows(str(tmp_path / "b.csv"),
                   [["id", "date", "w"], ["S01", "2026-03-01", "9"]])
    out = str(tmp_path / "o")
    main([a, b, "--out-dir", out])
    for row in read_csv(os.path.join(out, "문제목록.csv"))[1:]:
        assert len(row[5]) < 400, row[4]


# --------------------------------------------------------------------------
# 입력이 이상할 때
# --------------------------------------------------------------------------

def test_one_file_is_refused(tmp_path, capsys):
    a = write_rows(str(tmp_path / "a.csv"), [["id", "v"], ["S01", "1"]])
    assert main([a, "--out-dir", str(tmp_path / "o")]) == 1
    assert "2개 이상" in capsys.readouterr().err


def test_no_files_is_refused(capsys):
    assert main([]) == 1


def test_missing_file_is_reported_clearly(tmp_path, capsys):
    a = write_rows(str(tmp_path / "a.csv"), [["id", "v"], ["S01", "1"]])
    assert main([a, str(tmp_path / "nope.csv")]) == 1
    assert "찾을 수 없습니다" in capsys.readouterr().err


def test_empty_data_file_is_reported(tmp_path, capsys):
    a = write_text(str(tmp_path / "a.csv"), "id,date,v\n")
    b = write_rows(str(tmp_path / "b.csv"),
                   [["id", "date", "w"], ["S01", "2026-03-01", "9"]])
    code = main([a, b, "--out-dir", str(tmp_path / "o")])
    # 값이 한 행도 없으니 '문제 없음'일 수 없다. 어떤 코드로 끝나든 진단이 남아야 한다.
    assert code in (2, 3), code


def test_single_row_files_work(tmp_path):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["id", "date", "v"], ["S01", "2026-03-01", "1"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["id", "date", "w"], ["S01", "2026-03-01", "9"]])
    out = str(tmp_path / "o")
    assert main([a, b, "--out-dir", out]) == 0
    assert len(read_csv(os.path.join(out, "merged.csv"))) == 2


def test_bad_night_cutoff_is_rejected(tmp_path, capsys):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["id", "date", "v"], ["S01", "2026-03-01", "1"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["id", "date", "w"], ["S01", "2026-03-01", "9"]])
    assert main([a, b, "--night-cutoff", "25:99"]) == 1
    assert "night-cutoff" in capsys.readouterr().err


def test_negative_tolerance_is_rejected(tmp_path):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["id", "date", "v"], ["S01", "2026-03-01", "1"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["id", "date", "w"], ["S01", "2026-03-01", "9"]])
    assert main([a, b, "--tolerance-days", "-1"]) == 1


def test_broken_spec_json_is_reported(tmp_path, capsys):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["id", "date", "v"], ["S01", "2026-03-01", "1"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["id", "date", "w"], ["S01", "2026-03-01", "9"]])
    spec = write_text(str(tmp_path / "spec.json"), "{ not json ")
    assert main([a, b, "--spec", spec]) == 1
    assert "JSON" in capsys.readouterr().err


# --------------------------------------------------------------------------
# 출력 형식
# --------------------------------------------------------------------------

def test_long_format_output(clean_set, tmp_path):
    out = str(tmp_path / "o")
    main([*clean_set, "--align", "night", "--long", "--out-dir", out])
    rows = read_csv(os.path.join(out, "merged.csv"))
    assert rows[0] == ["subject_id", "timepoint", "variable", "value"]
    assert all(len(r) == 4 for r in rows)
    assert all(r[3] != "" for r in rows[1:])       # 결측은 행을 만들지 않는다


def test_coverage_table_marks_the_missing_file(flawed_set, tmp_path):
    out = str(tmp_path / "o")
    main([*flawed_set, "--align", "night", "--unify-id-heads", "--out-dir", out])
    rows = read_csv(os.path.join(out, "키매칭표.csv"))
    header, body = rows[0], rows[1:]
    watch = header.index("watch_hrv.csv")
    # 수면일기에만 있는 S21 은 워치에 없다.
    s21 = [r for r in body if r[0] == "S21"]
    assert s21 and s21[0][watch] == "0"


def test_audit_report_contains_a_methods_paragraph(clean_set, tmp_path):
    out = str(tmp_path / "o")
    main([*clean_set, "--align", "night", "--out-dir", out])
    audit = open(os.path.join(out, "병합감사.md"), encoding="utf-8").read()
    assert "## 9. Methods 초안" in audit
    assert "유사도 기반 추정 매칭은 사용하지 않았다" in audit
    assert "no similarity-based or fuzzy matching was applied" in audit


def test_visit_alignment_end_to_end(tmp_path):
    a = write_rows(str(tmp_path / "isi.csv"),
                   [["id", "visit", "isi"],
                    ["S01", "BL", "18"], ["S01", "W4", "11"],
                    ["S02", "기저", "21"], ["S02", "4주", "15"]])
    b = write_rows(str(tmp_path / "psqi.csv"),
                   [["id", "방문", "psqi"],
                    ["S01", "baseline", "12"], ["S01", "week4", "8"],
                    ["S02", "baseline", "14"], ["S02", "week4", "9"]])
    out = str(tmp_path / "o")
    code = main([a, b, "--align", "visit", "--how", "inner", "--out-dir", out])
    assert code == 0
    rows = read_csv(os.path.join(out, "merged.csv"))
    assert len(rows) == 5
    assert {r[1] for r in rows[1:]} == {"baseline", "week4"}


def test_unknown_visit_label_is_reported_not_guessed(tmp_path):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["id", "visit", "v"],
                    ["S01", "baseline", "1"], ["S01", "2차추적", "2"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["id", "visit", "w"],
                    ["S01", "baseline", "9"], ["S01", "2차추적", "8"]])
    out = str(tmp_path / "o")
    code = main([a, b, "--align", "visit", "--out-dir", out])
    assert code == 2
    assert "미등록시점라벨" in issue_kinds(out)
    # 그래도 원본 표기끼리는 붙는다.
    rows = read_csv(os.path.join(out, "merged.csv"))
    assert len(rows) == 3


def test_quiet_mode_prints_one_line(clean_set, tmp_path, capsys):
    main([*clean_set, "--align", "night", "--quiet",
          "--out-dir", str(tmp_path / "o")])
    assert len(capsys.readouterr().out.strip().splitlines()) == 1

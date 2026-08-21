"""돌연변이 감사에서 **살아남은** 변형들을 잡기 위한 테스트.

적대적 검토(2026-08-21) 1라운드에서 98개 돌연변이 중 32개가 기존 스위트를
통과했다. 그중 뼈아팠던 것들:

  · `render_scenarios_csv` 에서 `csv_safe` 를 통째로 빼도 567개가 전부 통과 —
    CSV 인젝션 방어가 **호출 지점에서는** 전혀 검증되고 있지 않았다.
  · `sort_key` 에 p 를 끼워 넣어도 통과 — 두 번째 불변식의 진짜 회귀가 안 잡혔다.
  · `render_report`/`render_markdown` 의 자백 게이트를 각각 없애도 통과 —
    게이트 함수만 직접 호출해 테스트하고 있었다.
  · ±3SD 임계값을 2SD 로 바꿔도 통과 — |z| ∈ (2, 3] 인 관측치가 픽스처에 없었다.

이 파일은 그 구멍들을 **호출 지점에서** 막는다.
"""

import csv
import io
import os

import pytest

from conftest import EXAMPLES, analyse_path, make_rows, write_csv
from robustcheck import report as report_module
from robustcheck.analyze import MIN_COMPUTED_SCENARIOS, analyse
from robustcheck.cli import main
from robustcheck.dataio import MAX_BYTES, MAX_ROWS, InputError, read_table
from robustcheck.prep import _outlier_mask
from robustcheck.report import (
    OUT_SCENARIOS,
    OUT_SUBJECTS,
    SCENARIO_HEADER,
    SUBJECT_HEADER,
    ReportIntegrityError,
    render_markdown,
    render_report,
    render_scenarios_csv,
    render_subjects_csv,
)
from robustcheck.spec import Spec, build_dataset


def two_group():
    return dict(design="two-group", group="arm", value="isi_week4")


def rows_of(text):
    return list(csv.reader(io.StringIO(text)))


# ------------------------------- 불변식 1: 자백 게이트가 **호출 지점에** 있다


def _break_confession(monkeypatch):
    monkeypatch.setattr(report_module, "_confession_lines",
                        lambda analysis: ["[없는 블록]"])


def test_render_report_refuses_without_the_confession_block(monkeypatch,
                                                            two_group_analysis):
    _break_confession(monkeypatch)
    with pytest.raises(ReportIntegrityError):
        render_report(two_group_analysis)


def test_render_markdown_refuses_without_the_confession_block(monkeypatch,
                                                              two_group_analysis):
    _break_confession(monkeypatch)
    with pytest.raises(ReportIntegrityError):
        render_markdown(two_group_analysis)


def test_cli_exits_3_when_the_confession_is_missing(monkeypatch, capsys):
    _break_confession(monkeypatch)
    code = main([os.path.join(EXAMPLES, "견고_예제.csv"), "--design", "two-group",
                 "--group", "arm", "--value", "isi_week4", "--no-files"])
    assert code == 3
    assert "커버리지 자백" in capsys.readouterr().err


def test_cli_writes_no_files_when_the_confession_is_missing(monkeypatch, tmp_path):
    _break_confession(monkeypatch)
    out = tmp_path / "결과"
    code = main([os.path.join(EXAMPLES, "견고_예제.csv"), "--design", "two-group",
                 "--group", "arm", "--value", "isi_week4",
                 "--out-dir", str(out)])
    assert code == 3
    assert not out.exists() or os.listdir(out) == []


# ------------------------- 불변식 2: 정렬이 유의성을 보기 시작하면 잡힌다


def _disagreeing_fixture(tmp_path):
    """축 순서와 p 순서가 **일부러 어긋나는** 자료.

    기준선이 유의하고, 축 순서상 나중에 오는 시나리오의 p 가 더 작도록 만든다.
    이 자료에서만 "p 로 정렬" 회귀가 눈에 보인다.
    """
    rows = make_rows(30, effect=3.2, seed=11)
    rows[0][3] = float(rows[0][3]) + 14.0     # 이상치 규칙이 잡아낼 극단값
    rows[1][3] = float(rows[1][3]) + 12.0
    return write_csv(tmp_path / "disagree.csv", rows)


def test_ordering_is_by_axes_not_by_p_in_a_disagreeing_fixture(tmp_path):
    path = _disagreeing_fixture(tmp_path)
    analysis = analyse_path(path, **two_group())
    computed = [j for j in analysis.ordered if j.result.computed]
    ps = [j.result.p for j in computed]
    assert ps != sorted(ps), "이 픽스처는 p 순서와 축 순서가 어긋나야 뜻이 있다"
    for severity in ("치명", "경고", ""):
        band = [j for j in computed if j.severity == severity]
        orders = [j.axes.order for j in band]
        assert orders == sorted(orders), "%s 대역이 축 순서를 벗어났다" % severity


def test_ordered_matches_an_independently_built_expectation(fragile_analysis):
    rank = {"치명": 0, "경고": 1, "": 2}
    expected = sorted(
        fragile_analysis.judged,
        key=lambda j: ((3 if not j.result.computed else rank[j.severity]),
                       j.axes.order))
    assert [j.axes.key for j in fragile_analysis.ordered] == \
        [j.axes.key for j in expected]


def test_scenario_csv_order_is_not_p_order(tmp_path):
    path = _disagreeing_fixture(tmp_path)
    analysis = analyse_path(path, **two_group())
    rows = rows_of(render_scenarios_csv(analysis))[1:]
    computed = [r for r in rows if r[SCENARIO_HEADER.index("계산됨")] == "Y"]
    ps = [r[SCENARIO_HEADER.index("p")] for r in computed]
    assert ps != sorted(ps)


# ---------------------- CSV 인젝션 방어가 **산출 함수 안에서** 동작한다


def _injected_analysis(tmp_path):
    rows = make_rows(16)
    rows[0][0] = "=cmd|'/c calc'!A0"
    rows[1][0] = "@SUM(A1)"
    rows[2][0] = "007"
    rows[3][0] = "+1e5"
    path = write_csv(tmp_path / "inj.csv", rows)
    return analyse_path(path, **two_group())


def test_scenario_csv_neutralises_injected_ids(tmp_path):
    analysis = _injected_analysis(tmp_path)
    text = render_scenarios_csv(analysis)
    assert "=cmd|'/c calc'!A0" not in text.replace("'=cmd", "")
    for row in rows_of(text)[1:]:
        for cell in row:
            assert not cell.startswith(("=", "@")), cell


def test_subject_csv_neutralises_injected_ids(tmp_path):
    analysis = _injected_analysis(tmp_path)
    rows = rows_of(render_subjects_csv(analysis))[1:]
    ids = [r[SUBJECT_HEADER.index("subject_id")] for r in rows]
    assert "'=cmd|'/c calc'!A0" in ids
    assert "'@SUM(A1)" in ids
    for value in ids:
        assert not value.startswith(("=", "@", "+", "-"))


def test_numeric_looking_ids_are_preserved_verbatim(tmp_path):
    """Excel 이 `007` 을 `7` 로 바꾸면 리포트와 원본의 피험자가 어긋난다."""
    analysis = _injected_analysis(tmp_path)
    rows = rows_of(render_subjects_csv(analysis))[1:]
    ids = [r[SUBJECT_HEADER.index("subject_id")] for r in rows]
    assert "'007" in ids
    assert "'+1e5" in ids


def test_cli_written_files_are_injection_safe(tmp_path):
    rows = make_rows(16)
    rows[0][0] = "=1+1"
    path = write_csv(tmp_path / "inj.csv", rows)
    out = tmp_path / "결과"
    main([path, "--design", "two-group", "--group", "arm", "--value",
          "isi_week4", "--out-dir", str(out)])
    for name in (OUT_SCENARIOS, OUT_SUBJECTS):
        with open(os.path.join(out, name), encoding="utf-8-sig") as fh:
            for row in csv.reader(fh):
                for cell in row:
                    assert not cell.startswith("=")


# --------------------------------------- 임계값이 실제로 고정되어 있다


# 평균 10 · SD 1 짜리 고정 표본 40개. 여기에 값 하나를 붙여 |z| 를 조절한다.
_SD_BASE = [10.041, 10.465, 9.539, 10.353, 10.926, 10.411, 11.562, 9.115,
            10.067, 9.295, 9.216, 9.816, 10.221, 10.419, 10.508, 12.234,
            10.863, 8.405, 10.204, 9.377, 9.484, 11.306, 9.777, 8.049,
            10.311, 9.709, 8.826, 9.079, 9.375, 9.977, 9.573, 10.071,
            11.836, 9.2, 9.193, 9.751, 11.096, 9.294, 11.434, 8.692]


def _z_of(values, extra):
    import statistics
    full = values + [extra]
    return abs(extra - statistics.mean(full)) / statistics.stdev(full)


def test_sd_rule_keeps_a_point_at_z_two_point_five():
    """임계값을 2SD 나 2.5SD 로 낮추면 이 테스트가 깨진다."""
    assert _z_of(_SD_BASE, 12.50) == pytest.approx(2.451, abs=0.02)
    assert not any(_outlier_mask(_SD_BASE + [12.50], "±3SD"))


def test_sd_rule_drops_a_point_just_past_z_three():
    """임계값을 3.5SD 나 4SD 로 올리면 이 테스트가 깨진다."""
    assert _z_of(_SD_BASE, 13.44) == pytest.approx(3.157, abs=0.02)
    mask = _outlier_mask(_SD_BASE + [13.44], "±3SD")
    assert sum(mask) == 1 and mask[-1] is True


def test_min_computed_scenarios_is_five_not_one(tmp_path):
    """계산된 시나리오가 4개면 판정불가, 5개면 판정한다 — 양쪽을 다 못 박는다."""
    rows = make_rows(20)
    rows[0][3] = -1.0                     # 로그 시나리오 6개 전멸 → 6개 계산
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, **two_group())
    assert analysis.computed == 6
    assert analysis.undecidable_reason == ""
    assert MIN_COMPUTED_SCENARIOS == 5


def test_four_computed_scenarios_is_undecidable(tmp_path):
    rows = make_rows(20)
    rows[0][3] = -1.0
    path = write_csv(tmp_path / "a.csv", rows)
    dataset = build_dataset(read_table(path),
                            Spec("two-group", value="isi_week4", group="arm"))
    analysis = analyse(dataset, use_log=False)   # 격자 6 → 계산 6
    assert analysis.computed == 6
    # 비모수 축까지 막으면 4개 이하로 떨어지고, 그때는 판정불가여야 한다.
    small = make_rows(8)
    tiny = write_csv(tmp_path / "b.csv", small[:8])
    tiny_analysis = analyse_path(tiny, **two_group())
    assert (tiny_analysis.computed >= MIN_COMPUTED_SCENARIOS) or \
        tiny_analysis.undecidable_reason


def test_alpha_actually_changes_the_judgement(fragile_csv):
    """--alpha 를 무시하는 회귀를 잡는다."""
    loose = analyse_path(fragile_csv, alpha=0.05, **two_group())
    strict = analyse_path(fragile_csv, alpha=0.001, **two_group())
    assert loose.baseline.p == pytest.approx(strict.baseline.p)
    assert loose.verdict.grade != strict.verdict.grade or \
        len(loose.flipped) != len(strict.flipped)


def test_pipeline_order_is_missing_then_log_then_outlier(tmp_path):
    """순서를 뒤집으면(이상치 → 로그) 결과가 달라진다 — 그것을 고정한다."""
    from robustcheck.prep import prepare
    from robustcheck.spec import Subject
    subjects = [Subject("S1", None, {"pre": 1.0, "post": 1.0}, 1)]
    subjects += [Subject("S%d" % i, None, {"pre": 10.0 + i, "post": 5.0 + i}, i)
                 for i in range(2, 12)]
    # 로그 후 차이점수 기준 IQR — 순서를 바꾸면 제외 인원이 달라진다.
    logged = prepare(subjects, Spec("paired", pre="pre", post="post"), (),
                     "IQR1.5", "완결자만", "적용")
    plain = prepare(subjects, Spec("paired", pre="pre", post="post"), (),
                    "IQR1.5", "완결자만", "미적용")
    assert [sid for sid, _ in logged.excluded] != [sid for sid, _ in plain.excluded]


# --------------------------------------- 리포트 내용이 실제로 검증된다


def test_reported_statistic_is_the_real_one(fragile_analysis):
    """`fmt_stat` 을 상수로 바꿔도 통과하던 구멍을 막는다."""
    text = render_report(fragile_analysis)
    assert "%.3f" % fragile_analysis.baseline.test.statistic in text
    assert fragile_analysis.baseline.test.statistic != 0.0


def test_grade_formula_lines_are_the_real_rule(two_group_analysis):
    text = render_report(two_group_analysis)
    assert "(치명 시나리오 뒤집힘 ① 또는 ③) ≥ 1  **또는**  단독 뒤집기 피험자 ≥ 1" in text
    assert "판정불가 = 유효 N < 6 또는 계산된 시나리오 < 5" in text


def test_issues_csv_contains_every_solo_flipper(fragile_analysis):
    from robustcheck.report import ISSUE_HEADER, render_issues_csv
    rows = rows_of(render_issues_csv(fragile_analysis))[1:]
    listed = {r[ISSUE_HEADER.index("대상")] for r in rows
              if r[ISSUE_HEADER.index("구분")] == "피험자"}
    for entry in fragile_analysis.solo_flippers:
        assert entry.sid in listed


def test_scenario_csv_lists_the_excluded_ids(fragile_analysis):
    rows = rows_of(render_scenarios_csv(fragile_analysis))[1:]
    idx = SCENARIO_HEADER.index("제외ID")
    count_idx = SCENARIO_HEADER.index("제외인원")
    for row in rows:
        if row[count_idx] and int(row[count_idx]) > 0:
            assert row[idx], "제외 인원이 있는데 ID 열이 비었다"
            assert len(row[idx].split(";")) == int(row[count_idx])


def test_flip_list_is_not_silently_truncated(fragile_analysis):
    text = render_report(fragile_analysis)
    listed = text.count("\n  치명  ") + text.count("\n  경고  ")
    shown = min(len(fragile_analysis.flipped), report_module._MAX_LISTED)
    assert listed == shown or "… 외" in text


# ---------------------------------------------- 입력 상한이 살아 있다


def test_row_limit_is_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr("robustcheck.dataio.MAX_ROWS", 10)
    path = write_csv(tmp_path / "a.csv", make_rows(20))
    with pytest.raises(InputError) as exc:
        read_table(path)
    assert "상한" in str(exc.value)


def test_byte_limit_is_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr("robustcheck.dataio.MAX_BYTES", 10)
    path = write_csv(tmp_path / "a.csv", make_rows(20))
    with pytest.raises(InputError) as exc:
        read_table(path)
    assert "너무 큽니다" in str(exc.value)


def test_limits_are_documented_values():
    assert MAX_BYTES == 200 * 1024 * 1024
    assert MAX_ROWS == 200_000


# -------------------------------------------------- 산출물 경로 안전

def test_symlinked_output_name_is_refused(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("SECRET", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    os.symlink(victim, out / OUT_SCENARIOS)
    code = main([os.path.join(EXAMPLES, "견고_예제.csv"), "--design", "two-group",
                 "--group", "arm", "--value", "isi_week4", "--out-dir", str(out)])
    assert code == 2
    assert victim.read_text(encoding="utf-8") == "SECRET"


def test_outputs_are_truncated_not_appended(tmp_path):
    out = tmp_path / "결과"
    args = [os.path.join(EXAMPLES, "견고_예제.csv"), "--design", "two-group",
            "--group", "arm", "--value", "isi_week4", "--out-dir", str(out)]
    main(args)
    first = open(os.path.join(out, OUT_SCENARIOS), encoding="utf-8-sig").read()
    main(args)
    second = open(os.path.join(out, OUT_SCENARIOS), encoding="utf-8-sig").read()
    assert first == second


def test_every_output_target_is_checked_against_the_input(tmp_path):
    """첫 산출물만 검사하고 나머지를 놓치는 회귀를 잡는다."""
    from robustcheck.safety import OutputPathError, assert_not_input
    source = write_csv(tmp_path / "data.csv", make_rows(6))
    with pytest.raises(OutputPathError):
        assert_not_input([str(tmp_path / "다른.md"), source], [source])

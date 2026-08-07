"""적대적 리뷰(2026-08-07)에서 나온 결함의 회귀 테스트.

여기 있는 것은 전부 **한 번 실제로 툴을 조용히 틀리게 만들었던** 입력이다.
자세한 경위는 `HARDENING.md`.
"""

from __future__ import annotations

import csv
import os

import pytest

from conftest import write_rows, write_text
from joinaudit.cli import main


def read_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.reader(fh))


def kinds(out_dir):
    return {r[4] for r in read_csv(os.path.join(out_dir, "문제목록.csv"))[1:]}


def audit(out_dir):
    return open(os.path.join(out_dir, "병합감사.md"), encoding="utf-8").read()


# --------------------------------------------------------------------------
# R1-1 (심각) — 허용오차 스냅이 행 순서에 따라 한 행을 조용히 덮어썼다
# --------------------------------------------------------------------------

@pytest.mark.parametrize("order", ["asc", "desc"])
def test_snap_collision_is_order_independent(tmp_path, order):
    """두 시점이 한 기준 날짜로 끌려오면, 순서와 무관하게 둘 다 빠져야 한다.

    이전에는 나중에 오는 그룹의 시점이 이미 기준 날짜와 같으면 충돌 판정을
    건너뛰고 **앞서 옮겨 둔 값을 덮어썼다.** 덮인 행은 원장에 `사용` 으로 남아
    종료코드 0, "문제 없음" 으로 끝났다 — 이 툴이 절대 하면 안 되는 일이다.
    """
    base = write_rows(str(tmp_path / "base.csv"),
                      [["subject_id", "visit_date", "score"],
                       ["S01", "2026-03-10", "10"]])
    rows = [["S01", "2026-03-09", "111"], ["S01", "2026-03-10", "222"]]
    if order == "desc":
        rows.reverse()
    other = write_rows(str(tmp_path / "other.csv"),
                       [["subject_id", "measured_at", "rmssd"]] + rows)
    out = str(tmp_path / "o")
    code = main([base, other, "--tolerance-days", "1", "--out-dir", out])

    assert code == 2, "조용히 성공하면 안 된다"
    assert "시점충돌" in kinds(out)
    merged = read_csv(os.path.join(out, "merged.csv"))
    body = "\n".join(",".join(r) for r in merged[1:])
    # 어느 쪽도 몰래 채택되지 않았다.
    assert "111" not in body and "222" not in body
    # 그리고 두 행 모두 사유가 붙었다.
    assert "시점 충돌" in audit(out)


def test_snap_still_works_when_there_is_no_collision(tmp_path):
    """고쳐 놓고 정상 스냅까지 막아 버리면 안 된다."""
    base = write_rows(str(tmp_path / "base.csv"),
                      [["subject_id", "visit_date", "score"],
                       ["S01", "2026-03-10", "10"]])
    other = write_rows(str(tmp_path / "other.csv"),
                       [["subject_id", "measured_at", "rmssd"],
                        ["S01", "2026-03-11", "222"]])
    out = str(tmp_path / "o")
    main([base, other, "--tolerance-days", "1", "--how", "inner",
          "--out-dir", out])
    merged = read_csv(os.path.join(out, "merged.csv"))
    assert len(merged) == 2 and "222" in merged[1]


# --------------------------------------------------------------------------
# R1-2 (중대) — 파일마다 다른 접두어를 떼면 남남이 한 사람이 됐다
# --------------------------------------------------------------------------

def test_different_prefixes_across_files_do_not_silently_merge(tmp_path):
    """`BELL-001-01` 과 `BELL-002-01` 은 다른 사이트의 다른 사람이다.

    접두어 제거는 파일 단위 판단이라 양쪽 모두 `01` 로 줄어들어 조용히 병합됐고,
    종료코드는 0이었다. 이제는 자동 제거 자체를 취소하고 크게 알린다.
    """
    a = write_rows(str(tmp_path / "site1.csv"),
                   [["subject_id", "measured_at", "rmssd_ms"],
                    ["BELL-001-01", "2026-03-10", "31"],
                    ["BELL-001-02", "2026-03-10", "32"],
                    ["BELL-001-03", "2026-03-10", "33"]])
    b = write_rows(str(tmp_path / "site2.csv"),
                   [["subject_id", "날짜", "총수면시간_min"],
                    ["BELL-002-01", "2026-03-10", "401"],
                    ["BELL-002-02", "2026-03-10", "402"],
                    ["BELL-002-03", "2026-03-10", "403"]])
    out = str(tmp_path / "o")
    code = main([a, b, "--out-dir", out])

    assert code == 2
    assert "접두어불일치" in kinds(out)
    merged = read_csv(os.path.join(out, "merged.csv"))
    # 6명이 그대로 남는다(3명으로 합쳐지지 않는다).
    assert len(merged) == 7
    body = "\n".join(",".join(r) for r in merged[1:])
    for row in merged[1:]:
        # 어느 행에도 site1 의 값과 site2 의 값이 함께 있지 않다.
        assert not (row[2] and row[4]), row


def test_a_single_shared_prefix_is_still_removed(tmp_path):
    """접두어가 한 종류뿐이면 예전처럼 떼야 한다(과잉 방어 금지)."""
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "date", "v"],
                    ["BELL-001-01", "2026-03-10", "1"],
                    ["BELL-001-02", "2026-03-10", "2"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"],
                    ["01", "2026-03-10", "9"], ["02", "2026-03-10", "8"]])
    out = str(tmp_path / "o")
    main([a, b, "--how", "inner", "--out-dir", out])
    assert "접두어불일치" not in kinds(out)
    assert len(read_csv(os.path.join(out, "merged.csv"))) == 3


# --------------------------------------------------------------------------
# R1-3 (중대) — 파일 사이의 타임존 차이는 검사하지 않았다
# --------------------------------------------------------------------------

def test_different_timezones_across_files_block_the_merge(tmp_path):
    """각 열은 일관되지만 파일끼리 다르면, 같은 순간이 다른 날로 갈라진다.

    피험자는 100% 겹치므로 키 겹침 검사도 이것을 잡지 못한다. 결과는 "성공"이고
    표는 두 배로 늘어난 채 값이 서로 만나지 않는다.
    """
    a = write_rows(str(tmp_path / "watch.csv"),
                   [["subject_id", "measured_at", "rmssd_ms"],
                    ["S01", "2026-03-11T14:00:00+09:00", "42"],
                    ["S02", "2026-03-11T14:00:00+09:00", "43"]])
    b = write_rows(str(tmp_path / "resp.csv"),
                   [["subject_id", "measured_at", "rr_bpm"],
                    ["S01", "2026-03-11T05:00:00+00:00", "14"],
                    ["S02", "2026-03-11T05:00:00+00:00", "15"]])
    out = str(tmp_path / "o")
    code = main([a, b, "--align", "night", "--out-dir", out])
    assert code == 3
    assert "파일간타임존불일치" in kinds(out)
    assert not os.path.exists(os.path.join(out, "merged.csv"))


def test_uniform_naive_timestamps_are_not_flagged(tmp_path):
    """오프셋 표기가 아예 없는 평범한 자료에서 울면 안 된다."""
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "measured_at", "v"],
                    ["S01", "2026-03-11 14:00", "1"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "measured_at", "w"],
                    ["S01", "2026-03-11 15:00", "9"]])
    out = str(tmp_path / "o")
    assert main([a, b, "--out-dir", out]) == 0
    assert "파일간타임존불일치" not in kinds(out)


# --------------------------------------------------------------------------
# R1-4 (중대) — --dup-policy mean 에서 N-흐름 산술이 맞지 않았다
# --------------------------------------------------------------------------

def test_mean_policy_arithmetic_adds_up(tmp_path):
    """평균에 반영된 행은 **기여한** 행이다. 제외로 세면 4 = 2 + 0 이 된다."""
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "visit_date", "v"],
                    ["S01", "2026-03-10", "10"], ["S01", "2026-03-10", "30"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "visit_date", "w"],
                    ["S01", "2026-03-10", "5"], ["S02", "2026-03-10", "7"]])
    out = str(tmp_path / "o")
    main([a, b, "--dup-policy", "mean", "--out-dir", out])
    text = audit(out)
    assert "산술 확인: 입력 4 = 기여 4 + 제외 0" in text
    assert "제외된 행 없음" in text
    # 평균은 손으로 계산한 값과 같아야 한다: (10 + 30) / 2 = 20
    merged = read_csv(os.path.join(out, "merged.csv"))
    row = [r for r in merged[1:] if r[0] == "S01"][0]
    assert "20" in row


def test_mean_rows_that_end_up_unused_are_recounted(tmp_path):
    """평균에 반영됐어도 그 그룹이 최종 표에 못 들면 '기여'가 아니다."""
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "visit_date", "v"],
                    ["S01", "2026-03-10", "10"],
                    ["S02", "2026-03-10", "1"], ["S02", "2026-03-10", "3"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "visit_date", "w"],
                    ["S01", "2026-03-10", "5"]])
    out = str(tmp_path / "o")
    main([a, b, "--dup-policy", "mean", "--how", "inner", "--out-dir", out])
    text = audit(out)
    assert "산술 확인: 입력 4 = 기여 2 + 제외 2" in text


# --------------------------------------------------------------------------
# R1-5 (중대) — 피험자 단위 파일을 --how left 의 기준으로 잡으면 표가 비었다
# --------------------------------------------------------------------------

def test_left_join_on_a_subject_level_base_keeps_the_data(tmp_path):
    """기준 파일에 시점이 없으면 그 키는 (피험자, None) 이라 아무와도 안 만났다."""
    base = write_rows(str(tmp_path / "base.csv"),
                      [["subject_id", "visit_date", "score"],
                       ["S01", "2026-03-10", "10"],
                       ["S02", "2026-03-10", "20"]])
    isi = write_rows(str(tmp_path / "isi.csv"),
                     [["subject_id", "isi_total"], ["S01", "24"], ["S03", "19"]])
    out = str(tmp_path / "o")
    main([base, isi, "--how", "left", "--base", "isi.csv", "--out-dir", out])
    merged = read_csv(os.path.join(out, "merged.csv"))
    header, body = merged[0], merged[1:]
    s01 = [r for r in body if r[0] == "S01"][0]
    # S01 은 기준 파일에도 있고 시점 있는 파일에도 있다 — 값이 모두 있어야 한다.
    assert s01[header.index("base_score")] == "10"
    assert s01[header.index("isi_isi_total")] == "24"
    # 기준 파일에만 있는 S03 도 살아남는다(left join 의 뜻).
    assert any(r[0] == "S03" for r in body)


# --------------------------------------------------------------------------
# R1-6 (중대) — 결과가 0행인데 "문제 없음"으로 끝났다
# --------------------------------------------------------------------------

def test_an_empty_result_is_not_reported_as_success(tmp_path):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "visit_date", "v"], ["S01", "2026-03-10", "1"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "visit_date", "w"], ["S99", "2026-03-10", "9"]])
    out = str(tmp_path / "o")
    code = main([a, b, "--how", "inner", "--out-dir", out])
    assert code != 0
    assert "결과없음" in kinds(out)


def test_mostly_unmatched_merge_is_flagged(tmp_path):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "visit_date", "v"]]
                   + [[f"S{i:02d}", "2026-03-10", str(i)] for i in range(1, 11)])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "visit_date", "w"],
                    ["S01", "2026-03-10", "9"]])
    out = str(tmp_path / "o")
    code = main([a, b, "--how", "inner", "--out-dir", out])
    assert code == 2
    assert "미매칭과다" in kinds(out)


# --------------------------------------------------------------------------
# R1-7 (유용성) — --align date 로 수면 자료를 넣으면 정상 자료가 중복으로 보였다
# --------------------------------------------------------------------------

def test_duplicate_advice_points_at_align_night_first(clean_set, tmp_path):
    """`--dup-policy first` 를 권하면 사람이 멀쩡한 측정치를 절반 버린다."""
    out = str(tmp_path / "o")
    code = main([*clean_set, "--out-dir", out])          # --align night 없이
    assert code == 2
    assert "야간귀속권고" in kinds(out)
    rows = read_csv(os.path.join(out, "문제목록.csv"))
    dup_advice = [r[6] for r in rows[1:] if r[4] == "중복키"]
    assert dup_advice and all("--align night" in a for a in dup_advice)


def test_the_hint_does_not_fire_when_align_night_is_used(clean_set, tmp_path):
    out = str(tmp_path / "o")
    assert main([*clean_set, "--align", "night", "--out-dir", out]) == 0
    assert "야간귀속권고" not in kinds(out)


# --------------------------------------------------------------------------
# R1-8 (유용성) — 파일 하나가 곧 한 시점인 자료를 정렬할 수 없었다
# --------------------------------------------------------------------------

def test_visit_label_aligns_per_visit_export_files(tmp_path):
    """`설문_기저.csv` / `설문_4주.csv` 는 Phase-3 설문의 표준 모양이다."""
    a = write_rows(str(tmp_path / "설문_기저.csv"),
                   [["id", "isi"], ["S01", "20"], ["S02", "18"]])
    b = write_rows(str(tmp_path / "설문_4주.csv"),
                   [["id", "isi"], ["S01", "12"], ["S02", "15"]])
    out = str(tmp_path / "o")
    code = main([a, b, "--align", "visit",
                 "--visit-label", "설문_기저.csv=기저",
                 "--visit-label", "설문_4주.csv=W4", "--out-dir", out])
    assert code == 0
    merged = read_csv(os.path.join(out, "merged.csv"))
    # 라벨도 사전 정의표를 거치므로 '기저'→baseline, 'W4'→week4 로 정규화된다.
    assert {r[1] for r in merged[1:]} == {"baseline", "week4"}
    assert len(merged) == 5                       # 피험자 2 × 시점 2 + 헤더


def test_align_visit_without_a_visit_column_warns(tmp_path):
    """예전에는 timepoint 가 빈 칸인 표를 만들고 종료코드 0으로 끝났다."""
    a = write_rows(str(tmp_path / "b1.csv"),
                   [["id", "isi"], ["S01", "20"], ["S02", "18"]])
    b = write_rows(str(tmp_path / "b2.csv"),
                   [["id", "isi"], ["S01", "12"], ["S02", "15"]])
    out = str(tmp_path / "o")
    code = main([a, b, "--align", "visit", "--out-dir", out])
    assert code == 2
    assert "시점라벨없음" in kinds(out)


# --------------------------------------------------------------------------
# R1-9 (유용성) — "제로패딩 189건" 이 깨끗한 자료에서 소음이었다
# --------------------------------------------------------------------------

def test_normalization_line_says_whether_it_actually_merged_anything(
        clean_set, tmp_path, capsys):
    main([*clean_set, "--align", "night", "--out-dir", str(tmp_path / "o")])
    text = capsys.readouterr().out
    assert "서로 다른 표기가 한 사람으로 합쳐진 경우는 없었습니다" in text
    assert "병합 결과를 바꾸지 않았습니다" in text


def test_normalization_line_reports_real_collapses(tmp_path, capsys):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "date", "v"], ["S1", "2026-03-10", "1"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"], ["S01", "2026-03-10", "9"]])
    main([a, b, "--how", "inner", "--out-dir", str(tmp_path / "o")])
    text = capsys.readouterr().out
    assert "한 사람으로 합쳐진 경우 **1건**" in text
    assert "S01/S1" in text or "S1/S01" in text


# ==========================================================================
# 라운드 1 · 견고성 심사관 (약 1,600회 공격) — 크래시는 0건이었지만 아래가 나왔다
# ==========================================================================

def test_named_pipe_input_fails_fast_instead_of_hanging(tmp_path):
    """이름있는 파이프를 그냥 열면 **영원히 멈춘다**(출력도 없다)."""
    fifo = str(tmp_path / "pipe.csv")
    os.mkfifo(fifo)
    a = write_rows(str(tmp_path / "a.csv"), [["id", "v"], ["S01", "1"]])
    # 열리지 않으므로 예외로 즉시 끝나야 한다(멈추면 이 테스트가 영원히 걸린다).
    assert main([a, fifo, "--out-dir", str(tmp_path / "o")]) == 1


def test_character_device_input_is_refused(tmp_path):
    """`/dev/zero` 는 끝이 없다 — 다 읽은 뒤에 상한을 확인하면 이미 늦다."""
    a = write_rows(str(tmp_path / "a.csv"), [["id", "v"], ["S01", "1"]])
    if not os.path.exists("/dev/zero"):
        pytest.skip("/dev/zero 가 없는 환경")
    assert main([a, "/dev/zero", "--out-dir", str(tmp_path / "o")]) == 1


def test_usage_errors_exit_1_not_2(tmp_path, capsys):
    """2는 '경고는 있지만 병합은 됐다'는 뜻이다. 오타가 그 코드를 쓰면 안 된다."""
    a = write_rows(str(tmp_path / "a.csv"), [["id", "v"], ["S01", "1"]])
    b = write_rows(str(tmp_path / "b.csv"), [["id", "w"], ["S01", "9"]])
    with pytest.raises(SystemExit) as exc:
        main([a, b, "--how", "sideways"])
    assert exc.value.code == 1


def test_explicit_visit_column_that_does_not_exist_blocks(tmp_path):
    """`--key`/`--date` 는 멈추는데 `--visit` 만 조용히 무시하고 있었다."""
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "visit", "v"],
                    ["S01", "BL", "1"], ["S01", "W4", "2"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "visit", "w"],
                    ["S01", "BL", "9"], ["S01", "W4", "8"]])
    out = str(tmp_path / "o")
    code = main([a, b, "--align", "visit", "--visit", "없는열", "--out-dir", out])
    assert code == 3
    assert not os.path.exists(os.path.join(out, "merged.csv"))


def test_over_long_row_does_not_shadow_an_existing_column(tmp_path):
    """헤더보다 긴 행에 붙인 이름이 기존 열과 겹치면 원래 값이 통째로 사라졌다."""
    from joinaudit.dataio import load_table
    path = str(tmp_path / "a.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("subject_id,열4,measured_at\n"
                 "S01,a,2026-03-01\n"
                 "S02,b,2026-03-02,EXTRA\n")
    frame = load_table(path)
    assert len(set(frame.header)) == len(frame.header), frame.header
    assert frame.column("열4") == ["a", "b"]          # 원래 값이 살아 있다


def test_generated_column_never_collides_with_the_reserved_ones(tmp_path):
    """`subject_id` 열이 두 개인 표는 pandas 가 하나를 조용히 버린다."""
    a = write_rows(str(tmp_path / "subject.csv"),
                   [["pid", "id", "measured_at"],
                    ["P1", "7", "2026-03-01"], ["P2", "8", "2026-03-01"]])
    b = write_rows(str(tmp_path / "other.csv"),
                   [["pid", "x", "measured_at"],
                    ["P1", "1", "2026-03-01"], ["P2", "2", "2026-03-01"]])
    out = str(tmp_path / "o")
    code = main([a, b, "--key", "pid", "--out-dir", out])
    header = read_csv(os.path.join(out, "merged.csv"))[0]
    assert len(set(header)) == len(header), header
    assert code == 0


def test_missing_tokens_become_blank_cells_in_wide_output(tmp_path):
    """README 는 '결측은 빈 칸'을 약속한다. `NA` 를 흘려보내면 스스로 실패한다."""
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "measured_at", "v"],
                    ["S01", "2026-03-01", "NA"], ["S02", "2026-03-01", "5"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "measured_at", "w"],
                    ["S01", "2026-03-01", "1"], ["S02", "2026-03-01", "2"]])
    out = str(tmp_path / "o")
    code = main([a, b, "--out-dir", out])
    assert code == 0
    assert "스키마검증실패" not in kinds(out)
    rows = read_csv(os.path.join(out, "merged.csv"))
    assert all("NA" not in row for row in rows[1:])


def test_ambiguous_base_is_rejected(tmp_path):
    """두 폴더에 같은 이름의 파일이 있으면 첫 번째를 조용히 골랐다."""
    d1, d2 = tmp_path / "a", tmp_path / "b"
    d1.mkdir(), d2.mkdir()
    f1 = write_rows(str(d1 / "same.csv"),
                    [["id", "date", "v"], ["S01", "2026-03-01", "1"]])
    f2 = write_rows(str(d2 / "same.csv"),
                    [["id", "date", "w"], ["S01", "2026-03-01", "9"]])
    assert main([f1, f2, "--base", "same.csv", "--how", "left"]) == 1


def test_sheet_option_on_a_csv_is_rejected(tmp_path):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["id", "date", "v"], ["S01", "2026-03-01", "1"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["id", "date", "w"], ["S01", "2026-03-01", "9"]])
    assert main([a, b, "--sheet", "Sheet1"]) == 1


def test_fake_xlsx_gets_an_xlsx_shaped_error(tmp_path, capsys):
    """예전에는 CSV 로 읽어 '피험자 ID 열을 못 찾음' 이라는 엉뚱한 말을 했다."""
    fake = write_rows(str(tmp_path / "fake.xlsx"), [["id", "v"], ["S01", "1"]])
    a = write_rows(str(tmp_path / "a.csv"), [["id", "v"], ["S01", "1"]])
    assert main([a, fake]) == 1
    assert "올바른 엑셀 파일이 아닙니다" in capsys.readouterr().err


def test_four_digit_year_below_0100_is_not_rewritten():
    from joinaudit.timeline import parse_date_cell, plan_date_column
    plan = plan_date_column(["0001-01-01", "0002-01-01"])
    parsed = parse_date_cell("0001-01-01", plan)
    assert parsed is not None and parsed.date.year == 1     # 1901 이 아니다


def test_two_digit_year_is_still_expanded():
    from joinaudit.timeline import parse_date_cell, plan_date_column
    plan = plan_date_column(["26.3.10"])
    assert parse_date_cell("26.3.10", plan).date.year == 2026


def test_wide_preamble_row_is_still_skipped(tmp_path):
    """안내문이 데이터만큼 넓으면 폭 규칙만으로는 구분되지 않는다."""
    from joinaudit.dataio import load_table
    path = str(tmp_path / "a.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("2026년 3월 측정 결과,보고서,버전3\n"
                 "\n"
                 "subject_id,measured_at,v\n"
                 "S01,2026-03-01,1\n"
                 "S02,2026-03-02,2\n")
    frame = load_table(path)
    assert frame.header == ["subject_id", "measured_at", "v"]
    assert frame.nrows == 2


def test_infinite_range_bounds_are_rejected(tmp_path):
    from joinaudit.spec import SpecError, load_spec
    path = write_text(str(tmp_path / "spec.json"),
                      '{"ranges": {"rmssd_ms": [0, 1e999]}}')
    with pytest.raises(SpecError) as exc:
        load_spec(path)
    assert "무한대" in str(exc.value)


# ==========================================================================
# 라운드 1 · 안전/테스트품질 감사관
# ==========================================================================

# --------------------------------------------------------------------------
# S1 (높음) — 이름이 달라도 같은 파일이면 원본을 덮어썼다
# --------------------------------------------------------------------------

def test_case_insensitive_filename_does_not_overwrite_an_input(tmp_path):
    """macOS(APFS)는 대소문자를 구분하지 않는다 — `MERGED.CSV` 가 곧 `merged.csv`."""
    out = tmp_path / "r"
    out.mkdir()
    victim = write_rows(str(out / "MERGED.CSV"),
                        [["id", "date", "v"], ["S01", "2026-03-01", "1"]])
    other = write_rows(str(tmp_path / "b.csv"),
                       [["id", "date", "w"], ["S01", "2026-03-01", "9"]])
    before = open(victim, "rb").read()
    code = main([victim, other, "--out-dir", str(out)])
    if open(victim, "rb").read() != before:
        pytest.fail("입력 파일이 덮어써졌습니다")
    # 대소문자를 구분하는 파일시스템에서는 서로 다른 파일이므로 정상 동작한다.
    assert code in (0, 1, 2)


def test_hardlinked_input_is_not_overwritten(tmp_path):
    """경로가 달라도 아이노드가 같으면 같은 파일이다."""
    out = tmp_path / "r"
    out.mkdir()
    victim = write_rows(str(tmp_path / "원본.csv"),
                        [["id", "date", "v"], ["S01", "2026-03-01", "1"]])
    os.link(victim, str(out / "merged.csv"))
    other = write_rows(str(tmp_path / "b.csv"),
                       [["id", "date", "w"], ["S01", "2026-03-01", "9"]])
    before = open(victim, "rb").read()
    assert main([victim, other, "--out-dir", str(out)]) == 1
    assert open(victim, "rb").read() == before


def test_nfd_normalised_korean_filename_is_recognised(tmp_path):
    """Finder·unzip 은 한글 파일 이름을 NFD 로 만든다. realpath 는 정규화하지 않는다."""
    import unicodedata
    out = tmp_path / "r"
    out.mkdir()
    nfd = unicodedata.normalize("NFD", "키매칭표.csv")
    victim = write_rows(str(out / nfd),
                        [["id", "date", "v"], ["S01", "2026-03-01", "1"]])
    other = write_rows(str(tmp_path / "b.csv"),
                       [["id", "date", "w"], ["S01", "2026-03-01", "9"]])
    before = open(victim, "rb").read()
    main([victim, other, "--out-dir", str(out)])
    assert open(victim, "rb").read() == before


# --------------------------------------------------------------------------
# S2 / S5 — 심볼릭 링크를 따라가 엉뚱한 파일을 잘랐다 / 권한이 넓었다
# --------------------------------------------------------------------------

def test_output_does_not_follow_a_symlink(tmp_path):
    precious = tmp_path / "precious.txt"
    precious.write_text("건드리면 안 되는 파일", encoding="utf-8")
    out = tmp_path / "r"
    out.mkdir()
    os.symlink(str(precious), str(out / "merged.csv"))
    a = write_rows(str(tmp_path / "a.csv"),
                   [["id", "date", "v"], ["S01", "2026-03-01", "1"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["id", "date", "w"], ["S01", "2026-03-01", "9"]])
    code = main([a, b, "--out-dir", str(out)])
    assert code == 1
    assert precious.read_text(encoding="utf-8") == "건드리면 안 되는 파일"


def test_outputs_are_owner_readable_only(tmp_path):
    """임상 자료 산출물이 기본 umask 로 0644 가 되면 안 된다."""
    import stat
    a = write_rows(str(tmp_path / "a.csv"),
                   [["id", "date", "v"], ["S01", "2026-03-01", "1"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["id", "date", "w"], ["S01", "2026-03-01", "9"]])
    out = str(tmp_path / "o")
    main([a, b, "--out-dir", out])
    for name in ("merged.csv", "병합감사.md", "문제목록.csv", "키매칭표.csv"):
        mode = os.stat(os.path.join(out, name)).st_mode
        assert not (mode & (stat.S_IRGRP | stat.S_IROTH)), name


# --------------------------------------------------------------------------
# S3 — 데이터 안의 `|` 하나가 마크다운 표를 통째로 어긋나게 했다
# --------------------------------------------------------------------------

def test_pipe_in_a_subject_id_does_not_corrupt_the_markdown_table(tmp_path):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "date", "v"],
                    ["S01|X", "2026-03-01", "1"], ["S02", "2026-03-01", "2"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"], ["S02", "2026-03-01", "9"]])
    out = str(tmp_path / "o")
    main([a, b, "--out-dir", out])
    text = audit(out)
    assert "S01\\|X" in text
    # 커버리지 표의 모든 행이 같은 칸 수를 가진다.
    table = [ln for ln in text.splitlines()
             if ln.startswith("| ") and "`a.csv`" in ln or ln.startswith("| S0")]
    widths = {len(ln.split("|")) for ln in table if ln.startswith("| S0")}
    assert len(widths) <= 1, table


# --------------------------------------------------------------------------
# S4 — 리포트에 원본 셀이 통째로 실렸다
# --------------------------------------------------------------------------

def test_free_text_is_truncated_everywhere_it_can_appear(tmp_path):
    secret = "담당의 박영희 010-3333-4444 세브란스병원"
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "date", "메모"],
                    ["S01", "2026-03-01", secret],
                    ["S02", "2026-03-01", secret],
                    ["S03", "2026-03-01", secret]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"], ["S01", "2026-03-01", "9"]])
    out = str(tmp_path / "o")
    main([a, b, "--out-dir", out])
    problems = open(os.path.join(out, "문제목록.csv"), encoding="utf-8-sig").read()
    assert "010-3333-4444" not in problems
    assert "010-3333-4444" not in audit(out)


# --------------------------------------------------------------------------
# T1 — 원장 검증이 죽은 코드였다(본문을 `return None` 으로 바꿔도 전부 통과)
# --------------------------------------------------------------------------

def test_ledger_verify_catches_a_row_wrongly_marked_as_used(tmp_path):
    """버려진 행이 '사용'으로 둔갑하면 논문의 N이 부푼다 — 반드시 잡혀야 한다."""
    from joinaudit.dataio import load_table
    from joinaudit.detect import Detection
    from joinaudit.issues import IssueLog
    from joinaudit.keys import KeyNormalizer
    from joinaudit.merge import (USED, FilePlan, Ledger, assign_keys_and_times,
                                 make_prefix, merge_files, resolve_duplicates)
    from joinaudit.timeline import VisitNormalizer, plan_date_column
    import datetime as dt

    def build(name, rows, key="id", date_col="date", index=0):
        frame = load_table(write_rows(str(tmp_path / name), rows))
        plan = FilePlan(index=index, frame=frame, prefix=make_prefix(name),
                        key_det=Detection(role="key", column=key,
                                          confidence="명시"))
        plan.time_kind, plan.time_col = "date", date_col
        plan.date_plan = plan_date_column(frame.column(date_col))
        plan.value_columns = [c for c in frame.header if c != key]
        return plan

    plans = [
        build("a.csv", [["id", "date", "v"],
                        ["S01", "2026-03-01", "10"],
                        ["S01", "2026-03-01", "30"]], index=0),
        build("b.csv", [["id", "date", "w"],
                        ["S01", "2026-03-01", "9"]], index=1),
    ]
    issues = IssueLog()
    ledger = Ledger([p.frame.nrows for p in plans])
    normalizer, visits = KeyNormalizer(), VisitNormalizer()
    for plan in plans:
        assign_keys_and_times(plan, normalizer, ledger, issues, "date",
                              dt.time(12, 0), visits)
    for plan in plans:
        resolve_duplicates(plan, ledger, issues, "first", "date")
    result = merge_files(plans, ledger, issues, normalizer, "outer", "date", 0)

    # 정상 상태에서는 조용하다.
    assert result.ledger_error is None

    # 버려졌던 중복 행을 '사용'으로 바꿔치기한다.
    dropped = [i for i in range(2) if ledger.get(0, i) != USED]
    ledger.reassign(0, dropped[0], USED, "조작")
    final_set = set(result.final_keys)
    subjects = {k for k, _ in result.final_keys}
    error = ledger.verify(plans, final_set, subjects)
    assert error is not None and "어긋납니다" in error


def test_ledger_verify_catches_a_value_with_no_backing_row(tmp_path):
    """표에 값이 있는데 근거 행이 없다 = 어디선가 값이 덮어써졌다는 뜻이다."""
    from joinaudit.issues import IssueLog
    from joinaudit.merge import DROP_DUPLICATE, USED, Ledger

    class FakePlan:
        index = 0
        subject_level = False
        backing = {("S1", "2026-03-01"): [0]}

    ledger = Ledger([1])
    ledger.set(0, 0, DROP_DUPLICATE, "버려짐")
    error = ledger.verify([FakePlan()], {("S1", "2026-03-01")}, {"S1"})
    assert error is not None and "근거 행이 없는" in error
